# -*- coding: utf-8 -*-
"""把 C 级（码表登记不全）的真实码值反写回治理库 `fz01_governance`。

背景：C 级 = 仿真值与**真实库同值**，仅因治理码表未登记该码项而被判越码。
本脚本把这些"真实库确实在用、但码表漏登"的码项补录进 `code_value_items`。

安全约定：
  1. 只 INSERT，绝不 UPDATE / DELETE（不动现有登记、不动映射关系）
  2. 写入前先备份 code_value_items / code_values / code_value_column_form 为 SQL
  3. 生成可回滚台账（记录本次插入的每一行 id），--undo 按 id 精确删除
  4. 默认 dry-run 只打印补录清单，须显式 --commit 才落库

item_name 取值优先级（不自造描述）：
  A. 同 code_name 内去零后能对上已有码项 → 用该码项名称
     （如 inst_lv '1' ↔ '01' 顶级；read_type '3' ↔ '03' 正向有功（峰））
  B. 全局其他 code_name 下已登记同名条目 → 借用其码与名
     （如 '电力网点交费' → pay_chan '101'）
  C. 查不到权威名称 → item_code=item_name=原值（如实登记事实，不臆造语义）

用法：
    python codebook_backfill.py --check-json <code_check_final.json>          # 只出清单
    python codebook_backfill.py --check-json ... --commit                     # 落库
    python codebook_backfill.py --undo --manifest <backfill_xxx.json>         # 精确回滚
"""
import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys

import pymysql

HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST_DIR = os.path.join(HERE, 'manifest')
BACKUP_DIR = r'C:\Users\11051\Desktop\database'


def load_env():
    env = {}
    for p in [os.path.join(HERE, '..', '..', '..', '..', '.env'), r'D:\codex\deshu5\.env']:
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


def sz(s):
    s = str(s).strip()
    return s.lstrip('0') or '0'


# ----------------------------------------------------------- 权威名称索引
def build_index(conn_gov, conn_ont):
    """返回 by_name: {item_name: [(code_name, item_code, item_name), ...]}"""
    cur = conn_gov.cursor()
    cur.execute("SELECT code_name, item_code, item_name FROM code_value_items")
    rows = cur.fetchall()
    if conn_ont:
        oc = conn_ont.cursor()
        oc.execute("SELECT code_name, items FROM ontology_enumerations")
        for cn, items in oc.fetchall():
            if not items:
                continue
            try:
                for it in json.loads(items):
                    if it.get('name') is not None:
                        rows.append((cn, it.get('code'), it.get('name')))
            except Exception:
                pass
    by_name, by_cn = {}, {}
    for cn, ic, inm in rows:
        ic = None if ic is None else str(ic).strip()
        inm = None if inm is None else str(inm).strip()
        if inm:
            by_name.setdefault(inm, []).append((cn, ic, inm))
        if ic:
            by_cn.setdefault(cn, []).append((ic, inm))
    return by_name, by_cn


def load_existing(conn_gov):
    """(code_name) -> {'codes':{..},'names':{..}}"""
    cur = conn_gov.cursor()
    cur.execute("SELECT code_name, item_code, item_name FROM code_value_items")
    out = {}
    for cn, ic, inm in cur.fetchall():
        d = out.setdefault(cn, {'codes': {}, 'names': {}})
        if ic is not None:
            d['codes'][str(ic).strip()] = None if inm is None else str(inm).strip()
        if inm is not None:
            d['names'][str(inm).strip()] = None if ic is None else str(ic).strip()
    return out


# ----------------------------------------------------------- 补录推导
# 借用同名条目时的偏好：目标 code_name 必须出现在该列表里，避免借到语义无关的码
BORROW_PREFER = {
    'publ_clg_flag_desc': ['line_publ_clg_flag', 'supl_type', 'publ_clg_flag'],
    'pay_mode_desc': ['pay_chan', 'pay_chan_desc', 'pay_mode'],
    'volt_lv': ['voltage_desc', 'volt_code', 'cust_volt_code'],
    'volt_lv_desc': ['voltage_desc', 'volt_code', 'cust_volt_code'],
    'dist_lv_desc': ['dist_lv', 'urb_region_level', 'region_level'],
    'rpt_ec_categ': ['ec_categ', 'prc_ec_categ', 'cust_ec_categ'],
}


