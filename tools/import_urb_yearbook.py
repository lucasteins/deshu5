# -*- coding: utf-8 -*-
"""2024年城市建设统计年鉴（试点 5 张表）→ database01 标准化窄表入库（skill: ads-table-standard 第七节）

转换规则：
- 多级表头宽表 → 窄表：指标转行（indicator_code/indicator_name/unit/meas_value）
- 地区维度：region_name + region_level（全国/省/城市），行政区划口径（与电力 mgt_org 不同域）
- stat_period：年度表 VARCHAR 'yyyy'（历年表取行内年份，2024 年表固定 '2024'）
- 治理库同步：business_domains(Urb) / schema_table_docs / schema_column_docs /
  code_values + code_value_items（urb_*_indicator、urb_region_level）/ code_value_column_form /
  keyword_table_map；本体库：ontology_entity_defs + 提案审批换版

用法：
  python tools/import_urb_yearbook.py --dry-run   # 只打印计划与样例数据
  python tools/import_urb_yearbook.py             # 执行（幂等：重跑先清本脚本产出）
"""
import argparse
import re
import sys
from datetime import datetime

sys.path.insert(0, r'D:\codex\deshu5')
import pymysql
import xlrd

import config

XLS_PATH = r'C:\Users\11051\Downloads\2024年城市建设统计年鉴.xls'
SRC_NAME = '2024年城市建设统计年鉴'

DB_BIZ = 'database01'
DB_GOV = 'database01_governance'
DB_ONT = 'database01_ontology'

# ==================== 指标定义（列号 → 指标编码/中文名/单位） ====================
# sheet 3 全国历年城市供水情况（8 列，c0=年份）
WS_NAT = {
    1: ('ws_capacity', '综合生产能力', '万立方米/日'),
    2: ('ws_pipeline_len', '供水管道长度', '公里'),
    3: ('ws_total_qty', '供水总量-合计', '万立方米'),
    4: ('ws_life_qty', '供水总量-生活用量', '万立方米'),
    5: ('ws_population', '用水人口', '万人'),
    6: ('ws_daily_per_capita', '人均日生活用水量', '升'),
    7: ('ws_coverage_rate', '供水普及率', '%'),
}
# sheet 3-1 / 3-4 城市供水（14 列，c0=地区，c13=重复名称列丢弃）
WS_REGION = {
    1: ('ws_capacity', '综合生产能力', '万立方米/日'),
    2: ('ws_capacity_groundwater', '综合生产能力-地下水', '万立方米/日'),
    3: ('ws_pipeline_len', '供水管道长度', '公里'),
    4: ('ws_pipeline_len_built', '供水管道长度-建成区', '公里'),
    5: ('ws_total_qty', '供水总量-合计', '万立方米'),
    6: ('ws_prod_qty', '生产运营用水量', '万立方米'),
    7: ('ws_public_qty', '公共服务用水量', '万立方米'),
    8: ('ws_residential_qty', '居民家庭用水量', '万立方米'),
    9: ('ws_other_qty', '其他用水量', '万立方米'),
    10: ('ws_households', '用水户数', '户'),
    11: ('ws_households_family', '其中-家庭用户数', '户'),
    12: ('ws_population', '用水人口', '万人'),
}
# sheet 9-1 / 9-2 排水和污水（19 列，c0=地区，c18=重复名称列丢弃）
SEWAGE = {
    1: ('sewage_discharge_qty', '污水排放量', '万立方米'),
    2: ('drain_pipeline_len', '排水管道长度-合计', '公里'),
    3: ('drain_pipeline_sewage', '排水管道长度-污水管道', '公里'),
    4: ('drain_pipeline_rain', '排水管道长度-雨水管道', '公里'),
    5: ('drain_pipeline_combined', '排水管道长度-雨污合流管道', '公里'),
    6: ('drain_pipeline_built', '排水管道长度-建成区', '公里'),
    7: ('wtp_count', '污水处理厂座数', '座'),
    8: ('wtp_capacity', '污水处理厂处理能力', '万立方米/日'),
    9: ('wtp_treat_qty', '污水处理厂处理量', '万立方米'),
    10: ('sludge_prod_qty', '干污泥产生量', '吨'),
    11: ('sludge_disp_qty', '干污泥处置量', '吨'),
    12: ('other_wtp_capacity', '其他污水处理装置处理能力', '万立方米/日'),
    13: ('other_wtp_qty', '其他污水处理装置处理量', '万立方米'),
    14: ('sewage_treat_total_qty', '污水处理总量', '万立方米'),
    15: ('recycled_capacity', '市政再生水生产能力', '万立方米/日'),
    16: ('recycled_use_qty', '市政再生水利用量', '万立方米'),
    17: ('recycled_pipeline_len', '市政再生水管道长度', '公里'),
}

