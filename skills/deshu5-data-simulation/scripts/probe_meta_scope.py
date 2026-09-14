# -*- coding: utf-8 -*-
"""元数据整改前置勘察（只读）。

1) 打印治理库三张表的 DDL（看是否有 status/remark 可挂"停用"，避免物理删除）
2) 统计服务器上所有 schema 的规模
3) 对 S1 死规则列名，逐个查它到底出现在哪些 schema（判断"规则来自生产库"还是"纯悬空"）
4) 对 S3 孤儿 code_name，查它在 ontology_enumerations 里是否有对应枚举（能否改名/关联修复）
"""
import json
import os
import re
import sys
from collections import defaultdict

import pymysql

HERE = os.path.dirname(os.path.abspath(__file__))
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
    env = load_env()
    pw = os.environ.get('MYSQL_PASSWORD') or env.get('MYSQL_PASSWORD')
    root = pymysql.connect(host=env.get('MYSQL_HOST', '127.0.0.1'),
                           port=int(env.get('MYSQL_PORT', '3306')),
                           user=env.get('MYSQL_USER', 'root'), password=pw,
                           charset='utf8mb4')
    r = root.cursor()

    print('=' * 90)
    print('一、服务器 schema 一览')
    print('=' * 90)
    r.execute("SELECT schema_name FROM information_schema.schemata "
              "WHERE schema_name NOT IN ('information_schema','mysql','performance_schema','sys') "
              "ORDER BY schema_name")
    schemas = [x[0] for x in r.fetchall()]
    for s in schemas:
        r.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=%s", (s,))
        nt = r.fetchone()[0]
        r.execute("SELECT COUNT(*) FROM information_schema.columns WHERE table_schema=%s", (s,))
        nc = r.fetchone()[0]
        print(f'  {s:34s} 表 {nt:4d}  列 {nc:5d}')
    print(f'  —— 共 {len(schemas)} 个 schema（不含系统库；不含 production marketing_* 之外）')

    print()
    print('=' * 90)
    print('二、治理库三表 DDL（判断能否"停用"而非删除）')
    print('=' * 90)
    g = connect(database='fz01_governance')
    gc = g.cursor()
    for t in ('code_values', 'code_value_items', 'code_value_column_form'):
        gc.execute(f"SHOW CREATE TABLE `{t}`")
        row = gc.fetchone()
        print(f'\n--- {t} ---')
        print(row[1])
        gc.execute(f"SELECT COUNT(*) FROM `{t}`")
        print(f'  → 行数 {gc.fetchone()[0]}')

    print()
    print('=' * 90)
    print('三、S1 死规则列名的实际归属（查遍全服所有 schema）')
    print('=' * 90)
    audit = json.load(open(AUDIT, encoding='utf-8'))
    s1 = audit['S1_any_dead_rule']
    # 建 (schema, column) 索引太贵，直接按列名 in (schema) 批量查
    # 先取所有 schema 的列名集合
    r.execute("SELECT table_schema, column_name FROM information_schema.columns "
              "WHERE table_schema NOT IN ('information_schema','mysql','performance_schema','sys')")
    col2sch = defaultdict(set)
    col2tabs = defaultdict(set)
    for sch, col in r.fetchall():
        col2sch[col].add(sch)
        col2tabs[col].add(sch)
    dead = [x for x in s1 if x['column_name'] not in col2sch]
    alive = [x for x in s1 if x['column_name'] in col2sch]
    print(f'S1 死规则 {len(s1)} 条 → 全服无此列名 {len(dead)} 条 / 其他 schema 有 {len(alive)} 条')
    if alive:
        for x in alive:
            print(f"  · {x['column_name']:32s} 存在于 {sorted(col2sch[x['column_name']])}")
    print(f'  纯悬空（任何 schema 都没有）示例：{[x["column_name"] for x in dead[:15]]}')

    print()
    print('=' * 90)
    print('四、S3 孤儿 code_name 与本体枚举的对照')
    print('=' * 90)
    o = connect(database='fz01_ontology')
    oc = o.cursor()
    oc.execute("SELECT code_name FROM ontology_enumerations")
    ont_cn = {x[0] for x in oc.fetchall()}
    orph = audit['S3_orphan_code_names']
    hit = [c for c in orph if c in ont_cn]
    miss = [c for c in orph if c not in ont_cn]
    print(f'S3 孤儿 code_name {len(orph)} 个 → 本体有同名枚举 {len(hit)} 个 / 本体也没有 {len(miss)} 个')
    print(f'  本体有（可直接关联修复）：{hit[:40]}')
    print(f'  本体也没有（真孤儿）：{miss[:40]}')

    # 本体枚举里 code_name 是否是 "表.列" 命名？看点样本
    oc.execute("SELECT code_name, cn_name, column_refs FROM ontology_enumerations LIMIT 5")
    print('\n  ontology_enumerations 样本：')
    for cn, cnn, refs in oc.fetchall():
        print(f'    code_name={cn!r} cn_name={cnn!r} refs={(refs or "")[:120]!r}')


if __name__ == '__main__':
    main()