# 跨语义的泛化列名：同名列在不同表承载完全不同的码系，禁止"去零借用"
# （反例：dim_equ_t_p_pd_cablepsr.type='01' 是 PMS 电缆类型，去零会借到杆型码 '1' 直线）
NO_ZERO_BORROW = {'type'}
# 复合 code_name（'a,b' 形态）优先补哪个：值多为名称时补 *_desc，为码时补前者
DESC_SUFFIX = '_desc'


def derive(check_json, existing, by_name, by_cn):
    """C 级字段 → 补录清单 [{code_name, item_code, item_name, source, why}]

    注意：check 输出里的 `code_names` 可能是**逗号复合串**（如
    'pay_mode,pay_mode_desc'），由 code_value_column_form 的 column_name
    'a;b' 写法展开而来。校验时按整串查码表，故两个 code_name 都要补，任一命中即通过。
    """
    # 原名快照：A2 判据必须看"补录前的码表"，否则会被本次补录自身写回的索引误触发
    orig_names = {cn: set(d['names']) for cn, d in existing.items()}
    data = json.load(open(check_json, encoding='utf-8'))
    plans, seen = [], set()
    for r in data['results']:
        if not r.get('level') or not r['level'].startswith('C'):
            continue
        raw = r.get('code_names') or r['column']
        if isinstance(raw, (list, tuple)):
            cns = [str(x).strip() for x in raw if str(x).strip()]
        else:
            cns = [x.strip() for x in str(raw).split(',') if x.strip()]
        if not cns:
            cns = [r['column']]
        for cn in cns:
            if cn not in existing:
                existing[cn] = {'codes': {}, 'names': {}}
            ex = existing[cn]
            for val, _n in (r.get('bad_values') or []):
                v = str(val).strip()

                item_code = item_name = None
                source = why = ''

                # A. 同 code_name 内去零匹配已有码项（跨语义列名除外）
                if cn not in NO_ZERO_BORROW:
                    for c, n in ex['codes'].items():
                        if sz(c) == sz(v) and n:
                            item_code, item_name = v, n
                            source = '同表去零'
                            why = f"'{c}' {n}"
                            break
                # A2. 同 code_name 内该值已是登记名称（真实库按名存，列 form 却标编码）
                if not item_code and v in orig_names.get(cn, ()):
                    item_code, item_name = v, v
                    source = '同表已登记名'
                    why = f"码表已有名称 {v}（列 form 标为编码）"
                # B. 全局同名借用（按偏好 code_name 排序，避免借到语义无关的码）
                if not item_code:
                    prefer = BORROW_PREFER.get(cn)
                    cands = [c for c in by_name.get(v, []) if c[1]]
                    if prefer:
                        cands = sorted(cands, key=lambda x: (x[0] not in prefer, x[0]))
                    if cands:
                        bcn, bic, binm = cands[0]
                        item_code, item_name = bic, binm
                        source = '全局同名借用'
                        why = f"{bcn} 的 '{bic}' {binm}"
                # B2. 按 item_code 在偏好 code_name 内借用
                #     （volt_lv '107' ← volt_code '107' 交流220V；v 必须是编码形态）
                if not item_code:
                    prefer = BORROW_PREFER.get(cn)
                    if prefer and re.fullmatch(r'[A-Za-z0-9]+', v):
                        for pcn in prefer:
                            hit = [x for x in by_cn.get(pcn, []) if x[0] == v and x[1]]
                            if hit:
                                item_code, item_name = v, hit[0][1]
                                source = '按码借用'
                                why = f"{pcn} 的 '{v}' {hit[0][1]}"
                                break
                # C. 兜底：如实登记原值（不臆造语义）
                if not item_code:
                    item_code, item_name = v, v
                    source = '原值登记'
                    why = '未找到权威名称来源，按值登记待治理组补语义'

                # 码冲突检查：借来的码在本 code_name 下已被占用且名称不同 → 放弃借用按值登记，
                # 否则同一 code_name 内会出现"一码两名"（如 publ_clg_flag '01' 已是公变，
                # 不能再登记成公线）
                if source in ('全局同名借用', '按码借用'):
                    occupied = ex['codes'].get(item_code)
                    if occupied is not None and occupied != item_name:
                        why = f"码 '{item_code}' 在 {cn} 已登记为「{occupied}」，避免一码两名改为按值登记"
                        item_code, item_name, source = v, v, '原值登记(避冲突)'

                key = (cn, item_code)
                if key in seen:
                    continue
                seen.add(key)
                plans.append({'code_name': cn, 'item_code': item_code,
                              'item_name': item_name, 'source': source, 'why': why,
                              'table': r['table'], 'column': r['column'],
                              'rows': _n, 'form': r.get('form')})
                # 更新内存索引，避免同批重复
                ex['codes'][item_code] = item_name
                ex['names'][item_name] = item_code
    return plans


