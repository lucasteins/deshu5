# -*- coding: utf-8 -*-
"""sjgl 系列数据库引导（幂等）：建四库 + 15 张运维运营指标业务表 + 治理/本体/日志结构

数据来源：D:\\codex\\运维运营指标梳理结果汇总\\汇总-运维运营指标相关表-建表语句（rds）.txt
（MySQL/RDS 方言版；ods 表 requestquerystring 列名笔误已在加载时修正）

- sjgl            业务库：15 张指标基础表（仅结构；样例数据装载见 import_sjgl_data）
- sjgl_governance 治理库：按 sc01_governance 结构空表重建 + business_domains 参考数据
- sjgl_log        日志库：generation_logs 结构
- sjgl_ontology   本体库：按 sc01_ontology 结构空表重建

用法：
    python tools/bootstrap_sjgl.py            # 全部步骤（幂等）
    python tools/bootstrap_sjgl.py --ddl-only # 只建四库+业务表
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pymysql  # noqa: E402

import config  # noqa: E402

PREFIX = 'sjgl'
DBS = (PREFIX, f'{PREFIX}_governance', f'{PREFIX}_log', f'{PREFIX}_ontology')

DDL_FILE = r'D:\codex\运维运营指标梳理结果汇总\汇总-运维运营指标相关表-建表语句（rds）.txt'
# 汇总 DDL 笔误：ods 表 `requestqueryVARCHAR(255) VARCHAR(255)`（样例数据 INSERT 证实
# 真实列名为 requestquerystring）
DDL_FIXUPS = (('requestqueryVARCHAR(255) VARCHAR(255)', 'requestquerystring VARCHAR(255)'),)
# 所有 VARCHAR/CHAR 的源端是 ODPS STRING（无界），样例数据实测超宽（1406 报错，
# 如 demand_desc、dest_table_guid）；view_text 等视图定义甚至超 TEXT(64KB)。
# 建表时统一放宽为 MEDIUMTEXT（这些表无主键/索引列依赖 VARCHAR 宽度，JOIN/GROUP BY 不受影响）


def _connect(db=None):
    return pymysql.connect(
        host=config.MYSQL_HOST, port=config.MYSQL_PORT,
        user=config.MYSQL_USER, password=config.MYSQL_PASSWORD,
        database=db, charset=config.MYSQL_CHARSET, autocommit=True)


def ensure_databases():
    conn = _connect()
    try:
        with conn.cursor() as cur:
            for name in DBS:
                cur.execute(
                    f"CREATE DATABASE IF NOT EXISTS `{name}` "
                    "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci")
                print(f'  [库] {name} 就绪')
    finally:
        conn.close()


def load_business_ddl() -> list:
    """汇总 txt → [(表名, DDL)]。按分号结尾行切语句（COMMENT 串内无分号）。"""
    with open(DDL_FILE, encoding='utf-8') as f:
        text = f.read()
    for bad, good in DDL_FIXUPS:
        text = text.replace(bad, good)
    text = re.sub(r'(?i)\bVAR?CHAR\(\d+\)', 'MEDIUMTEXT', text)
    stmts, buf = [], []
    for line in text.splitlines():
        buf.append(line)
        if line.rstrip().endswith(';'):
            stmt = '\n'.join(buf).strip().rstrip(';').strip()
            buf = []
            if stmt.upper().startswith('CREATE TABLE'):
                m = re.match(r'(?i)CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+(\w+)', stmt)
                stmts.append((m.group(1), stmt))
    return stmts


def create_business_tables():
    stmts = load_business_ddl()
    conn = _connect(PREFIX)
    try:
        with conn.cursor() as cur:
            for name, ddl in stmts:
                cur.execute(ddl)
                print(f'  [表] sjgl.{name}')
    finally:
        conn.close()
    return len(stmts)


def clone_structures(src_db: str, dst_db: str, include=None, copy_data=()):
    """按 src_db 的 SHOW CREATE TABLE 在 dst_db 空表重建；copy_data 中的表整表拷贝。"""
    src, dst = _connect(src_db), _connect(dst_db)
    try:
        with src.cursor() as cur:
            cur.execute('''SELECT table_name FROM information_schema.tables
                           WHERE table_schema=%s AND table_type='BASE TABLE' ORDER BY table_name''',
                        (src_db,))
            tables = [r[0] for r in cur.fetchall()
                      if include is None or r[0] in include]
        for t in tables:
            with src.cursor() as cur:
                cur.execute(f'SHOW CREATE TABLE `{t}`')
                ddl = cur.fetchone()[1].replace('CREATE TABLE', 'CREATE TABLE IF NOT EXISTS', 1)
            with dst.cursor() as dc:
                dc.execute(ddl)
            print(f'  [结构] {dst_db}.{t}')
            if t in copy_data:
                with dst.cursor() as dc:
                    dc.execute(f'SELECT COUNT(*) FROM `{t}`')
                    if dc.fetchone()[0] > 0:
                        print(f'  [数据] {dst_db}.{t} 非空，跳过')
                        continue
                with src.cursor() as cur:
                    cur.execute(f'SELECT * FROM `{t}`')
                    rows = cur.fetchall()
                    cols = [d[0] for d in cur.description]
                quoted = ', '.join(f'`{c}`' for c in cols)
                ph = ', '.join(['%s'] * len(cols))
                with dst.cursor() as dc:
                    dc.executemany(f'INSERT INTO `{t}` ({quoted}) VALUES ({ph})', rows)
                print(f'  [数据] {dst_db}.{t} 拷贝 {len(rows)} 行')
    finally:
        src.close()
        dst.close()


def main():
    ddl_only = '--ddl-only' in sys.argv
    print(f'[1/2] 预建四库（前缀 {PREFIX}）')
    ensure_databases()
    n = create_business_tables()
    print(f'[2/2] 业务表建表完成（{n} 张）')
    if ddl_only:
        return
    print('[治理库] 按 sc01_governance 结构重建（空表）+ business_domains 参考数据')
    clone_structures('sc01_governance', f'{PREFIX}_governance', copy_data=('business_domains',))
    print('[日志库] generation_logs 结构')
    clone_structures('sc01_log', f'{PREFIX}_log', include=('generation_logs',))
    print('[本体库] 按 sc01_ontology 结构重建（空表）')
    clone_structures('sc01_ontology', f'{PREFIX}_ontology')
    print('[done] sjgl 系列库引导完成')


if __name__ == '__main__':
    main()
