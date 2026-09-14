# -*- coding: utf-8 -*-
"""治理库 `fz01_governance` 元数据整改（三组问题，逐条可回滚）。

三组整改（对应 2026-09-13 体检报告）：
  F1 悬空规则清理：`code_value_column_form` 中 table_name='ANY' 且 column_name
      在**全服所有 schema** 均无该列名的规则 → 删除（含"无损失"守卫）
  F2 码名大小写修复：`code_value_items.code_name` 与 `code_values.code_name`
      仅大小写不同 → 对齐到已登记形态（bill_Mode → bill_mode）
  F3 标注与唯一性自愈：form 与真实取值形态不符 → 改 form；
      一列映多个 code_name → 按真实取值命中率保留最优、删冗余

只读产出（不改库）：
  F4 《待补码表清单》—— code_name 在 code_values 有登记但全库无码项
      （校验被静默跳过 = 管控盲区），交治理组补码，本脚本不臆造码值

安全约定：
  1. 先备份三张表（mysqldump，失败退化为行级 JSON）
  2. 台账记录**被删整行原始值**与**被改字段原值**，--undo 精确还原
  3. 默认 dry-run 只出变更单，须显式 --commit 才落库
  4. F1 带"无损失"守卫：仅当同一 code_name 在真实列上已有有效映射时才删

用法：
    python fix_governance_metadata.py                       # dry-run 全部
    python fix_governance_metadata.py --commit              # 落库（全组）
    python fix_governance_metadata.py --commit --only F1,F3 # 只落指定组
    python fix_governance_metadata.py --undo --manifest manifest/fix_meta_xxx.json
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
GOV_DB = 'fz01_governance'
ONT_DB = 'fz01_ontology'
BIZ_DB = 'fz01'
TABLES = ['code_values', 'code_value_items', 'code_value_column_form']
SYS = ('information_schema', 'mysql', 'performance_schema', 'sys')

# 环境档案：staging 与生产两套
PROFILES = {
    'staging': {'gov': 'fz01_governance', 'ont': 'fz01_ontology',
                'biz': 'fz01', 'tag': 'staging'},
    'prod': {'gov': 'sc01_governance', 'ont': 'sc01_ontology',
             'biz': 'sc01', 'tag': 'prod'},
}


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


def sz(s):
    s = str(s).strip()
    return s.lstrip('0') or '0'


# ============================================================ 变更检测
def load_context(cg, cob=None):
    """返回 (mappings, items, vals_cn, col2schemas, ont_items)"""
    g = cg.cursor()
    g.execute("SELECT id, table_name, column_name, code_name, form, updated_at "
              "FROM code_value_column_form")
    mappings = [dict(id=r[0], table_name=r[1], column_name=r[2], code_name=r[3],
                     form=r[4], updated_at=str(r[5]) if r[5] else None)
                for r in g.fetchall()]
    g.execute("SELECT id, code_name, item_code, item_name, sort_order FROM code_value_items")
    items = [dict(id=r[0], code_name=r[1], item_code=r[2], item_name=r[3], sort_order=r[4])
             for r in g.fetchall()]
    g.execute("SELECT code_name FROM code_values")
    vals_cn = {r[0] for r in g.fetchall()}

    # 全服列名索引（不只 staging，避免误删生产专用规则）
    j = conn().cursor()
    j.execute("SELECT table_schema, table_name, column_name FROM information_schema.columns "
              "WHERE table_schema NOT IN %s", (SYS,))
    col2loc = defaultdict(set)
    for sch, tb, col in j.fetchall():
        col2loc[col].add((sch, tb))
    # 目标业务库的列集合（F1 守卫用：对偶列必须真实落在目标环境）
    j.execute("SELECT column_name FROM information_schema.columns WHERE table_schema=%s",
              (BIZ_DB,))
    biz_cols = {r[0] for r in j.fetchall()}

    ont_items = {}
    if cob is not None:
        o = cob.cursor()
        o.execute("SELECT code_name, items FROM ontology_enumerations")
        for cn, its in o.fetchall():
            n = 0
            if its:
                try:
                    n = len(json.loads(its))
                except Exception:
                    n = 0
            ont_items[cn] = ont_items.get(cn, 0) + n
    return mappings, items, vals_cn, col2loc, ont_items, biz_cols


def detect_f1(mappings, col2loc, biz_cols=None):
    """F1 悬空规则：ANY 规则列名全服不存在。附'无损失'守卫。

    redundant=True  ：该规则指向的列全服不存在，且它的"对偶真实列"**在目标业务库
                      真实存在**、**已被目标治理库中任一条规则覆盖**
                      → 删除不减少任何校验覆盖（可自动删）
    redundant=False ：对偶列未落地到目标业务库、或未被覆盖（或本 code_name 仅此一条
                      映射）→ 只报告，交由治理组判断"改指"还是"废弃"

    注意 biz_cols 必须传**目标环境**的业务库列集合：staging 用 fz01（120 表）、
    生产用 sc01（84 表）。两库列不完全重合，例：`pay_mode_desc` 只在 staging
    存在，生产库没有 —— 若按全服判定会误删生产库仍需保留的规则。
    """
    colnames = set(col2loc)
    biz_cols = set(biz_cols) if biz_cols is not None else colnames
    # 映射表里出现过的所有列名（含复合拆分）
    all_mapped_cols = set()
    for m in mappings:
        for c in str(m['column_name']).split(';'):
            if c.strip():
                all_mapped_cols.add(c.strip())
    cn_count = defaultdict(int)
    for m in mappings:
        cn_count[m['code_name']] += 1

    dead = []
    for m in mappings:
        if m['table_name'] != 'ANY':
            continue
        names = [x.strip() for x in str(m['column_name']).split(';') if x.strip()]
        if not names or any(n in colnames for n in names):
            continue
        # 推导"对偶真实列"：词根相同、后缀互换（_code / _name / _desc / 无后缀）
        cands = []
        for n in names:
            base = n
            for suf in ('_desc', '_code', '_name'):
                if n.endswith(suf):
                    base = n[:-len(suf)]
                    break
            probe = {base, base + '_desc', base + '_code', base + '_name'} - {n}
            for c in probe:
                if c in colnames:
                    cands.append(c)
        in_biz = [c for c in cands if c in biz_cols]
        covered = [c for c in cands if c in all_mapped_cols]
        covered_in_biz = [c for c in in_biz if c in all_mapped_cols]
        dead.append({**m, 'counterpart': sorted(set(cands)),
                     'counterpart_in_biz': sorted(set(in_biz)),
                     'covered_by': sorted(set(covered)),
                     'cn_other_rules': cn_count[m['code_name']] - 1,
                     'redundant': bool(covered_in_biz)})
    return dead, sorted(set(m['code_name'] for m in dead))


def detect_f2(items, vals_cn):
    """F2 大小写失联：items 的 code_name 不在 code_values，但忽略大小写能命中。"""
    low = {v.lower(): v for v in vals_cn}
    orphans = {}
    for it in items:
        cn = it['code_name']
        if cn is None or cn in vals_cn:
            continue
        hit = low.get(str(cn).lower())
        if hit:
            orphans.setdefault(cn, {'target': hit, 'rows': []})['rows'].append(it)
    return orphans


def detect_f3(cg, mappings, items, col2loc):
    """F3a form 与真实取值不符；F3b 一列映多个 code_name。"""
    by_cn = defaultdict(lambda: {'codes': set(), 'names': set()})
    for it in items:
        if it['code_name'] is None:
            continue
        if it['item_code'] is not None:
            by_cn[it['code_name']]['codes'].add(str(it['item_code']).strip())
        if it['item_name'] is not None:
            by_cn[it['code_name']]['names'].add(str(it['item_name']).strip())

    def real_vals(col, tables, limit=60):
        vals = set()
        b = conn(database=BIZ_DB).cursor()
        for tb in tables:
            try:
                b.execute(f"SELECT DISTINCT CAST(`{col}` AS CHAR) FROM `{tb}` "
                          f"WHERE `{col}` IS NOT NULL LIMIT {limit}")
                for (v,) in b.fetchall():
                    if v is not None:
                        vals.add(str(v).strip())
            except Exception:
                continue
        vals.discard('\\N')
        return vals

    # --- F3a ---
    f3a = []
    for m in mappings:
        if m['form'] not in ('编码', '名称'):
            continue
        d = by_cn.get(m['code_name'])
        if not d or (not d['codes'] and not d['names']):
            continue
        col = str(m['column_name']).split(';')[0].strip()
        if m['table_name'] == 'ANY':
            tables = sorted({tb for (_sch, tb) in col2loc.get(col, ())})[:3]
        else:
            tables = [m['table_name']]
        vals = real_vals(col, tables)
        if not vals:
            continue
        codes_nz = {sz(x) for x in d['codes']}
        names_nz = {sz(x) for x in d['names']}
        n_code = sum(1 for v in vals if v in d['codes'] or sz(v) in codes_nz)
        n_name = sum(1 for v in vals if v in d['names'] or sz(v) in names_nz)
        tot = len(vals)
        if m['form'] == '编码' and n_name / tot > 0.6 and n_code / tot < 0.4:
            f3a.append({**m, 'suggest': '名称', 'n': tot, 'match_code': n_code,
                        'match_name': n_name, 'sample': sorted(vals)[:6]})
        elif m['form'] == '名称' and n_code / tot > 0.6 and n_name / tot < 0.4:
            f3a.append({**m, 'suggest': '编码', 'n': tot, 'match_code': n_code,
                        'match_name': n_name, 'sample': sorted(vals)[:6]})

    # --- F3b ---
    bycol = defaultdict(list)
    for m in mappings:
        for nc in [x.strip() for x in str(m['column_name']).split(';') if x.strip()]:
            bycol[(m['table_name'], nc)].append(m)
    f3b = []
    for (t, c), ms in bycol.items():
        cns = sorted({m['code_name'] for m in ms})
        if len(cns) < 2:
            continue
        if t == 'ANY':
            tables = sorted({tb for (_sch, tb) in col2loc.get(c, ())})[:3]
        else:
            tables = [t]
        vals = real_vals(c, tables)
        if not vals:
            f3b.append({'table_name': t, 'column_name': c, 'values': 0,
                        'score': {}, 'keep': None, 'drop_ids': [],
                        'note': '真实取值为空，仅报告不动作'})
            continue
        score = {}
        for cn in cns:
            d = by_cn.get(cn) or {'codes': set(), 'names': set()}
            codes_nz = {sz(x) for x in d['codes']}
            names_nz = {sz(x) for x in d['names']}
            hit = sum(1 for v in vals if v in d['codes'] or sz(v) in codes_nz
                      or v in d['names'] or sz(v) in names_nz)
            score[cn] = round(hit / len(vals), 3)
        rank = sorted(score.items(), key=lambda x: -x[1])
        keep = rank[0][0] if rank[0][1] > 0.6 else None
        clear = keep and (len(rank) == 1 or rank[1][1] < 0.4)
        drop = [m['id'] for m in ms if m['code_name'] != keep] if clear else []
        f3b.append({'table_name': t, 'column_name': c, 'values': len(vals),
                    'score': score, 'keep': keep if clear else None,
                    'drop_ids': drop, 'samples': sorted(vals)[:6],
                    'note': '' if clear else '命中率无明确优劣，仅报告不动作'})
    return f3a, f3b


def detect_f4(mappings, items, vals_cn, ont_items):
    """F4 空壳枚举：有映射、code_values 已登记，但全库零码项 → 管控盲区。"""
    have_items = {it['code_name'] for it in items}
    mapped_cn = {m['code_name'] for m in mappings}
    hollow = []
    for cn in sorted(mapped_cn):
        if cn in have_items:
            continue
        hollow.append({'code_name': cn, 'in_code_values': cn in vals_cn,
                       'ontology_item_count': ont_items.get(cn, 0),
                       'mapping_rows': len([m for m in mappings if m['code_name'] == cn]),
                       'columns': sorted({m['column_name'] for m in mappings
                                          if m['code_name'] == cn})[:6]})
    return hollow


# ============================================================ 备份 / 执行 / 回滚
DUMP_CANDIDATES = ['mysqldump', r'C:\Program Files\MySQL\MySQL Server 8.0\bin\mysqldump.exe']


def _find_dump():
    for c in DUMP_CANDIDATES:
        try:
            subprocess.run([c, '--version'], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=10)
            return c
        except Exception:
            continue
    return None


def backup(cg, stamp, gov_db=None, tag='governance'):
    gov_db = gov_db or GOV_DB
    os.makedirs(BACKUP_DIR, exist_ok=True)
    env = load_env()
    pw = os.environ.get('MYSQL_PASSWORD') or env.get('MYSQL_PASSWORD')
    dump = _find_dump()
    if dump:
        out = os.path.join(BACKUP_DIR, f'{tag}_meta_{stamp}.sql')
        cmd = [dump, '-h', env.get('MYSQL_HOST', '127.0.0.1'),
               '-P', env.get('MYSQL_PORT', '3306'), '-u', env.get('MYSQL_USER', 'root'),
               f'-p{pw}', '--skip-add-locks', '--no-tablespaces', gov_db] + TABLES
        with open(out, 'wb') as f:
            r = subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE)
        if r.returncode == 0:
            return out
        print('[warn] mysqldump 失败，改用 JSON 备份：', r.stderr.decode('utf-8', 'ignore')[:200])
    out = os.path.join(BACKUP_DIR, f'{tag}_meta_{stamp}.json')
    cur = cg.cursor()
    data = {}
    for t in TABLES:
        cur.execute(f"SELECT * FROM `{t}`")
        cols = [d[0] for d in cur.description]
        data[t] = [dict(zip(cols, r)) for r in cur.fetchall()]
    json.dump(data, open(out, 'w', encoding='utf-8'), ensure_ascii=False,
              default=lambda o: str(o) if isinstance(o, dt.datetime) else o)
    return out


def apply_changes(cg, f1, f2, f3a, f3b):
    cur = cg.cursor()
    ops = {'f1_delete': [], 'f2_update': [], 'f3a_update': [], 'f3b_delete': []}

    for m in f1:
        if not m['redundant']:
            continue
        cur.execute("DELETE FROM code_value_column_form WHERE id=%s", (m['id'],))
        if cur.rowcount:
            ops['f1_delete'].append({k: m[k] for k in
                                     ('id', 'table_name', 'column_name', 'code_name',
                                      'form', 'updated_at')})

    for cn, info in f2.items():
        tgt = info['target']
        ids = [r['id'] for r in info['rows']]
        cur.execute("UPDATE code_value_items SET code_name=%s WHERE code_name=%s", (tgt, cn))
        if cur.rowcount:
            ops['f2_update'].append({'from': cn, 'to': tgt, 'ids': ids,
                                     'rows': info['rows']})

    for m in f3a:
        cur.execute("UPDATE code_value_column_form SET form=%s WHERE id=%s",
                    (m['suggest'], m['id']))
        if cur.rowcount:
            ops['f3a_update'].append({'id': m['id'], 'table_name': m['table_name'],
                                      'column_name': m['column_name'],
                                      'code_name': m['code_name'],
                                      'old_form': m['form'], 'new_form': m['suggest']})

    for g in f3b:
        for _id in g['drop_ids']:
            cur.execute("SELECT id, table_name, column_name, code_name, form, updated_at "
                        "FROM code_value_column_form WHERE id=%s", (_id,))
            row = cur.fetchone()
            if not row:
                continue
            cur.execute("DELETE FROM code_value_column_form WHERE id=%s", (_id,))
            if cur.rowcount:
                ops['f3b_delete'].append(dict(id=row[0], table_name=row[1], column_name=row[2],
                                              code_name=row[3], form=row[4],
                                              updated_at=str(row[5]) if row[5] else None,
                                              keep_code_name=g['keep']))
    cg.commit()
    return ops


def undo(cg, manifest):
    m = json.load(open(manifest, encoding='utf-8'))
    cur = cg.cursor()
    c = defaultdict(int)
    for r in m['ops']['f3b_delete'] + m['ops']['f1_delete']:
        cur.execute("INSERT INTO code_value_column_form "
                    "(id, table_name, column_name, code_name, form, updated_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s) "
                    "ON DUPLICATE KEY UPDATE table_name=VALUES(table_name), "
                    "column_name=VALUES(column_name), code_name=VALUES(code_name), "
                    "form=VALUES(form), updated_at=VALUES(updated_at)",
                    (r['id'], r['table_name'], r['column_name'], r['code_name'],
                     r['form'], r['updated_at']))
        c['column_form_restored'] += 1
    for u in m['ops']['f3a_update']:
        cur.execute("UPDATE code_value_column_form SET form=%s WHERE id=%s",
                    (u['old_form'], u['id']))
        c['form_restored'] += cur.rowcount
    for u in m['ops']['f2_update']:
        cur.execute("UPDATE code_value_items SET code_name=%s WHERE code_name=%s",
                    (u['from'], u['to']))
        c['items_code_name_restored'] += cur.rowcount
    cg.commit()
    print('[undo]', dict(c))


# ============================================================ 待补码表清单导出
def export_worklist(hollow, ont_items, stamp):
    os.makedirs(REPORT_DIR, exist_ok=True)
    rows = [h for h in hollow]
    md = [f'# 治理库待补码表清单（管控盲区）', '',
          f'生成时间：{dt.datetime.now():%Y-%m-%d %H:%M} ｜ 目标库：`{GOV_DB}`', '',
          f'**共 {len(rows)} 个 code_name 在映射表中被引用、`code_values` 中已登记，'
          f'但全库（本体 + `code_value_items`）零码项 → 校验时被静默跳过。**', '',
          '> 本清单只登记事实，不臆造码值。请治理组按业务口径补齐 `item_code` / `item_name`。', '',
          '| # | code_name | 本体枚举中文名/码项数 | code_values | 映射行数 | 关联列（示例） |',
          '|---|---|---|---|---|---|']
    g = conn(database=GOV_DB).cursor()
    for i, h in enumerate(rows, 1):
        g.execute("SELECT code_cn_name FROM code_values WHERE code_name=%s", (h['code_name'],))
        cn_name = (g.fetchone() or [None])[0] or ''
        md.append(f"| {i} | `{h['code_name']}` | {(cn_name or '—')[:26]} / "
                  f"{h['ontology_item_count']} | {'是' if h['in_code_values'] else '否'} | "
                  f"{h['mapping_rows']} | {', '.join(h['columns'][:3])} |")
    md += ['', '## 整改建议', '',
           '1. 优先补 **本体已有枚举但码项为空** 的项——本体 `ontology_enumerations.items` 为空说明'
           '登记链路本身断在源头。',
           '2. 码项补齐后重跑 `check_code_values.py` 复检，这些列将从"跳过"转为"实检"。',
           '3. 若某 code_name 已废弃，应同步清理其映射行，避免"有规则无码表"的空管控。']
    p = os.path.join(REPORT_DIR, f'治理库待补码表清单_{TAG}.md')
    open(p, 'w', encoding='utf-8').write('\n'.join(md))
    json.dump(rows, open(os.path.join(REPORT_DIR, f'meta_f4_hollow_{TAG}.json'), 'w',
                         encoding='utf-8'), ensure_ascii=False, indent=1)
    # xlsx 版（列：序号 / code_name / 本体中文名 / 本体码项数 / code_values / 映射行数 / 关联列）
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        wb = Workbook()
        ws = wb.active
        ws.title = '待补码表'
        head = ['序号', 'code_name', '本体枚举中文名', '本体码项数',
                'code_values 已登记', '映射行数', '关联列（示例）']
        ws.append(head)
        g2 = conn(database=GOV_DB).cursor()
        for i, h in enumerate(rows, 1):
            g2.execute("SELECT code_cn_name FROM code_values WHERE code_name=%s", (h['code_name'],))
            cn_name = (g2.fetchone() or [None])[0] or ''
            ws.append([i, h['code_name'], cn_name, h['ontology_item_count'],
                       '是' if h['in_code_values'] else '否', h['mapping_rows'],
                       ', '.join(h['columns'])])
        for c in ws[1]:
            c.font = Font(bold=True, color='FFFFFF')
            c.fill = PatternFill('solid', fgColor='C00000')
            c.alignment = Alignment(horizontal='center', vertical='center')
        for w, col in zip((6, 26, 34, 12, 18, 10, 52), 'ABCDEFG'):
            ws.column_dimensions[col].width = w
        ws.freeze_panes = 'A2'
        xp = os.path.join(REPORT_DIR, f'治理库待补码表清单_{TAG}.xlsx')
        wb.save(xp)
        print(f'待补码表清单 xlsx 已写 {xp}')
    except Exception as e:
        print('[warn] xlsx 导出跳过：', e)
    return p


# ============================================================ 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--commit', action='store_true', help='真正落库（默认只出变更单）')
    ap.add_argument('--only', default='F1,F2,F3', help='只落指定组，逗号分隔')
    ap.add_argument('--undo', action='store_true')
    ap.add_argument('--manifest', default=None)
    ap.add_argument('--profile', choices=sorted(PROFILES), default='staging',
                    help='环境档案：staging= fz01_*，prod= marketing_*')
    ap.add_argument('--gov-db', default=None, help='覆盖治理库名')
    ap.add_argument('--ont-db', default=None, help='覆盖本体库名')
    ap.add_argument('--biz-db', default=None, help='覆盖业务库名（form 校验取真实值）')
    args = ap.parse_args()

    # 环境档案落到模块级常量（各 detect_* 函数按调用时取值）
    global GOV_DB, ONT_DB, BIZ_DB, TAG
    prof = PROFILES[args.profile]
    GOV_DB = args.gov_db or prof['gov']
    ONT_DB = args.ont_db or prof['ont']
    BIZ_DB = args.biz_db or prof['biz']
    TAG = args.tag if hasattr(args, 'tag') else prof['tag']

    stamp = dt.datetime.now().strftime('%Y%m%d_%H%M%S')
    print(f'[env] profile={args.profile} 治理={GOV_DB} 本体={ONT_DB} 业务={BIZ_DB}')
    cg = conn(database=GOV_DB)
    try:
        cob = conn(database=ONT_DB)
    except Exception:
        cob = None

    if args.undo:
        if not args.manifest:
            sys.exit('--undo 需要 --manifest')
        undo(cg, args.manifest)
        return

    mappings, items, vals_cn, col2loc, ont_items, biz_cols = load_context(cg, cob)
    f1, f1cn = detect_f1(mappings, col2loc, biz_cols)
    f2 = detect_f2(items, vals_cn)
    f3a, f3b = detect_f3(cg, mappings, items, col2loc)
    f4 = detect_f4(mappings, items, vals_cn, ont_items)

    print('=' * 108)
    print(f'治理库元数据体检与整改 ｜ 映射 {len(mappings)} 行 ｜ 码项 {len(items)} 条')
    print('=' * 108)

    print(f'\n【F1 悬空规则】{len(f1)} 条（涉及 {len(f1cn)} 个 code_name）；'
          f'其中"对偶列已在目标业务库落地且被覆盖"可安全删除 '
          f'{len([x for x in f1 if x["redundant"]])} 条')
    print(f"  {'id':>6s} {'悬空列名':32s} {'code_name':22s} {'对偶真实列':30s} "
          f"{'在业务库':8s} 可删")
    for x in f1:
        print(f"  {x['id']:6d} {str(x['column_name'])[:32]:32s} {str(x['code_name'])[:22]:22s} "
              f"{','.join(x['counterpart'])[:30]:30s} "
              f"{'是' if x['counterpart_in_biz'] else '否':8s} "
              f"{'是' if x['redundant'] else '否(仅报告)'}")

    print(f'\n【F2 码名大小写失联】{len(f2)} 组')
    for cn, info in f2.items():
        print(f"  {cn!r} → {info['target']!r}（{len(info['rows'])} 条码项："
              f"{[(r['item_code'], r['item_name']) for r in info['rows']]}）")

    print(f'\n【F3a form 与真实取值不符】{len(f3a)} 条')
    for x in f3a:
        loc = f"{x['table_name']}.{x['column_name']}"
        print(f"  id={x['id']} {loc} → {x['code_name']}: 「{x['form']}」应为「{x['suggest']}」"
              f"（名命中 {x['match_name']}/{x['n']}，例 {x['sample'][:4]}）")
    print(f'\n【F3b 一列映多 code_name】{len(f3b)} 组')
    for x in f3b:
        print(f"  {x['table_name']}.{x['column_name']}: 命中率 {x['score']} → 保留 {x['keep']}，"
              f"删 id {x['drop_ids']} {x['note']}")

    print(f'\n【F4 空壳枚举（管控盲区）】{len(f4)} 个 code_name —— 只读产出清单，不改库')
    print(f"  本体码项为 0 的：{len([x for x in f4 if x['ontology_item_count'] == 0])} 个")

    # 变更单落盘
    plan = {'stamp': stamp, 'db': GOV_DB,
            'F1': f1, 'F2': {k: v for k, v in f2.items()},
            'F3a': f3a, 'F3b': f3b, 'F4': f4}
    os.makedirs(MANIFEST_DIR, exist_ok=True)
    pp = os.path.join(MANIFEST_DIR, f'fix_meta_{TAG}_{stamp}_plan.json')
    json.dump(plan, open(pp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1,
              default=str)
    print(f'\n变更单已写 {pp}')
    wl = export_worklist(f4, ont_items, stamp)
    print(f'待补码表清单已写 {wl}')

    if not args.commit:
        print('\n[dry-run] 未落库。确认无误后加 --commit（可用 --only F1,F2,F3 指定组）')
        return

    only = {s.strip().upper() for s in args.only.split(',') if s.strip()}
    do_f1 = f1 if 'F1' in only else []
    do_f2 = f2 if 'F2' in only else {}
    do_f3a = f3a if 'F3' in only else []
    do_f3b = f3b if 'F3' in only else []

    bk = backup(cg, stamp, gov_db=GOV_DB, tag=TAG)
    print('[backup]', bk)
    ops = apply_changes(cg, do_f1, do_f2, do_f3a, do_f3b)
    mp = os.path.join(MANIFEST_DIR, f'fix_meta_{TAG}_{stamp}.json')
    json.dump({'stamp': stamp, 'db': GOV_DB, 'profile': args.profile,
               'biz_db': BIZ_DB, 'backup': bk, 'ops': ops, 'plan': pp},
              open(mp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1, default=str)
    print(f"[commit] F1 删 {len(ops['f1_delete'])} 行 ｜ "
          f"F2 改 {sum(len(u['ids']) for u in ops['f2_update'])} 行 ｜ "
          f"F3a 改 {len(ops['f3a_update'])} 行 ｜ F3b 删 {len(ops['f3b_delete'])} 行")
    print('[manifest]', mp)
    print(f'[rollback] python fix_governance_metadata.py --undo --manifest "{mp}"')


if __name__ == '__main__':
    main()
