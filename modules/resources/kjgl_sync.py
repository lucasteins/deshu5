# -*- coding: utf-8 -*-
"""kjgl → kjgl01 数据同步（kjgl 系列档位专用）

供两处复用：
- 数据资源页的「同步 kjgl→kjgl01」按钮（modules/resources/routes.py 的同步端点）
- 命令行 tools/bootstrap_kjgl01.py / tools/seed_kjgl01_schema_docs.py（薄封装）

安全边界（硬约束，不接受外部参数绕过）：
- 源库固定读 kjgl 系（默认 kjgl，KJGL_SOURCE_DB 可换），全程只读
- 目标库只允许 kjgl01 前缀系列，且必须与源库不同名
- 绝不允许写 fz01/sc01 任何库
"""
import json
import os
import time

import pymysql

import config

SOURCE_DB = os.environ.get('KJGL_SOURCE_DB', 'kjgl')
TARGET_PREFIX = os.environ.get('KJGL01_PREFIX', 'kjgl01')
TARGET_DB = TARGET_PREFIX  # 业务库名即前缀
GOV_DB = f'{TARGET_PREFIX}_governance'

# 硬安全闸：目标只允许 kjgl 数字系列（kjgl01/kjgl02…），绝不落到其他库
assert TARGET_PREFIX != SOURCE_DB, '目标前缀与源库同名，拒绝'
assert TARGET_PREFIX.startswith('kjgl') and TARGET_PREFIX[4:].isdigit(), \
    f'非法目标前缀（仅允许 kjglNN 形式）: {TARGET_PREFIX}'


def _connect(db=None):
    return pymysql.connect(
        host=config.MYSQL_HOST, port=config.MYSQL_PORT,
        user=config.MYSQL_USER, password=config.MYSQL_PASSWORD,
        database=db, charset='utf8mb4', autocommit=True)


def ensure_databases():
    """幂等预建 kjgl01 系列四库。"""
    with _connect() as conn, conn.cursor() as cur:
        for name in (TARGET_DB, GOV_DB, f'{TARGET_PREFIX}_log', f'{TARGET_PREFIX}_ontology'):
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{name}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci")


def _source_tables(cur):
    cur.execute(
        "SELECT TABLE_NAME FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA=%s AND TABLE_TYPE='BASE TABLE' ORDER BY TABLE_NAME",
        (SOURCE_DB,))
    return [r[0] for r in cur.fetchall()]


def rebuild_business():
    """全量重同步业务库：DROP 目标全表 → LIKE 建表 → INSERT SELECT → 重建外键 → 行数比对。

    返回 {tables: [{name, rows}], fk_added, all_match, mismatched, duration_ms}。
    """
    t0 = time.perf_counter()
    ensure_databases()
    stats = {'tables': [], 'fk_added': 0, 'all_match': True, 'mismatched': []}

    with _connect() as conn, conn.cursor() as cur:
        tables = _source_tables(cur)

        # 目标库清场：表间有外键引用，DROP 阶段须关外键检查（仅本会话）
        cur.execute('SET SESSION foreign_key_checks = 0')
        try:
            cur.execute(
                "SELECT TABLE_NAME FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA=%s AND TABLE_TYPE='BASE TABLE'", (TARGET_DB,))
            for (t,) in cur.fetchall():
                cur.execute(f'DROP TABLE `{TARGET_DB}`.`{t}`')

            for t in tables:
                cur.execute(f'CREATE TABLE `{TARGET_DB}`.`{t}` LIKE `{SOURCE_DB}`.`{t}`')
                cur.execute(
                    f'INSERT INTO `{TARGET_DB}`.`{t}` SELECT * FROM `{SOURCE_DB}`.`{t}`')
                cur.execute(f'SELECT COUNT(*) FROM `{TARGET_DB}`.`{t}`')
                stats['tables'].append({'name': t, 'rows': cur.fetchone()[0]})
        finally:
            cur.execute('SET SESSION foreign_key_checks = 1')

        # 重建物理外键（供 SchemaPreloader 关系图）
        cur.execute(
            "SELECT k.CONSTRAINT_NAME, k.TABLE_NAME, k.COLUMN_NAME, "
            "       k.REFERENCED_TABLE_NAME, k.REFERENCED_COLUMN_NAME, "
            "       r.DELETE_RULE, r.UPDATE_RULE "
            "FROM information_schema.KEY_COLUMN_USAGE k "
            "JOIN information_schema.REFERENTIAL_CONSTRAINTS r "
            "  ON r.CONSTRAINT_SCHEMA=k.CONSTRAINT_SCHEMA "
            " AND r.CONSTRAINT_NAME=k.CONSTRAINT_NAME "
            " AND r.TABLE_NAME=k.TABLE_NAME "
            "WHERE k.CONSTRAINT_SCHEMA=%s AND k.REFERENCED_TABLE_NAME IS NOT NULL "
            "ORDER BY k.TABLE_NAME, k.CONSTRAINT_NAME, k.ORDINAL_POSITION",
            (SOURCE_DB,))
        fks = {}
        for name, table, col, ref_t, ref_c, del_r, upd_r in cur.fetchall():
            fks.setdefault((table, name, ref_t, del_r, upd_r), []).append((col, ref_c))
        for (table, name, ref_t, del_r, upd_r), cols in sorted(fks.items()):
            col_list = ', '.join(f'`{c}`' for c, _ in cols)
            ref_list = ', '.join(f'`{c}`' for _, c in cols)
            cur.execute(
                f'ALTER TABLE `{TARGET_DB}`.`{table}` '
                f'ADD CONSTRAINT `{name}` FOREIGN KEY ({col_list}) '
                f'REFERENCES `{ref_t}` ({ref_list}) '
                f'ON DELETE {del_r} ON UPDATE {upd_r}')
            stats['fk_added'] += 1

        # 行数比对（源 vs 目标）
        for item in stats['tables']:
            cur.execute(f'SELECT COUNT(*) FROM `{SOURCE_DB}`.`{item["name"]}`')
            src_rows = cur.fetchone()[0]
            if src_rows != item['rows']:
                stats['all_match'] = False
                stats['mismatched'].append(
                    {'table': item['name'], 'source': src_rows, 'target': item['rows']})

    stats['duration_ms'] = int((time.perf_counter() - t0) * 1000)
    return stats


