# -*- coding: utf-8 -*-
"""浙江省统计局「月度卡片」抓取 → database01 主题窄表入库（skill: ads-table-standard 第七节）

数据源：https://mapi.zjzwfw.gov.cn/web/mgop/gov-open/zj/2001941911/reserved/index.html#/monthlyCard
链路：mgop h5 网关（免 token，sign=md5("token=&ak=..&api=..&ts=..&data=null")），
     经 Playwright 驱动本机 Chrome 发起（源站 WAF 拦截非浏览器 UA，故不走裸 curl）。
流程：主题×报告期 → queryMCdDetailByZt 一次拿全主题表（HTML）→ 解析 → 窄表入库。
入库：增量模式（2026-09 起）——CREATE TABLE IF NOT EXISTS + 仅按本次报告期 DELETE 重插，
     其他期间存量数据不动（早期为 DROP 重建，会清掉已有期间，已废弃）。

主题 → ADS 表（14 张）：
  2 GDP→gdp / 3 工业→industry / 4 交通邮电→transport / 8 价格→price / 11 主要指标→main /
  12 能源→energy / 13+21 社零(旅游)→retail / 14 外经→foreign / 15 服务业→service /
  16 财政金融保险→finance / 17 就业创新创业→employment / 18 居民收支→income /
  19 信心指数→confidence / 23 投资→invest

窄表列（混合维度型）：id, stat_period, report_name, dim_type, dim_name,
  region_adcode, region_level, indicator_name, unit, meas_value, datasource_id, etl_time

用法：
  python tools/crawl_tjj_monthly.py --dry-run          # 只抓 + 打印解析结果
  python tools/crawl_tjj_monthly.py                    # 抓取 + 入库 + 三库同步
  python tools/crawl_tjj_monthly.py --bgq 20260005     # 只跑 5 月
  python tools/crawl_tjj_monthly.py --from 202501 --to 202604   # 跑 2025-01 ~ 2026-04 区间
"""
import argparse
import hashlib
import html as html_mod
import json
import re
import sys
import time
import urllib.parse
from datetime import datetime

sys.path.insert(0, r'D:\codex\deshu5')
sys.path.insert(0, r'D:\codex\deshu5\.vendor\playwright')
import pymysql

import config
from tools.extend_region_adcode import load_gb, build_gb_index, find_gb

AK = 'udqmn52d+2001941911+elgttz'
GW = 'https://mapi.zjzwfw.gov.cn/h5/mgop'
PAGE = 'https://mapi.zjzwfw.gov.cn/web/mgop/gov-open/zj/2001941911/reserved/index.html#/monthlyCard'
SRC_NAME = '浙江省统计局月度卡片'

DB_BIZ = 'database01'
DB_GOV = 'database01_governance'

THEMES = [  # (ztCode, 主题名, ads 表名, 主题中文全称)
    ('2', 'GDP', 'ads_tjj_gdp_mon', '月度-GDP'),
    ('3', '工业', 'ads_tjj_industry_mon', '月度-工业'),
    ('4', '交通邮电', 'ads_tjj_transport_mon', '月度-交通邮电'),
    ('8', '价格', 'ads_tjj_price_mon', '月度-价格'),
    ('11', '主要指标', 'ads_tjj_main_mon', '月度-国民经济主要指标'),
    ('12', '能源', 'ads_tjj_energy_mon', '月度-能源'),
    ('13', '社零旅游', 'ads_tjj_retail_mon', '月度-社会消费品零售'),
    ('21', '社零', 'ads_tjj_retail_mon', '月度-社会消费品零售'),
    ('14', '外经', 'ads_tjj_foreign_mon', '月度-对外经济'),
    ('15', '服务业', 'ads_tjj_service_mon', '月度-服务业'),
    ('16', '财政金融保险', 'ads_tjj_finance_mon', '月度-财政金融保险'),
    ('17', '就业、创新、创业', 'ads_tjj_employment_mon', '月度-就业创新创业'),
    ('18', '居民收支', 'ads_tjj_income_mon', '月度-居民收支'),
    ('19', '信心指数', 'ads_tjj_confidence_mon', '月度-信心指数'),
    ('23', '投资', 'ads_tjj_invest_mon', '月度-投资'),
]

