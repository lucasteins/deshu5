# -*- coding: utf-8 -*-
"""两段式工作流的审计与快速修复协作者：low 档合理性审计（可采纳修正版）+ 报错驱动的定点修复
（自 engine/sql_generator.py 下沉；SQLGenerator 经同名薄委托转发，调用点零改动）"""
import json
import re
import time
from typing import Dict, Optional

import config


class AuditRepair:
    """call_llm：LLM 调用入口（SQLGenerator._call_llm 薄封装）；
    san：SqlSanitizer（清洗管道/校验/试执行）；table_names_fn：合法表名来源"""

    def __init__(self, call_llm, san, table_names_fn):
        self._call_llm_fn = call_llm
        self._san = san
        self._table_names_fn = table_names_fn

    def audit_sql_with_llm(self, user_question: str, sql: str, probe, columns_context: str,
                            join_hint: str = '') -> Optional[Dict]:
        """low 档合理性审计：业务原问题 + SQL + 执行结果/报错 → {"pass":bool, "reason", "sql"?}。

        审计模型走 GEN_AUDIT_EFFORT（默认 low）思考档；失败返回 None（调用方按草稿放行）。"""
        total, sample, headers, err = probe
        if err is None:
            exec_text = f"执行成功，返回 {total} 行。列：{headers}；样例（至多5行）：{json.dumps(sample, ensure_ascii=False, default=str)[:600]}"
        else:
            exec_text = f"执行报错：{err}"
        prompt = f"""你是电力营销数据仓库的 SQL 审计专家。审计下面这条由快速模型生成的 SQL 是否正确回答了业务问题。

【业务问题】
{user_question}

【待审计 SQL】
{sql}

【执行情况】
{exec_text}

【已定位表字段参考】
{columns_context[:3000]}
{join_hint[:600] if join_hint else ''}

【审计要点】
1. 筛选条件与问题一致（时间、分类、状态等无遗漏、无捏造）；条件值形态正确（存编码字段必须用编码值）
2. 主业务对象的编号、名称、所属单位已保留在 SELECT；对比/环比题的双侧度量与比例列齐全
3. JOIN 策略正确：维表/弱关联/仅取属性的关联用 LEFT JOIN（防止主表数据丢失）；JOIN 条件字段合法
4. 聚合必有 GROUP BY 且分组字段在 SELECT 中；日期函数为 MySQL 方言（DATE_FORMAT）
5. 若执行为空结果，判断是条件过严/SQL 逻辑问题还是数据本身无匹配

【输出】只输出 JSON，不要任何其他内容：
- 通过：{{"pass": true}}
- 不通过：{{"pass": false, "reason": "问题简述", "sql": "修正后的完整可执行 SQL"}}
推理从简，直接给结论。"""
        try:
            result = self._call_llm_fn(prompt, user_question=user_question, max_tokens=6000,
                                    thinking=getattr(config, 'GEN_AUDIT_THINKING', False),
                                    effort=getattr(config, 'GEN_AUDIT_EFFORT', 'low'))
            content = result.get('content', '')
            m = re.search(r'\{.*\}', content, re.S)
            if not m:
                return None
            data = json.loads(m.group(0))
            if 'pass' not in data:
                return None
            return data
        except Exception as e:
            print(f"[WARN] SQL 审计调用失败: {e}", flush=True)
            return None

    def audit_stage(self, user_question: str, sql: str, columns_context: str, join_hint: str,
                     think, timers: Dict):
        """两段式审计阶段：执行草稿 → low 档审计 → 必要时采纳修正版（修正版须可执行）。

        返回 (final_sql, exec_error, note)：exec_error 为 None 表示最终 SQL 可执行；
        note 取值 ''（草稿通过/审计不可用）/ 'audited'（采纳审计修正）。"""
        t0 = time.perf_counter()
        total, sample, headers, err = self._san.probe_sql_result(sql)
        think({'kind': 'audit_llm', 'phase': 'start',
               'exec_ok': err is None, 'row_count': total, 'error': (err or '')[:200]})
        verdict = self.audit_sql_with_llm(user_question, sql, (total, sample, headers, err),
                                           columns_context, join_hint)
        timers['audit_llm'] = (time.perf_counter() - t0) * 1000
        if not verdict:
            # 审计不可用：草稿可执行则放行，不可执行交后续修复通道
            think({'kind': 'audit_llm', 'phase': 'done', 'verdict': 'unavailable',
                   'ms': int(timers['audit_llm'])})
            return sql, err, ''
        if verdict.get('pass'):
            think({'kind': 'audit_llm', 'phase': 'done', 'verdict': 'pass',
                   'ms': int(timers['audit_llm'])})
            return sql, err, ''
        fixed_raw = verdict.get('sql') or ''
        reason = (verdict.get('reason') or '')[:200]
        if not fixed_raw.strip():
            think({'kind': 'audit_llm', 'phase': 'done', 'verdict': 'fail_nosql', 'reason': reason,
                   'ms': int(timers['audit_llm'])})
            return sql, err, ''
        # 审计修正版：清洗 + 校验 + 试执行，全部通过才采纳
        fixed = self._san.clean_sql_pipeline(fixed_raw)
        valid = (fixed and self._san.validate_sql(fixed) and not self._san.is_sentinel_sql(fixed)
                 and all(t in set(self._table_names_fn())
                         for t in self._san.extract_tables_from_sql(fixed)))
        if valid:
            _, _, _, err2 = self._san.probe_sql_result(fixed)
            if err2 is None:
                think({'kind': 'audit_llm', 'phase': 'done', 'verdict': 'fixed', 'reason': reason,
                       'ms': int(timers['audit_llm'])})
                return fixed, None, 'audited'
            think({'kind': 'audit_llm', 'phase': 'done', 'verdict': 'fix_still_broken',
                   'reason': (err2 or '')[:200], 'ms': int(timers['audit_llm'])})
        else:
            think({'kind': 'audit_llm', 'phase': 'done', 'verdict': 'fix_invalid',
                   'reason': reason, 'ms': int(timers['audit_llm'])})
        return sql, err, ''

    def repair_sql_with_llm(self, user_question: str, bad_sql: str, error: str,
                             columns_context: str, join_hint: str = '', pattern_hint: str = '') -> Optional[str]:
        """快速修复：只把失败 SQL + 数据库报错 + 业务问题（+已定位字段）回喂 LLM 定点修正，
        小 prompt、快速模式，避免整轮重新生成。返回修复后的 SQL（未执行校验，调用方负责），
        无法修复返回 None。pattern_hint（R3）：定位表匹配的模式骨架，作结构参照。"""
        if not bad_sql:
            return None
        pattern_section = ''
        if pattern_hint:
            # 截断保护：修复 prompt 保持小体量（个别 CTE 骨架可超 1KB）
            pattern_section = ('\n【参考模式骨架】（结构参照，{{}} 槽位须替换为本题实际条件值）\n'
                               + pattern_hint[:800] + '\n')
        prompt = f"""你是电力营销数据仓库的 SQL 修复专家。下面这条 SQL 执行报错，请修复后只输出一行可执行的 SELECT（或 WITH...SELECT），不要 Markdown 代码块，不要解释。

【业务问题】
{user_question}

【执行报错的 SQL】
{bad_sql}

【数据库报错】
{error}

【修复要求】
1. 优先按报错修正字段名/表名/JOIN 条件，保持原查询意图不变，不要重写整句结构。
2. 只能使用下方 Schema 中出现的表名和字段名，禁止编造。
3. WHERE 中等值筛选的维度属性字段须同时出现在 SELECT 中；条件值形态服从码值标注（存编码写编码）。
4. JOIN 策略：维表/弱关联/仅取属性的关联用 LEFT JOIN（防止关联表无匹配时主表数据丢失）；仅业务语义确实要求两侧必须匹配时才用 INNER JOIN。
5. 推理从简，直接输出修复后的 SQL。

{columns_context}
{join_hint}
{pattern_section}
【输出】一行修复后的 SQL："""
        try:
            result = self._call_llm_fn(prompt, user_question=user_question, max_tokens=4000, thinking=False,
                                    model_override=getattr(config, 'AUX_MODEL', None))
            sql = self._san.clean_sql_pipeline(result['content'])
            if not sql or self._san.is_sentinel_sql(sql):
                return None
            allowed = ['SELECT', 'WITH']
            if not any(sql.strip().upper().startswith(p) for p in allowed):
                return None
            tables_in_sql = self._san.extract_tables_from_sql(sql)
            valid_tables = set(self._table_names_fn())
            if any(t not in valid_tables for t in tables_in_sql):
                return None
            return sql
        except Exception as e:
            print(f"[WARN] SQL 快速修复调用失败: {e}", flush=True)
            return None
