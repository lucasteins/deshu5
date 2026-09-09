# -*- coding: utf-8 -*-
"""Prompt 组装协作者：静态头部、v3/v2.4 主 Prompt、问答对/错题/码值/列上下文、模式骨架、单位提示
（自 engine/sql_generator.py 下沉；SQLGenerator 保留 8 个薄委托，组装细节全部内部化）"""
import json
import re
import time
from typing import Dict, List, Optional

from modules.resources.providers.business_rule import get_caliber_rules, render_caliber_injection
from modules.training.engine.constants import PLACE_KEYWORDS

# ===== 业务专用规则（不常驻 Prompt，按问题关键词条件注入，见 _build_v3_prompt）=====
# 用电量口径（触发词见 _ELEC_TRIGGER_KW）：血泪规则——关联口径与 UNION ALL 全体用户
_ELEC_RULES = """【用电量查询口径——必须严格遵守】
- 默认只用专变表 dwd_cst_meter_energy_day_h_xz：客户/用户级用电量聚合与取极值（用电量TOP-N客户、各供电单位用电量总额、高压客户用电量、单客户最大单日用电量等）、计量点用电量TOP-N，一律只查该表，禁止 UNION ALL 低压表或公变表。
- 日电量表只能经 meter_asset_no 关联（通常通过 dwd_cst_meter_run 桥接），禁止将 cust_id、inst_id 直接 JOIN 到日电量表。
- 低压表 dwd_cst_es_meter_energy_day_l_xz 仅在问题明确要求"低压"口径、或查询单个计量点的日电量明细（计量点可能属低压）时，才与专变表 UNION ALL 使用。
- 仅全社会总量/趋势类问题（如"某日用电量总和""各天总用电量趋势"）：UNION ALL 合并专变表与公变表 dwd_cst_es_meter_energy_day_p 后再聚合；公变表仅限此口径，不与用户级查询混合。
- 日电量三表口径：低压=dwd_cst_es_meter_energy_day_l_xz、专变/高压=dwd_cst_meter_energy_day_h_xz、公变/台区=dwd_cst_es_meter_energy_day_p；电量字段均为 pap_e。
- 查询用电量 TOP-N 的计量点时，SELECT 必须同时包含 meter_asset_no、inst_name、SUM(pap_e) 和 AVG(pap_e)。"""
_ELEC_TRIGGER_KW = ('电量', '用电', 'pap_e')

# 供电单位/行政区划口径（触发词见 _ORG_TRIGGER_KW）
_ORG_RULES = """【供电单位口径——必须严格遵守】
- WHERE 使用具体 mgt_org_code 时，SELECT 同时输出 mgt_org_code 和 mgt_org_name 并按两者 GROUP BY。
- 按行政区划地名（如"杭州""拱墅"）筛选供电单位时，必须用 dim_cst_mgt_org.city_name（地市）/county_name（区县）/station_name（供电所）匹配，禁止用 mgt_org_name LIKE 上级地名——下属单位的 mgt_org_name 不含上级地名，会漏掉全部下属单位。"""
_ORG_TRIGGER_KW = ('供电', '管理单位', '地市', '区县', 'mgt_org') + PLACE_KEYWORDS


