# -*- coding: utf-8 -*-
"""孤儿 code_name 的"另一侧"勘察（只读）：本体枚举 items 是否为空、code_values 是否登记、映射面多大。"""
import json
import os
import re
import sys

import pymysql

ENV = r'D:\codex\deshu5\.env'
AUDIT = r'D:\codex\deshu5\.workbuddy\reports\meta_audit.json'


def load_env():
    env = {}
    for line in open(ENV, encoding='utf-8'):
        m = re.match(r'\s*([A-Z_]+)\s*=\s*(.*)', line)
        if m:
            env[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return env


def connect(**kw):
    env = load_env()
    pw = os.environ.get('MYSQL_PASSWORD') or env.get('MYSQL_PASSWORD')
    if not pw:
        sys.exit('未找到 MYSQL_PASSWORD')
    return pymysql.connect(host=env.get('MYSQL_HOST', '127.0.0.1'),
                           port=int(env.get('MYSQL_PORT', '3306')),
                           user=env.get('MYSQL_USER', 'root'), password=pw,
                           charset='utf8mb4', **kw)


def main():
    audit = json.load(open(AUDIT, encoding='utf-8'))
    orphan = audit['S3_orphan_code_names']
    dead = audit['S1_any_dead_rule']

    o = connect(database='fz01_ontology')
    oc = o.cursor()
    oc.execute("SELECT code_name, cn_name, items, column_refs FROM ontology_enumerations")
    ont = {}
    for cn, cnn, items, refs in oc.fetchall():
        try:
            il = json.loads(items) if items else []
        except Exception:
            il = []
        try:
            rl = json.loads(refs) if refs else []
        except Exception:
            rl = []
        ont.setdefault(cn, []).append((cnn, il, rl))

    g = connect(database='fz01_governance')
    gc = g.cursor()
    gc.execute("SELECT DISTINCT code_name FROM code_values")
    in_vals = {x[0] for x in gc.fetchall()}
    gc.execute("SELECT code_name, COUNT(*) FROM code_value_items GROUP BY code_name")
    item_cnt = dict(gc.fetchall())
    gc.execute("SELECT code_name, table_name, column_name FROM code_value_column_form WHERE code_name IN (%s)"
               % ','.join(['%s'] * len(orphan)), tuple(orphan))
    maps = gc.fetchall()

    print('=' * 100)
    print(f'孤儿 code_name 共 {len(orphan)} 个')
    print('=' * 100)
    print(f'{"code_name":30s} {"本体枚举":>6s} {"本体码项":>8s} {"本体列ref":>8s} {"code_values":>11s} {"映射数":>6s}  本体中文名/码项示例')
    rows = []
    for cn in orphan:
        e = ont.get(cn, [])
        n_item = sum(len(x[1]) for x in e)
        n_ref = sum(len(x[2]) for x in e)
        n_map = len([m for m in maps if m[0] == cn])
        cnn = (e[0][0] if e and e[0][0] else '')[:22]
        sample = ''
        if e and e[0][1]:
            sample = str([(i.get('code'), i.get('name')) for i in e[0][1][:3]])
        rows.append((cn, len(e), n_item, n_ref, cn in in_vals, n_map, cnn, sample))

    for cn, ne, ni, nr, inv, nm, cnn, sample in sorted(rows, key=lambda x: -x[2]):
        print(f'{cn:30s} {ne:6d} {ni:8d} {nr:8d} {("是" if inv else "否"):>11s} {nm:6d}  {cnn} {sample}')

    print()
    print('=' * 100)
    print('汇总')
    print('=' * 100)
    with_item = [r for r in rows if r[2] > 0]
    no_item = [r for r in rows if r[2] == 0]
    print(f'本体枚举带码项的   : {len(with_item):3d} 个 → 码项合计 {sum(r[2] for r in with_item)} 条  ← 可直接灌入 code_value_items')
    print(f'本体枚举也不带码项 : {len(no_item):3d} 个 → 无法补码，只能标"待补"')
    print(f'已在 code_values 登记: {len([r for r in rows if r[4]]):3d} 个（未登记 {len([r for r in rows if not r[4]])} 个）')
    print(f'映射行数合计        : {sum(r[5] for r in rows)} 行')
    print()
    print('本体带码项、可修复的：', [r[0] for r in with_item])
    print('本体也无码项、真盲区的：', [r[0] for r in no_item])

    print()
    print('=' * 100)
    print('S4 大小写重复排查：bill_Mode / bill_mode')
    print('=' * 100)
    gc.execute("SELECT code_name FROM code_values WHERE LOWER(code_name) IN ('bill_mode')")
    print('  code_values 中 大小写不敏感命中:', [x[0] for x in gc.fetchall()])
    gc.execute("SELECT code_name, item_code, item_name FROM code_value_items WHERE LOWER(code_name)='bill_mode'")
    r = gc.fetchall()
    print(f'  code_value_items 命中 {len(r)} 条:', r[:8])
    gc.execute("SELECT table_name,column_name,code_name,form FROM code_value_column_form WHERE LOWER(code_name)='bill_mode'")
    print('  映射行:', gc.fetchall())
    for meta in ('fz01', 'sc01'):
        c = connect(database=meta).cursor()
        c.execute("SELECT table_name FROM information_schema.columns WHERE table_schema=%s AND column_name='bill_mode'", (meta,))
        print(f'  {meta} 中含 bill_mode 列的表: {[x[0] for x in c.fetchall()]}')

    print()
    print('=' * 100)
    print('S6 volt_cls01 复核')
    print('=' * 100)
    gc.execute("SELECT table_name,column_name,code_name,form,id FROM code_value_column_form WHERE code_name='volt_cls01' OR column_name='volt_cls01'")
    for x in gc.fetchall():
        print('  mapping:', x)
    gc.execute("SELECT item_code,item_name FROM code_value_items WHERE code_name='volt_cls01'")
    print('  items:', gc.fetchall())
    c = connect(database='fz01').cursor()
    c.execute("SELECT DISTINCT volt_cls01 FROM dim_cst_elec_cons_cust WHERE volt_cls01 IS NOT NULL LIMIT 10")
    print('  真实取值:', [x[0] for x in c.fetchall()])
    oc.execute("SELECT cn_name,items FROM ontology_enumerations WHERE code_name='volt_cls01'")
    print('  本体:', oc.fetchall())


if __name__ == '__main__':
    main()
