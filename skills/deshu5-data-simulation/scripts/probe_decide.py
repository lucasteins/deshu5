# -*- coding: utf-8 -*-
"""只读探针：为 4 个待决字段取【真实行】权威取值（严格排除台账内仿真主键）。

用法: python probe_decide.py [--manifest ...json]
"""
import argparse
import json
import os
import re
import sys

import pymysql

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
ENVS = ['.env', r'D:\codex\deshu5\.env']


def env_load():
    env = {}
    for p in ENVS:
        if os.path.exists(p):
            for line in open(p, encoding='utf-8'):
                m = re.match(r'\s*([A-Z_]+)\s*=\s*(.*)', line)
                if m:
                    env[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return env


TARGETS = [
    ('dwd_cst_conn_gen_power_app', 'arch_status', 'conn_gen_power_app_id'),
    ('dwd_cst_dev_rcpt_app_dtl_info', 'dev_use_stat', 'dev_rcpt_app_dtl_info_id'),
    ('dwd_cst_gc_app_rec', 'bus_type', 'cust_gen_app_rec_id'),
    ('dwd_cst_gc_app_rec', 'bus_categ', 'cust_gen_app_rec_id'),
    ('dim_grid_t_ts_sg_da_acline_b', 'linetype', 'id'),
    ('dim_grid_t_ts_sg_da_tline_b', 'line_type', 'id'),
    ('dim_grid_t_ts_sg_da_conversubstation_b', 'type', 'id'),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', default='fz01')
    ap.add_argument('--manifest', default=None)
    ap.add_argument('--gov-db', default='fz01_governance')
    args = ap.parse_args()

    env = env_load()
    pw = os.environ.get('MYSQL_PASSWORD') or env.get('MYSQL_PASSWORD')
    if not pw:
        sys.exit('未找到 MYSQL_PASSWORD')
    kw = dict(host=env.get('MYSQL_HOST', '127.0.0.1'), port=int(env.get('MYSQL_PORT', '3306')),
              user=env.get('MYSQL_USER', 'root'), password=pw, charset='utf8mb4')
    conn = pymysql.connect(database=args.db, **kw)
    cur = conn.cursor()

    # 建仿真主键黑名单（仅本次台账）
    man_paths = []
    if args.manifest:
        man_paths.append(args.manifest)
    else:
        md = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'manifest')
        man_paths = [os.path.join(md, f) for f in os.listdir(md)
                     if f.endswith('.json') and 'purged' not in f]
    print('台账:', [os.path.basename(p) for p in man_paths])
    simpk = {}
    for mp in man_paths:
        man = json.load(open(mp, encoding='utf-8'))
        for t, info in man.get('tables', {}).items():
            pks = info.get('pks') or info.get('pk_values') or []
            simpk.setdefault(t, set()).update(str(x) for x in pks)

    for table, col, pk in TARGETS:
        print('=' * 90)
        print(f'{table}.{col}   (pk={pk})')
        cur.execute(f"SELECT COUNT(*) FROM `{table}`")
        print('  总行数:', cur.fetchone()[0])
        # 真实值分布
        cur.execute(f"SELECT CAST(`{col}` AS CHAR) v, COUNT(*) c FROM `{table}` "
                    f"GROUP BY v ORDER BY c DESC LIMIT 30")
        rows = cur.fetchall()
        print('  全表分布:', [(r[0], r[1]) for r in rows][:15])
        pks = simpk.get(table)
        if pks:
            cur.execute('DROP TEMPORARY TABLE IF EXISTS `_pk_`')
            cur.execute('CREATE TEMPORARY TABLE `_pk_` (k VARCHAR(64) PRIMARY KEY)')
            cur.executemany('INSERT IGNORE INTO `_pk_` VALUES (%s)', [(p,) for p in pks])
            cond = (f"t.`{pk}` = p.k" if pk == 'id' or pk.endswith('_id')
                    else f"CAST(t.`{pk}` AS CHAR) = p.k")
            cur.execute(f"SELECT CAST(t.`{col}` AS CHAR) v, COUNT(*) c FROM `{table}` t "
                        f"WHERE NOT EXISTS(SELECT 1 FROM `_pk_` p WHERE {cond}) "
                        f"GROUP BY v ORDER BY c DESC LIMIT 30")
            print('  ★真实行分布:', [(r[0], r[1]) for r in cur.fetchall()])
        else:
            print('  ★真实行分布: (台账无此表)')

    # 码值名称核对
    print('=' * 90)
    try:
        gov = pymysql.connect(database=args.gov_db, **kw)
        gc = gov.cursor()
        for cn, codes in [('arch_status', ['01', '02', '03', '04']),
                          ('dev_use_stat', ['01', '02', '03', '3']),
                          ('linetype', ['1', '2', '3', '4', '5', '1001'])]:
            gc.execute("SELECT code_value, value_name FROM code_value_items "
                       "WHERE code_name=%s AND code_value IN %s ORDER BY code_value",
                       (cn, tuple(codes)))
            print(f'  [{cn}]', gc.fetchall())
        gc.execute("SELECT code_value, value_name FROM code_value_items "
                   "WHERE code_name='bus_type' AND code_value IN ('032302','032313','040601')")
        print('  [bus_type]', gc.fetchall())
        gov.close()
    except Exception as e:
        print('  码表查询失败:', e)

    conn.close()


if __name__ == '__main__':
    main()
