# -*- coding: utf-8 -*-
"""本体库消除「码值副本」：`ontology_enumerations` 下线，码值以治理库为唯一权威。

背景（2026-09-13 诊断 diag_ontology_dup.py，D2 层结论）：
  本体库 `ontology_enumerations`(v28) 与治理库 `code_values` / `code_value_items`
  是**完全重复存储**——
    · code_name：本体 420 / 治理 420，**仅本体 0 个、仅治理 0 个**
    · cn_name ：420/420 完全一致
    · 码项   ：314 完全一致、23 处有差异且**全部是"治理库更多"**（仅本体 0 条）
  → 治理库是严格超集，删除本体副本**零信息损失**。

  双份存储的真实代价：治理库补码（如 C 级反写 36 条）后本体副本滞后，
  两份数据不一致，且无任何机制保证同步。

配套代码改动（同批）：
  · core/ontology/store.py  load_active()  —— 改为直读治理库，不再读本体副本
  · core/ontology/store.py  save_active()  —— 不再写入 ontology_enumerations
  · core/ontology/builder.py fill_enumerations_from_governance()  —— 两处共用同一装载逻辑

安全约定：
  1. 前置校验：治理库必须是超集（仅本体 code_name / 码项均为 0），不满足即中止
  2. 备份整表（mysqldump → Desktop/database，失败退化行级 JSON）
  3. 台账逐行记录被删内容与 id，--undo 精确还原
  4. 默认只出变更单，须显式 --commit 才落库

用法：
    python dedup_ontology_enumerations.py                      # dry-run（只出变更单）
    python dedup_ontology_enumerations.py --commit             # 备份 + 删除 + 校验
    python dedup_ontology_enumerations.py --undo --manifest manifest/ontology_dedup_xxx.json
    python dedup_ontology_enumerations.py --profile prod       # 生产库（默认 staging）
"""
import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
from collections import defaultdict

import pymysql

HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST_DIR = os.path.join(HERE, 'manifest')
BACKUP_DIR = r'C:\Users\11051\Desktop\database'
REPORT_DIR = r'D:\codex\deshu5\.workbuddy\reports'
PROJ_DIR = r'D:\codex\deshu5'
ONT_TABLE = 'ontology_enumerations'
DUMP_CANDIDATES = ['mysqldump', r'C:\Program Files\MySQL\MySQL Server 8.0\bin\mysqldump.exe']

PROFILES = {
    'staging': {'gov': 'fz01_governance', 'ont': 'fz01_ontology',
                'biz': 'fz01', 'tag': 'staging'},
    'prod': {'gov': 'sc01_governance', 'ont': 'sc01_ontology',
             'biz': 'sc01', 'tag': 'prod'},
}

ONT_DB = PROFILES['staging']['ont']
GOV_DB = PROFILES['staging']['gov']


