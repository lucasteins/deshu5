# -*- coding: utf-8 -*-
"""region_adcode（GB/T 2260 行政区划代码）贯通改造（skill: ads-table-standard 第八节）

三件事：
1. dim_org_region_map 重建为「GB/T 2260 全量区划（省/市/区县 3351 行）LEFT JOIN 电网机构映射」：
   region_name 用 GB/T 2260 标准名；无电网映射的行 mgt_org_* 留空；电网侧映射挂到对应区划行
2. 5 张 ads_urb_* 表加列 region_adcode VARCHAR(12) 并按名称回填（全国/新疆兵团无码留 NULL）
3. 两张 city 表删除 region_level IN ('全国','省') 的混入行（层级纯化）

数据源：db/database/pcas_code_gbt2260.json（modood 行政区划数据集，民政/统计口径）
同步：治理库（schema docs / code_values.region_adcode / column_form / keyword）+ 本体换版
幂等可重跑；--dry-run 只打印。
"""
import argparse
import json
import re
import sys
from datetime import datetime

sys.path.insert(0, r'D:\codex\deshu5')
import pymysql

import config
from tools.build_org_region_map import build_seeds

DB_BIZ = 'fz01'
DB_GOV = 'fz01_governance'
GB_FILE = r'db/database/pcas_code_gbt2260.json'
BRIDGE = 'dim_org_region_map'

URB_TABLES = ['ads_urb_water_supply_nat_yr', 'ads_urb_water_supply_prov_yr',
              'ads_urb_water_supply_city_yr', 'ads_urb_sewage_prov_yr', 'ads_urb_sewage_city_yr']
CITY_TABLES = ['ads_urb_water_supply_city_yr', 'ads_urb_sewage_city_yr']

# 名称归一化后缀（长后缀优先）
SUFFIXES = ['壮族自治区', '回族自治区', '维吾尔自治区', '特别行政区', '自治州', '自治区',
            '地区', '省', '市', '盟']


def norm_name(name):
    n = (name or '').strip()
    for s in SUFFIXES:
        if n.endswith(s) and len(n) > len(s):
            return n[: -len(s)]
    return n


def load_gb():
    """数据集 → [(adcode6, name, level, parent_adcode6)]，level ∈ 省/城市/区县。"""
    data = json.load(open(GB_FILE, encoding='utf-8'))
    out = []
    for p in data:
        pcode = p['code'].ljust(6, '0')
        out.append((pcode, p['name'], '省', None))
        for c in p.get('children') or []:
            ccode = c['code'].ljust(6, '0')
            out.append((ccode, c['name'], '城市', pcode))
            for d in c.get('children') or []:
                if d['name'] == c['name'] and d['code'].startswith(c['code']):
                    continue  # 直筒子市（东莞/中山/嘉峪关/儋州等）的伪区县级壳（码可能与市级相同或相邻）
                out.append((d['code'], d['name'], '区县', ccode))
    return out


def build_gb_index(divs):
    """归一化名 → [(adcode, name, level)]（重名保留多个，回填时报歧义）。"""
    idx = {}
    for code, name, level, _p in divs:
        idx.setdefault(norm_name(name), []).append((code, name, level))
    return idx


# 俗名 → GB/T 2260 标准名
ALIAS = {'杨凌区': '杨陵区', '济源示范区': '济源市', '全省': '浙江省', '全省合计': '浙江省'}


def find_gb(idx, urb_name, prefer_level=None):
    """urb 地区名 → (adcode, gb_name, level)；全国/兵团/歧义 → None。
    prefer_level: '省'/'城市' — 多命中时按表粒度过滤（城市级含 GB 区县级的县级市），解'吉林'省/市歧义。"""
    n = (urb_name or '').strip()
    n = ALIAS.get(n, n)
    if n in ('全国', '新疆兵团'):
        return None
    hits = idx.get(norm_name(n)) or []
    if prefer_level == '城市':
        hits = [h for h in hits if h[2] in ('城市', '区县')]
        if not hits and n in ('北京市', '天津市', '上海市', '重庆市'):
            hits = idx.get(norm_name(n)) or []  # 直辖市在 GB 中仅省级一行，城市行回填省级码
    elif prefer_level:
        hits = [h for h in hits if h[2] == prefer_level]
    if len(hits) == 1:
        return hits[0]
    # 重名时优先精确名匹配（未归一）
    exact = [h for h in hits if h[1] == n]
    if len(exact) == 1:
        return exact[0]
    return None  # 歧义或无