# ==================== sheet → 表 规格 ====================
# level_mode: fixed=全部行同层级；by_name=按首列值判层级（全国行 + 省/城市行）
SHEETS = [
    dict(sheet='3', table='ads_urb_water_supply_nat_yr', cn='全国历年城市供水情况',
         indicators=WS_NAT, period_mode='row_year', level_mode='fixed', level='全国',
         first_data_row=4, domain_l2='Urb02', code_domain='urb_water_indicator',
         keywords=['供水', '用水', '自来水', '供水管道', '用水人口', '供水普及率', '历年供水']),
    dict(sheet='3-1', table='ads_urb_water_supply_prov_yr', cn='2024年按省分列的城市供水情况',
         indicators=WS_REGION, period_mode='fixed', period='2024', level_mode='by_name', level='省',
         first_data_row=4, domain_l2='Urb02', code_domain='urb_water_indicator',
         keywords=['供水', '用水', '自来水', '供水管道', '用水人口', '分省供水']),
    dict(sheet='3-4', table='ads_urb_water_supply_city_yr', cn='2024年按城市分列的城市供水情况',
         indicators=WS_REGION, period_mode='fixed', period='2024', level_mode='by_name', level='城市',
         first_data_row=4, domain_l2='Urb02', code_domain='urb_water_indicator',
         keywords=['供水', '用水', '自来水', '供水管道', '用水人口', '分城市供水']),
    dict(sheet='9-1', table='ads_urb_sewage_prov_yr', cn='2024年按省分列的城市排水和污水处理情况',
         indicators=SEWAGE, period_mode='fixed', period='2024', level_mode='by_name', level='省',
         first_data_row=5, domain_l2='Urb03', code_domain='urb_sewage_indicator',
         keywords=['污水', '排水', '污水处理', '污泥', '再生水', '分省污水']),
    dict(sheet='9-2', table='ads_urb_sewage_city_yr', cn='2024年按城市分列的城市排水和污水处理情况',
         indicators=SEWAGE, period_mode='fixed', period='2024', level_mode='by_name', level='城市',
         first_data_row=5, domain_l2='Urb03', code_domain='urb_sewage_indicator',
         keywords=['污水', '排水', '污水处理', '污泥', '再生水', '分城市污水']),
]

# 窄表列定义（注释供物理/治理双写）
NARROW_COLS = [
    ('id', 'BIGINT', '物理主键'),
    ('stat_period', 'VARCHAR(6)', '统计期间（年度，yyyy）'),
    ('region_name', 'VARCHAR(64)', '地区名称（全国/省/城市）'),
    ('region_level', 'VARCHAR(16)', '地区层级（全国/省/城市）'),
    ('indicator_code', 'VARCHAR(64)', '指标编码'),
    ('indicator_name', 'VARCHAR(255)', '指标名称'),
    ('unit', 'VARCHAR(32)', '计量单位'),
    ('meas_value', 'DECIMAL(20,4)', '指标值'),
    ('datasource_id', 'INT', '数据来源标识（外部年鉴为空，来源见表注释）'),
    ('etl_time', 'DATETIME', '数据写入时间'),
]

SKIP_NAME_PAT = re.compile(r'(名称|Year|Name|注|Note|说\s*明|续表)')

