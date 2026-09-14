# -*- coding: utf-8 -*-
"""本体模块前端端点回归校验（只读）。

用途：改动本体装载路径（如码值副本下线、load_active 改直读治理库）后，
      用 Flask test_client 打真实 HTTP 端点，确认前端引用不中断。

校验项：
  E1 /api/ontology/summary            —— 总览可用、版本与计数正常
  E2 /api/ontology/enumerations       —— 码值域列表非空，条数与治理库 code_values 一致
  E3 /api/ontology/enumerations?code_name=X —— 单码值域详情可查（含 items / column_refs）
  E4 /api/ontology/classes/<name>     —— 类详情能带出落列码值（column_refs 关联未断）
  E5 /api/ontology/concepts           —— 概念列表可用

用法: python verify_ontology_endpoints.py [--profile staging|prod]
退出码非 0 表示校验失败。
"""
import argparse
import json
import os
import re
import sys

import pymysql

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ_DIR = r'D:\codex\deshu5'
ENV = r'D:\codex\deshu5\.env'
PROFILES = {'staging': 'fz01_governance', 'prod': 'sc01_governance'}


def load_env():
    env = {}
    for line in open(ENV, encoding='utf-8'):
        m = re.match(r'\s*([A-Z_]+)\s*=\s*(.*)', line)
        if m:
            env[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return env


def gov_count(gov_db):
    """治理库 code_values 的 code_name 去重总数（与端点口径一致，含空串行）。"""
    env = load_env()
    pw = os.environ.get('MYSQL_PASSWORD') or env.get('MYSQL_PASSWORD')
    c = pymysql.connect(host=env.get('MYSQL_HOST', '127.0.0.1'),
                        port=int(env.get('MYSQL_PORT', '3306')),
                        user=env.get('MYSQL_USER', 'root'), password=pw,
                        charset='utf8mb4', database=gov_db).cursor()
    c.execute("SELECT COUNT(DISTINCT code_name) FROM code_values")
    total = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM code_values WHERE code_name IS NULL OR code_name = ''")
    empty = c.fetchone()[0]
    if empty:
        print(f'  [note] 治理库 code_values 有 {empty} 行 code_name 为空（既有数据质量问题，非本次引入）')
    return total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--profile', choices=sorted(PROFILES), default='staging')
    args = ap.parse_args()

    sys.path.insert(0, PROJ_DIR)
    os.chdir(PROJ_DIR)
    # 必须显式设定档位，否则端点读 db_profile.json 当前值、与 --profile 口径不符
    os.environ['DB_PROFILE'] = {'prod': 'production', 'staging': 'staging'}[args.profile]
    from app import create_app
    c = create_app().test_client()

    fails = []
    print('=' * 62)
    print(f'本体端点回归校验（profile={args.profile}）')
    print('=' * 62)

    # E1 summary
    r = c.get('/api/ontology/summary')
    j = r.get_json() or {}
    ok = r.status_code == 200 and j.get('success') and j.get('available')
    print(f"E1 /summary             HTTP {r.status_code}  available={j.get('available')} "
          f"version={(j.get('meta') or {}).get('version')}  {'OK' if ok else 'FAIL'}")
    if not ok:
        fails.append(('E1', r.status_code, str(j)[:200]))

    # E2 enumerations
    r = c.get('/api/ontology/enumerations')
    j = r.get_json() or {}
    items = j.get('items') or []
    expect = gov_count(PROFILES[args.profile])
    ok = r.status_code == 200 and j.get('success') and len(items) == expect
    print(f"E2 /enumerations        HTTP {r.status_code}  {len(items)} 个码值域"
          f"（治理库 {expect}）  {'OK' if ok else 'FAIL'}")
    if not ok:
        fails.append(('E2', r.status_code, f'got {len(items)} expect {expect}'))

    # E3 single detail —— 取一个有码项的
    cand = next((it for it in items if it.get('item_count')), None)
    if cand:
        cn = cand['code_name']
        r = c.get(f'/api/ontology/enumerations?code_name={cn}')
        j = r.get_json() or {}
        it = j.get('item') or {}
        ok = (r.status_code == 200 and j.get('success')
              and it.get('code_name') == cn and len(it.get('items') or []) == cand['item_count'])
        print(f"E3 /enumerations?{cn}  HTTP {r.status_code}  items={len(it.get('items') or [])}  "
              f"{'OK' if ok else 'FAIL'}")
        if not ok:
            fails.append(('E3', r.status_code, str(j)[:200]))
    else:
        fails.append(('E3', 0, '无带码项的码值域可供抽查'))

    # E4 class detail 带落列码值
    cls = next((it for it in items if it.get('column_refs')), None)
    if cls:
        tbl = (cls['column_refs'][0] or {}).get('table')
        r = c.get(f'/api/ontology/classes/{tbl}')
        j = r.get_json() or {}
        enums = j.get('enumerations') or []
        ok = r.status_code == 200 and j.get('success') and len(enums) > 0
        print(f"E4 /classes/{tbl}  HTTP {r.status_code}  落列码值 {len(enums)} 项  "
              f"{'OK' if ok else 'FAIL'}")
        if not ok:
            fails.append(('E4', r.status_code, str(j)[:200]))

    # E5 concepts
    r = c.get('/api/ontology/concepts')
    j = r.get_json() or {}
    ok = r.status_code == 200 and j.get('success')
    print(f"E5 /concepts            HTTP {r.status_code}  "
          f"{len(j.get('items') or [])} 个概念  {'OK' if ok else 'FAIL'}")
    if not ok:
        fails.append(('E5', r.status_code, str(j)[:200]))

    print('-' * 62)
    if fails:
        print('失败项：')
        for f in fails:
            print('  ', f)
        sys.exit(1)
    print('全部通过 ✓')


if __name__ == '__main__':
    main()
