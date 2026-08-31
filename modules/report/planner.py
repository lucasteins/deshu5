# -*- coding: utf-8 -*-
"""报告规划器：用户意图 → 模板匹配 → LLM 分解为标准化子问题 → 标准问答对命中标注。

复用：
- 模板：治理库 report_templates（trigger_words/outline）
- 词表：治理库 business_domains + 本体实体（OntologyService.get_entities）
- 标准问答对匹配：core.context.rag_retriever.retrieve（含 is_usable/schema 校验口径）
- LLM 调用：core.llm_config.call_chat
"""
import json
import os
import re
from datetime import datetime

from core.context import db_manager, rag_retriever, get_ontology_service
from core.llm_config import call_chat

# 标准问答对命中阈值（combined_score = 关键词0.35 + 相似度0.35 + schema有效0.3，满分1.0）
QA_MATCH_THRESHOLD = float(os.environ.get('REPORT_QA_MATCH_THRESHOLD', '0.5'))
# 单意图子问题数量硬顶
MAX_QUESTIONS = int(os.environ.get('REPORT_MAX_QUESTIONS', '10'))

_PLAN_SYSTEM = '你是电力营销数据平台的报告规划器，负责把报告意图拆解为可执行的标准化取数问题。'

_PLAN_PROMPT = '''把用户的报告意图拆解为一组标准化取数问题。

【用户意图】
{intent}

【当前日期】
{today}

{template_block}

【可用业务域（供选题参考，不必全覆盖）】
{domains}

【数据底座实体（主数据/业务数据/统计报表三层，供选题参考）】
{entities}

拆解要求：
1. 每道问题必须是完整自含的一句话：显式带上单位范围与统计期间（如"2026年3月""杭州公司"），禁止省略上下文、禁止使用"该单位/当月"等指代；
2. 每道问题只问一个主题，能用一条 SELECT 查询回答（统计/排名/明细清单均可）；
3. 问题总数 4~{max_q} 道，按报告章节组织；
4. 若用户提到多家单位（如"浙江公司/杭州公司"），关键指标问题应同时覆盖各单位（可用同一问题的分组统计覆盖，不必逐单位重复出题）；
5. 若大纲章节给出了【问数问题】，优先沿用这些问题（按用户意图补全单位/期间后可原样使用），不足时再补充；
6. 只输出严格 JSON，不要输出其他任何文字：
{{"report_title": "报告标题",
  "org_scope": ["单位1", "单位2"],
  "period": "统计期间，如 2026年3月",
  "sections": [{{"section_title": "章节名", "questions": [{{"question": "标准化问题"}}]}}]}}'''

# 模板直挂路径：LLM 只做实例化（填单位/期间），不改写问题本身
_INSTANTIATE_PROMPT = '''用户的报告意图已命中模板《{template_name}》，模板各章节已带标准问数问题。
你的任务只是把这些标准问题实例化：填入意图中的单位与期间（如"2026年3月""杭州公司"），其余表述尽量保持原样。

【用户意图】
{intent}

【当前日期】
{today}

【标准问题清单（按顺序）】
{numbered_questions}

只输出严格 JSON，不要输出其他任何文字：
{{"report_title": "报告标题",
  "org_scope": ["单位1", "单位2"],
  "period": "统计期间，如 2026年3月",
  "concrete_questions": ["实例化后的问题1", "实例化后的问题2"]}}

注意：concrete_questions 必须与标准问题清单一一对应、数量一致、顺序一致。'''


def _extract_json(text: str) -> dict:
    """从 LLM 输出中稳健提取首个 JSON 对象。"""
    text = text.strip()
    text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text, flags=re.MULTILINE).strip()
    start = text.find('{')
    end = text.rfind('}')
    if start < 0 or end <= start:
        raise ValueError(f'LLM 未输出 JSON: {text[:200]}')
    return json.loads(text[start:end + 1])