def load_env():
    env = {}
    for p in [r'D:\codex\deshu5\.env', os.path.join(HERE, '..', '..', '..', '..', '.env')]:
        if os.path.exists(p):
            for line in open(p, encoding='utf-8'):
                m = re.match(r'\s*([A-Z_]+)\s*=\s*(.*)', line)
                if m:
                    env[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return env


def conn(**kw):
    env = load_env()
    pw = os.environ.get('MYSQL_PASSWORD') or env.get('MYSQL_PASSWORD')
    if not pw:
        sys.exit('未找到 MYSQL_PASSWORD（环境变量或 .env）')
    return pymysql.connect(host=env.get('MYSQL_HOST', '127.0.0.1'),
                           port=int(env.get('MYSQL_PORT', '3306')),
                           user=env.get('MYSQL_USER', 'root'), password=pw,
                           charset='utf8mb4', **kw)


def nz(s):
    return str(s).strip() if s is not None else ''


# ============================================================ 前置校验
def collect(co, cg):
    """本体枚举全量行 + 治理码表全量索引。"""
    co.execute(f"SELECT id, code_name, cn_name, domain_l1, domain_l2, domain_l3, "
               f"items, column_refs, version FROM `{ONT_TABLE}` ORDER BY version, code_name")
    ont_rows = []
    for r in co.fetchall():
        ont_rows.append(dict(id=r[0], code_name=nz(r[1]), cn_name=nz(r[2]),
                             domain_l1=nz(r[3]), domain_l2=nz(r[4]), domain_l3=nz(r[5]),
                             items=r[6], column_refs=r[7], version=r[8]))

    cg.execute("SELECT code_name, code_cn_name FROM code_values")
    gov_names = {}
    for cn, zh in cg.fetchall():
        gov_names[nz(cn)] = nz(zh)
    cg.execute("SELECT code_name, item_code, item_name FROM code_value_items")
    gov_items = defaultdict(dict)
    for cn, ic, inm in cg.fetchall():
        gov_items[nz(cn)][nz(ic)] = nz(inm)

    ont_names = {r['code_name'] for r in ont_rows}
    # 只比 v 最大的一版（生效版）做内容校验；历史版仅统计
    vers = sorted({r['version'] for r in ont_rows if r['version'] is not None})
    latest = vers[-1] if vers else None
    latest_rows = [r for r in ont_rows if r['version'] == latest]

    only_in_ont_names = sorted(ont_names - set(gov_names))
    only_in_ont_items = []
    name_mismatch = []
    gov_more = 0
    for r in latest_rows:
        cn = r['code_name']
        try:
            il = json.loads(r['items']) if r['items'] else []
        except Exception:
            il = []
        oi = {nz(i.get('code')): nz(i.get('name')) for i in il if i.get('code') is not None}
        gi = dict(gov_items.get(cn, {}))
        for c in oi:
            if c not in gi:
                only_in_ont_items.append({'code_name': cn, 'item_code': c, 'name': oi[c]})
        if set(gi) - set(oi):
            gov_more += 1
        if cn in gov_names and r['cn_name'] != gov_names[cn]:
            name_mismatch.append({'code_name': cn, 'ont': r['cn_name'], 'gov': gov_names[cn]})

    pf = {
        'ont_rows_total': len(ont_rows),
        'ont_versions': vers,
        'ont_rows_by_version': {v: sum(1 for r in ont_rows if r['version'] == v) for v in vers},
        'ont_latest_version': latest,
        'ont_latest_code_names': len({r['code_name'] for r in latest_rows}),
        'gov_code_names': len(gov_names),
        'gov_items_code_names': len(gov_items),
        'only_in_ontology_code_names': only_in_ont_names,
        'only_in_ontology_items': only_in_ont_items,
        'cn_name_mismatch': name_mismatch,
        'code_names_where_gov_has_more_items': gov_more,
    }
    return ont_rows, pf


def preflight_ok(pf):
    return (not pf['only_in_ontology_code_names']
            and not pf['only_in_ontology_items']
            and not pf['cn_name_mismatch'])


# ============================================================ 备份 / 执行 / 回滚
def _find_dump():
    for c in DUMP_CANDIDATES:
        try:
            subprocess.run([c, '--version'], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=10)
            return c
        except Exception:
            continue
    return None


def backup(stamp, ont_db, tag):
    os.makedirs(BACKUP_DIR, exist_ok=True)
    env = load_env()
    pw = os.environ.get('MYSQL_PASSWORD') or env.get('MYSQL_PASSWORD')
    dump = _find_dump()
    if dump:
        out = os.path.join(BACKUP_DIR, f'ontology_enum_{tag}_{stamp}.sql')
        cmd = [dump, '-h', env.get('MYSQL_HOST', '127.0.0.1'),
               '-P', env.get('MYSQL_PORT', '3306'), '-u', env.get('MYSQL_USER', 'root'),
               f'-p{pw}', '--skip-add-locks', '--no-tablespaces', ont_db, ONT_TABLE]
        with open(out, 'wb') as f:
            r = subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE)
        if r.returncode == 0:
            return out
        print('[warn] mysqldump 失败，改用 JSON 备份：', r.stderr.decode('utf-8', 'ignore')[:200])
    out = os.path.join(BACKUP_DIR, f'ontology_enum_{tag}_{stamp}.json')
    c = conn(database=ont_db).cursor()
    c.execute(f"SELECT * FROM `{ONT_TABLE}`")
    cols = [d[0] for d in c.description]
    json.dump([dict(zip(cols, r)) for r in c.fetchall()],
              open(out, 'w', encoding='utf-8'), ensure_ascii=False,
              default=lambda o: str(o) if isinstance(o, dt.datetime) else o)
    return out


