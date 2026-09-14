# -*- coding: utf-8 -*-
"""本体库 vs 治理库「完全重复存储」排查（只读）。

产出 JSON 报告，避免控制台乱码。检查三层：
  D1 本体库内部：版本化冗余（旧版本是否为纯历史快照）
  D2 跨库内容重复：ontology_enumerations(v28) vs 治理 code_values/code_value_items/code_value_column_form
  D3 大字段冗余：ontology_proposals.snapshot 是否内嵌完整本体快照

用法: python diag_ontology_dup.py [--out xxx.json]
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
from collections import defaultdict

import pymysql

HERE = os.path.dirname(os.path.abspath(__file__))
ENV = r'D:\codex\deshu5\.env'
REPORT = r'D:\codex\deshu5\.workbuddy\reports'
ONT = 'fz01_ontology'
GOV = 'fz01_governance'
BIZ = 'fz01'


def load_env():
    env = {}
    for line in open(ENV, encoding='utf-8'):
        m = re.match(r'\s*([A-Z_]+)\s*=\s*(.*)', line)
        if m:
            env[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return env


def conn(**kw):
    env = load_env()
    pw = os.environ.get('MYSQL_PASSWORD') or env.get('MYSQL_PASSWORD')
    if not pw:
        sys.exit('未找到 MYSQL_PASSWORD')
    return pymysql.connect(host=env.get('MYSQL_HOST', '127.0.0.1'),
                           port=int(env.get('MYSQL_PORT', '3306')),
                           user=env.get('MYSQL_USER', 'root'), password=pw,
                           charset='utf8mb4', **kw)


def nz(s):
    return str(s).strip() if s is not None else ''


def sz(s):
    s = nz(s)
    return s.lstrip('0') or '0'


REPORT_DATA = {}


def d1_versions(o):
    """版本化冗余：每张版本表按 version 分组，比较各版本内容是否与最新版一致。"""
    tables = {
        'ontology_classes': ('name', 'label', 'kind', 'comment'),
        'ontology_properties': ('class_name', 'name', 'label', 'data_type', 'xsd_type', 'is_pk'),
        'ontology_relations': ('from_class', 'to_class', 'join_conditions',
                               'business_scenarios', 'source'),
        'ontology_enumerations': ('code_name', 'cn_name', 'domain_l1', 'domain_l2',
                                  'domain_l3', 'items', 'column_refs'),
        'ontology_concepts': ('concept', 'maps_to', 'alt_labels'),
        'ontology_entities': ('name', 'label', 'layer', 'parent', 'member_tables', 'comment'),
    }
    out = {}
    for t, cols in tables.items():
        sel = ', '.join(cols)
        o.execute(f"SELECT version, {sel} FROM `{t}`")
        rows = o.fetchall()
        byv = defaultdict(list)
        for r in rows:
            byv[r[0]].append(tuple('' if x is None else str(x) for x in r[1:]))
        vers = sorted(byv)
        latest = vers[-1]
        latest_set = set(byv[latest])
        info = {'versions': vers, 'latest': latest,
                'rows_by_version': {v: len(byv[v]) for v in vers},
                'obsolete_rows': sum(len(byv[v]) for v in vers[:-1]),
                'obsolete_identical': {},
                'obsolete_diff_only': {}}
        for v in vers[:-1]:
            s = set(byv[v])
            same = len(s & latest_set)
            only = len(s - latest_set)
            info['obsolete_identical'][v] = same
            info['obsolete_diff_only'][v] = only
        out[t] = info
    REPORT_DATA['D1_versions'] = out
    return out


def d2_cross_db(o, g):
    """跨库内容重复：本体枚举 vs 治理码表。"""
    o.execute("SELECT version FROM ontology_enumerations ORDER BY version DESC LIMIT 1")
    latest = o.fetchone()[0]
    o.execute("SELECT code_name, cn_name, domain_l1, domain_l2, domain_l3, items, column_refs "
              "FROM ontology_enumerations WHERE version=%s", (latest,))
    ont = [dict(code_name=nz(r[0]), cn_name=nz(r[1]), l1=nz(r[2]), l2=nz(r[3]),
                l3=nz(r[4]), items=r[5], column_refs=r[6]) for r in o.fetchall()]

    g.execute("SELECT code_name, code_cn_name, description, domain_l1, domain_l2, domain_l3 "
              "FROM code_values")
    gv = {nz(r[0]): dict(cn_name=nz(r[1]), desc=nz(r[2]), l1=nz(r[3]), l2=nz(r[4]), l3=nz(r[5]))
          for r in g.fetchall()}
    g.execute("SELECT DISTINCT code_name FROM code_value_items")
    g_items_cn = {nz(r[0]) for r in g.fetchall()}
    g.execute("SELECT code_name, item_code, item_name, sort_order FROM code_value_items")
    gi = defaultdict(dict)
    for cn, ic, inm, so in g.fetchall():
        gi[nz(cn)][nz(ic)] = dict(name=nz(inm), sort=so)
    g.execute("SELECT table_name, column_name, code_name, form FROM code_value_column_form")
    refs = defaultdict(set)
    for t, c, cn, f in g.fetchall():
        refs[nz(cn)].add((nz(t), nz(c), nz(f)))

    ont_cn = {x['code_name'] for x in ont}
    stats = {'ont_latest_version': latest, 'ont_code_names': len(ont_cn),
             'gov_code_values': len(gv), 'gov_items_code_names': len(g_items_cn),
             'only_in_ontology': sorted(ont_cn - set(gv)),
             'only_in_governance': sorted(set(gv) - ont_cn)}

    items_identical = items_diff = ont_items_empty = gov_items_empty = 0
    diffs = []
    name_identical = name_diff = 0
    refs_identical = refs_diff = 0
    for x in ont:
        cn = x['code_name']
        try:
            il = json.loads(x['items']) if x['items'] else []
        except Exception:
            il = []
        # 本体 items 是 list[{code,name,sort}]，去重后比 dict
        oi = {}
        for i in il:
            c = i.get('code')
            if c is None:
                continue
            key = nz(c)
            oi[key] = dict(name=nz(i.get('name')), sort=i.get('sort'))
        gset = gi.get(cn, {})
        if not oi:
            ont_items_empty += 1
        elif not gset:
            gov_items_empty += 1
        elif oi == gset:
            items_identical += 1
        else:
            items_diff += 1
            if len(diffs) < 25:
                oc, gc = set(oi), set(gset)
                diffs.append({
                    'code_name': cn,
                    'ont_n': len(oi), 'gov_n': len(gset),
                    'only_in_ont': sorted(oc - gc)[:8],
                    'only_in_gov': sorted(gc - oc)[:8],
                    'name_mismatch': [k for k in (oc & gc)
                                      if oi[k]['name'] != gset[k]['name']][:8],
                })
        if cn in gv:
            if x['cn_name'] == gv[cn]['cn_name']:
                name_identical += 1
            else:
                name_diff += 1
        # column_refs 对比
        try:
            rl = json.loads(x['column_refs']) if x['column_refs'] else []
        except Exception:
            rl = []
        orefs = {(nz(r.get('table')), nz(r.get('column'))) for r in rl}
        grefs_any = {(t, c) for (t, c, f) in refs.get(cn, set()) if t != 'ANY'}
        grefs_all = {(t, c) for (t, c, f) in refs.get(cn, set())}
        if not orefs and not grefs_all:
            refs_identical += 1
        elif orefs == grefs_any or orefs == grefs_all:
            refs_identical += 1
        else:
            refs_diff += 1
            if len(diffs) < 25:
                pass
    stats.update({
        'items_identical': items_identical, 'items_diff': items_diff,
        'ont_items_empty': ont_items_empty, 'gov_items_empty': gov_items_empty,
        'cn_name_identical': name_identical, 'cn_name_diff': name_diff,
        'refs_identical': refs_identical, 'refs_diff': refs_diff,
        'diff_samples': diffs,
    })
    REPORT_DATA['D2_cross_db'] = stats
    return stats


def d3_bigfield(o):
    """大字段冗余：ontology_proposals.snapshot / diff 体积与内容。"""
    o.execute("SELECT id, base_fingerprint, LENGTH(diff), LENGTH(snapshot), status, "
              "created_at, decided_at FROM ontology_proposals")
    rows = [dict(id=r[0], fp=nz(r[1]), diff_bytes=r[2] or 0, snapshot_bytes=r[3] or 0,
                 status=nz(r[4]), created_at=str(r[5]), decided_at=str(r[6]))
            for r in o.fetchall()]
    o.execute("SELECT SUM(LENGTH(snapshot)), SUM(LENGTH(diff)) FROM ontology_proposals")
    tot = o.fetchone()
    o.execute("SELECT id, snapshot FROM ontology_proposals ORDER BY id DESC LIMIT 1")
    r = o.fetchone()
    keys = sorted(json.loads(r[1]).keys()) if r and r[1] else []
    out = {'proposals': rows, 'total_snapshot_bytes': tot[0] or 0,
           'total_diff_bytes': tot[1] or 0,
           'latest_snapshot_top_keys': keys}
    REPORT_DATA['D3_bigfield'] = out
    return out


def d3b_table_sizes(o):
    """各表数据+索引体积。"""
    q = ("SELECT table_name, table_rows, "
         "ROUND((data_length+index_length)/1024/1024,2) AS mb, "
         "ROUND(data_length/1024/1024,2) AS data_mb "
         "FROM information_schema.tables WHERE table_schema=%s ORDER BY (data_length+index_length) DESC")
    out = []
    for db in (ONT, GOV):
        o.execute(q, (db,))
        out.append({'db': db, 'tables': [
            {'table': r[0], 'rows': r[1], 'mb': float(r[2] or 0), 'data_mb': float(r[3] or 0)}
            for r in o.fetchall()]})
    REPORT_DATA['D3b_sizes'] = out
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=None)
    args = ap.parse_args()
    o = conn(database=ONT).cursor()
    g = conn(database=GOV).cursor()

    d1_versions(o)
    d2_cross_db(o, g)
    d3_bigfield(o)
    d3b_table_sizes(o)

    out = args.out or os.path.join(REPORT, 'ontology_dup_diag.json')
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump(REPORT_DATA, open(out, 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1, default=str)
    print('已写', out)
    print()
    print('D1 版本化冗余：')
    for t, v in REPORT_DATA['D1_versions'].items():
        print(f"  {t:26s} 版本 {v['versions']} 最新 v{v['latest']} "
              f"旧版行数合计 {v['obsolete_rows']}｜旧版中与最新版完全相同的行 {v['obsolete_identical']}")
    print()
    s = REPORT_DATA['D2_cross_db']
    print('D2 跨库重复：')
    print(f"  本体 v{s['ont_latest_version']} code_name {s['ont_code_names']}｜"
          f"治理 code_values {s['gov_code_values']}｜治理有码项 {s['gov_items_code_names']}")
    print(f"  仅本体 {len(s['only_in_ontology'])} 个｜仅治理 {len(s['only_in_governance'])} 个")
    print(f"  items 完全相同 {s['items_identical']}｜有差异 {s['items_diff']}｜"
          f"本体空 {s['ont_items_empty']}｜治理空 {s['gov_items_empty']}")
    print(f"  cn_name 一致 {s['cn_name_identical']}｜不一致 {s['cn_name_diff']}")
    print(f"  column_refs 一致 {s['refs_identical']}｜不一致 {s['refs_diff']}")
    print()
    b = REPORT_DATA['D3_bigfield']
    print(f"D3 大字段：proposals {len(b['proposals'])} 条｜snapshot 合计 "
          f"{b['total_snapshot_bytes']/1024/1024:.2f} MB｜diff 合计 {b['total_diff_bytes']/1024/1024:.2f} MB")
    for r in b['proposals']:
        print(f"  id={r['id']} status={r['status']:10s} snapshot={r['snapshot_bytes']/1024/1024:.2f}MB "
              f"diff={r['diff_bytes']/1024:.0f}B {r['created_at']}")


if __name__ == '__main__':
    main()
