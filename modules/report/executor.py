# -*- coding: utf-8 -*-
"""子问题执行器：并发执行报告计划中的标准化问题。

取数策略（标准问答对优先）：
- 命中 qa_pairs 的题：轻量 LLM "SQL 适配"（仅改写单位/期间等条件值）→ 只读试跑；
  适配或执行失败自动回退到 NL2SQL 完整链路。
- 未命中的题：SQLGenerator.generate（自带意图解析/审查/修复/兜底）→ safe_execute_sql。

并发：ThreadPoolExecutor；SQLGenerator 线程局部持有（构造轻、避免实例级 usage 计数串扰）。
单题失败不阻断整体，产出 status=failed 由拼装层降级处理。
"""
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from core.llm_config import call_chat
from core.sql_exec import safe_execute_sql

MAX_WORKERS = 3

_thread_local = threading.local()

_ADAPT_SYSTEM = '你是 SQL 条件适配器。只做条件值替换，不做任何其他修改。'
_ADAPT_PROMPT = '''给定一条已验证正确的标准 SQL 和一个新的取数问题。
若新问题中的单位、期间、码值等过滤条件与标准 SQL 中的不一致，仅改写 WHERE 子句中的对应条件值使其匹配新问题；若完全一致则原样返回。
纪律：
1. 只允许修改 WHERE 中的条件值（字符串/日期/数值字面量），不得改表名、字段、JOIN、GROUP BY、聚合方式；
2. 不要加注释，只输出 SQL 本身。

【新问题】{question}
【标准 SQL】
{standard_sql}'''


def _get_generator():
    """线程局部 SQLGenerator（复用 training 引擎，不走 routes 层）。"""
    gen = getattr(_thread_local, 'generator', None)
    if gen is None:
        from modules.training.engine.sql_generator import SQLGenerator
        gen = SQLGenerator()
        _thread_local.generator = gen
    return gen


def _extract_sql(text: str) -> str:
    """从 LLM 输出提取 SQL：去围栏，从首个 SELECT/WITH 截起。"""
    text = re.sub(r'```(?:sql)?', '', text).strip()
    m = re.search(r'\b(SELECT|WITH)\b', text, re.IGNORECASE)
    return text[m.start():].strip() if m else text


def _fresh_standard_sql(qa_id) -> str:
    """执行时按 qa_id 重取最新 standard_sql：后台按业务理解改过 SQL 后，新报告自动用新逻辑。"""
    if not qa_id:
        return ''
    try:
        from core.context import db_manager
        with db_manager.connect_governance() as conn:
            row = conn.execute(
                'SELECT standard_sql FROM qa_pairs WHERE id = ? AND (is_usable IS NULL OR is_usable = 1)',
                (qa_id,)).fetchone()
        return (row[0] or '').strip() if row else ''
    except Exception:
        return ''


def _run_one(item: dict, event_cb=None) -> dict:
    """执行一道子问题，返回 detail 记录。"""
    question = item['question']
    start = time.time()
    detail = {
        'qid': item['qid'],
        'section_title': item['section_title'],
        'question': question,
        'source': item.get('source', 'generated'),
        'qa_id': item.get('qa_id'),
        'sql': '', 'headers': [], 'rows': [], 'row_count': 0,
        'status': 'failed', 'error': '', 'ms': 0,
    }

    # 路径一：标准问答对 SQL 适配复用（qa_id 优先实时重取，保证用到最新口径）
    standard_sql = _fresh_standard_sql(item.get('qa_id')) or (item.get('standard_sql') or '')
    if standard_sql and (item.get('qa_id') or item.get('source') == 'qa_pair'):
        try:
            resp = call_chat(
                [{'role': 'system', 'content': _ADAPT_SYSTEM},
                 {'role': 'user', 'content': _ADAPT_PROMPT.format(
                     question=question, standard_sql=standard_sql)}],
                max_tokens=2000, thinking=False, effort='low')
            adapted = _extract_sql(resp['content'])
            detail['sql'] = adapted  # 留痕：适配产出的 SQL（即使执行失败）
            result = safe_execute_sql(adapted)
            if result.get('success'):
                detail.update(sql=adapted, headers=result['headers'], rows=result['rows'],
                              row_count=result['row_count'], status='ok', source='qa_pair',
                              ms=int((time.time() - start) * 1000))
                return detail
            detail['error'] = f"标准SQL适配后执行失败: {result.get('error', '')[:200]}"
        except Exception as e:
            detail['error'] = f'标准SQL适配失败: {str(e)[:200]}'
        # 回退 NL2SQL（不覆盖 detail['error']，最终成功则清除）

    # 路径二：NL2SQL 完整链路
    try:
        gen = _get_generator()
        gen_result = gen.generate(user_question=question)
        if gen_result.get('sql'):
            detail['sql'] = gen_result['sql']  # 留痕：生成 SQL（即使执行失败）
        if not gen_result.get('success'):
            raise ValueError(gen_result.get('error', 'SQL 生成失败'))
        sql = gen_result['sql']
        result = safe_execute_sql(sql)
        if not result.get('success'):
            raise ValueError(f"SQL 执行失败: {result.get('error', '')[:200]}")
        detail.update(sql=sql, headers=result['headers'], rows=result['rows'],
                      row_count=result['row_count'], status='ok', error='',
                      ms=int((time.time() - start) * 1000))
    except Exception as e:
        if not detail['error']:
            detail['error'] = str(e)[:300]
        detail['ms'] = int((time.time() - start) * 1000)
    return detail


def execute_plan(plan: dict, event_cb=None) -> list:
    """并发执行 plan 中全部子问题，返回按原顺序排列的 detail 列表。

    event_cb(detail) 在每道题完成时回调（供 SSE 推送进度）。
    """
    items = []
    for sec in plan.get('sections', []):
        for q in sec.get('questions', []):
            items.append({**q, 'section_title': sec.get('section_title', '')})

    details = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(_run_one, item): item for item in items}
        for fut in as_completed(futures):
            item = futures[fut]
            try:
                detail = fut.result()
            except Exception as e:
                detail = {'qid': item['qid'], 'section_title': item['section_title'],
                          'question': item['question'], 'source': item.get('source', 'generated'),
                          'qa_id': item.get('qa_id'), 'sql': '', 'headers': [], 'rows': [],
                          'row_count': 0, 'status': 'failed', 'error': str(e)[:300], 'ms': 0}
            details[item['qid']] = detail
            if event_cb:
                try:
                    event_cb(detail)
                except Exception:
                    pass
    return [details[item['qid']] for item in items]