def apply_changes(co, ont_rows):
    cur = co
    cur.execute(f"DELETE FROM `{ONT_TABLE}`")
    deleted = cur.rowcount
    cur.connection.commit()
    return {'delete_all': deleted,
            'deleted_ids': [r['id'] for r in ont_rows],
            'rows_by_version': {str(v): sum(1 for r in ont_rows if r['version'] == v)
                                for v in sorted({r['version'] for r in ont_rows})}}


def undo(ont_db, manifest):
    m = json.load(open(manifest, encoding='utf-8'))
    c = conn(database=ont_db)
    cur = c.cursor()
    n = 0
    for r in m['rows']:
        cur.execute(
            f"INSERT INTO `{ONT_TABLE}` "
            f"(id, code_name, cn_name, domain_l1, domain_l2, domain_l3, items, column_refs, version) "
            f"VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            f"ON DUPLICATE KEY UPDATE code_name=VALUES(code_name), cn_name=VALUES(cn_name), "
            f"domain_l1=VALUES(domain_l1), domain_l2=VALUES(domain_l2), "
            f"domain_l3=VALUES(domain_l3), items=VALUES(items), "
            f"column_refs=VALUES(column_refs), version=VALUES(version)",
            (r['id'], r['code_name'], r['cn_name'], r['domain_l1'], r['domain_l2'],
             r['domain_l3'], r['items'], r['column_refs'], r['version']))
        n += cur.rowcount
    c.commit()
    cur.execute(f"SELECT COUNT(*) FROM `{ONT_TABLE}`")
    print(f"[undo] 还原 {n} 行；当前表 {cur.fetchone()[0]} 行")


# ============================================================ 落库后校验
def verify_runtime(expected_code_names):
    """用真实运行时代码路径校验：store.load_active() 现在应直读治理库。"""
    sys.path.insert(0, PROJ_DIR)
    try:
        from core.ontology.store import OntologyStore
        ont = OntologyStore().load_active()
    except Exception as e:
        return {'ok': False, 'error': f'{type(e).__name__}: {e}'}
    if ont is None:
        return {'ok': False, 'error': 'load_active() 返回 None（本体未构建）'}
    enums = ont.enumerations
    with_items = sum(1 for e in enums.values() if e.items)
    with_refs = sum(1 for e in enums.values() if e.column_refs)
    return {'ok': len(enums) == expected_code_names,
            'loaded_code_names': len(enums),
            'expected_code_names': expected_code_names,
            'with_items': with_items, 'with_column_refs': with_refs,
            'sample': sorted(enums)[:5]}


