# -*- coding: utf-8 -*-
"""窄表元数据富化：让检索层能找到指标转行的窄表（skill: ads-table-standard 第七节）

问题背景（2026-09-04 排查）：窄表的指标名/报表名在数据行（dim_name/report_name 列值）里，
表级元数据（table_comment/doc_text）只写主题名——关键词检索（schema_kb.retrieve_table_docs
按 doc_text 子串命中）永远打不中（如"用电量"在 ads_tjj_energy_mon 的元数据里零出现）。

动作（对 19 张窄表：5 urb + 14 tjj）：
1. schema_table_docs.doc_text 追加：报表清单（distinct report_name）+ 高频科目词 + 地区覆盖
2. keyword_table_map 补充指标级关键词 → 表（报表名全量 + 高频科目词，每表上限 25 条）

幂等可重跑；--dry-run 只打印。
"""
import argparse
import re
import sys
from datetime import datetime

sys.path.insert(0, r'D:\codex\deshu5')
import pymysql

import config

DB_BIZ = 'database01'
DB_GOV = 'database01_governance'

TJJ = ['ads_tjj_gdp_mon', 'ads_tjj_industry_mon', 'ads_tjj_transport_mon', 'ads_tjj_price_mon',
       'ads_tjj_main_mon', 'ads_tjj_energy_mon', 'ads_tjj_retail_mon', 'ads_tjj_foreign_mon',
       'ads_tjj_service_mon', 'ads_tjj_finance_mon', 'ads_tjj_employment_mon', 'ads_tjj_income_mon',
       'ads_tjj_confidence_mon', 'ads_tjj_invest_mon']
URB = ['ads_urb_water_supply_nat_yr', 'ads_urb_water_supply_prov_yr', 'ads_urb_water_supply_city_yr',
       'ads_urb_sewage_prov_yr', 'ads_urb_sewage_city_yr']

STOP_DIM = {'全省', '合计', '总计', '其中', '小计'}

ZJ_CITIES = ['杭州市', '宁波市', '温州市', '嘉兴市', '湖州市', '绍兴市',
             '金华市', '衢州市', '舟山市', '台州市', '丽水市']

# tjj 窄表共享值域（通道二注入：定位表后把这些合法值带进提示词，防 LLM 瞎猜条件值）
TJJ_VALUE_DOMAINS = {  # 域名 → (中文名, 列名)
    'tjj_report_name': ('统计月报报表名', 'report_name'),
    'tjj_indicator_name': ('统计月报指标列名', 'indicator_name'),
    'tjj_dim_name': ('统计月报科目名', 'dim_name'),
}

INDICATOR_COL_COMMENT = '指标列名（如 5月、同比±%、1-5月-同比±%；合法值以码值域 tjj_indicator_name 为准）'


def connect(db):
    return pymysql.connect(host=config.MYSQL_HOST, port=config.MYSQL_PORT,
                           user=config.MYSQL_USER, password=config.MYSQL_PASSWORD,
                           database=db, charset='utf8mb4')


def q(s):
    return "'" + str(s).replace('\\', '\\\\').replace("'", "\\'") + "'"


