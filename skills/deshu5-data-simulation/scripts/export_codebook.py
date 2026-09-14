#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
导出码表快照 → references/codebook.json

库内无独立码表库，码值定义分散在两处，本脚本合并后落成一个快照文件，
供 simulator.py 按码表取值（避免把码值硬编码进引擎）：

  1. fz01_ontology.ontology_enumerations   items + column_refs
  2. fz01_governance.code_values / code_value_items / code_value_column_form

默认只导出"本次仿真 83 张表被引用到的码表"（含通配规则展开），
加 --all 导出全部 420 张码表。加 --tables 可指定表清单来源。

用法：
  python export_codebook.py --out ../references/codebook.json
  python export_codebook.py --all --out /tmp/codebook_full.json
"""
import argparse
import json
import os
import sys
from collections import defaultdict

try:
    import pymysql
except ImportError:
    sys.exit("需要 pymysql")


def connect(db, host, port, user, pw):
    return pymysql.connect(host=host, port=port, user=user, password=pw,
                           database=db, charset='utf8mb4')


def main():
    ap = argparse.ArgumentParser(description='导出码表快照')
    ap.add_argument('--manifest', default=None,
                    help='仿真台账 json；从中取表清单，只导出被引用的码表')
    ap.add_argument('--tables', default=None,
                    help='表清单文件（每行一个表名）')
    ap.add_argument('--all', action='store_true', help='导出全部码表')
    ap.add_argument('--out', default='../references/codebook.json')
    ap.add_argument('--db', default='fz01')
    ap.add_argument('--ont-db', default='fz01_ontology')
    ap.add_argument('--gov-db', default='fz01_governance')
    args = ap.parse_args()

    import re
    env = {}
    for p in ('.env', 'D:/codex/deshu5/.env'):
        if os.path.exists(p):
            for line in open(p, encoding='utf-8'):
                m = re.match(r'\s*([A-Z_]+)\s*=\s*(.*)', line)
                if m:
                    env[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    pw = os.environ.get('MYSQL_PASSWORD') or env.get('MYSQL_PASSWORD')
    if not pw:
        sys.exit('未找到 MYSQL_PASSWORD')
    host = os.environ.get('MYSQL_HOST', '127.0.0.1')
    port = int(os.environ.get('MYSQL_PORT', '3306'))
    user = os.environ.get('MYSQL_USER', 'root')

    # ---- 目标表清单 ----
    if args.all:
        want = None
    elif args.manifest:
        want = set(json.load(open(args.manifest, encoding='utf-8'))['tables'])
    elif args.tables:
        want = {l.strip() for l in open(args.tables, encoding='utf-8') if l.strip()}
    else:
        sys.exit('需给出 --manifest / --tables / --all 之一')
    print('目标表数:', len(want) if want else '全部')

    # ---- 码项 ----
    codes = {}                       # code_name -> {'cn':..,'items':[{'code','name'}]}
    col_map = defaultdict(lambda: {'codes': set(), 'form': '', 'src': set()})

    # 源1：本体枚举
    c1 = connect(args.ont_db, host, port, user, pw)
    k1 = c1.cursor()
    k1.execute("SELECT code_name, cn_name, items, column_refs FROM ontology_enumerations")
    n_ont = 0
    for cn, cnn, items, refs in k1.fetchall():
        try:
            il = json.loads(items) if items else []
        except Exception:
            il = []
        try:
            rl = json.loads(refs) if refs else []
        except Exception:
            rl = []
        if cn and il:
            seen = set()
            merged = []
            for it in il:
                key = (str(it.get('code')), str(it.get('name')))
                if key in seen or it.get('code') is None:
                    continue
                seen.add(key)
                merged.append({'code': str(it.get('code')), 'name': str(it.get('name') or '')})
            if cn not in codes or len(merged) > len(codes[cn]['items']):
                codes[cn] = {'cn': cnn or '', 'items': merged}
                n_ont += 1
        for r in rl:
            t, col = r.get('table'), r.get('column')
            if not t or not col:
                continue
            if want is not None and t not in want:
                continue
            d = col_map[f'{t}.{col}']
            if cn:
                d['codes'].add(cn)
            if r.get('form'):
                d['form'] = r['form']
            d['src'].add('ontology')

    # 源2：治理码表
    c2 = connect(args.gov_db, host, port, user, pw)
    k2 = c2.cursor()
    k2.execute("SELECT code_name, item_code, item_name FROM code_value_items")
    tmp = defaultdict(list)
    for cn, ic, inm in k2.fetchall():
        if cn is None or ic is None:
            continue
        tmp[cn].append({'code': str(ic), 'name': str(inm or '')})
    for cn, lst in tmp.items():
        seen = set()
        merged = []
        for it in lst:
            if it['code'] in seen:
                continue
            seen.add(it['code'])
            merged.append(it)
        if cn not in codes or len(merged) > len(codes[cn]['items']):
            codes[cn] = {'cn': codes.get(cn, {}).get('cn', ''), 'items': merged}
    k2.execute("SELECT table_name, column_name, code_name, form FROM code_value_column_form")
    n_any = 0
    wild_map = defaultdict(lambda: {'codes': set(), 'form': ''})
    for t, col, cn, form in k2.fetchall():
        if not col:
            continue
        wild = (t == 'ANY')
        if wild:
            n_any += 1
        else:
            if want is not None and t not in want:
                continue
        names = [x.strip() for x in str(col).split(';') if x.strip()]
        for nc in names:
            if wild:
                # 通配规则按列名存，不展开成 (表,列)，避免快照膨胀
                d = wild_map[nc]
            else:
                d = col_map[f'{t}.{nc}']
            if cn:
                d['codes'].add(cn)
            if form and not d['form']:
                d['form'] = form
            if not wild:
                d['src'].add('code_value_column_form')

    # 只保留有码项的码表
    codes = {k: v for k, v in codes.items() if v['items']}
    cols = {}
    for k, v in col_map.items():
        cs = sorted(c for c in v['codes'] if c in codes)
        if cs:
            cols[k] = {'codes': cs, 'form': v['form'], 'src': sorted(v['src'])}
    wilds = {}
    for nc, v in wild_map.items():
        cs = sorted(c for c in v['codes'] if c in codes)
        if cs:
            wilds[nc] = {'codes': cs, 'form': v['form']}

    out = {
        'note': '码表快照：由 ontology_enumerations 与 governance.code_values 合并生成，'
                '供仿真引擎按码表取值。columns 是精确映射，wildcards 是按列名匹配的通配规则。',
        'source': {'ontology': args.ont_db, 'governance': args.gov_db},
        'scope': 'all' if want is None else f'{len(want)} tables',
        'codes': codes,
        'columns': cols,
        'wildcards': wilds,
    }
    path = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), args.out))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(out, open(path, 'w', encoding='utf-8'), ensure_ascii=False, indent=None,
              sort_keys=True)
    size = os.path.getsize(path)
    print(f'码表 {len(codes)} 张，码项 {sum(len(v["items"]) for v in codes.values()):,} 条')
    print(f'精确字段映射 {len(cols)} 个；通配规则 {len(wilds)} 条（原始 {n_any} 行）')
    print(f'已写 {path}  ({size/1024:.0f} KB)')


if __name__ == '__main__':
    main()