NARROW_COLS = [
    ('id', 'BIGINT', '物理主键'),
    ('stat_period', 'VARCHAR(6)', '统计期间（月度，yyyymm）'),
    ('report_name', 'VARCHAR(255)', '报表名称（如 全社会用电量）'),
    ('region_name', 'VARCHAR(64)', '地区名称（GB/T 2260 标准名；科目行默认全省=浙江省）'),
    ('region_adcode', 'VARCHAR(12)', '行政区划代码（GB/T 2260；科目行默认 330000）'),
    ('region_level', 'VARCHAR(16)', '地区层级（省/城市/区县；科目行默认省）'),
    ('dim_name', 'VARCHAR(255)', '科目维度名称（行业/品种/科目；地区行为空）'),
    ('indicator_name', 'VARCHAR(255)', '指标列名（当月值/同比±%/累计值等）'),
    ('unit', 'VARCHAR(32)', '计量单位'),
    ('meas_value', 'DECIMAL(20,4)', '指标值'),
    ('datasource_id', 'INT', '数据来源标识（统计局月度卡片为空，见表注释）'),
    ('etl_time', 'DATETIME', '数据写入时间'),
]

ENTITY_FIX = {'&plusmn;': '±', '&nbsp;': ' ', '&m2;': '㎡'}


def clean_text(s):
    s = html_mod.unescape(s or '')
    for k, v in ENTITY_FIX.items():
        s = s.replace(k, v)
    return re.sub(r'\s+', '', s.replace(' ', ' ')).strip()


def to_number(v):
    s = clean_text(v).replace(',', '')
    if s in ('', '-', '—', '–', '…', '…　'):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def bgq_to_period(bgq):
    """'20260005' → '202605'"""
    return bgq[:4] + bgq[-2:]


def parse_report_html(content):
    """报表 HTML → (headers, rows)。headers=数据列头（跳过首列行标签列），
    rows=[(行标签, 缩进层级, [单元格原文...])]。"""
    m = re.search(r'<table.*?</table>', content, re.S)
    if not m:
        return [], []
    trs = re.findall(r'<tr[^>]*>(.*?)</tr>', m.group(0), re.S)
    grid = []
    for tr in trs:
        cells = re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', tr, re.S)
        grid.append([c for c in cells])
    # 找表头行（含最多非空文本的第一行）；首列是行标签列（标题）
    header_idx = None
    for i, row in enumerate(grid):
        texts = [clean_text(c) for c in row]
        if sum(1 for t in texts if t) >= 2:
            header_idx = i
            break
    if header_idx is None:
        return [], []
    headers = [clean_text(c) for c in grid[header_idx]][1:]  # 跳过行标签列
    # 重名列头消歧：同名列加前驱列限定（如两个'同比±%' → '6月-同比±%'/'1-6月-同比±%'）
    dup = {h for h in headers if h and headers.count(h) > 1}
    if dup:
        headers = [f'{headers[j - 1]}-{h}' if (h in dup and j > 0) else h
                   for j, h in enumerate(headers)]
    rows = []
    for row in grid[header_idx + 1:]:
        raw_label = row[0] if row else ''
        label = clean_text(raw_label)
        if not label or label.startswith('注') or label.startswith('说明'):
            continue
        indent = len(re.findall(r'&nbsp;| ', raw_label))  # 缩进层级（其中： 子项）
        rows.append((label, indent, row[1:]))
    return headers, rows


def narrow_rows(theme_cn, report_name, period, headers, rows, gb_idx):
    """一张表 → 窄表行 [(period, report, region_name, adcode, level, dim_name, ind, unit, val)]

    两轴模型：地区轴 region_*（整库为浙江省月度数据，科目行默认 330000/省）+
    科目轴 dim_name（行业/品种/科目；地区行为空）。
    """
    out = []
    has_unit_col = any(h == '计量单位' for h in headers)
    for label, indent, cells in rows:
        # 行单位（计量单位列）
        unit = ''
        data_cells = cells
        if has_unit_col:
            uidx = headers.index('计量单位')
            if uidx < len(cells):
                unit = clean_text(cells[uidx])
                data_cells = [c for j, c in enumerate(cells) if j != uidx]
        # 地区判定：命中 GB → 地区行；未命中 → 科目行（默认全省口径）
        hit = find_gb(gb_idx, label)
        if hit:
            region_name, adcode, level = hit[1], hit[0], hit[2]
            dim_name = None
        else:
            region_name, adcode, level = '浙江省', '330000', '省'
            dim_name = label
        for h, c in zip([x for x in headers if x != '计量单位'], data_cells):
            v = to_number(c)
            if v is None:
                continue
            out.append((period, report_name, region_name, adcode, level, dim_name,
                        h or '值', unit, v))
    return out