def table_inventory(biz, table):
    """从数据行提取检索素材：报表清单 / 科目高频词 / 指标词 / 地区覆盖。"""
    with biz.cursor() as cur:
        cur.execute(f'SELECT COUNT(*) FROM information_schema.columns WHERE table_schema=%s '
                    f'AND table_name=%s AND column_name=%s', (DB_BIZ, table, 'report_name'))
        has_report = cur.fetchone()[0] > 0
        if has_report:
            cur.execute(f'SELECT DISTINCT report_name FROM `{table}`')
            reports = [r[0] for r in cur.fetchall() if r[0]]
            cur.execute(f'SELECT dim_name, COUNT(*) FROM `{table}` WHERE dim_name IS NOT NULL '
                        f'GROUP BY dim_name ORDER BY COUNT(*) DESC LIMIT 40')
            dims = [r[0] for r in cur.fetchall()]
        else:
            cur.execute(f'SELECT DISTINCT indicator_name FROM `{table}`')
            reports = []
            dims = [r[0] for r in cur.fetchall() if r[0]]
        cur.execute(f'SELECT DISTINCT indicator_name FROM `{table}`')
        indicators = [r[0] for r in cur.fetchall() if r[0]]
        cur.execute(f'SELECT DISTINCT region_name FROM `{table}` WHERE region_name IS NOT NULL')
        regions = [r[0] for r in cur.fetchall() if r[0]]
    return reports, dims, indicators, regions


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    biz = connect(DB_BIZ)
    gov = connect(DB_GOV)
    now = datetime.now()

    with gov.cursor() as gcur, biz.cursor() as bcur:
        for table in TJJ + URB:
            reports, dims, indicators, regions = table_inventory(biz, table)
            # 关键词清单：报表名 + 高频科目词（去停用/短词）+ 指标词（去重控制 25 条）
            kws = []
            for w in reports:
                w = re.sub(r'[（(].*?[)）]', '', w).strip()  # 去单位括注
                if w and w not in kws:
                    kws.append(w)
            for w in dims:
                if w and w not in STOP_DIM and len(w) >= 2 and w not in kws:
                    kws.append(w)
            kws = kws[:25]

            # doc_text 富化
            bcur.execute('SELECT doc_text FROM %s.schema_table_docs WHERE table_name=%%s' % DB_GOV, (table,))
            row = bcur.fetchone()
            base_doc = (row[0] if row else '') or ''
            base_doc = base_doc.split('【检索清单】')[0].strip()  # 幂等：去旧富化段
            # 地区覆盖：城市级表须点名浙江 11 地市（防'杭州市'类关键词撞不到首屏截断）
            zj_cities = [r for r in regions if r in ZJ_CITIES]
            if len(regions) > 14:
                region_txt = (f'全国 {len(regions)} 设市城市（含浙江省 11 地市：{"、".join(zj_cities) or "—"}）'
                              if zj_cities else f'全国 {len(regions)} 设市城市')
            else:
                region_txt = '、'.join(regions)
            enrich = (f'【检索清单】报表：{"、".join(reports[:20])}；'
                      f'科目词：{"、".join(dims[:20])}；地区覆盖：{region_txt}。')
            new_doc = base_doc + enrich
            if args.dry_run:
                print(f'[dry] {table}: 关键词 {len(kws)} 条，doc_text +{len(enrich)} 字符')
                print('      关键词样例:', kws[:8])
                continue
            gcur.execute('UPDATE schema_table_docs SET doc_text=%s, updated_at=%s WHERE table_name=%s',
                         (new_doc, now, table))
            for kw in kws:
                gcur.execute('INSERT INTO keyword_table_map (keyword, table_name, enabled, updated_at) '
                             'VALUES (%s,%s,1,%s) ON DUPLICATE KEY UPDATE enabled=1, updated_at=VALUES(updated_at)',
                             (kw, table, now))
            print(f'[ok] {table}: 关键词 {len(kws)} 条')
    if not args.dry_run:
        gov.commit()
        # ============ tjj 共享值域注册（RAG 通道二注入） ============
        print('== tjj 共享值域（report_name/indicator_name/dim_name）==')
        with gov.cursor() as gcur, biz.cursor() as bcur:
            for code_name, (cn_name, col) in TJJ_VALUE_DOMAINS.items():
                vals = set()
                for t in TJJ:
                    bcur.execute(f'SELECT DISTINCT `{col}` FROM `{t}` WHERE `{col}` IS NOT NULL')
                    vals |= {r[0] for r in bcur.fetchall() if r[0]}
                vals = sorted(vals)
                gcur.execute('INSERT INTO code_values (code_name, code_cn_name, data_type, description, '
                             'domain_l1, domain_l2) VALUES (%s,%s,%s,%s,%s,%s) '
                             'ON DUPLICATE KEY UPDATE code_cn_name=VALUES(code_cn_name), '
                             'description=VALUES(description)',
                             (code_name, cn_name, 'VARCHAR(255)',
                              f'{cn_name}（tjj 窄表共享值域，数据行实际值全集）', 'Tjj', None))
                gcur.execute('DELETE FROM code_value_items WHERE code_name=%s', (code_name,))
                gcur.executemany('INSERT INTO code_value_items (code_name, item_code, item_name, sort_order) '
                                 'VALUES (%s,%s,%s,%s)',
                                 [(code_name, f'{i + 1:03d}', v, i + 1) for i, v in enumerate(vals)])
                for t in TJJ:
                    gcur.execute('INSERT INTO code_value_column_form (table_name, column_name, code_name, '
                                 'form, updated_at) VALUES (%s,%s,%s,%s,%s) '
                                 'ON DUPLICATE KEY UPDATE form=VALUES(form), updated_at=VALUES(updated_at)',
                                 (t, col, code_name, '名称', now))
                print(f'  [ok] {code_name}: {len(vals)} 值')
            # indicator_name 列注释修正（旧注释示例值"当月值"不在数据里，LLM 被误导）
            for t in TJJ:
                bcur.execute(f"ALTER TABLE `{t}` MODIFY COLUMN `indicator_name` VARCHAR(255) NULL "
                             f"COMMENT %s", (INDICATOR_COL_COMMENT,))
                gcur.execute('UPDATE schema_column_docs SET column_comment=%s, doc_text=%s, data_type=%s '
                             'WHERE table_name=%s AND column_name=%s',
                             (INDICATOR_COL_COMMENT,
                              f'表 {t} 的字段 indicator_name，中文注释：{INDICATOR_COL_COMMENT}，类型：VARCHAR(255)',
                              'VARCHAR(255)', t, 'indicator_name'))
            biz.commit()
            gov.commit()
            print('  [ok] indicator_name 列注释修正（物理+治理）× 14 表')
    biz.close()
    gov.close()
    print('[done] 窄表元数据富化完成')


if __name__ == '__main__':
    main()