# 省级短名（城市分列表中的省小计行；直辖市北京/天津/上海/重庆按省级计）
PROV_NAMES = {'北京', '天津', '河北', '山西', '内蒙古', '辽宁', '吉林', '黑龙江', '上海', '江苏',
              '浙江', '安徽', '福建', '江西', '山东', '河南', '湖北', '湖南', '广东', '广西',
              '海南', '重庆', '四川', '贵州', '云南', '西藏', '陕西', '甘肃', '青海', '宁夏', '新疆', '新疆兵团'}


def to_number(v):
    """单元格 → float 或 None（空/横线/非数字皆 NULL）。"""
    if v is None or v == '':
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(',', '')
    if s in ('', '-', '—', '–', '…'):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_sheet(wb, spec):
    """返回 (rows, skipped)：rows=[(stat_period, region_name, region_level, indicator_code, indicator_name, unit, meas_value)]"""
    s = wb.sheet_by_name(spec['sheet'])
    rows, skipped = [], []
    for r in range(spec['first_data_row'], s.nrows):
        name = str(s.cell_value(r, 0)).strip()
        if not name or SKIP_NAME_PAT.search(name):
            skipped.append((r, name[:30]))
            continue
        if spec['period_mode'] == 'row_year':
            yv = to_number(s.cell_value(r, 0))
            if yv is None:
                skipped.append((r, name[:30]))
                continue
            period = str(int(yv))
            region, level = '全国', '全国'
        else:
            period = spec['period']
            region = name
            level = '全国' if name == '全国' else ('省' if name in PROV_NAMES else spec['level'])
        for c, (code, cn, unit) in spec['indicators'].items():
            v = to_number(s.cell_value(r, c))
            if v is None:
                continue  # 空值不落行（窄表稀疏原则）
            rows.append((period, region, level, code, cn, unit, v))
    return rows, skipped


def narrow_ddl(spec):
    lines = [f'  `{n}` {t} {"NOT NULL AUTO_INCREMENT" if n == "id" else "NULL"} COMMENT {q(c)}'
             for n, t, c in NARROW_COLS]
    lines.append('  PRIMARY KEY (`id`)')
    lines.append('  KEY `idx_region_period` (`region_name`, `stat_period`)')
    lines.append('  KEY `idx_indicator` (`indicator_code`, `stat_period`)')
    return ('CREATE TABLE `%s` (\n%s\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT=%s'
            % (spec['table'], ',\n'.join(lines),
               q(f"{spec['cn']}（窄表，指标转行；来源：{SRC_NAME} sheet {spec['sheet']}）")))


def q(s):
    return "'" + str(s).replace('\\', '\\\\').replace("'", "\\'") + "'"


def connect(db):
    return pymysql.connect(host=config.MYSQL_HOST, port=config.MYSQL_PORT,
                           user=config.MYSQL_USER, password=config.MYSQL_PASSWORD,
                           database=db, charset='utf8mb4')


# ==================== 治理库同步 ====================