# ----------------------------------------------------------- 备份 / 落库 / 回滚
DUMP_CANDIDATES = [
    'mysqldump',
    r'C:\Program Files\MySQL\MySQL Server 8.0\bin\mysqldump.exe',
]
TABLES = ['code_values', 'code_value_items', 'code_value_column_form']


def _find_dump():
    for c in DUMP_CANDIDATES:
        try:
            subprocess.run([c, '--version'], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=10)
            return c
        except Exception:
            continue
    return None


def backup(conn_gov, stamp):
    """两级备份：优先 mysqldump 出 .sql；失败则退化为 Python 导出 .json（行级可重建）。"""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    env = load_env()
    pw = os.environ.get('MYSQL_PASSWORD') or env.get('MYSQL_PASSWORD')
    dump = _find_dump()
    if dump:
        out = os.path.join(BACKUP_DIR, f'governance_codebook_{stamp}.sql')
        cmd = [dump, '-h', env.get('MYSQL_HOST', '127.0.0.1'),
               '-P', env.get('MYSQL_PORT', '3306'), '-u', env.get('MYSQL_USER', 'root'),
               f'-p{pw}', '--skip-add-locks', '--no-tablespaces',
               'fz01_governance'] + TABLES
        with open(out, 'wb') as f:
            r = subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE)
        if r.returncode == 0:
            return out
        print('[warn] mysqldump 失败，改用 JSON 备份：', r.stderr.decode('utf-8', 'ignore')[:200])
    # 兜底：JSON 行级备份
    out = os.path.join(BACKUP_DIR, f'governance_codebook_{stamp}.json')
    cur = conn_gov.cursor()
    data = {}
    for t in TABLES:
        cur.execute(f"SELECT * FROM `{t}`")
        cols = [d[0] for d in cur.description]
        data[t] = [dict(zip(cols, r)) for r in cur.fetchall()]
    json.dump(data, open(out, 'w', encoding='utf-8'), ensure_ascii=False,
              default=lambda o: str(o) if isinstance(o, dt.datetime) else o)
    return out


def commit(conn_gov, plans, stamp):
    cur = conn_gov.cursor()
    # 补 code_values 空壳（缺失的 code_name）
    cur.execute("SELECT code_name FROM code_values")
    have = {r[0] for r in cur.fetchall()}
    new_codes = sorted({p['code_name'] for p in plans} - have)
    for cn in new_codes:
        cur.execute("INSERT INTO code_values (code_name, code_cn_name, description) "
                    "VALUES (%s,%s,%s)", (cn, cn, f'{stamp} 由仿真码值复检补录'))

    inserted = []
    for p in plans:
        cur.execute("SELECT COUNT(*) FROM code_value_items "
                    "WHERE code_name=%s AND item_code=%s AND IFNULL(item_name,'')=%s",
                    (p['code_name'], p['item_code'], p['item_name'] or ''))
        if cur.fetchone()[0]:
            continue
        cur.execute("SELECT IFNULL(MAX(sort_order),0)+1 FROM code_value_items "
                    "WHERE code_name=%s", (p['code_name'],))
        so = cur.fetchone()[0]
        cur.execute("INSERT INTO code_value_items (code_name, item_code, item_name, sort_order) "
                    "VALUES (%s,%s,%s,%s)",
                    (p['code_name'], p['item_code'], p['item_name'], so))
        inserted.append({'id': cur.lastrowid, 'code_name': p['code_name'],
                         'item_code': p['item_code'], 'item_name': p['item_name']})
    conn_gov.commit()
    return new_codes, inserted