def narrow_ddl(table, cn):
    lines = [f'  `{n}` {t} {"NOT NULL AUTO_INCREMENT" if n == "id" else "NULL"} COMMENT {q(c)}'
             for n, t, c in NARROW_COLS]
    lines.append('  PRIMARY KEY (`id`)')
    lines.append('  KEY `idx_region_period` (`region_adcode`, `stat_period`)')
    lines.append('  KEY `idx_report` (`report_name`, `stat_period`)')
    lines.append('  KEY `idx_period` (`stat_period`)')
    return ('CREATE TABLE `%s` (\n%s\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT=%s'
            % (table, ',\n'.join(lines),
               q(f'{cn}（主题窄表，行列转换；来源：{SRC_NAME}，{PAGE}）')))


def q(s):
    return "'" + str(s).replace('\\', '\\\\').replace("'", "\\'") + "'"


# ==================== 网关抓取（经浏览器上下文，绕源站 WAF） ====================

class MgopBrowser:
    def __init__(self):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self.browser = self._pw.chromium.launch(channel='chrome', headless=True)
        self.page = self.browser.new_page()
        self.page.goto(PAGE, wait_until='domcontentloaded', timeout=90000)
        self.page.wait_for_timeout(3500)

    def close(self):
        self.browser.close()
        self._pw.stop()

    def call(self, api, params, page_no=1, pagerows=500):
        ts = str(int(time.time() * 1000))
        sign = hashlib.md5(f'token=&ak={AK}&api={api}&ts={ts}&data=null'.encode()).hexdigest()
        url = GW + '?' + urllib.parse.urlencode({'ak': AK, 'api': api, 'ts': ts, 'sign': sign})
        items = []
        if pagerows:
            items = [{'vtype': 'pagination', 'name': k, 'data': v} for k, v in
                     [('pagerows', pagerows), ('totalrows', 0), ('page', page_no),
                      ('sortName', ''), ('sortOrder', '')]]
        items += [{'vtype': 'attr', 'name': k, 'data': v} for k, v in params.items()]
        outer = {'postData': json.dumps({'data': items}, ensure_ascii=False)}
        outer.update({k: str(v) for k, v in params.items()})
        txt = self.page.evaluate(
            """async ([url, body]) => {
                const r = await fetch(url, {method:'POST',
                    headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
                return await r.text();
            }""", [url, outer])
        return json.loads(txt)

    def theme_detail(self, ztcode, bgq):
        """一主题一报告期的全部报表 [(instrumentName, content_html)]。"""
        params = {'ztCode': ztcode, 'bgq': bgq, 'dqCode': '33', 'orgCode': '33',
                  'type': '1', 'instrumentName': ''}
        r = self.call('mgop.gw.sjzj.monthCardqueryMCdDetailByZt', params, pagerows=0)
        for item in (r.get('data') or {}).get('data') or []:
            if item.get('name') == 'data' and isinstance(item.get('data'), list):
                return [(t.get('instrumentName') or '', t.get('content') or '')
                        for t in item['data']]
        return []