def sync_governance(gov, parsed, dry_run):
    now = datetime.now()
    tables = [s['table'] for s in SHEETS]
    with gov.cursor() as cur:
        # 0) business_domains：Urb 域（一级）+ Urb02 供水 / Urb03 排水和污水（二级）
        #    （Urb01 预留给后续年鉴主题，本次不建）
        domains = [
            (1, 'Urb', '城市建设', None, 'URBAN CONSTRUCTION', f'{SRC_NAME}（外部接入）'),
            (2, 'Urb02', '供水', 'Urb', None, f'{SRC_NAME}（外部接入）'),
            (2, 'Urb03', '排水和污水', 'Urb', None, f'{SRC_NAME}（外部接入）'),
        ]
        for level, code, name, parent, en, src in domains:
            if dry_run:
                print(f'  [dry] business_domains {code} {name}')
                continue
            cur.execute('INSERT INTO business_domains (level, domain_code, domain_name, parent_code, name_en, source) '
                        'VALUES (%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE domain_name=VALUES(domain_name), '
                        'parent_code=VALUES(parent_code), source=VALUES(source)',
                        (level, code, name, parent, en, src))

        # 1) code_values + code_value_items：指标域 ×2 + 地区层级域
        domains_cv = [
            ('urb_water_indicator', '城市建设-供水指标', 'Urb', 'Urb02', WS_NAT | WS_REGION),
            ('urb_sewage_indicator', '城市建设-排水污水指标', 'Urb', 'Urb03', SEWAGE),
            ('urb_region_level', '地区层级', 'Urb', None,
             {i + 1: v for i, v in enumerate(['全国', '省', '城市'])}),
        ]
        for code_name, cn_name, l1, l2, items in domains_cv:
            if dry_run:
                print(f'  [dry] code_values {code_name}: {len(items)} 项')
                continue
            cur.execute('INSERT INTO code_values (code_name, code_cn_name, data_type, description, domain_l1, domain_l2) '
                        'VALUES (%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE code_cn_name=VALUES(code_cn_name), '
                        'description=VALUES(description), domain_l1=VALUES(domain_l1), domain_l2=VALUES(domain_l2)',
                        (code_name, cn_name, 'VARCHAR(64)', f'{cn_name}码值（{SRC_NAME}窄表指标/维度）', l1, l2))
            cur.execute('DELETE FROM code_value_items WHERE code_name=%s', (code_name,))  # 幂等：明细先清后插
            for i, (_k, v) in enumerate(items.items()):
                if code_name == 'urb_region_level':
                    item_code, item_name = f'{_k:02d}', v
                else:
                    item_code, item_name = _k, v[0] + (f'（{v[1]}）' if v[1] else '')
                cur.execute('INSERT INTO code_value_items (code_name, item_code, item_name, sort_order) '
                            'VALUES (%s,%s,%s,%s)', (code_name, item_code, item_name, i + 1))

        # 2) code_value_column_form：indicator_code 存编码 / indicator_name 存名称 / region_level 存名称
        for spec in SHEETS:
            forms = [('indicator_code', spec['code_domain'], '编码'),
                     ('indicator_name', spec['code_domain'], '名称'),
                     ('region_level', 'urb_region_level', '名称')]
            for col, cd, form in forms:
                if dry_run:
                    continue
                cur.execute('INSERT INTO code_value_column_form (table_name, column_name, code_name, form, updated_at) '
                            'VALUES (%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE form=VALUES(form), updated_at=VALUES(updated_at)',
                            (spec['table'], col, cd, form, now))
        if dry_run:
            print('  [dry] code_value_column_form 每表 3 行 × 5 表')

        # 3) schema_table_docs / schema_column_docs
        for spec in SHEETS:
            t, cn = spec['table'], spec['cn']
            rc = len(parsed[t])
            doc_text = (f'表 {t}，中文注释：{cn}，{rc} 行数据，{len(NARROW_COLS)} 个字段。'
                        f'窄表结构（指标转行：indicator_code/indicator_name/unit/meas_value），'
                        f'地区维度 region_name/region_level；来源：{SRC_NAME} sheet {spec["sheet"]}。')
            if dry_run:
                print(f'  [dry] schema_table_docs {t}: {rc} 行')
                continue
            cur.execute('INSERT INTO schema_table_docs (table_name, table_comment, row_count, column_count, doc_text, '
                        'domain_l1, domain_l2, updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) '
                        'ON DUPLICATE KEY UPDATE table_comment=VALUES(table_comment), row_count=VALUES(row_count), '
                        'column_count=VALUES(column_count), doc_text=VALUES(doc_text), domain_l1=VALUES(domain_l1), '
                        'domain_l2=VALUES(domain_l2), updated_at=VALUES(updated_at)',
                        (t, cn, rc, len(NARROW_COLS), doc_text, 'Urb', spec['domain_l2'], now))
            cur.execute('DELETE FROM schema_column_docs WHERE table_name=%s', (t,))
            for n, typ, cmt in NARROW_COLS:
                cur.execute('INSERT INTO schema_column_docs (table_name, column_name, column_comment, data_type, is_pk, doc_text) '
                            'VALUES (%s,%s,%s,%s,%s,%s)',
                            (t, n, cmt, typ, 1 if n == 'id' else 0,
                             f'表 {t} 的字段 {n}，中文注释：{cmt}，类型：{typ}'))

        # 4) keyword_table_map：主题词 → 表
        for spec in SHEETS:
            for kw in spec['keywords'] + ['城市建设', '统计年鉴']:
                if dry_run:
                    continue
                cur.execute('INSERT INTO keyword_table_map (keyword, table_name, enabled, updated_at) '
                            'VALUES (%s,%s,1,%s) ON DUPLICATE KEY UPDATE enabled=1, updated_at=VALUES(updated_at)',
                            (kw, spec['table'], now))
        if dry_run:
            print('  [dry] keyword_table_map 每表 主题词+城市建设+统计年鉴')
    if not dry_run:
        gov.commit()