def write_manifest(stamp, plans, new_codes, inserted, backup_path, check_json):
    os.makedirs(MANIFEST_DIR, exist_ok=True)
    p = os.path.join(MANIFEST_DIR, f'governance_backfill_{stamp}.json')
    json.dump({'stamp': stamp, 'db': 'fz01_governance',
               'source_check': os.path.basename(check_json),
               'backup': backup_path,
               'new_code_values': new_codes,
               'items': inserted,
               'plans': plans},
              open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    return p


def undo(conn_gov, manifest):
    m = json.load(open(manifest, encoding='utf-8'))
    cur = conn_gov.cursor()
    n = 0
    for it in m['items']:
        cur.execute("DELETE FROM code_value_items WHERE id=%s", (it['id'],))
        n += cur.rowcount
    nc = 0
    for cn in m['new_code_values']:
        cur.execute("DELETE FROM code_values WHERE code_name=%s "
                    "AND code_cn_name=%s", (cn, cn))
        nc += cur.rowcount
    conn_gov.commit()
    print(f'[undo] 已删除 code_value_items {n} 行、code_values {nc} 行')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--check-json', default=r'D:\codex\deshu5\.workbuddy\reports\code_check_final.json')
    ap.add_argument('--gov-db', default='fz01_governance')
    ap.add_argument('--ont-db', default='fz01_ontology')
    ap.add_argument('--commit', action='store_true', help='真正落库（默认只出清单）')
    ap.add_argument('--undo', action='store_true')
    ap.add_argument('--manifest', default=None)
    ap.add_argument('--out', default=None, help='补录清单 json 输出路径')
    args = ap.parse_args()

    stamp = dt.datetime.now().strftime('%Y%m%d_%H%M%S')
    cg = conn(database=args.gov_db)
    try:
        co = conn(database=args.ont_db)
    except Exception:
        co = None

    if args.undo:
        if not args.manifest:
            sys.exit('--undo 需要 --manifest')
        undo(cg, args.manifest)
        return

    existing = load_existing(cg)
    by_name, by_cn = build_index(cg, co)
    plans = derive(args.check_json, existing, by_name, by_cn)

    print(f'待补录码项 {len(plans)} 条，覆盖 {len({p["code_name"] for p in plans})} 个 code_name\n')
    print(f"{'code_name':24s} {'item_code':10s} {'item_name':22s} {'来源':14s} 依据")
    print('-' * 118)
    for p in sorted(plans, key=lambda x: (x['source'], x['code_name'], x['item_code'])):
        print(f"{p['code_name']:24s} {p['item_code']:10s} {str(p['item_name'])[:20]:22s} "
              f"{p['source']:14s} {p['why']}")

    from collections import Counter
    print('\n按来源：', dict(Counter(p['source'] for p in plans)))

    out = args.out or os.path.join(HERE, 'manifest', f'governance_backfill_{stamp}_plan.json')
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump(plans, open(out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('清单已写', out)

    if not args.commit:
        print('\n[dry-run] 未落库。确认无误后加 --commit')
        return

    bk = backup(cg, stamp)
    print('[backup]', bk)
    new_codes, inserted = commit(cg, plans, stamp)
    mp = write_manifest(stamp, plans, new_codes, inserted, bk, args.check_json)
    print(f'[commit] 新增 code_values {len(new_codes)} 条、code_value_items {len(inserted)} 条')
    print('[manifest]', mp)


if __name__ == '__main__':
    main()