class PromptBuilder:
    """wf：工作流配置；db：DatabaseManager（单位提示查业务库）；san：SqlSanitizer（SQL 片段解析）；
    schema_src_fn：Schema 知识源；rag：RAGRetriever（问题分词）；
    retrieve_code_values_fn / concept_map_fn：知识源解析留在 SQLGenerator 侧；collapse_series_fn：曲线列折叠"""

    def __init__(self, wf, db, san, schema_src_fn, rag, retrieve_code_values_fn,
                 concept_map_fn, collapse_series_fn):
        self._wf = wf
        self._db = db
        self._san = san
        self._schema_src_fn = schema_src_fn
        self._rag = rag
        self._retrieve_code_values_fn = retrieve_code_values_fn
        self._concept_map_fn = concept_map_fn
        self._collapse_series_fn = collapse_series_fn
        self._head_cache = None

    def static_prompt_head(self) -> str:
        """极简静态头部（~1K）：通用 SQL 生成纪律。全库 Schema、few-shot 示例已移除
        （由定位表字段上下文 + JOIN 路径提示 + 问答对 RAG 定向覆盖）；
        用电量/供电单位等业务专用规则由 _build_v3_prompt 按问题关键词条件注入。"""
        if self._head_cache is None:
            date_hint = (
                "日期过滤请使用 DATE_FORMAT(date_col, '%Y-%m') = 'YYYY-MM'、DATE_FORMAT(date_col, '%Y-%m-%d') = 'YYYY-MM-DD' 或 BETWEEN 语法。"
            )
            self._head_cache = f"""你是电力营销数据仓库的 SQL 专家。根据下方定位到的表字段与问题，生成一条可执行的 MySQL SELECT 语句。

【生成规则】
1. 只输出一行纯 SELECT（或 WITH...SELECT），不要 Markdown 代码块，不要解释性文字。
2. 只能使用下方 Schema 中出现的表名和字段名，禁止编造；JOIN 必须基于 Schema 列出的关联字段或推荐 JOIN 路径。
3. {date_hint}
4. 聚合函数（SUM/COUNT/AVG/MAX/MIN）必须有 GROUP BY，且分组字段必须出现在 SELECT 中。
5. WHERE 中作为等值筛选条件的维度属性字段（如状态、分类、单位代码）必须同时出现在 SELECT 列表中（多表查询带表别名）。
6. 问题中出现"X 为/是/等于'Y'"的属性值条件时，必须关联 X 所在的维度表，在 WHERE 中写入该条件，并在 SELECT 中输出该维度字段。
7. 输出完整性（重要）：
   a) 任何题目，SELECT 必须保留主业务对象的对外编号和名称（主语为客户/用户时取 cust_no、cust_name；主语为安装点时取 inst_no、inst_name；其余对象同理），以及所属单位（mgt_org_code、mgt_org_name）；
   b) WHERE 中出现的筛选条件字段必须在 SELECT 中保留对应输出列；
   c) 涉及对比/环比/同比/超阈值的题目，对比双方的度量和计算结果列必须全部输出。如"哪些低压用户的电费超过上月30%"：SELECT 客户编号、客户名称、所属单位、本月电费、上月电费、增长比例，缺一不可。
8. TOP N 用 ORDER BY ... DESC LIMIT N；无排序要求时不加 ORDER BY。
9. SELECT 字段使用英文代码名，不要加中文别名；除第 5/6/7 条要求保留的字段外，不要堆砌与问题无关的维度字段；表内部主键标识列（cust_id、inst_id 等 _id 列）仅用于 JOIN/关联，禁止输出到 SELECT（对外编号用 cust_no、inst_no 等）。
10. WHERE 条件值的存储形态严格服从【维度码值】标注：标注"存编码"的字段一律写编码（按"名称=编码 对照"翻译），禁止写中文描述。
11. JOIN 策略（重要）：以问题主语所在的表为主表；维表、弱关联表、仅取属性的关联一律用 LEFT JOIN，防止关联表无匹配记录时主表数据丢失；仅当业务语义确实要求"两侧都必须匹配"时才用 INNER JOIN。多级关联（客户→安装点→计量点→电量）逐级 LEFT JOIN 同理。
12. 生成前自查：问题中每个筛选条件、分组维度、统计指标都必须在 SQL 中有对应体现，禁止省略。
13. 推理从简：内部推理控制在 3 步以内（定位字段→确定关联→写出 SQL），不要反复权衡验证，直接输出最终 SQL。
"""
        return self._head_cache

    def build_v3_prompt(
        self,
        user_question: str,
        intent: Dict,
        pattern_context: str,
        error_context: str,
        draft_sql: str,
        review_feedback: Optional[str] = None,
        previous_error: Optional[str] = None,
        org_hints: str = "",
        qa_context: str = "",
        columns_context: str = "",
        code_value_context: str = "",
        join_hint: str = ""
    ) -> str:
        """构建单次生成 Prompt：静态头部（初始化时构建一次）+ 每题动态部分（业务问题 + 3条参考问答对等）"""

        # 意图摘要
        intent_summary = f"""问题类型：{intent.get('question_type', '查询')}
涉及表：{', '.join(intent.get('tables', []))}
聚合：{', '.join(f.get('agg', '') for f in intent.get('fields', []) if f.get('role') == 'aggregate')}
过滤条件：{json.dumps(intent.get('filters', []), ensure_ascii=False)}
分组：{', '.join(intent.get('group_by', []))}
排序：{json.dumps(intent.get('order_by', []), ensure_ascii=False)}
LIMIT：{intent.get('limit', '无')}"""

        feedback_section = f"\n【审查反馈】{review_feedback}\n请修正后重新生成。" if review_feedback else ''
        draft_section = f"\n【草稿 SQL 参考】\n{draft_sql}\n你可以参考上述草稿的结构，但请根据 Schema 生成最准确的 SQL。" if draft_sql else ''
        error_section = f"\n【上次生成错误】\n{previous_error}\n请根据错误信息修正 SQL，确保字段和表名真实存在、JOIN 条件正确。" if previous_error else ''
        org_section = f"\n{org_hints}" if org_hints else ''

        # 当前日期上下文：供“今年X月/去年/本月”等相对时间换算
        now = time.localtime()
        date_context = (
            f"【当前日期】{time.strftime('%Y-%m-%d', now)}"
            f"（今年={now.tm_year}年、去年={now.tm_year - 1}年、本月={time.strftime('%Y-%m', now)}；"
            f"问题中的“今年X月”“去年X月”“本月”等相对时间必须按此换算）"
        )

        # 业务专用规则按问题关键词条件注入（不命中不付费）
        # 统一走 knowledge_retriever.retrieve（查询无类型过滤，2026-08-19 瘦身后表内也无类型列），
        # 取 items 中触发词命中且带 sql_rule 片段的规则行（item_type='rule'）渲染注入；
        # 检索层异常回退 get_caliber_rules 直读；空表再回退 _ELEC_RULES/_ORG_RULES 代码常量。
        rule_modules = []
        _kr_items = None
        try:
            from core.knowledge_retriever import retrieve as _kr_retrieve
            _kr_items = _kr_retrieve(user_question, top_k=20,
                                     concept_map=self._concept_map_fn('intent'))['items']
        except Exception as e:
            print(f"[WARN] 统一知识检索失败，口径注入回退规则直读: {e}", flush=True)
        if _kr_items is not None:
            _seen_nm = set()
            for _it in _kr_items:
                if _it.get('item_type') != 'rule' or not (_it.get('sql_rule') or '').strip():
                    continue
                if _it.get('scores', {}).get('trigger', 0) <= 0:
                    continue  # 口径注入仍以触发词命中为前提（与原 trigger_words 口径一致）
                if _it['id'] in _seen_nm:
                    continue
                _seen_nm.add(_it['id'])
                rule_modules.append(render_caliber_injection({
                    'name': _it.get('name') or '', 'description': _it.get('description') or '',
                    'sql_rule': _it.get('sql_rule') or '',
                    'sql_tables': _it.get('table_list') or []}))
        else:
            caliber_rules = get_caliber_rules()
            if caliber_rules:
                for _rule in caliber_rules:
                    if any(k in user_question for k in _rule['triggers']):
                        rule_modules.append(render_caliber_injection(_rule))
            else:
                if any(k in user_question for k in _ELEC_TRIGGER_KW):
                    rule_modules.append(_ELEC_RULES)
                if any(k in user_question for k in _ORG_TRIGGER_KW):
                    rule_modules.append(_ORG_RULES)
        modules_section = '\n' + '\n\n'.join(rule_modules) + '\n' if rule_modules else ''
        join_section = f"\n{join_hint}\n" if join_hint else ''

        prompt = f"""{self.static_prompt_head()}
{modules_section}{columns_context}
{join_section}{code_value_context}
{org_section}
{qa_context}
{pattern_context}

{error_context}

{date_context}
【用户问题】
{user_question}

【问题意图解析】
{intent_summary}
{draft_section}
{feedback_section}
{error_section}

【输出】一行纯代码 SQL：
"""
        # 总预算硬顶：超配时从低优先级段开始整段裁撤（错题 → 问答对），
        # JOIN 提示与码值段保护不裁（JOIN 路径是多表正确性的关键证据，且仅数百字符；
        # 条件值正确性依赖码值段，其自身有 2500/3000 软硬顶）；
        # 核心段（极简规则、字段上下文、问题本身）不动
        budget = self._wf['prompt']['budget']
        if len(prompt) > budget:
            for section in (error_context, qa_context):
                if len(prompt) <= budget:
                    break
                if section and section.strip() and section in prompt:
                    prompt = prompt.replace(section, '', 1)
                    print(f"[WARN] prompt 超预算({budget})，裁撤一段: {section.strip().split(chr(10))[0][:20]}", flush=True)
            if len(prompt) > budget:
                print(f"[WARN] prompt 裁撤后仍超预算: {len(prompt)} > {budget}", flush=True)
        return prompt

    def build_prompt(
        self,
        user_question: str,
        schema_context: str,
        qa_context: str,
        related_tables: List[str],
        error_context: Optional[str] = None,
        review_feedback: Optional[str] = None
    ) -> str:
        """构建 LLM Prompt（v2.5 极简版：只保留核心信息，避免模型混淆）"""
        feedback_section = ""
        if review_feedback:
            feedback_section = f"""
【审查反馈】{review_feedback}
请修正后重新生成。"""
        
        # 简化：去掉 few-shot、历史参考、错题修正，避免模型被无关信息干扰
        prompt = f"""你是 MySQL SQL 专家。根据问题生成一行可执行的 SELECT 语句。

【表名白名单——只能用这些表，禁止编造任何其他表名】
{chr(10).join(f'- {t}' for t in related_tables)}

【高频关联路径——客户查用电量，照此 JOIN】
- 只按客户汇总：dim_cst_elec_cons_cust c JOIN dwd_cst_meter_run mr ON c.cust_id = mr.cust_id JOIN 日电量表 e ON mr.meter_asset_no = e.meter_asset_no
- 需要安装点属性时改为经安装点中转：c.cust_id = ie.cust_id（dim_cst_inst_elec_cons ie）、ie.inst_id = mr.inst_id
- 日电量表：低压 dwd_cst_es_meter_energy_day_l_xz / 专变 dwd_cst_meter_energy_day_h_xz / 公变 dwd_cst_es_meter_energy_day_p，电量字段 pap_e；用户级聚合/极值、计量点TOP-N 只用专变表，禁止 UNION ALL；低压表仅明确低压口径或单计量点明细时用；全社会总量/趋势才 UNION ALL 专变+公变；cust_id、inst_id 禁止直接连日电量表

【规则——严格遵守】
- 必须且只能使用上面白名单中列出的表名，禁止编造不存在的表名（如 electricity_usage、customer_id 等）
- 必须以 SELECT 或 WITH 开头
- SELECT 字段用英文代码名，不要加中文别名。字段后的 /* 中文注释 */ 仅作参考，不要写入 SQL
- 不要 Markdown 代码块
- 不要只返回说明性文字或注释，必须输出可执行的 SQL 代码
- 日期用 DATE_FORMAT(date_col, '%Y-%m') 格式（MySQL 语法）
- 字符串匹配用 LIKE 'keyword%' 或 LIKE '%keyword'
- TOP N 用 ORDER BY ... DESC LIMIT N（MySQL 语法）
- WHERE 中作为等值筛选条件的维度属性字段（如状态、分类、单位代码等）必须同时出现在 SELECT 列表中

【可用表结构详细】
{schema_context}
{feedback_section}
【当前问题】
{user_question}

【输出】一行纯代码 SQL，字段名用英文代码名：
"""
        return prompt

    def build_qa_context(self, retrieved_pairs: List[Dict]) -> str:
        """
        构建RAG参考上下文（v3.2 平衡版）
        
        关键原则：展示完整SQL结构供LLM学习，但标注条件值需要替换。
        既保留few-shot学习能力，又防止无脑复制。
        """
        if not retrieved_pairs:
            return ""
        
        lines = []
        total = 0
        cap = self._wf['prompt']['qa_max']
        for i, pair in enumerate(retrieved_pairs[:3], 1):
            question = pair.get('question', '')
            sql = pair.get('standard_sql', '')

            # 提取表关联路径
            tables = self._san.extract_tables_from_sql(sql)
            table_chain = ' → '.join(tables) if len(tables) > 1 else tables[0] if tables else '未知'

            # 提取关键条件字段（用于标注哪些条件需要替换）
            condition_fields = self._san.extract_condition_fields(sql)

            seg = [f"参考{i}（问题：{question}）："]
            seg.append(f"  · 涉及的表：{table_chain}")
            seg.append(f"  · SQL：{sql}")
            if condition_fields:
                seg.append(f"  · 【注意】以下条件值仅适用于'{question}'，当前问题不同时必须替换：{condition_fields}")
            seg.append("")
            seg_len = sum(len(s) + 1 for s in seg)
            if total + seg_len > cap and lines:
                break  # 超配额则少给参考，保住核心段
            lines.extend(seg)
            total += seg_len

        return "\n".join(lines)

    def build_error_context(self, error_records: List[Dict]) -> str:
        if not error_records:
            return ""
        lines = ["【历史错题修正】（以下问题之前曾生成错误SQL，已修正，请勿再犯同样错误）\n"]
        total = len(lines[0])
        cap = self._wf['prompt']['error_max']
        for i, rec in enumerate(error_records[:2], 1):  # 只取最多2个错题
            wrong_sql = rec.get('wrong_sql', '')
            correct_sql = rec.get('correct_sql', '')
            # 截断SQL以避免prompt过长
            if len(wrong_sql) > 200:
                wrong_sql = wrong_sql[:200] + '...'
            if len(correct_sql) > 200:
                correct_sql = correct_sql[:200] + '...'
            seg = [f"--- 错题 #{i} ---"]
            seg.append(f"问题：{rec.get('question', 'N/A')}")
            seg.append(f"❌ 错误SQL（{rec.get('error_type', '错误')}）：{wrong_sql}")
            seg.append(f"✅ 正确SQL：{correct_sql}")
            if rec.get('reason'):
                seg.append(f"原因：{rec['reason']}")
            seg.append("")
            seg_len = sum(len(s) + 1 for s in seg)
            if total + seg_len > cap and len(lines) > 1:
                break  # 超配额则少给错题
            lines.extend(seg)
            total += seg_len
        return '\n'.join(lines)

    def build_org_hints(self, user_question: str) -> str:
        """如果问题中包含已知供电单位名称，注入其代码提示，避免 LIKE 泛化导致错误。"""
        try:
            with self._db.connect_business() as conn:
                rows = conn.execute(
                    "SELECT mgt_org_code, mgt_org_name FROM dim_cst_mgt_org WHERE mgt_org_name IS NOT NULL AND mgt_org_name != ''"
                ).fetchall()
        except Exception:
            return ''
        hints = []
        for code, name in rows:
            if name and name in user_question:
                hints.append(f"- 问题中出现的供电单位 '{name}' 对应 mgt_org_code = '{code}'，请优先使用该代码做等值匹配。")
        if not hints:
            return ''
        return "【供电单位精确匹配提示】\n" + "\n".join(hints) + "\n"

    def build_columns_context(self, tables: List[str], user_question: str = '',
                               ctx: Optional[Dict] = None) -> str:
        """检索定位表的全量字段信息（来自启动时预加载的元数据，每题零 DB 开销）。
        紧凑表除关键列外，按问题关键词补入条件承载列（如“已拆除”对应的拆除状态列）。"""
        ctx = ctx if ctx is not None else {}
        ctx['columns_summary'] = []  # 每表注入行数/总列数摘要（思考事件透出用）
        if not tables:
            return ''
        try:
            preloader = self._schema_src_fn()
        except Exception:
            return ''
        # 问题关键词（用于紧凑表的条件列补入）；通用词不参与，近义词先做小映射
        q_tokens = []
        if user_question:
            try:
                _STOP = {'用电', '客户', '用户', '供电', '电力', '电费', '电量', '单位', '公司',
                         '数据', '信息', '情况', '查询', '统计', '哪些', '什么', '是否', '存在'}
                q_tokens = [t for t in self._rag.extract_keywords(user_question)
                            if len(t) >= 2 and t not in _STOP]
                for alias, target in (('城市', '城乡'), ('农村', '城乡')):
                    if alias in user_question and target not in q_tokens:
                        q_tokens.append(target)
            except Exception:
                q_tokens = []
        lines = ["【相关表字段全量（以下为问题定位到的表的全部字段，SELECT/WHERE/JOIN 只能使用这些字段）】"]
        bridge_tables = ctx.get('bridge_tables') or set()
        full_tables = ctx.get('full_tables')
        max_cols = self._wf['prompt']['column_max_per_table']
        for t in tables:
            cols = preloader.get_columns(t)
            if not cols:
                continue
            comment = preloader.get_table_comment(t)
            # 桥接表与低分表只承担 JOIN/弱相关，给关键列 + 问题条件列即可
            compact = (t in bridge_tables) or (full_tables is not None and t not in full_tables)
            if compact:
                keys = [c for c in cols if c.get('pk') or c['name'].endswith(('_id', '_no', '_code'))
                        or 'date' in c['name'] or c['name'].endswith('_ym')][:10]
                key_names = {c['name'] for c in keys}
                extras = [c for c in cols
                          if c['name'] not in key_names
                          and (c['name'].endswith(('_desc', '_flag'))
                               or any(tok in (c.get('comment') or '') for tok in q_tokens))][:8]
                tag = '桥接表' if t in bridge_tables else '低相关表'
                head = f"表 {t}（{comment}）[仅关键列]：" if comment else f"表 {t}[仅关键列]："
                lines.append(head)
                for c in keys + extras:
                    pk_flag = ' PK' if c.get('pk') else ''
                    line = f"  - {c['name']} {c.get('type') or ''}{pk_flag}".rstrip()
                    if c.get('comment'):
                        line += f"：{c['comment']}"
                    lines.append(line)
                ctx['columns_summary'].append({'table': t, 'shown': len(keys + extras), 'total': len(cols)})
                continue
            lines.append(f"表 {t}（{comment}）：" if comment else f"表 {t}：")
            col_lines = self.format_column_lines(self.prioritize_columns(cols, q_tokens))
            shown = min(len(col_lines), max_cols)
            if len(col_lines) > max_cols:  # 单表列数硬封顶（防超宽维表失控）
                col_lines = col_lines[:max_cols] + [f"  …（其余 {len(col_lines) - max_cols} 列略）"]
            lines.extend(col_lines)
            ctx['columns_summary'].append({'table': t, 'shown': shown, 'total': len(cols)})
        return '\n'.join(lines) if len(lines) > 1 else ''

    def build_code_value_context(self, user_question: str, tables: List[str],
                                  ctx: Optional[Dict] = None) -> str:
        """码值维度上下文：按语义注入问题相关的码值域（可选值、维度↔表字段关系）"""
        ctx = ctx if ctx is not None else {}
        ctx['code_value_hits'] = []
        try:
            domains = self._retrieve_code_values_fn(user_question, tables)
        except Exception as e:
            print(f"[WARN] 码值检索失败: {e}", flush=True)
            return ''
        if not domains:
            return ''
        # 记录命中域供接口透传（前端上下文面板展示）
        ctx['code_value_hits'] = [
            {'code_name': d['code_name'], 'cn_name': d['cn_name'],
             'form': d.get('form'), 'matched': d['matched'],
             'columns': [f"{t}.{c}" for t, c in (d.get('columns') or [])]}
            for d in domains
        ]
        lines = ["【维度码值（WHERE 条件值必须从这里取，禁止臆造；已标注码值所在表与字段）】"]
        cap = self._wf['prompt']['code_value_max']
        hard_cap = int(cap * 1.2)  # 命中域/落列域也不超过的硬顶（模糊命中过多时防失控）
        total = len(lines[0])
        # 排序：定位表落列的域优先（生成真正要用的，如曲线表 data_type），其次问题命中域；
        # 软顶只裁“既没落列也没命中”的域，硬顶一律停
        located = set(tables or [])

        def _dom_located(d):
            return any(t in located for t, _ in (d.get('columns') or []))

        domains = sorted(domains, key=lambda d: (not _dom_located(d), not bool(d.get('matched'))))
        for d in domains:
            matched = d.get('matched') or []
            located_dom = _dom_located(d)
            # 明细瘦身（2026-08-14）：既未命中也未落列的域不进 prompt
            if not matched and not located_dom:
                continue
            seg = []
            cols = d['columns']
            if cols:
                col_desc = '、'.join(f"{t}.{c}" for t, c in cols[:2])
                head = f"- {d['code_name']}（{d['cn_name']}，字段 {col_desc}）"
            else:
                head = f"- {d['code_name']}（{d['cn_name']}，码值域，未直接落列）"
            form = d.get('form')
            if form == '编码':
                head += '【该字段存编码，WHERE 条件请使用编码，不要用中文描述】'
            if matched:
                head += f" ★问题命中: {'、'.join(matched)}"
            seg.append(head)
            if form == '编码' and d.get('pairs'):
                # 对照表：命中域只传命中项，落列未命中域留 5 个样例，并标注域内总项数
                pairs = [(n, c) for n, c in d['pairs'] if c]
                full = d.get('total') or len(pairs)
                if matched:
                    pairs = [nc for nc in pairs if nc[0] in matched]
                else:
                    pairs = pairs[:5]
                pair_txt = '、'.join(f"{n}={c}" for n, c in pairs)
                if pair_txt:
                    suffix = (f'（共{full}项，仅示命中）' if matched and full > len(pairs)
                              else (f'（共{full}项）' if full > len(pairs) else ''))
                    seg.append(f"  名称=编码 对照：{pair_txt}{suffix}")
            elif d['values']:
                full = d.get('total') or len(d['values'])
                vals = matched if matched else d['values'][:5]
                suffix = (f'（共{full}项，仅示命中）' if matched and full > len(vals)
                          else (f'（共{full}项）' if full > len(vals) else ''))
                seg.append(f"  可选值：{'、'.join(vals)}{suffix}")
            seg_len = sum(len(s) + 1 for s in seg)
            if len(lines) > 1:
                if total + seg_len > hard_cap:
                    break
                if total + seg_len > cap and not (d.get('matched') or _dom_located(d)):
                    break  # 软顶：只裁既未命中也未落列的域
            lines.extend(seg)
            total += seg_len
        return '\n'.join(lines)

    def build_pattern_context(self, user_question: str, located_tables: List[str] = None) -> str:
        """从统一知识检索层构建 SQL 模式上下文。

        取 knowledge_retriever.retrieve 统一打分后 sql_rule 为完整骨架（SELECT/WITH 开头）
        的模板行 Top-2（按 定位表∩sql_tables 重叠度），注入参数化骨架 + 一句场景描述
        （description 首行——迁移时 doc_text 场景行保持在前）；
        定位表与任何模式都无重叠时不注入（避免无关模式误导，防误导口径同前）。
        2026-08-19 瘦身：骨架/表列改为 sql_rule/sql_tables，replay_pass 列已删。
        """
        try:
            from core.knowledge_retriever import retrieve
            items = retrieve(user_question, tables=located_tables, top_k=10,
                             concept_map=self._concept_map_fn('intent'))['items']
        except Exception as e:
            print(f"[WARN] 知识检索失败，模式段为空: {e}", flush=True)
            return ''
        # 骨架行 = sql_rule 为完整查询（SELECT/WITH 开头）；规则片段（SUM(...) 等）不算
        skeleton_rows = [it for it in items
                         if re.match(r'(?i)^\s*(SELECT|WITH)\b', it.get('sql_rule') or '')]
        if not skeleton_rows:
            return ''

        located = set(located_tables or [])

        def _path(it):
            return set(it.get('table_list') or [])

        scored = [(len(_path(p) & located), p) for p in skeleton_rows]
        scored = [s for s in scored if s[0] > 0]
        if not scored:
            return ''
        scored.sort(key=lambda x: (-x[0], -x[1].get('total_score', 0)))
        lines = ["【参考 SQL 模式】（按定位表匹配）"]
        for _, p in scored[:2]:
            scene = (p.get('description') or '').split('\n')[0]
            tables_txt = ' + '.join(sorted(_path(p)))
            lines.append(f"模式「{p.get('name', '')}」（适用：{tables_txt}）：{scene}")
            lines.append(p['sql_rule'])
        return '\n'.join(lines)

    def format_column_lines(self, cols: List[Dict]) -> List[str]:
        """格式化字段行；连续编号列（曲线表 96 点）折叠为一行范围描述，避免撑爆 prompt"""
        def fmt(c):
            pk_flag = ' PK' if c.get('pk') else ''
            line = f"  - {c['name']} {c.get('type') or ''}{pk_flag}".rstrip()
            if c.get('comment'):
                line += f"：{c['comment']}"
            return line

        lines = []
        for entry in self._collapse_series_fn(cols):
            if entry[0] == 'range':
                _, first, last, count = entry
                head = f"  - {first['name']} ~ {last['name']}（共 {count} 个连续编号列）"
                if first.get('comment') or last.get('comment'):
                    head += f"：{first.get('comment', '')} ~ {last.get('comment', '')}"
                lines.append(head)
            else:
                lines.append(fmt(entry[1]))
        return lines

    @staticmethod
    def prioritize_columns(cols: List[Dict], q_tokens: List[str] = None) -> List[Dict]:
        """字段按“生成 SQL 价值”重排（同层保持 DDL 原序）：
        PK > 主外键/日期 > 问题命中列 > 维度描述与标志列 > 其余。
        用于列数封顶时优先保留条件与维度承载列。"""
        q_tokens = q_tokens or []

        def rank(c):
            name = c['name']
            cmt = c.get('comment') or ''
            if c.get('pk'):
                return 0
            if name.endswith(('_id', '_no', '_code', '_ym')) or 'date' in name:
                return 1
            if q_tokens and any(tok in cmt for tok in q_tokens):
                return 2
            if name.endswith(('_desc', '_flag')):
                return 3
            return 4

        return [cols[i] for i in sorted(range(len(cols)), key=lambda i: (rank(cols[i]), i))]