# ==================== 本体库同步 ====================

def sync_ontology(dry_run):
    if dry_run:
        print('  [dry] ontology_entity_defs +5 并 rebuild')
        return
    ont = connect(DB_ONT)
    now = datetime.now()
    with ont.cursor() as cur:
        # 纪律（2026-09 起）：不建逐表实体，urb 5 表全部挂入 ReportUrban 域实体
        from tools.enrich_report_relations import attach_tables_to_domain
        attach_tables_to_domain(cur, 'ReportUrban', '城市建设类报表',
                                [s['table'] for s in SHEETS], f'{SRC_NAME}（外部接入）')
    ont.commit()
    ont.close()
    from core.ontology.service import OntologyService
    svc = OntologyService()
    result = svc.rebuild_proposal()
    ont_new = svc.approve(result['proposal_id'])
    print(f'  [ok] 本体提案 #{result["proposal_id"]} 已批准，版本 v{ont_new.version}')


# ==================== 主流程 ====================

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    wb = xlrd.open_workbook(XLS_PATH)
    print(f'== 1/4 解析 sheet（{"DRY-RUN" if args.dry_run else "执行"}）==')
    parsed, skipped_all = {}, {}
    for spec in SHEETS:
        rows, skipped = parse_sheet(wb, spec)
        parsed[spec['table']] = rows
        skipped_all[spec['table']] = skipped
        regions = {r[1] for r in rows}
        inds = {r[3] for r in rows}
        print(f'  [{spec["sheet"]}] -> {spec["table"]}: {len(rows)} 窄行（地区 {len(regions)}，指标 {len(inds)}，跳过 {len(skipped)} 行）')
        if args.dry_run:
            for r in rows[:3]:
                print('    样例:', r)
            if skipped[:3]:
                print('    跳过样例:', skipped[:3])

    print('== 2/4 物理建表 + 数据写入 ==')
    biz = connect(DB_BIZ)
    with biz.cursor() as cur:
        for spec in SHEETS:
            t = spec['table']
            if args.dry_run:
                print(f'  [dry] {narrow_ddl(spec)[:200]}...')
                continue
            cur.execute(f'DROP TABLE IF EXISTS `{t}`')
            cur.execute(narrow_ddl(spec))
            cur.executemany(
                f'INSERT INTO `{t}` (stat_period, region_name, region_level, indicator_code, indicator_name, '
                f'unit, meas_value, etl_time) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',
                [(p, rg, lv, ic, ikn, u, mv, datetime.now()) for (p, rg, lv, ic, ikn, u, mv) in parsed[t]])
            biz.commit()
            cur.execute(f'SELECT COUNT(*), SUM(meas_value IS NULL) FROM `{t}`')
            cnt, nulls = cur.fetchone()
            print(f'  [ok] {t}: {cnt} 行（NULL 值 {nulls}）')

    print('== 3/4 治理库同步 ==')
    sync_governance(connect(DB_GOV), parsed, args.dry_run)

    print('== 4/4 本体库同步 ==')
    sync_ontology(args.dry_run)

    biz.close()
    print('[done] 年鉴试点入库完成')


if __name__ == '__main__':
    main()