# ============================================================ 主流程
def main():
    global ONT_DB, GOV_DB
    ap = argparse.ArgumentParser()
    ap.add_argument('--commit', action='store_true', help='真正落库（默认只出变更单）')
    ap.add_argument('--undo', action='store_true')
    ap.add_argument('--manifest', default=None)
    ap.add_argument('--profile', choices=sorted(PROFILES), default='staging')
    ap.add_argument('--ont-db', default=None, help='覆盖本体库名')
    ap.add_argument('--gov-db', default=None, help='覆盖治理库名')
    ap.add_argument('--force', action='store_true', help='超集校验失败仍继续（危险）')
    args = ap.parse_args()

    prof = PROFILES[args.profile]
    ONT_DB = args.ont_db or prof['ont']
    GOV_DB = args.gov_db or prof['gov']
    tag = prof['tag']

    if args.undo:
        if not args.manifest:
            sys.exit('--undo 需配合 --manifest')
        undo(ONT_DB, args.manifest)
        return

    co = conn(database=ONT_DB).cursor()
    cg = conn(database=GOV_DB).cursor()
    ont_rows, pf = collect(co, cg)

    ok = preflight_ok(pf)
    print('=' * 68)
    print(f'本体码值副本下线  profile={args.profile}  本体库={ONT_DB}  治理库={GOV_DB}')
    print('=' * 68)
    print(f"本体 {ONT_TABLE}        : {pf['ont_rows_total']} 行 / {len(pf['ont_versions'])} 版 "
          f"{pf['ont_rows_by_version']}")
    print(f"  最新生效版 v{pf['ont_latest_version']}: {pf['ont_latest_code_names']} 个 code_name")
    print(f"治理 code_values         : {pf['gov_code_names']} 个 code_name")
    print(f"治理 code_value_items    : {pf['gov_items_code_names']} 个 code_name 有码项")
    print(f"  仅本体有 code_name     : {len(pf['only_in_ontology_code_names'])}")
    print(f"  仅本体有码项           : {len(pf['only_in_ontology_items'])}")
    print(f"  cn_name 不一致         : {len(pf['cn_name_mismatch'])}")
    print(f"  治理码项更多的 code_name: {pf['code_names_where_gov_has_more_items']}（治理为超集，正常）")
    if pf['only_in_ontology_code_names'][:10]:
        print('   样例 code_name:', pf['only_in_ontology_code_names'][:10])
    if pf['only_in_ontology_items'][:10]:
        print('   样例 码项:', pf['only_in_ontology_items'][:10])
    print()

    if not ok and not args.force:
        print('✗ 前置校验未通过：本体存在治理库没有的内容，删除会造成信息损失。')
        print('  如确认可弃，请加 --force。')
        return
    if not ok:
        print('! --force 已指定：仍继续（存在信息损失风险）')
    else:
        print('✓ 前置校验通过：治理库是本体码值的严格超集 → 删除本体副本零信息损失')

    stamp = dt.datetime.now().strftime('%Y%m%d_%H%M%S')
    if not args.commit:
        print(f'\n[dry-run] 待删 {pf["ont_rows_total"]} 行。加 --commit 落库。')
        return

    print('\n[1/3] 备份整表 ...')
    bk = backup(stamp, ONT_DB, tag)
    print('      备份 →', bk)

    print('[2/3] 删除本体副本 ...')
    ops = apply_changes(co, ont_rows)
    print(f'      已删 {ops["delete_all"]} 行  分布 {ops["rows_by_version"]}')

    print('[3/3] 校验 ...')
    co.execute(f"SELECT COUNT(*) FROM `{ONT_TABLE}`")
    left = co.fetchone()[0]
    print(f'      本体表剩余 {left} 行（应 0）')
    vr = verify_runtime(pf['gov_code_names'])
    print('      运行时 load_active():', json.dumps(vr, ensure_ascii=False)[:300])

    os.makedirs(MANIFEST_DIR, exist_ok=True)
    man = os.path.join(MANIFEST_DIR, f'ontology_dedup_{tag}_{stamp}.json')
    json.dump({
        'stamp': stamp, 'profile': args.profile, 'ont_db': ONT_DB, 'gov_db': GOV_DB,
        'table': ONT_TABLE, 'backup': bk, 'preflight': pf, 'ops': ops,
        'left_after': left, 'verify_runtime': vr, 'rows': ont_rows,
    }, open(man, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('      台账 →', man)
    print('\n完成。回滚：python dedup_ontology_enumerations.py --undo --manifest', man)


if __name__ == '__main__':
    main()
