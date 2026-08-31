# -*- coding: utf-8 -*-
"""SQL 只读执行器（公共底座）。

从 modules/training/routes.py 下沉，供训练问答与报告生成等模块共用：
- 白名单前缀校验（config.ALLOWED_SQL_PREFIXES）+ 禁用关键词（config.FORBIDDEN_KEYWORDS）
- 剥离注释、只取第一条语句、自动补 LIMIT（config.SQL_MAX_ROWS）
- 经 db_manager.connect_business() 在业务库执行（跟随 db_profile 档位）
"""
import re

import config
from core.context import db_manager


def safe_execute_sql(sql: str) -> dict:
    """安全执行 SQL（只读，限制行数）"""
    if not sql:
        return {'success': False, 'error': 'SQL为空'}

    sql = strip_sql_comments(sql)

    # 清理 SQL：只保留第一条有效语句（以第一个分号分割）
    sql = sql.strip()
    first_semicolon = sql.find(';')
    if first_semicolon > 0:
        after_semicolon = sql[first_semicolon + 1:].strip()
        if after_semicolon:
            sql = sql[:first_semicolon + 1]

    # 安全检查
    sql_upper = sql.strip().upper()
    allowed_prefixes = config.ALLOWED_SQL_PREFIXES
    if not any(sql_upper.startswith(p) for p in allowed_prefixes):
        prefix_list = ' / '.join(allowed_prefixes)
        return {'success': False, 'error': f'只允许执行 {prefix_list} 语句'}

    # 词边界匹配：避免 UPDATE_TIME / CREATED_AT 等列名误伤（原先子串匹配误报）
    for kw in config.FORBIDDEN_KEYWORDS:
        if re.search(r'\b' + re.escape(kw) + r'\b', sql_upper):
            return {'success': False, 'error': f'包含禁止的关键词: {kw}'}

    try:
        with db_manager.connect_business() as conn:
            sql_for_limit = sql.rstrip(';').strip()
            limited_sql = add_limit_if_needed(sql_for_limit)
            cursor = conn.execute(limited_sql)

            headers = [desc[0] for desc in cursor.description] if cursor.description else []
            rows = cursor.fetchall()

            rows_serializable = [
                [str(cell) if cell is not None else None for cell in row]
                for row in rows
            ]

            return {
                'success': True,
                'headers': headers,
                'rows': rows_serializable,
                'row_count': len(rows_serializable)
            }
    except Exception as e:
        return {'success': False, 'error': str(e)}


def strip_sql_comments(sql: str) -> str:
    """去掉 SQL 中的注释行和多行注释"""
    sql = re.sub(r'/\*.*?\*/', '', sql, flags=re.DOTALL)
    lines = []
    for line in sql.split('\n'):
        stripped = line.strip()
        if not stripped.startswith('--') and not stripped.startswith('//'):
            lines.append(line)
    return '\n'.join(lines)


def add_limit_if_needed(sql: str) -> str:
    """如果 SQL 没有 LIMIT，自动添加"""
    sql_stripped = sql.strip()
    if not re.search(r'\bLIMIT\s+\d+\s*$', sql_stripped, re.IGNORECASE):
        sql_stripped += f' LIMIT {config.SQL_MAX_ROWS}'
    return sql_stripped
