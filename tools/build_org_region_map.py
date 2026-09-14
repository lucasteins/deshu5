# -*- coding: utf-8 -*-
"""电网管理单位 ↔ 行政区划 桥接维表 dim_org_region_map 构建（skill: ads-table-standard 第八节）

- 种子映射 14 行：值全部取自电力 ads 表实际出现的 (mgt_org_code, mgt_org_name)；
  中文名→行政区划名/GB/T 2260 码为人工规则（map_source 留痕，待业务确认）
- 损毁值 2147483647（int 溢出）不建映射
- 同步：治理库 schema_table_docs / schema_column_docs / keyword_table_map /
  code_value_column_form；本体库 entity_defs(master 层) + 提案审批换版

用法：python tools/build_org_region_map.py [--dry-run]
"""
import argparse
import re
import sys
from datetime import datetime

sys.path.insert(0, r'D:\codex\deshu5')
import pymysql

import config

DB_BIZ = 'fz01'
DB_GOV = 'fz01_governance'
DB_ONT = 'fz01_ontology'
TABLE = 'dim_org_region_map'

# (mgt_org_code, mgt_org_name, org_level, region_adcode, region_name, region_level, remark)
# 省级假定行（用户确认：国网浙江省公司单位范围=浙江省）；地市/区县行由 dim_cst_mgt_org 全树推导
PROVINCE_SEED = ('33101', '国网浙江省电力有限公司', '省', '330000', '浙江', '省',
                 '假定：省公司单位范围=浙江省')

# GB/T 2260 浙江地市码（城市名 → adcode）
CITY_ADCODE = {
    '杭州市': '330100', '宁波市': '330200', '温州市': '330300', '嘉兴市': '330400',
    '湖州市': '330500', '绍兴市': '330600', '金华市': '330700', '衢州市': '330800',
    '舟山市': '330900', '台州市': '331000', '丽水市': '331100',
}


def _parse_city_region(org_name):
    """'国网浙江省电力有限公司舟山供电公司' → '舟山市'；解析不出返回 None。"""
    m = re.search(r'有限公司(.+?)供电公司$', org_name or '')
    return (m.group(1) + '市') if m else None


def _parse_county_region(org_name):
    """区县供电主体名 → 行政区划名：
    - '国网浙江省电力有限公司XX县供电公司' / '国网浙江XX县供电有限公司' → 'XX县'（区/县级市同理）
    - 'XX供电分公司' → 'XX区'（市辖区分公司，启发式）
    客服中心/电力局/配售电公司等非区县供电主体返回 None（不建映射）。"""
    n = re.sub(r'^国网浙江省电力有限公司|^国网浙江省电力公司|^国网浙江', '', org_name or '')
    n = re.sub(r'^国网.{0,15}?市', '', n)  # 再剥'国网XX市'类市名前缀（国网宁波市海曙供电分公司→海曙供电分公司）
    m = re.match(r'^(.+?)供电(?:有限)?(?:责任)?公司$', n)
    if m:
        core = m.group(1)
        # '杭州市萧山区' → '萧山区'（剥嵌入的地市前缀）
        cm = re.match(r'^.+?市(.+[区县市])$', core)
        if cm:
            core = cm.group(1)
        return core if core.endswith(('县', '区', '市')) else None
    m = re.match(r'^(.+?)(?:供电)?分公司$', n)
    if m:
        core = m.group(1)
        core = re.sub(r'^.+?供电公司', '', core)  # 剥嵌入式公司前缀（温州供电公司鹿城→鹿城）
        # '金华婺城' → '婺城'（剥浙江地市短名前缀）
        for city in ('杭州', '宁波', '温州', '嘉兴', '湖州', '绍兴', '金华', '衢州', '舟山', '台州', '丽水'):
            if core.startswith(city) and len(core) > len(city):
                core = core[len(city):]
                break
        return core + '区'
    return None