class ReportPlanner:
    """意图识别 + 任务分解。"""

    # ---------- 模板 ----------

    def load_templates(self, enabled_only: bool = False) -> list:
        sql = 'SELECT id, name, trigger_words, outline, enabled, remark, created_at, updated_at FROM report_templates'
        if enabled_only:
            sql += ' WHERE enabled = 1'
        sql += ' ORDER BY id DESC'
        with db_manager.connect_governance() as conn:
            rows = conn.execute(sql).fetchall()
        out = []
        for r in rows:
            out.append({
                'id': r[0], 'name': r[1],
                'trigger_words': _safe_json(r[2], []),
                'outline': _safe_json(r[3], []),
                'enabled': bool(r[4]), 'remark': r[5] or '',
                'created_at': str(r[6]) if r[6] else None,
                'updated_at': str(r[7]) if r[7] else None,
            })
        return out

    def match_template(self, intent_text: str, template_id=None):
        """按 id 指定或按 trigger_words 命中（命中词数最多者胜）。返回模板 dict 或 None。"""
        try:
            templates = self.load_templates(enabled_only=True)
        except Exception as e:
            print(f'[WARN] 报告模板加载失败: {e}', flush=True)
            return None
        if template_id:
            for t in templates:
                if t['id'] == template_id:
                    return t
            return None
        best, best_hits = None, 0
        for t in templates:
            hits = sum(1 for w in t['trigger_words'] if w and w in intent_text)
            if hits > best_hits:
                best, best_hits = t, hits
        return best

    # ---------- 词表 ----------

    def _domain_vocab(self) -> list:
        try:
            with db_manager.connect_governance() as conn:
                rows = conn.execute(
                    'SELECT DISTINCT domain_name FROM business_domains LIMIT 80').fetchall()
            return [r[0] for r in rows if r[0]]
        except Exception:
            return []

    def _entity_vocab(self) -> list:
        try:
            svc = get_ontology_service()
            names = [e.get('name') for e in svc.get_entities() if e.get('name')]
            return names[:60]
        except Exception:
            return []

    # ---------- 主流程 ----------

    # ---------- 模板直挂：意图 → 标准问答对（不经模糊匹配） ----------

    def _template_qa_map(self, template_id: int) -> list:
        """模板联动的问答对：[(qa_id, question, standard_sql, usable)]。"""
        try:
            with db_manager.connect_governance() as conn:
                rows = conn.execute(
                    'SELECT id, question, standard_sql, is_usable '
                    'FROM qa_pairs WHERE report_template_id = ?',
                    (template_id,)).fetchall()
            return [(r[0], r[1], r[2] or '', bool(r[3])) for r in rows if r[1]]
        except Exception:
            return []

    def _plan_from_template(self, intent_text: str, template: dict, tpl_qas: list,
                            period_hint: str = '', org_hint: str = '') -> dict:
        """模板命中且大纲带问数问题：直接把模板问题实例化（LLM 只补单位/期间），
        qa_id/standard_sql 直挂，不经文本模糊匹配。intent_text 为空时跳过实例化。"""
        # 模板大纲顺序展开的问题清单，回挂 qa 信息
        tpl_items = []  # [(section_title, question_text, qa_id, standard_sql, usable)]
        qa_by_norm = {re.sub(r'\s+', '', q): (qid, sql, usable) for qid, q, sql, usable in tpl_qas}
        qa_by_id = {qid: (q, sql, usable) for qid, q, sql, usable in tpl_qas}
        for sec in (template.get('outline') or []):
            for q in (sec.get('questions') or []):
                text = str(q.get('question') if isinstance(q, dict) else q or '').strip()
                if not text:
                    continue
                # 优先用提炼时携带的 qa_id 直挂（题干文字可能已被泛化改写）；否则按题干规范化匹配
                hint_id = q.get('qa_id') if isinstance(q, dict) else None
                if hint_id and hint_id in qa_by_id:
                    qa_id = hint_id
                    _raw, sql, usable = qa_by_id[hint_id]
                else:
                    qa_id, sql, usable = qa_by_norm.get(re.sub(r'\s+', '', text), (None, '', False))
                tpl_items.append((sec.get('section_title') or '', text, qa_id, sql, usable))
        if not tpl_items:
            return None

        # LLM 只做实例化：把单位/期间填进题干，其余表述保持（无意图输入时跳过实例化）
        usage = {}
        if intent_text:
            numbered = '\n'.join(f'{i + 1}. [{sec}] {q}' for i, (sec, q, *_rest) in enumerate(tpl_items))
            prompt = _INSTANTIATE_PROMPT.format(
                intent=intent_text,
                today=datetime.now().strftime('%Y-%m-%d'),
                template_name=template.get('name', ''),
                numbered_questions=numbered,
            )
            resp = call_chat([{'role': 'user', 'content': prompt}],
                             max_tokens=3000, thinking=False, effort='low')
            usage = resp.get('usage') or {}
            raw = _extract_json(resp['content'])
            concrete = [str(x).strip() for x in (raw.get('concrete_questions') or []) if str(x).strip()]
            if len(concrete) != len(tpl_items):
                # LLM 实例化失败/数量不符：确定性兜底——把"本月/当月"等相对期间替换为意图中的期间，补单位
                period = str(raw.get('period') or '')
                org = '、'.join(str(x) for x in (raw.get('org_scope') or []))
                concrete = []
                for _sec, text, *_r in tpl_items:
                    t = text
                    if period:
                        t = t.replace('本月', period).replace('当月', period).replace('本期', period)
                    if org and org not in t:
                        t = f'{org}{t}'
                    concrete.append(t)
        else:
            raw = {'period': period_hint, 'org_scope': [org_hint] if org_hint else []}
            # 无意图输入（直选模板）：用显式传入的 period/org 做确定性替换
            concrete = []
            for _sec, text, *_r in tpl_items:
                t = text
                if period_hint:
                    t = t.replace('本月', period_hint).replace('当月', period_hint).replace('本期', period_hint)
                if org_hint and org_hint not in t:
                    t = f'{org_hint}{t}'
                concrete.append(t)

        sections, idx = [], 0
        for i, (sec_title, text, qa_id, sql, usable) in enumerate(tpl_items):
            if not sections or sections[-1]['section_title'] != sec_title:
                sections.append({'section_title': sec_title or f'章节{len(sections) + 1}', 'questions': []})
            idx += 1
            qd = {'qid': f'q{idx}', 'question': concrete[i] if concrete else text,
                  'source': 'generated'}
            if qa_id:
                qd['qa_id'] = qa_id
                qd['from_template'] = True
                if sql and usable:
                    qd['source'] = 'qa_pair'
                    qd['standard_sql'] = sql
                    qd['match_score'] = 1.0
            sections[-1]['questions'].append(qd)
        return {
            'report_title': str(raw.get('report_title') or intent_text[:30] or template.get('name') or ''),
            'org_scope': [str(x) for x in (raw.get('org_scope') or [])],
            'period': str(raw.get('period') or ''),
            'intent_text': intent_text,
            'template_id': template['id'],
            'template_name': template['name'],
            'sections': sections,
            'question_count': idx,
            'usage': usage,
        }

    def plan(self, intent_text: str, template_id=None, period_hint: str = '', org_hint: str = '') -> dict:
        """意图 → plan_dict。LLM 失败时抛出异常（由路由层兜底返回 500）。"""
        template = self.match_template(intent_text, template_id)

        # 模板命中且大纲带问数问题：模板问题直挂标准问答对（两步法第一步）；
        # 用户直选模板（template_id 显式传入）时即使未联动问答对也走此路径
        if template:
            tpl_qas = self._template_qa_map(template['id'])
            has_outline_qs = any((s.get('questions') or []) for s in (template.get('outline') or []))
            if tpl_qas or (template_id and has_outline_qs):
                try:
                    plan = self._plan_from_template(intent_text, template, tpl_qas,
                                                    period_hint=period_hint, org_hint=org_hint)
                except Exception as e:
                    print(f'[WARN] 模板直挂实例化失败，回退 LLM 自由分解: {e}', flush=True)
                    plan = None
                if plan:
                    return plan

        if template and template.get('outline'):
            outline_lines = []
            for i, s in enumerate(template['outline']):
                line = f"{i + 1}. {s.get('section_title', '')}：{s.get('hint', '')}"
                qs = [q for q in (s.get('questions') or []) if str(q).strip()]
                if qs:
                    line += '\n   【问数问题】' + '；'.join(str(q).strip() for q in qs)
                outline_lines.append(line)
            template_block = f'【报告模板大纲（必须按此章节组织）】\n模板《{template["name"]}》：\n' + '\n'.join(outline_lines)
        else:
            template_block = '【报告模板大纲】无（请按该类报告的通行结构自行划分章节）'

        prompt = _PLAN_PROMPT.format(
            intent=intent_text,
            today=datetime.now().strftime('%Y-%m-%d'),
            template_block=template_block,
            domains='、'.join(self._domain_vocab()) or '（未配置）',
            entities='、'.join(self._entity_vocab()) or '（未配置）',
            max_q=MAX_QUESTIONS,
        )
        resp = call_chat(
            [{'role': 'system', 'content': _PLAN_SYSTEM},
             {'role': 'user', 'content': prompt}],
            max_tokens=4000, thinking=False, effort='low')
        raw = _extract_json(resp['content'])

        plan = self._normalize(raw, intent_text, template)
        self._annotate_qa_hits(plan)
        plan['usage'] = resp.get('usage') or {}
        return plan

    def _normalize(self, raw: dict, intent_text: str, template) -> dict:
        """把 LLM 输出规整为稳定结构，并截断到 MAX_QUESTIONS。

        模板大纲带问数问题时，按题干规范化匹配回链 qa_id（经 _sync_template_questions 已入库）。
        """
        qa_pairs_tpl = []  # [(raw_question, qa_id, standard_sql, usable)]
        if template:
            try:
                with db_manager.connect_governance() as conn:
                    rows = conn.execute(
                        'SELECT id, question, standard_sql, is_usable '
                        'FROM qa_pairs WHERE report_template_id = ?',
                        (template['id'],)).fetchall()
                qa_pairs_tpl = [(r[1], r[0], r[2] or '', bool(r[3])) for r in rows if r[1]]
            except Exception:
                pass

        def _match_template_qa(text: str):
            """先规范化精确匹配，再 RAG 相似度模糊匹配（LLM 改写题干后仍能回链模板问题）。"""
            norm = re.sub(r'\s+', '', text or '')
            for raw, qa_id, sql, usable in qa_pairs_tpl:
                if re.sub(r'\s+', '', raw) == norm:
                    return qa_id, sql, usable
            best, best_score = None, 0.55
            for raw, qa_id, sql, usable in qa_pairs_tpl:
                score = max(rag_retriever._similarity_score(text, raw),
                            rag_retriever._partial_similarity_score(text, raw))
                if score > best_score:
                    best, best_score = (qa_id, sql, usable), score
            return best or (None, '', False)

        sections, idx, total = [], 0, 0
        for sec in (raw.get('sections') or []):
            if not isinstance(sec, dict):
                continue
            questions = []
            for q in (sec.get('questions') or []):
                text = (q.get('question') if isinstance(q, dict) else q) or ''
                text = str(text).strip()
                if not text or total >= MAX_QUESTIONS:
                    continue
                idx += 1
                total += 1
                qd = {'qid': f'q{idx}', 'question': text, 'source': 'generated'}
                qa_id, std_sql, usable = _match_template_qa(text)
                if qa_id:
                    qd['qa_id'] = qa_id
                    qd['from_template'] = True
                    # 模板问题已带验证过的标准 SQL：直接标标准问答对（执行期复用）
                    if std_sql and usable:
                        qd['source'] = 'qa_pair'
                        qd['standard_sql'] = std_sql
                        qd['match_score'] = 1.0
                questions.append(qd)
            if questions:
                sections.append({'section_title': str(sec.get('section_title') or f'章节{len(sections) + 1}'),
                                 'questions': questions})
        if not sections:
            raise ValueError('LLM 未拆解出有效子问题')
        return {
            'report_title': str(raw.get('report_title') or intent_text[:30]),
            'org_scope': [str(x) for x in (raw.get('org_scope') or [])],
            'period': str(raw.get('period') or ''),
            'intent_text': intent_text,
            'template_id': template['id'] if template else None,
            'template_name': template['name'] if template else None,
            'sections': sections,
            'question_count': total,
        }

    def _annotate_qa_hits(self, plan: dict):
        """每道子问题标注标准问答对命中（命中即携带 standard_sql，执行期复用）。
        已被模板回链补齐 standard_sql 的题跳过（模板链路优先于 RAG 相似度）。"""
        for sec in plan['sections']:
            for q in sec['questions']:
                if q.get('standard_sql'):
                    continue
                try:
                    hits = rag_retriever.retrieve(q['question'], top_k=1)
                except Exception:
                    hits = []
                if hits and hits[0].get('standard_sql') and \
                        (hits[0].get('combined_score') or 0) >= QA_MATCH_THRESHOLD:
                    q['source'] = 'qa_pair'
                    q['qa_id'] = hits[0]['id']
                    q['qa_question'] = hits[0]['question']
                    q['standard_sql'] = hits[0]['standard_sql']
                    q['match_score'] = hits[0]['combined_score']


def _safe_json(text, default):
    try:
        return json.loads(text) if text else default
    except Exception:
        return default