def refresh_schema_docs():
    """以 kjgl01 业务库 information_schema 为权威源，重建治理库三张 Schema 文档表。

    检索方每次实时查库、无缓存，跑完即生效。
    返回 {table_docs, column_docs, relationship_docs}。
    """
    with _connect(TARGET_DB) as biz, _connect(GOV_DB) as gov:
        bcur, gcur = biz.cursor(), gov.cursor()

        bcur.execute(
            "SELECT TABLE_NAME, TABLE_COMMENT, TABLE_ROWS "
            "FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA=%s AND TABLE_TYPE='BASE TABLE' ORDER BY TABLE_NAME",
            (TARGET_DB,))
        tables = bcur.fetchall()

        bcur.execute(
            "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_COMMENT, COLUMN_TYPE, COLUMN_KEY "
            "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s "
            "ORDER BY TABLE_NAME, ORDINAL_POSITION", (TARGET_DB,))
        cols_by_table = {}
        for t, c, comment, ctype, key in bcur.fetchall():
            cols_by_table.setdefault(t, []).append((c, comment or '', ctype, key == 'PRI'))

        gcur.execute('DELETE FROM schema_table_docs')
        gcur.execute('DELETE FROM schema_column_docs')
        gcur.execute('DELETE FROM schema_relationship_docs')

        n_col = 0
        for t, comment, rows in tables:
            cols = cols_by_table.get(t, [])
            doc = f"表名：{t}"
            if comment:
                doc += f"，中文名：{comment}"
            doc += f"，行数：{rows or 0}，字段数：{len(cols)}。业务含义：{comment or '业务表'}"
            gcur.execute(
                'INSERT INTO schema_table_docs '
                '(table_name, table_comment, row_count, column_count, doc_text) '
                'VALUES (%s,%s,%s,%s,%s)', (t, comment or '', rows or 0, len(cols), doc))
            for c, ccomment, ctype, is_pk in cols:
                cdoc = f"表 {t} 的字段 {c}"
                if ccomment:
                    cdoc += f"，中文名：{ccomment}"
                cdoc += f"，类型：{ctype}" + ("，主键" if is_pk else "")
                gcur.execute(
                    'INSERT INTO schema_column_docs '
                    '(table_name, column_name, column_comment, data_type, is_pk, doc_text) '
                    'VALUES (%s,%s,%s,%s,%s,%s)',
                    (t, c, ccomment, ctype, 1 if is_pk else 0, cdoc))
                n_col += 1

        bcur.execute(
            "SELECT k.TABLE_NAME, k.COLUMN_NAME, k.REFERENCED_TABLE_NAME, "
            "       k.REFERENCED_COLUMN_NAME "
            "FROM information_schema.KEY_COLUMN_USAGE k "
            "WHERE k.CONSTRAINT_SCHEMA=%s AND k.REFERENCED_TABLE_NAME IS NOT NULL "
            "ORDER BY k.TABLE_NAME, k.CONSTRAINT_NAME, k.ORDINAL_POSITION", (TARGET_DB,))
        comments = {t: c for t, c, _ in tables}
        n_rel = 0
        for t, c, rt, rc in bcur.fetchall():
            title = f'{t} → {rt}'
            jc = [f'{t}.{c} = {rt}.{rc}']
            scenarios = [f'{comments.get(t) or t} 与 {comments.get(rt) or rt} 关联查询']
            doc = (f"表关联路径：{title}\nJOIN 条件：\n{jc[0]}\n"
                   f"适用场景：{'、'.join(scenarios)}")
            gcur.execute(
                'INSERT INTO schema_relationship_docs '
                '(title, path, join_conditions, business_scenarios, doc_text) '
                'VALUES (%s,%s,%s,%s,%s)',
                (title, json.dumps([t, rt], ensure_ascii=False),
                 json.dumps(jc, ensure_ascii=False),
                 json.dumps(scenarios, ensure_ascii=False), doc))
            n_rel += 1

    return {'table_docs': len(tables), 'column_docs': n_col, 'relationship_docs': n_rel}