BRIDGE_DDL = """CREATE TABLE `dim_org_region_map` (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '物理主键',
  `mgt_org_code` VARCHAR(32) NULL COMMENT '电网管理单位编码（紧凑码，如 33401；无映射留空）',
  `mgt_org_name` VARCHAR(128) NULL COMMENT '电网管理单位名称',
  `org_level` VARCHAR(16) NULL COMMENT '电网层级（省/地市/区县/服务站）',
  `region_adcode` VARCHAR(12) NULL COMMENT '行政区划代码（GB/T 2260，6 位）',
  `region_name` VARCHAR(64) NOT NULL COMMENT '行政区划名称（GB/T 2260 标准名）',
  `region_level` VARCHAR(16) NOT NULL COMMENT '地区层级（省/城市/区县；县级市按 urb 口径记城市）',
  `map_source` VARCHAR(32) NOT NULL COMMENT '映射来源（GB/T 2260 预载/dim_cst_mgt_org/人工规则）',
  `remark` VARCHAR(255) NULL COMMENT '口径备注',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_adcode` (`region_adcode`),
  UNIQUE KEY `uk_org` (`mgt_org_code`),
  KEY `idx_region` (`region_name`, `region_level`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='电网管理单位 ↔ 行政区划 映射桥表（GB/T 2260 全量预载 + 电网机构挂载）'"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    dry = args.dry_run
    now = datetime.now()

    divs = load_gb()
    gb_idx = build_gb_index(divs)
    print(f'GB/T 2260 区划加载：{len(divs)} 行（省 {sum(1 for d in divs if d[2]=="省")}，'
          f'城市 {sum(1 for d in divs if d[2]=="城市")}，区县 {sum(1 for d in divs if d[2]=="区县")}）')

    biz = connect(DB_BIZ)

    print('== 1/5 桥表重建（GB 全量 + 电网挂载）==')
    org_seeds = build_seeds(biz)  # (code, org_name, org_level, adcode_old, region_name, region_level, remark)
    # 归一化 GB 名 → 区划行（按目标层级过滤避免区县/城市重名冲突）
    def match_gb(region_name, region_level):
        hits = gb_idx.get(norm_name(region_name)) or []
        # 电网机构均在浙江：仅认 33 前缀（防"南城区"误挂东莞等同名外区县）
        hits = [h for h in hits if h[0].startswith('33')]
        if region_level == '城市':
            # 县级市按 urb 口径记城市，但 GB 里在区县级；两级都找
            hits = [h for h in hits if h[2] in ('城市', '区县')]
        else:
            hits = [h for h in hits if h[2] == region_level]
        if not hits and region_level == '区县':
            # 自治县兜底：'景宁县' → '景宁畲族自治县'
            base = norm_name(region_name).rstrip('县区')
            hits = [(c, n, l) for c, n, l, _p in divs
                    if l == '区县' and n.endswith('自治县') and n.startswith(base) and c.startswith('33')]
        if len(hits) == 1:
            return hits[0]
        exact = [h for h in hits if h[1] == region_name]
        return exact[0] if len(exact) == 1 else None

    bridge_rows = []  # (mgt_org_code, org_name, org_level, adcode, region_name, region_level, map_source, remark)
    used_adcodes = set()
    unmatched_orgs = []
    for code, org_name, org_level, _adc, region, rlevel, remark in org_seeds:
        hit = match_gb(region, rlevel)
        if hit:
            adcode, gb_name, _gb_level = hit
            used_adcodes.add(adcode)
            src = '人工规则' if org_level == '省' else 'dim_cst_mgt_org'
            bridge_rows.append((code, org_name, org_level, adcode, gb_name, rlevel, src, remark))
        else:
            unmatched_orgs.append((code, org_name, org_level, None, region, rlevel,
                                   'dim_cst_mgt_org', '名称解析未命中 GB/T 2260，待人工确认'))
    for code, name, level, parent in divs:
        if code not in used_adcodes:
            bridge_rows.append((None, None, None, code, name, level, 'GB/T 2260 预载', ''))
    bridge_rows += unmatched_orgs
    print(f'  桥表行数 {len(bridge_rows)}（电网映射 {len(org_seeds)}：挂载 {len(org_seeds)-len(unmatched_orgs)}，'
          f'未命中 GB {len(unmatched_orgs)}；纯区划预载 {len(divs) - len(used_adcodes)}）')
    if unmatched_orgs:
        for u in unmatched_orgs[:8]:
            print('   未命中:', u[0], u[1], '->', u[4])
    with biz.cursor() as cur:
        if dry:
            print('  [dry] DROP+CREATE dim_org_region_map，INSERT', len(bridge_rows), '行')
        else:
            cur.execute(f'DROP TABLE IF EXISTS `{BRIDGE}`')
            cur.execute(BRIDGE_DDL)
            cur.executemany(
                f'INSERT INTO `{BRIDGE}` (mgt_org_code, mgt_org_name, org_level, region_adcode, '
                f'region_name, region_level, map_source, remark) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',
                bridge_rows)
            biz.commit()
            cur.execute(f'SELECT COUNT(*), COUNT(mgt_org_code), COUNT(DISTINCT region_adcode) FROM `{BRIDGE}`')
            print('  [ok] 桥表：', cur.fetchone())

    print('== 2/5 urb 表加 region_adcode 列并回填 ==')
    for t in URB_TABLES:
        with biz.cursor() as cur:
            cur.execute('SELECT COUNT(*) FROM information_schema.columns '
                        'WHERE table_schema=%s AND table_name=%s AND column_name=%s',
                        (DB_BIZ, t, 'region_adcode'))
            exists = cur.fetchone()[0] > 0
            if dry:
                print(f'  [dry] {t} ADD region_adcode + 回填')
                continue
            if not exists:
                cur.execute(f"ALTER TABLE `{t}` ADD COLUMN `region_adcode` VARCHAR(12) NULL "
                            f"COMMENT '行政区划代码（GB/T 2260）' AFTER `region_level`")
            cur.execute(f'SELECT DISTINCT region_name FROM `{t}`')
            names = [r[0] for r in cur.fetchall()]
            prefer = '省' if t.endswith('_prov_yr') else ('城市' if t.endswith('_city_yr') else None)
            unmatched = []
            for name in names:
                hit = find_gb(gb_idx, name, prefer_level=prefer)
                if hit:
                    cur.execute(f'UPDATE `{t}` SET region_adcode=%s WHERE region_name=%s', (hit[0], name))
                else:
                    unmatched.append(name)
            biz.commit()
            cur.execute(f'SELECT COUNT(*), SUM(region_adcode IS NULL) FROM `{t}`')
            total, nulls = cur.fetchone()
            print(f'  [ok] {t}: {total} 行，adcode 空 {nulls}'
                  + (f'，未匹配地区: {unmatched}' if unmatched else ''))

    print('== 3/5 city 表层级纯化（删 全国/省 行）==')
    for t in CITY_TABLES:
        with biz.cursor() as cur:
            if dry:
                cur.execute(f"SELECT region_level, COUNT(*) FROM `{t}` GROUP BY region_level")
                print(f'  [dry] {t} 当前层级分布: {cur.fetchall()}')
                continue
            cur.execute(f"DELETE FROM `{t}` WHERE region_level IN ('全国','省')")
            n = cur.rowcount
            biz.commit()
            cur.execute(f"SELECT region_level, COUNT(DISTINCT region_name) FROM `{t}` GROUP BY region_level")
            print(f'  [ok] {t}: 删 {n} 窄行，余层级 {cur.fetchall()}')

    print('== 4/5 治理库同步 ==')
    gov = connect(DB_GOV)
    with gov.cursor() as cur:
        if dry:
            print('  [dry] code_values.region_adcode 域 + schema docs + column_form + keyword')
        else:
            # 码值域：行政区划代码（全量）
            cur.execute('INSERT INTO code_values (code_name, code_cn_name, data_type, description, '
                        'domain_l1, domain_l2) VALUES (%s,%s,%s,%s,%s,%s) '
                        'ON DUPLICATE KEY UPDATE code_cn_name=VALUES(code_cn_name), description=VALUES(description)',
                        ('region_adcode', '行政区划代码', 'VARCHAR(6)',
                         'GB/T 2260 行政区划代码（6 位，省/市/区县三级）', 'Pub', None))
            cur.execute('DELETE FROM code_value_items WHERE code_name=%s', ('region_adcode',))
            cur.executemany('INSERT INTO code_value_items (code_name, item_code, item_name, sort_order) '
                            'VALUES (%s,%s,%s,%s)',
                            [('region_adcode', code, name, i + 1)
                             for i, (code, name, _l, _p) in enumerate(divs)])
            # urb_region_level 补 区县
            cur.execute('SELECT MAX(sort_order) FROM code_value_items WHERE code_name=%s', ('urb_region_level',))
            mx = cur.fetchone()[0] or 0
            cur.execute('SELECT COUNT(*) FROM code_value_items WHERE code_name=%s AND item_name=%s',
                        ('urb_region_level', '区县'))
            if not cur.fetchone()[0]:
                cur.execute('INSERT INTO code_value_items (code_name, item_code, item_name, sort_order) '
                            'VALUES (%s,%s,%s,%s)', ('urb_region_level', f'{mx + 1:02d}', '区县', mx + 1))
            # column_form：region_adcode=编码，region_name=名称
            for t in URB_TABLES + [BRIDGE]:
                for col, form in (('region_adcode', '编码'), ('region_name', '名称')):
                    cur.execute('INSERT INTO code_value_column_form (table_name, column_name, code_name, form, updated_at) '
                                'VALUES (%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE form=VALUES(form), updated_at=VALUES(updated_at)',
                                (t, col, 'region_adcode', form, now))
            # schema_column_docs：urb 5 表补 region_adcode 行
            for t in URB_TABLES:
                cur.execute('DELETE FROM schema_column_docs WHERE table_name=%s AND column_name=%s',
                            (t, 'region_adcode'))
                cur.execute('INSERT INTO schema_column_docs (table_name, column_name, column_comment, data_type, '
                            'is_pk, doc_text) VALUES (%s,%s,%s,%s,0,%s)',
                            (t, 'region_adcode', '行政区划代码（GB/T 2260）', 'VARCHAR(12)',
                             f'表 {t} 的字段 region_adcode，中文注释：行政区划代码（GB/T 2260），类型：VARCHAR(12)'))
            # schema_table_docs：行数/列数刷新（city 表纯化后）+ 桥表刷新
            for t in URB_TABLES + [BRIDGE]:
                with biz.cursor() as bcur:
                    bcur.execute(f'SELECT COUNT(*) FROM `{DB_BIZ}`.`{t}`')
                    rc = bcur.fetchone()[0]
                    bcur.execute('SELECT COUNT(*) FROM information_schema.columns '
                                 'WHERE table_schema=%s AND table_name=%s', (DB_BIZ, t))
                    cc = bcur.fetchone()[0]
                cur.execute('UPDATE schema_table_docs SET row_count=%s, column_count=%s, updated_at=%s '
                            'WHERE table_name=%s', (rc, cc, now, t))
            cur.execute('UPDATE schema_table_docs SET table_comment=%s, doc_text=%s WHERE table_name=%s',
                        ('电网管理单位 ↔ 行政区划 映射桥表（GB/T 2260 全量预载）',
                         f'表 {BRIDGE}，中文注释：电网管理单位 ↔ 行政区划 映射桥表。'
                         f'GB/T 2260 全量区划（省/城市/区县 {len(divs)} 行）预载为基准行，'
                         f'电网机构映射（{len(org_seeds)} 条，源自 dim_cst_mgt_org 全树 + 省级人工规则）按区划挂载；'
                         f'无电网映射的行 mgt_org_* 留空。贯通主键 = region_adcode（两侧均已回填）；'
                         f'region_name 为 GB/T 2260 标准名。', BRIDGE))
            for kw in ['行政区划代码', 'GB/T 2260', '区划代码']:
                cur.execute('INSERT INTO keyword_table_map (keyword, table_name, enabled, updated_at) '
                            'VALUES (%s,%s,1,%s) ON DUPLICATE KEY UPDATE enabled=1, updated_at=VALUES(updated_at)',
                            (kw, BRIDGE, now))
        gov.commit()

    print('== 5/5 本体库换版 ==')
    if dry:
        print('  [dry] rebuild')
    else:
        from core.ontology.service import OntologyService
        svc = OntologyService()
        result = svc.rebuild_proposal()
        ont = svc.approve(result['proposal_id'])
        print(f'  [ok] 本体提案 #{result["proposal_id"]} 已批准，版本 v{ont.version}')
        # 贯通实测：电力 → 桥（adcode）→ urb
        from core.sql_exec import safe_execute_sql
        r = safe_execute_sql("""
            SELECT m.region_adcode, m.region_name, u.indicator_name, u.meas_value, u.unit
            FROM dim_org_region_map m
            JOIN ads_urb_water_supply_city_yr u ON u.region_adcode = m.region_adcode
            WHERE m.mgt_org_code = '33401' AND u.indicator_code = 'ws_population'""")
        print('  adcode 贯通实测（杭州 33401 → 用水人口）:',
              r['rows'] if r['success'] else r['error'])
        r2 = safe_execute_sql("""
            SELECT COUNT(*) FROM ads_urb_water_supply_city_yr u
            JOIN dim_org_region_map m ON u.region_adcode = m.region_adcode
            WHERE m.mgt_org_code IS NOT NULL""")
        print('  urb city 表经 adcode 挂上电网机构的行数:', r2['rows'] if r2['success'] else r2['error'])

    biz.close()
    gov.close()
    print('[done] region_adcode 贯通改造完成')


def connect(db):
    return pymysql.connect(host=config.MYSQL_HOST, port=config.MYSQL_PORT,
                           user=config.MYSQL_USER, password=config.MYSQL_PASSWORD,
                           database=db, charset='utf8mb4')


if __name__ == '__main__':
    main()
