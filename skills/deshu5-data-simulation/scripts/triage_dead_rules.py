# -*- coding: utf-8 -*-
"""对 S1 的 62 条悬空 ANY 规则逐条归因（只读）：
 - 该列名是否因"伴生列漂移"而写错（真名存在 xxx_desc / 去 _desc / xxx_code / xxx_name）
 - 若存在候选真列，检查其映射是否已单独登记（避免重复）
输出 改指 / 删除 两类清单。
"""
import json
import os
import re
from collections import defaultdict

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


def conn(db=None):
    env = load_env()
    pw = os.environ.get('MYSQL_PASSWORD') or env.get('MYSQL_PASSWORD')
    return pymysql.connect(host=env.get('MYSQL_HOST', '127.0.0.1'),
                           port=int(env.get('MYSQL_PORT', '3306')),
                           user=env.get('MYSQL_USER', 'root'), password=pw,
                           charset='utf8mb4', database=db)


def main():
    audit = json.load(open(AUDIT, encoding='utf-8'))
    s1 = audit['S1_any_dead_rule']

    r = conn().cursor()
    r.execute("SELECT table_schema, table_name, column_name FROM information_schema.columns "
              "WHERE table_schema NOT IN ('information_schema','mysql','performance_schema','sys')")
    col2loc = defaultdict(set)
    for sch, tb, col in r.fetchall():
        col2loc[col].add((sch, tb))

    g = conn('fz01_governance').cursor()
    g.execute("SELECT table_name, column_name, code_name FROM code_value_column_form")
    existing_maps = {(t, c) for t, c, _ in g.fetchall()}
    mapped_cols = {c for _, c in existing_maps}

    def cands(col):
        out = {}
        if col.endswith('_desc'):
            out['去_desc'] = col[:-5]
        else:
            out['加_desc'] = col + '_desc'
        out['改_code'] = col + '_code'
        out['改_name'] = col + '_name'
        return out

    repoint, delete, uncertain = [], [], []
    for x in s1:
        col = x['column_name']
        found = {}
        for tag, c in cands(col).items():
            if c in col2loc:
                found[tag] = (c, sorted(col2loc[c])[:3], c in mapped_cols)
        if found:
            repoint.append({**x, 'candidates': found})
        else:
            delete.append(x)

    print('=' * 110)
    print(f'S1 悬空规则 {len(s1)} 条 → 可"改指"到真实列 {len(repoint)} 条 / 无任何候选（应删） {len(delete)} 条')
    print('=' * 110)
    print('\n【A. 可改指】—— 列名漂移，真实列存在')
    print(f'{"id":>6s} {"悬空列名":34s} {"code_name":24s} 候选 → 已单独登记?')
    for x in repoint:
        for tag, (c, locs, already) in x['candidates'].items():
            sch = ','.join(sorted({s for s, _ in locs}))
            print(f"{x['id']:6d} {x['column_name']:34s} {x['code_name']:24s} "
                  f"{tag}:{c:30s} 所在库[{sch}] 已在映射表={'是' if already else '否'}")

    print('\n【B. 无候选 → 建议删除】')
    for x in delete:
        print(f"{x['id']:6d} {x['column_name']:34s} {x['code_name']:24s}")

    print('\n【C. 分类统计】')
    a_cnt = sum(1 for x in repoint for _ in x['candidates'])
    print(f'  可改指(候选数) {a_cnt}；建议删除 {len(delete)}')

    out = {'repoint': repoint, 'delete': delete}
    p = os.path.join(os.path.dirname(AUDIT), 'meta_s1_triage.json')
    json.dump(out, open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('\n明细已写', p)


if __name__ == '__main__':
    main()
