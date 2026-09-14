# -*- coding: utf-8 -*-
"""治理库元数据体检（只读）。

检查项：
  S1  ANY 通配规则的列名在全库不存在（死规则）
  S2  具体表映射指向不存在的表/列
  S3  映射指向的 code_name 没有码项（孤儿映射）
  S4  code_value_items 有码项但 code_values 未登记（含大小写不一致）
  S5  同一 (表,列) 映射到多个语义冲突的 code_name
  S6  form 标注与真实库实际取值形态不符（编码列存名 / 名称列存码）
  S7  同一 code_name 被语义不同的列共用（跨语义共用）
  S8  column_name 含分号（复合列名）

用法: python audit_metadata.py [--json-out xxx.json] [--sample-limit 200]
"""
import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict

import pymysql

HERE = os.path.dirname(os.path.abspath(__file__))
# 语义可确认"共用即冲突"的 code_name 白名单（多个域共用会互相放宽校验）
SHARED_SUSPECT = {'type', 'plant_type', 'running_state', 'stat', 'status'}


def load_env():
    env = {}
    for p in [r'D:\codex\deshu5\.env']:
        if os.path.exists(p):
            for line in open(p, encoding='utf-8'):
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


def sz(s):
    s = str(s).strip()
    return s.lstrip('0') or '0'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--json-out', default=None)
    ap.add_argument('--sample-limit', type=int, default=200)
    ap.add_argument('--gov-db', default='fz01_governance')
    ap.add_argument('--ont-db', default='fz01_ontology')
    ap.add_argument('--biz-db', default='fz01')
    args = ap.parse_args()

    gov = connect(database=args.gov_db)
    g = gov.cursor()
    biz = connect(database=args.biz_db)
    b = biz.cursor()
    ont = connect(database=args.ont_db)
    o = ont.cursor()

    g.execute("SELECT id,table_name,column_name,code_name,form FROM code_value_column_form")
    mappings = g.fetchall()
    g.execute("SELECT code_name,item_code,item_name FROM code_value_items")
    items = g.fetchall()
    g.execute("SELECT DISTINCT code_name FROM code_values")
    vals_cn = {r[0] for r in g.fetchall()}

    b.execute("SELECT table_name,column_name FROM information_schema.columns "
              "WHERE table_schema=%s", (args.biz_db,))
    real_cols = {(r[0], r[1]) for r in b.fetchall()}
    real_tabs = {t for t, _ in real_cols}
    all_colnames = {c for _, c in real_cols}

    # code_name -> (codes, names)
    by_cn = defaultdict(lambda: {'codes': {}, 'names': {}})
    for cn, ic, inm in items:
        if cn is None:
            continue
        d = by_cn[cn]
        if ic is not None:
            d['codes'][str(ic).strip()] = None if inm is None else str(inm).strip()
        if inm is not None:
            d['names'][str(inm).strip()] = None if ic is None else str(ic).strip()

    out = {}

    # ---------------- S1 ----------------
    s1 = []
    for _id, t, c, cn, form in mappings:
        if t != 'ANY':
            continue
        names = [x.strip() for x in str(c).split(';') if x.strip()]
        if names and not any(n in all_colnames for n in names):
            s1.append({'id': _id, 'column_name': c, 'code_name': cn})
    out['S1_any_dead_rule'] = s1

    # ---------------- S2 ----------------
    s2 = []
    for _id, t, c, cn, form in mappings:
        if t == 'ANY':
            continue
        if t not in real_tabs:
            s2.append({'id': _id, 'table': t, 'column': c, 'code_name': cn, 'why': '表不存在'})
        elif (t, c) not in real_cols:
            s2.append({'id': _id, 'table': t, 'column': c, 'code_name': cn, 'why': '列不存在'})
    out['S2_missing_target'] = s2

    # ---------------- S3 ----------------
    s3 = [{'id': _id, 'table_name': t, 'column_name': c, 'code_name': cn}
          for _id, t, c, cn, form in mappings if cn not in by_cn]
    out['S3_orphan_mapping'] = s3
    out['S3_orphan_code_names'] = sorted({x['code_name'] for x in s3})

    # ---------------- S4 ----------------
    s4 = []
    for cn in sorted(set(by_cn) - vals_cn):
        low = {v.lower(): v for v in vals_cn}
        s4.append({'code_name': cn, 'possible_case_match': low.get(cn.lower())})
    out['S4_items_without_code_values'] = s4

    # ---------------- S5 ----------------
    bycol = defaultdict(list)
    for _id, t, c, cn, form in mappings:
        for nc in [x.strip() for x in str(c).split(';') if x.strip()]:
            bycol[(t, nc)].append({'id': _id, 'code_name': cn, 'form': form})
    s5 = [{'table_name': k[0], 'column_name': k[1], 'mappings': sorted(v, key=lambda x: x['id'])}
          for k, v in bycol.items() if len({x['code_name'] for x in v}) > 1]
    out['S5_multi_code_name'] = s5

    # ---------------- S6：form 与真实取值形态对照 ----------------
    # 只查 form 明确为 编码/名称 的映射；混合两者皆可，不判
    targets = []
    for _id, t, c, cn, form in mappings:
        if form not in ('编码', '名称'):
            continue
        if t == 'ANY':
            targets.append({'id': _id, 'any': True, 'column': c, 'code_name': cn, 'form': form})
        elif (t, c) in real_cols:
            targets.append({'id': _id, 'any': False, 'table': t, 'column': c,
                            'code_name': cn, 'form': form})

    s6 = []
    checked = 0
    for tg in targets:
        cn, form = tg['code_name'], tg['form']
        d = by_cn.get(cn)
        if not d or (not d['codes'] and not d['names']):
            continue
        if tg['any']:
            # 取该列名出现的所有表，抽样最多 3 张
            tbls = [t for (t, c) in real_cols if c == tg['column']][:3]
        else:
            tbls = [tg['table']]
        vals, tabs_used = set(), []
        for tb in tbls:
            col = tg['column']
            q = f"SELECT DISTINCT CAST(`{col}` AS CHAR) FROM `{tb}` WHERE `{col}` IS NOT NULL LIMIT 60"
            try:
                b.execute(q)
                got = {str(r[0]).strip() for r in b.fetchall() if r[0] is not None}
            except Exception:
                continue
            got.discard('\\N')
            if got:
                vals |= got
                tabs_used.append(tb)
        if not vals:
            continue
        checked += 1
        codes_nz = {sz(x) for x in d['codes']}
        names_nz = {sz(x) for x in d['names']}
        n_code = sum(1 for v in vals if v in d['codes'] or sz(v) in codes_nz)
        n_name = sum(1 for v in vals if v in d['names'] or sz(v) in names_nz)
        tot = len(vals)
        if form == '编码' and n_name / tot > 0.6 and n_code / tot < 0.4:
            s6.append({**tg, 'tables': tabs_used, 'n': tot, 'match_code': n_code,
                       'match_name': n_name, 'suggest': '名称',
                       'sample': sorted(vals)[:6]})
        elif form == '名称' and n_code / tot > 0.6 and n_name / tot < 0.4:
            s6.append({**tg, 'tables': tabs_used, 'n': tot, 'match_code': n_code,
                       'match_name': n_name, 'suggest': '编码',
                       'sample': sorted(vals)[:6]})
    out['S6_form_mismatch'] = s6
    out['S6_checked'] = checked

    # ---------------- S7：跨语义共用 ----------------
    cn_cols = defaultdict(set)
    for _id, t, c, cn, form in mappings:
        for nc in [x.strip() for x in str(c).split(';') if x.strip()]:
            cn_cols[cn].add(nc)
    s7 = []
    for cn in sorted(SHARED_SUSPECT & set(cn_cols)):
        cols = sorted(cn_cols[cn])
        if len(cols) > 1:
            s7.append({'code_name': cn, 'n_columns': len(cols), 'columns': cols[:30]})
    out['S7_shared_code_name'] = s7

    # ---------------- S8：复合列名 ----------------
    s8 = [{'id': _id, 'table_name': t, 'column_name': c, 'code_name': cn, 'form': form}
          for _id, t, c, cn, form in mappings if c and ';' in str(c)]
    out['S8_composite_column'] = s8

    # ---------------- 汇总打印 ----------------
    print('=' * 96)
    print(f'映射 {len(mappings)} 行｜码项 {len(items)} 条 / {len(by_cn)} 个 code_name')
    print('=' * 96)
    print(f"S1 ANY 死规则（列名全库不存在）      : {len(s1):4d} 行")
    print(f"S2 映射指向不存在的表/列              : {len(s2):4d} 行")
    print(f"S3 孤儿映射（code_name 无码项）       : {len(s3):4d} 行 / {len(out['S3_orphan_code_names'])} 个 code_name")
    print(f"S4 有码项但 code_values 未登记        : {len(s4):4d} 个")
    for x in s4:
        print(f"      - {x['code_name']!r}  疑似大小写重复→ {x['possible_case_match']!r}")
    print(f"S5 一列映射多个 code_name             : {len(s5):4d} 组")
    for x in s5:
        print(f"      - {x['table_name']}.{x['column_name']}: "
              f"{[ (m['code_name'], m['form'], m['id']) for m in x['mappings'] ]}")
    print(f"S6 form 标注与真实值不符（抽检 {checked} 项）: {len(s6):4d} 项")
    for x in s6:
        loc = x.get('table') or f"ANY.{x['column']}"
        print(f"      - {loc} → {x['code_name']}  标「{x['form']}」实为「{x['suggest']}」"
              f"  code命中{x['match_code']}/name命中{x['match_name']}/{x['n']}  例{x['sample']}")
    print(f"S7 跨语义共用 code_name               : {len(s7):4d} 个")
    for x in s7:
        print(f"      - {x['code_name']}: {x['n_columns']} 列 {x['columns'][:12]}")
    print(f"S8 复合列名（含分号）                 : {len(s8):4d} 行")
    for x in s8:
        print(f"      - id={x['id']} {x['table_name']}.{x['column_name']} → {x['code_name']} ({x['form']})")

    if args.json_out:
        json.dump(out, open(args.json_out, 'w', encoding='utf-8'),
                  ensure_ascii=False, indent=1)
        print('\n明细已写', args.json_out)
    return out


if __name__ == '__main__':
    main()
