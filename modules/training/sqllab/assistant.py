# -*- coding: utf-8 -*-
"""SQL Lab 轻量 LLM 助手：fix / explain / optimize 的 prompt 构造与响应解析。

与训练问答的重型 NL2SQL 管线（生成-审查循环）互补：
- generate 动作直接复用 modules.training.routes._perform_generate_sql（本文件不涉及）
- fix / explain / optimize 围绕"编辑器里已有一段 SQL"做轻量改写/解读，
  Schema 上下文取自启动预热的 SchemaPreloader（零 DB 开销）
"""
import re
from typing import Dict, List

from core.context import get_schema_preloader
from core.llm_config import call_chat

# 单次最多携带的表数 / 每表最多列数 / 字段上下文总预算（字符）
_MAX_TABLES = 8
_MAX_COLUMNS_PER_TABLE = 40
_CONTEXT_BUDGET = 4000

_SQL_FENCE_RE = re.compile(r'```(?:sql|mysql)?\s*\n(.*?)```', re.DOTALL | re.IGNORECASE)


def extract_tables_from_sql(sql: str) -> List[str]:
    """从 SQL 文本中词边界匹配已知的物理表名（忽略大小写），按出现顺序去重。"""
    if not sql:
        return []
    preloader = get_schema_preloader()
    hits = []
    seen = set()
    sql_lower = sql.lower()
    for table in preloader.get_table_names():
        if len(hits) >= _MAX_TABLES:
            break
        if re.search(r'(?<![\w])' + re.escape(table.lower()) + r'(?![\w])', sql_lower):
            if table not in seen:
                seen.add(table)
                hits.append(table)
    return hits


def build_schema_context(tables: List[str]) -> str:
    """拼紧凑字段上下文：表注释 + 列名/类型/注释 + 涉及表之间的 JOIN 关系。"""
    if not tables:
        return ''
    preloader = get_schema_preloader()
    parts = []
    budget = _CONTEXT_BUDGET
    used_tables = []
    for table in tables[:_MAX_TABLES]:
        comment = preloader.get_table_comment(table)
        columns = preloader.get_columns(table)[:_MAX_COLUMNS_PER_TABLE]
        if not columns:
            continue
        lines = [f"表 {table}" + (f"（{comment}）" if comment else '')]
        for col in columns:
            name = col.get('name') or col.get('COLUMN_NAME') or ''
            ctype = col.get('type') or col.get('COLUMN_TYPE') or ''
            ccomment = col.get('comment') or col.get('COLUMN_COMMENT') or ''
            lines.append(f"  {name} {ctype}".rstrip() + (f"  -- {ccomment}" if ccomment else ''))
        block = '\n'.join(lines)
        if budget - len(block) < 0 and parts:
            break
        budget -= len(block)
        parts.append(block)
        used_tables.append(table)

    rel_lines = []
    try:
        for rel in preloader.get_relationships():
            ft, tt = rel.get('path') or ('', '')
            if ft in used_tables and tt in used_tables and ft != tt:
                conds = rel.get('join_conditions') or []
                rel_lines.append(f"{ft} -> {tt}" + (f" ON {' AND '.join(conds)}" if conds else ''))
    except Exception:
        pass
    if rel_lines:
        parts.append('JOIN 关系：\n' + '\n'.join(rel_lines[:20]))
    return '\n\n'.join(parts)


def extract_sql_from_response(content: str) -> str:
    """从 LLM 响应中提取 SQL：优先 ```sql 围栏，无围栏取全文。"""
    if not content:
        return ''
    m = _SQL_FENCE_RE.search(content)
    sql = (m.group(1) if m else content).strip()
    return sql.rstrip(';').strip() + (';' if sql.strip() else '')


_SYSTEM_PROMPT = (
    '你是电力营销数据分析平台的 SQL 助手，数据库为 MySQL。'
    '纪律：只生成/改写只读查询（SELECT/WITH），绝不输出 DROP/DELETE/UPDATE/INSERT/ALTER/CREATE/TRUNCATE 等写操作；'
    '表名、字段名必须严格来自给定的 Schema 上下文，禁止臆造；'
    '涉及码值/状态字段时优先参考字段注释中的取值说明。'
)


def assist_fix(sql: str, error: str) -> Dict:
    """根据执行报错修复 SQL。返回 {'sql', 'note'}。"""
    tables = extract_tables_from_sql(sql)
    context = build_schema_context(tables)
    messages = [
        {'role': 'system', 'content': _SYSTEM_PROMPT},
        {'role': 'user', 'content': (
            '下面这段 SQL 执行报错了，请修复它。\n\n'
            f'【SQL】\n{sql}\n\n【错误信息】\n{error}\n\n'
            + (f'【Schema 上下文】\n{context}\n\n' if context else '')
            + '要求：先用一两句话说明错误原因，然后给出修复后的完整 SQL（```sql 围栏）。'
        )},
    ]
    resp = call_chat(messages, max_tokens=4000)
    content = resp['content']
    return {'sql': extract_sql_from_response(content), 'note': _SQL_FENCE_RE.sub('', content).strip()}


def assist_explain(sql: str) -> Dict:
    """解释 SQL 的业务含义。返回 {'explanation'}。"""
    tables = extract_tables_from_sql(sql)
    context = build_schema_context(tables)
    messages = [
        {'role': 'system', 'content': _SYSTEM_PROMPT},
        {'role': 'user', 'content': (
            '请用中文解释下面这段 SQL 的业务含义：取的是什么数、过滤条件、口径（粒度/期间/单位），'
            '分点简述，不要输出代码。\n\n'
            f'【SQL】\n{sql}\n\n'
            + (f'【Schema 上下文】\n{context}' if context else '')
        )},
    ]
    resp = call_chat(messages, max_tokens=2000)
    return {'explanation': resp['content'].strip()}


def assist_optimize(sql: str) -> Dict:
    """优化/规范化改写 SQL。返回 {'sql', 'note'}。"""
    tables = extract_tables_from_sql(sql)
    context = build_schema_context(tables)
    messages = [
        {'role': 'system', 'content': _SYSTEM_PROMPT},
        {'role': 'user', 'content': (
            '请审查下面这段 SQL，从性能（索引利用、避免 SELECT *、减少嵌套）与规范性两方面给出改写版本。\n\n'
            f'【SQL】\n{sql}\n\n'
            + (f'【Schema 上下文】\n{context}\n\n' if context else '')
            + '要求：先分点列出修改说明，然后给出改写后的完整 SQL（```sql 围栏）；'
              '若原 SQL 已无明显问题，原样返回并说明。'
        )},
    ]
    resp = call_chat(messages, max_tokens=4000)
    content = resp['content']
    return {'sql': extract_sql_from_response(content), 'note': _SQL_FENCE_RE.sub('', content).strip()}