# ==================== 主流程 ====================

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--bgq', default='', help='只跑一个报告期，如 20260005')
    ap.add_argument('--from', dest='bgq_from', default='', help='区间起始月 yyyymm，如 202501')
    ap.add_argument('--to', dest='bgq_to', default='', help='区间截止月 yyyymm，如 202604（含）')
    args = ap.parse_args()
    if args.bgq:
        bgqs = [args.bgq]
    elif args.bgq_from or args.bgq_to:
        y, m = int(args.bgq_from[:4]), int(args.bgq_from[4:6])
        end_y, end_m = int(args.bgq_to[:4]), int(args.bgq_to[4:6])
        bgqs = []
        while (y, m) <= (end_y, end_m):
            bgqs.append(f'{y}00{m:02d}')
            m += 1
            if m > 12:
                y, m = y + 1, 1
    else:
        bgqs = ['20260005', '20260006']
    periods = {b: bgq_to_period(b) for b in bgqs}
    print('报告期:', ' '.join(f'{b}({periods[b]})' for b in bgqs))

    gb_idx = build_gb_index(load_gb())
    browser = MgopBrowser()
    parsed = {}  # table -> rows
    try:
        for ztcode, ztname, table, theme_cn in THEMES:
            for bgq in bgqs:
                try:
                    tables = browser.theme_detail(ztcode, bgq)
                except Exception as e:
                    print(f'  [FAIL] {ztname} {bgq}: {str(e)[:100]}')
                    continue
                n_rows = 0
                for report_name, content in tables:
                    headers, rows = parse_report_html(content)
                    nr = narrow_rows(theme_cn, report_name, periods[bgq], headers, rows, gb_idx)
                    parsed.setdefault(table, []).extend(nr)
                    n_rows += len(nr)
                print(f'  [抓] {ztname}({ztcode}) {bgq}: {len(tables)} 表 → {n_rows} 窄行')
                time.sleep(0.3)
    finally:
        browser.close()

    total = sum(len(v) for v in parsed.values())
    print(f'抓取合计：{total} 窄行，{len(parsed)} 张主题表')

    if args.dry_run:
        for t, rows in sorted(parsed.items()):
            print(f'  [dry] {t}: {len(rows)} 行')
            for r in rows[:2]:
                print('    样例:', r)
        return

    # ============ 物理入库（增量：建表 IF NOT EXISTS + 仅按本次报告期删除重插，不动其他期间） ============
    biz = connect(DB_BIZ)
    now = datetime.now()
    scope_periods = sorted(set(periods.values()))
    ph = ','.join(['%s'] * len(scope_periods))
    total_counts = {}
    with biz.cursor() as cur:
        for _zc, _zn, table, theme_cn in {t[2]: (t[0], t[1], t[2], t[3]) for t in THEMES}.values():
            rows = parsed.get(table, [])
            cur.execute(narrow_ddl(table, theme_cn).replace('CREATE TABLE', 'CREATE TABLE IF NOT EXISTS', 1))
            cur.execute(f'DELETE FROM `{table}` WHERE stat_period IN ({ph})', scope_periods)
            if rows:
                cur.executemany(
                    f'INSERT INTO `{table}` (stat_period, report_name, region_name, region_adcode, '
                    f'region_level, dim_name, indicator_name, unit, meas_value, etl_time) '
                    f'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                    [(p, rp, rn, ac, lv, dn, ind, u, v, now) for (p, rp, rn, ac, lv, dn, ind, u, v) in rows])
            cur.execute(f'SELECT COUNT(*) FROM `{table}`')
            total_counts[table] = cur.fetchone()[0]
            biz.commit()
            n_region = sum(1 for r in rows if r[4] != '省')  # 非省级（城市/区县）行数
            print(f'  [ok] {table}: 本次 +{len(rows)} 行（城市/区县 {n_region}），全表 {total_counts[table]} 行')

    # ============ 治理库同步 ============
    print('== 治理库同步 ==')
    gov = connect(DB_GOV)
    done_tables = sorted({t[2] for t in THEMES})
    with gov.cursor() as cur:
        # 业务域：Tjj + 14 二级
        cur.execute('INSERT INTO business_domains (level, domain_code, domain_name, parent_code, name_en, source) '
                    'VALUES (1,%s,%s,NULL,%s,%s) ON DUPLICATE KEY UPDATE domain_name=VALUES(domain_name)',
                    ('Tjj', '统计数据', 'STATISTICS', SRC_NAME))
        sub = {}
        for i, (table, theme_cn) in enumerate(sorted({t[2]: t[3] for t in THEMES}.items())):
            code = f'Tjj{i + 1:02d}'
            sub[table] = code
            cur.execute('INSERT INTO business_domains (level, domain_code, domain_name, parent_code, source) '
                        'VALUES (2,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE domain_name=VALUES(domain_name)',
                        (code, theme_cn.replace('月度-', ''), 'Tjj', SRC_NAME))
        # 码值域 tjj_dim_type 已废弃（dim_type 列删除），清理其域/明细/形态登记
        cur.execute('DELETE FROM code_values WHERE code_name=%s', ('tjj_dim_type',))
        cur.execute('DELETE FROM code_value_items WHERE code_name=%s', ('tjj_dim_type',))
        cur.execute('DELETE FROM code_value_column_form WHERE code_name=%s', ('tjj_dim_type',))
        for table in done_tables:
            theme_cn = next(t[3] for t in THEMES if t[2] == table)
            rc = total_counts.get(table, len(parsed.get(table, [])))  # 全表行数（含存量期间）
            doc_text = (f'表 {table}，中文注释：{theme_cn}，{rc} 行数据，{len(NARROW_COLS)} 个字段。'
                        f'主题窄表（行列转换：report_name/region_*/dim_name/indicator_name/meas_value），'
                        f'地区轴 region_name/region_adcode/region_level（GB/T 2260，整库默认浙江省 330000），'
                        f'科目轴 dim_name（地区行为空）；来源：{SRC_NAME}。')
            cur.execute('INSERT INTO schema_table_docs (table_name, table_comment, row_count, column_count, '
                        'doc_text, domain_l1, domain_l2, updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) '
                        'ON DUPLICATE KEY UPDATE table_comment=VALUES(table_comment), row_count=VALUES(row_count), '
                        'column_count=VALUES(column_count), doc_text=VALUES(doc_text), updated_at=VALUES(updated_at)',
                        (table, theme_cn, rc, len(NARROW_COLS), doc_text, 'Tjj', sub[table], now))
            cur.execute('DELETE FROM schema_column_docs WHERE table_name=%s', (table,))
            for n, typ, cmt in NARROW_COLS:
                cur.execute('INSERT INTO schema_column_docs (table_name, column_name, column_comment, data_type, '
                            'is_pk, doc_text) VALUES (%s,%s,%s,%s,%s,%s)',
                            (table, n, cmt, typ, 1 if n == 'id' else 0,
                             f'表 {table} 的字段 {n}，中文注释：{cmt}，类型：{typ}'))
            for col, cd, form in (('region_adcode', 'region_adcode', '编码'),
                                  ('region_name', 'region_adcode', '名称')):
                cur.execute('INSERT INTO code_value_column_form (table_name, column_name, code_name, form, updated_at) '
                            'VALUES (%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE form=VALUES(form), updated_at=VALUES(updated_at)',
                            (table, col, cd, form, now))
            for kw in {theme_cn.replace('月度-', ''), '月度数据', '统计局', SRC_NAME}:
                cur.execute('INSERT INTO keyword_table_map (keyword, table_name, enabled, updated_at) '
                            'VALUES (%s,%s,1,%s) ON DUPLICATE KEY UPDATE enabled=1, updated_at=VALUES(updated_at)',
                            (kw, table, now))
        gov.commit()

    # ============ 本体库同步 ============
    print('== 本体库同步 ==')
    ont = connect('database01_ontology')
    with ont.cursor() as cur:
        # 纪律（2026-09 起）：不建逐表实体，14 张主题表全部挂入 ReportStats 域实体
        from tools.enrich_report_relations import attach_tables_to_domain
        attach_tables_to_domain(cur, 'ReportStats', '统计类报表', done_tables, SRC_NAME)
    ont.commit()
    ont.close()
    from core.ontology.service import OntologyService
    svc = OntologyService()
    result = svc.rebuild_proposal()
    ont_new = svc.approve(result['proposal_id'])
    print(f'  [ok] 本体提案 #{result["proposal_id"]} 已批准，版本 v{ont_new.version}')

    biz.close()
    gov.close()
    print('[done] 月度卡片入库完成')


def connect(db):
    return pymysql.connect(host=config.MYSQL_HOST, port=config.MYSQL_PORT,
                           user=config.MYSQL_USER, password=config.MYSQL_PASSWORD,
                           database=db, charset='utf8mb4')


if __name__ == '__main__':
    main()
