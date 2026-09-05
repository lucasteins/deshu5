# -*- coding: utf-8 -*-
"""统计报表跨域关系 + 报表实体域设计补充（skill: ads-table-standard 第八节）

1. schema_relationship_docs 补充跨域 JOIN 路径（进 SchemaPreloader 关系图 → 本体关系 + NL2SQL 召回）：
   - ads_tjj_*/ads_urb_*  → dim_org_region_map（region_adcode 对接）
   - dim_org_region_map → dim_cst_mgt_org（mgt_org_code 对接）
2. 报表实体域：按业务域建 5 个报表实体类（layer=report），存量逐表 report 实体挂 parent

用法：python tools/enrich_report_relations.py [--dry-run]
"""
import argparse
import json
import sys
from datetime import datetime

sys.path.insert(0, r'D:\codex\deshu5')
import pymysql

import config

DB_GOV = 'database01_governance'
DB_ONT = 'database01_ontology'
BRIDGE = 'dim_org_region_map'
DIM_ORG = 'dim_cst_mgt_org'

# 报表实体域：域实体 → (中文标签, 成员逐表实体前缀/表名)
REPORT_DOMAINS = [
    ('ReportMarketing', '营销类报表', ['ads_cst_exp_cust_mon', 'ads_cst_exp_app_cap_da',
                                     'ads_cst_bill_fee_mon', 'ads_cst_rcvbl_acct_mon']),
    ('ReportGrid', '电网类报表', ['ads_grid_carbon_idx_da', 'ads_grid_prevday_power_da',
                                'ads_grid_city_cap_da', 'ads_grid_pv_cap_da', 'ads_grid_type_cap_da',
                                'ads_grid_load_point_mi', 'ads_grid_load_curve_mi', 'ads_grid_day_power_da']),
    ('ReportProject', '专题类报表', ['ads_prj_plant_summary_mon', 'ads_prj_society_power_mon',
                                   'ads_prj_county_power_mon', 'ads_prj_trade_power_mon']),
    ('ReportUrban', '城市建设类报表', ['ads_urb_water_supply_nat_yr', 'ads_urb_water_supply_prov_yr',
                                     'ads_urb_water_supply_city_yr', 'ads_urb_sewage_prov_yr',
                                     'ads_urb_sewage_city_yr']),
    ('ReportStats', '统计类报表', ['ads_tjj_gdp_mon', 'ads_tjj_industry_mon', 'ads_tjj_transport_mon',
                                 'ads_tjj_price_mon', 'ads_tjj_main_mon', 'ads_tjj_energy_mon',
                                 'ads_tjj_retail_mon', 'ads_tjj_foreign_mon', 'ads_tjj_service_mon',
                                 'ads_tjj_finance_mon', 'ads_tjj_employment_mon', 'ads_tjj_income_mon',
                                 'ads_tjj_confidence_mon', 'ads_tjj_invest_mon']),
]

URB_TJJ = REPORT_DOMAINS[3][2] + REPORT_DOMAINS[4][2]


def connect(db):
    return pymysql.connect(host=config.MYSQL_HOST, port=config.MYSQL_PORT,
                           user=config.MYSQL_USER, password=config.MYSQL_PASSWORD,
                           database=db, charset='utf8mb4')


def attach_tables_to_domain(cur, domain_name, domain_label, tables, comment=''):
    """报表表挂入报表实体域的 member_tables（不建逐表实体；域不存在则创建）。幂等。

    纪律（2026-09 起）：新报表表一律只挂域实体，不再为单表建 report 实体。
    """
    tables = sorted(set(tables))
    cur.execute('SELECT member_tables FROM ontology_entity_defs WHERE name=%s', (domain_name,))
    row = cur.fetchone()
    if row:
        members = set(json.loads(row[0] or '[]')) | set(tables)
        cur.execute('UPDATE ontology_entity_defs SET member_tables=%s, enabled=1, updated_at=%s '
                    'WHERE name=%s',
                    (json.dumps(sorted(members), ensure_ascii=False), datetime.now(), domain_name))
    else:
        cur.execute('INSERT INTO ontology_entity_defs (name, label, layer, parent, member_tables, '
                    'comment, enabled, updated_at) VALUES (%s,%s,%s,%s,%s,%s,1,%s)',
                    (domain_name, domain_label, 'report', '',
                     json.dumps(tables, ensure_ascii=False), comment, datetime.now()))


