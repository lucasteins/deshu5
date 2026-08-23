# -*- coding: utf-8 -*-
"""暂存库初始化 + SQLite 原始数据 → database01 迁移（幂等）

- 创建 database01（业务暂存）/ database01_governance（治理暂存）
- 迁移 C:\\Users\\11051\\Desktop\\原始数据 下 4 个 SQLite 库（约 104 张表）到 database01
- SQLite 类型 → MySQL 类型映射；列名反引号包裹防保留字；表名保持原样
"""
import os
import re
import sqlite3
import sys

import pymysql

sys.path.insert(0, r'D:\codex\deshu5')
import config

SRC_DIR = r'C:\Users\11051\Desktop\原始数据'
TARGET_DB = 'database01'
GOV_DB = 'database01_governance'


def _server_conn():
    return pymysql.connect(host=config.MYSQL_HOST, port=config.MYSQL_PORT,
                           user=config.MYSQL_USER, password=config.MYSQL_PASSWORD,
                           charset='utf8mb4')


def ensure_databases():
    conn = _server_conn()
    try:
        with conn.cursor() as cur:
            cur.execute('SHOW DATABASES')
            existing = {r[0] for r in cur.fetchall()}
            for db in (TARGET_DB, GOV_DB):
                if db not in existing:
                    cur.execute(
                        f'CREATE DATABASE `{db}` DEFAULT CHARACTER SET utf8mb4 '
                        f'COLLATE utf8mb4_general_ci')
                    print(f'[init] 已创建数据库 {db}')
                else:
                    print(f'[init] 数据库 {db} 已存在，跳过')
        conn.commit()
    finally:
        conn.close()


def _mysql_type(sqlite_type: str, pk: bool, single_int_pk: bool) -> str:
    t = (sqlite_type or '').upper()
    if single_int_pk and pk:
        return 'BIGINT PRIMARY KEY AUTO_INCREMENT'
    if 'INT' in t:
        return 'INT'
    if 'CHAR' in t or 'CLOB' in t or 'TEXT' in t:
        return 'TEXT'
    if 'REAL' in t or 'FLOA' in t or 'DOUB' in t:
        return 'DOUBLE'
    if 'BLOB' in t:
        return 'LONGBLOB'
    if 'BOOL' in t:
        return 'TINYINT(1)'
    if 'DEC' in t or 'NUM' in t:
        return 'DECIMAL(30,10)'
    # 其余（DATE/TIME 等，SQLite 实为 TEXT 存储）一律 TEXT 稳妥
    return 'TEXT'


def migrate_db(sqlite_path: str):
    src = sqlite3.connect(sqlite_path)
    src.row_factory = sqlite3.Row
    tables = [r[0] for r in src.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    conn = _server_conn()
    try:
        conn.select_db(TARGET_DB)
        created = total_rows = 0
        for t in tables:
            cols = src.execute(f'PRAGMA table_info([{t}])').fetchall()
            # 单列 INTEGER 主键 → AUTO_INCREMENT；其他主键 → 仅 NOT NULL + 普通索引
            pk_cols = [c['name'] for c in cols if c['pk']]
            single_int_pk = len(pk_cols) == 1 and 'INT' in (next(
                c['type'] for c in cols if c['name'] == pk_cols[0]) or '').upper()
            col_defs = []
            for c in cols:
                ctype = _mysql_type(c['type'], c['pk'], single_int_pk)
                nn = ' NOT NULL' if c['notnull'] and not (single_int_pk and c['pk']) else ''
                col_defs.append(f'`{c["name"]}` {ctype}{nn}')
            ddl = f'CREATE TABLE IF NOT EXISTS `{t}` ({", ".join(col_defs)}) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4'
            with conn.cursor() as cur:
                cur.execute(ddl)
            conn.commit()
            created += 1

            # 数据迁移（批量）
            cur_src = src.execute(f'SELECT * FROM [{t}]')
            rows = cur_src.fetchall()
            if not rows:
                continue
            names = [d[0] for d in cur_src.description]
            placeholders = ', '.join(['%s'] * len(names))
            quoted = ', '.join(f'`{n}`' for n in names)
            insert_sql = f'INSERT IGNORE INTO `{t}` ({quoted}) VALUES ({placeholders})'
            batch = [tuple(r) for r in rows]
            with conn.cursor() as cur:
                for i in range(0, len(batch), 500):
                    cur.executemany(insert_sql, batch[i:i + 500])
            conn.commit()
            total_rows += len(rows)
            print(f'  [ok] {t}: {len(rows)} 行')
        print(f'[migrate] {os.path.basename(sqlite_path)}: {created} 表 / {total_rows} 行')
    finally:
        src.close()
        conn.close()


def main():
    ensure_databases()
    files = [f for f in os.listdir(SRC_DIR) if f.endswith('.db')]
    for fn in sorted(files):
        print(f'== 迁移 {fn} ==')
        migrate_db(os.path.join(SRC_DIR, fn))
    print('[done] 全部迁移完成')


if __name__ == '__main__':
    main()