def build_seeds(biz):
    """从 dim_cst_mgt_org 全树推导种子：地市精确（city_code）、区县名称解析（county_code）。"""
    seeds = [PROVINCE_SEED]
    with biz.cursor() as cur:
        cur.execute("SELECT DISTINCT city_code, city_name FROM dim_cst_mgt_org "
                    "WHERE dist_lv_desc='地市' AND city_code IS NOT NULL ORDER BY city_code")
        for code, name in cur.fetchall():
            region = _parse_city_region(name)
            if region:
                seeds.append((code, name, '地市', CITY_ADCODE.get(region), region, '城市', ''))
        cur.execute("SELECT DISTINCT county_code, county_name FROM dim_cst_mgt_org "
                    "WHERE county_code IS NOT NULL AND dist_lv_desc='区县' ORDER BY county_code")
        county_rows = cur.fetchall()
        # urb 侧设市城市名单（县级市在年鉴中按"城市"层级承载，region_level 需与 urb 口径对齐才能 JOIN）
        cur.execute('SELECT DISTINCT region_name FROM ads_urb_water_supply_city_yr')
        urb_cities = {r[0] for r in cur.fetchall()}
        for code, name in county_rows:
            region = _parse_county_region(name)
            if not region:
                continue
            if region in urb_cities:
                seeds.append((code, name, '区县', None, region, '城市',
                              '县级市：urb 年鉴按城市层级承载，region_level 对齐为城市'))
            else:
                seeds.append((code, name, '区县', None, region, '区县',
                              '区县粒度：urb 年鉴表无对应行（非设市城市），预留'))
    return seeds


SEEDS_SQL_ORDER = None  # 占位：种子改为 build_seeds() 动态生成

COLS = [
    ('id', 'BIGINT', '物理主键'),
    ('mgt_org_code', 'VARCHAR(32)', '电网管理单位编码（紧凑码，如 33401）'),
    ('mgt_org_name', 'VARCHAR(128)', '电网管理单位名称'),
    ('org_level', 'VARCHAR(16)', '电网层级（省/地市/区县/服务站）'),
    ('region_adcode', 'VARCHAR(12)', '行政区划代码（GB/T 2260）'),
    ('region_name', 'VARCHAR(64)', '行政区划名称（与 urb 表 region_name 对齐，如 杭州市）'),
    ('region_level', 'VARCHAR(16)', '地区层级（省/城市/区县）'),
    ('map_source', 'VARCHAR(32)', '映射来源（人工规则/dim_cst_mgt_org）'),
    ('remark', 'VARCHAR(255)', '口径备注'),
]

DDL = """CREATE TABLE IF NOT EXISTS `dim_org_region_map` (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '物理主键',
  `mgt_org_code` VARCHAR(32) NOT NULL COMMENT '电网管理单位编码（紧凑码，如 33401）',
  `mgt_org_name` VARCHAR(128) NOT NULL COMMENT '电网管理单位名称',
  `org_level` VARCHAR(16) NOT NULL COMMENT '电网层级（省/地市/区县/服务站）',
  `region_adcode` VARCHAR(12) NULL COMMENT '行政区划代码（GB/T 2260）',
  `region_name` VARCHAR(64) NOT NULL COMMENT '行政区划名称（与 urb 表 region_name 对齐，如 杭州市）',
  `region_level` VARCHAR(16) NOT NULL COMMENT '地区层级（省/城市/区县）',
  `map_source` VARCHAR(32) NOT NULL COMMENT '映射来源（人工规则/dim_cst_mgt_org）',
  `remark` VARCHAR(255) NULL COMMENT '口径备注',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_org` (`mgt_org_code`),
  KEY `idx_region` (`region_name`, `region_level`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='电网管理单位 ↔ 行政区划 映射桥表（跨域贯通唯一入口）'"""