def upsert_relationship(cur, left, right, join, scenario, now_note):
    """关系文档 upsert（按 title 判重，遵循既有格式）。"""
    title = f'{left} → {right}'
    doc_text = (f'表关联路径：{title}\nJOIN 条件：\n  - {join}\n来源：{now_note}')
    cur.execute('SELECT id FROM schema_relationship_docs WHERE title=%s', (title,))
    row = cur.fetchone()
    if row:
        cur.execute('UPDATE schema_relationship_docs SET path=%s, join_conditions=%s, '
                    'business_scenarios=%s, doc_text=%s WHERE id=%s',
                    (json.dumps([left, right], ensure_ascii=False),
                     json.dumps([join], ensure_ascii=False),
                     json.dumps([scenario], ensure_ascii=False), doc_text, row[0]))
        return 'upd'
    cur.execute('INSERT INTO schema_relationship_docs (title, path, join_conditions, '
                'business_scenarios, doc_text) VALUES (%s,%s,%s,%s,%s)',
                (title, json.dumps([left, right], ensure_ascii=False),
                 json.dumps([join], ensure_ascii=False),
                 json.dumps([scenario], ensure_ascii=False), doc_text))
    return 'ins'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    dry = args.dry_run

    print('== 1/3 关系文档（schema_relationship_docs）==')
    gov = connect(DB_GOV)
    with gov.cursor() as cur:
        n = {'ins': 0, 'upd': 0}
        # tjj/urb → 桥表
        for t in URB_TJJ:
            r = upsert_relationship(cur, t, BRIDGE,
                                    f'{t}.region_adcode = {BRIDGE}.region_adcode',
                                    '人工补充：跨域贯通', '人工补充（跨域贯通）')
            n[r] += 1
        # 桥表 → 供电单位维表
        r = upsert_relationship(cur, BRIDGE, DIM_ORG,
                                f'{BRIDGE}.mgt_org_code = {DIM_ORG}.mgt_org_code',
                                '人工补充：跨域贯通', '人工补充（跨域贯通）')
        n[r] += 1
        gov.commit()
        print(f'  [ok] 关系文档：新增 {n["ins"]}，更新 {n["upd"]}（tjj/urb→桥 {len(URB_TJJ)} 条 + 桥→dim 1 条）')

    print('== 2/3 报表实体域（ontology_entity_defs）==')
    ont = connect(DB_ONT)
    now = datetime.now()
    with ont.cursor() as cur:
        for name, label, tables in REPORT_DOMAINS:
            if dry:
                print(f'  [dry] {name} {label}: {len(tables)} 表')
                continue
            cur.execute('INSERT INTO ontology_entity_defs (name, label, layer, parent, member_tables, '
                        'comment, enabled, updated_at) VALUES (%s,%s,%s,%s,%s,%s,1,%s) '
                        'ON DUPLICATE KEY UPDATE label=VALUES(label), member_tables=VALUES(member_tables), '
                        'enabled=1, updated_at=VALUES(updated_at)',
                        (name, label, 'report', '', json.dumps(tables, ensure_ascii=False),
                         f'报表实体域（{label}），按业务域聚合统计报表', now))
            # 逐表实体挂父级域
            cur.execute('UPDATE ontology_entity_defs SET parent=%s, updated_at=%s WHERE name IN (%s)'
                        % ('%s', '%s', ','.join(['%s'] * len(tables))),
                        (name, now, *tables))
        if not dry:
            ont.commit()
            for name, label, tables in REPORT_DOMAINS:
                cur.execute('SELECT COUNT(*) FROM ontology_entity_defs WHERE parent=%s', (name,))
                print(f'  [ok] {name}（{label}）: 成员表 {len(tables)}，子实体挂接 {cur.fetchone()[0]}')

    print('== 3/3 本体换版 ==')
    if dry:
        print('  [dry] rebuild')
    else:
        ont.close()
        from core.ontology.service import OntologyService
        svc = OntologyService()
        result = svc.rebuild_proposal()
        ont_new = svc.approve(result['proposal_id'])
        print(f'  [ok] 本体提案 #{result["proposal_id"]} 已批准，版本 v{ont_new.version}')
        print(f'       实体 {len(ont_new.entities)}，关系 {len(ont_new.relations)}')

    gov.close()
    if not dry:
        pass
    print('[done] 关系与实体域补充完成')


if __name__ == '__main__':
    main()
