#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""snapshot_counts.py — 快照一个库全部表的行数，用于验证 --purge 是否精确还原。

用法：
    python snapshot_counts.py out.json --db fz01
    # 生成前后各跑一次，逐表比对；差异表数应为 0

联网约定：库名默认 fz01，账号密码读 --password 或环境变量 MYSQL_PASSWORD。
"""
import argparse, json, os, sys

try:
    import pymysql
except ImportError:
    sys.exit("需要 pymysql：pip install pymysql")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('out', help='输出 JSON 路径')
    ap.add_argument('--db', default=os.environ.get('SIM_DB', 'fz01'))
    ap.add_argument('--host', default=os.environ.get('MYSQL_HOST', 'localhost'))
    ap.add_argument('--port', type=int, default=int(os.environ.get('MYSQL_PORT', '3306')))
    ap.add_argument('--user', default=os.environ.get('MYSQL_USER', 'root'))
    ap.add_argument('--password', default=os.environ.get('MYSQL_PASSWORD', ''))
    a = ap.parse_args()
    if not a.password:
        sys.exit('缺少 MySQL 密码：设置 MYSQL_PASSWORD 环境变量或传 --password')

    c = pymysql.connect(host=a.host, port=a.port, user=a.user, password=a.password,
                        database=a.db, charset='utf8mb4')
    cur = c.cursor()
    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema=%s", (a.db,))
    tabs = [r[0] for r in cur.fetchall()]
    out = {}
    for t in tabs:
        cur.execute(f"SELECT COUNT(*) FROM `{t}`")
        out[t] = cur.fetchone()[0]
    c.close()
    os.makedirs(os.path.dirname(os.path.abspath(a.out)) or '.', exist_ok=True)
    json.dump(out, open(a.out, 'w', encoding='utf-8'), ensure_ascii=False, sort_keys=True)
    print(f"snapshot {len(out)} tables -> {a.out}  total_rows={sum(out.values()):,}")


if __name__ == '__main__':
    main()