def connect(db):
    return pymysql.connect(host=config.MYSQL_HOST, port=config.MYSQL_PORT,
                           user=config.MYSQL_USER, password=config.MYSQL_PASSWORD,
                           database=db, charset='utf8mb4')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    dry = args.dry_run
    now = datetime.now()

    biz = connect(DB_BIZ)
    seeds = build_seeds(biz)
    n_prov = sum(1 for s in seeds if s[2] == '省')
    n_city = sum(1 for s in seeds if s[2] == '地市')
    n_county = sum(1 for s in seeds if s[2] == '区县')
    print(f'== 1/4 建桥表 + 种子（{"DRY-RUN" if dry else "执行"}）==')
    print(f'  种子：省 {n_prov} + 地市 {n_city} + 区县 {n_county} = {len(seeds)} 行（dim 树推导）')
    with biz.cursor() as cur:
        if not dry:
            cur.execute(DDL)
            cur.execute(f'DELETE FROM `{TABLE}`')  # 幂等：种子全量重建
            cur.executemany(
                f'INSERT INTO `{TABLE}` (mgt_org_code, mgt_org_name, org_level, region_adcode, '
                f'region_name, region_level, map_source, remark) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',
                [(*s[:6], '人工规则' if s[2] == '省' else 'dim_cst_mgt_org', s[6]) for s in seeds])
            biz.commit()
        # 自检 1：种子 mgt_org_code 与电力 ads 表实际值对得上
        cur.execute('SELECT DISTINCT mgt_org_code FROM ads_prj_society_power_mon')
        real_codes = {str(r[0]) for r in cur.fetchall()}
        for t in ['ads_grid_carbon_idx_da', 'ads_grid_city_cap_da', 'ads_cst_exp_cust_mon',
                  'ads_prj_county_power_mon']:
            cur.execute(f'SELECT DISTINCT mgt_org_code FROM {t}')
            real_codes |= {str(r[0]) for r in cur.fetchall()}
        seed_codes = {s[0] for s in seeds}
        print(f'  电力表实际机构码 ⊆ 种子: {real_codes <= seed_codes | {"2147483647"} }'
              f'（实际 {len(real_codes)} 个，种子 {len(seed_codes)} 个；2147483647 为损毁值不映射）')
        # 自检 2：region_name 在 urb 表命中（省/城市应 100%；区县预留多数无对应行）
        cur.execute('SELECT DISTINCT region_name FROM ads_urb_water_supply_city_yr')
        urb_city = {r[0] for r in cur.fetchall()}
        cur.execute("SELECT DISTINCT region_name FROM ads_urb_water_supply_prov_yr WHERE region_level='省'")
        urb_prov = {r[0] for r in cur.fetchall()}
        hit_c = [s for s in seeds if s[5] == '城市' and s[4] in urb_city]
        hit_p = [s for s in seeds if s[5] == '省' and s[4] in urb_prov]
        hit_x = [s for s in seeds if s[5] == '区县' and s[4] in urb_city]
        print(f'  region 命中：省 {len(hit_p)}/{n_prov}，城市 {len(hit_c)}/{n_city}，'
              f'区县 {len(hit_x)}/{n_county}（区县级 urb 仅县级市有行，属预期）')

    print('== 2/4 治理库登记 ==')
    gov = connect(DB_GOV)
    with gov.cursor() as cur:
        if dry:
            print(f'  [dry] schema_table_docs/column_docs/keyword/column_form 登记 {TABLE}')
        else:
            doc_text = (f'表 {TABLE}，中文注释：电网管理单位 ↔ 行政区划 映射桥表，{len(seeds)} 行数据，'
                        f'{len(COLS)} 个字段。跨域贯通唯一入口：电力 ads 表经 mgt_org_code 接本表，'
                        f'urb ads 表经 (region_name, region_level) 接本表。'
                        f'种子自 dim_cst_mgt_org 全树（5531 行）推导：地市精确映射、区县名称解析、省级为人工规则；'
                        f'粒度边界：urb 侧仅设市城市，区县电力数据多数无 urb 对应行（预留）；'
                        f'损毁值 2147483647（int 溢出）不建映射。')
            cur.execute('INSERT INTO schema_table_docs (table_name, table_comment, row_count, column_count, '
                        'doc_text, domain_l1, domain_l2, updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) '
                        'ON DUPLICATE KEY UPDATE table_comment=VALUES(table_comment), row_count=VALUES(row_count), '
                        'column_count=VALUES(column_count), doc_text=VALUES(doc_text), updated_at=VALUES(updated_at)',
                        (TABLE, '电网管理单位 ↔ 行政区划 映射桥表', len(seeds), len(COLS),
                         doc_text, 'Cst', 'Cst01', now))
            cur.execute('DELETE FROM schema_column_docs WHERE table_name=%s', (TABLE,))
            for n, typ, cmt in COLS:
                cur.execute('INSERT INTO schema_column_docs (table_name, column_name, column_comment, '
                            'data_type, is_pk, doc_text) VALUES (%s,%s,%s,%s,%s,%s)',
                            (TABLE, n, cmt, typ, 1 if n == 'id' else 0,
                             f'表 {TABLE} 的字段 {n}，中文注释：{cmt}，类型：{typ}'))
            for kw in ['机构映射', '行政区划', '单位贯通', '地区对照', '供电公司', '地市公司']:
                cur.execute('INSERT INTO keyword_table_map (keyword, table_name, enabled, updated_at) '
                            'VALUES (%s,%s,1,%s) ON DUPLICATE KEY UPDATE enabled=1, updated_at=VALUES(updated_at)',
                            (kw, TABLE, now))
            cur.execute('INSERT INTO code_value_column_form (table_name, column_name, code_name, form, updated_at) '
                        'VALUES (%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE form=VALUES(form), updated_at=VALUES(updated_at)',
                        (TABLE, 'region_level', 'urb_region_level', '名称', now))
        gov.commit()

    print('== 3/4 本体库同步 ==')
    if dry:
        print('  [dry] entity_defs +1 并 rebuild')
    else:
        ont = connect(DB_ONT)
        import json as _json
        with ont.cursor() as cur:
            cur.execute('INSERT INTO ontology_entity_defs (name, label, layer, parent, member_tables, comment, '
                        'enabled, updated_at) VALUES (%s,%s,%s,%s,%s,%s,1,%s) '
                        'ON DUPLICATE KEY UPDATE label=VALUES(label), member_tables=VALUES(member_tables), '
                        'enabled=1, updated_at=VALUES(updated_at)',
                        (TABLE, '电网单位-行政区划映射', 'master', '',
                         _json.dumps([TABLE], ensure_ascii=False), '跨域贯通桥表（dim 全树推导种子）', now))
        ont.commit()
        ont.close()
        from core.ontology.service import OntologyService
        svc = OntologyService()
        result = svc.rebuild_proposal()
        ont_new = svc.approve(result['proposal_id'])
        print(f'  [ok] 本体提案 #{result["proposal_id"]} 已批准，版本 v{ont_new.version}')

    print('== 4/4 贯通实测 ==')
    if not dry:
        from core.sql_exec import safe_execute_sql
        r = safe_execute_sql("""
            SELECT m.region_name, COUNT(DISTINCT p.trade_code) AS trade_cnt, SUM(p.mon_energy) AS total_energy
            FROM ads_prj_society_power_mon p
            JOIN dim_org_region_map m ON p.mgt_org_code = m.mgt_org_code
            WHERE p.stat_period = '202605'
            GROUP BY m.region_name ORDER BY total_energy DESC""")
        print(f'  电力×桥表: success={r["success"]} rows={r.get("row_count")}')
        for row in (r.get('rows') or [])[:3]:
            print('   ', row)
        r2 = safe_execute_sql("""
            SELECT m.mgt_org_name, u.region_name, u.meas_value
            FROM dim_org_region_map m
            JOIN ads_urb_water_supply_city_yr u
              ON u.region_name = m.region_name AND u.region_level = m.region_level
            WHERE u.stat_period='2024' AND u.indicator_code='ws_population'
            ORDER BY u.meas_value DESC""")
        print(f'  桥表×年鉴: success={r2["success"]} rows={r2.get("row_count")}')
        for row in (r2.get('rows') or [])[:3]:
            print('   ', row)
    biz.close()
    print('[done] 桥接维表构建完成')


if __name__ == '__main__':
    main()
