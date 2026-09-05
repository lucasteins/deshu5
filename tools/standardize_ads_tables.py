# -*- coding: utf-8 -*-
"""database01 十七张 ads_ 表标准化迁移（一次性治理脚本，规范见 skill: ads-table-standard）

迁移内容（backup 先行：db/database/backup_ads_std_20260831_140352.sql）：
1. 物理层：16 张旧表 → 影子新表（四段式命名 + 公共列 + 类型收敛 + PK/索引 + 注释双写），
   校验后 DROP 旧表；ads_prj_01_ngdis_st2d_trade_dq_1 直接删除（与保留表零重叠，用户确认）
2. 治理库 database01_governance：schema_table_docs / schema_column_docs 重建 ads 段、
   schema_relationship_docs / keyword_table_map / ingest_provenance 改名同步、
   qa_pairs.standard_sql 与 report_templates.outline 改写 + 只读验证
3. 本体库 database01_ontology：entity_defs.member_tables 改名后走 rebuild_proposal + approve

用法：
  python tools/standardize_ads_tables.py --dry-run   # 只打印计划，不动库
  python tools/standardize_ads_tables.py             # 执行（幂等，断点续跑）
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

# 管理单位编码 → 名称（来源：sdxqsh/tpzb 现有 code→name 对）
ORG_NAME_MAP = {
    '33101': '国网浙江省电力有限公司',
    '33401': '国网浙江省电力有限公司杭州供电公司',
}

STD_COMMENTS = {
    'id': '物理主键',
    'stat_period': '统计期间（月度，yyyymm）',
    'stat_date': '统计日期',
    'ts': '数据时间（分钟级点）',
    'mgt_org_code': '管理单位编码',
    'mgt_org_name': '管理单位名称',
    'datasource_id': '数据来源标识',
    'etl_time': '数据写入时间',
    'src_id': '源端标识（原 id 列）',
}

# ==================== 逐表迁移规格 ====================
# period: 新期间列（col/type/comment/expr/uses 旧列）
# renames: 旧列→新列（值直接承接，类型按 overrides/通用规则）
# drops: 不承接的旧列
# overrides: 列→(新类型, 表达式或 None=直接承接)
# extra_cols: 旧表没有的新列 (名, 类型, 注释, 表达式)
# reuse_id: 旧 id 已是 PK AUTO_INCREMENT，直接沿用为物理主键
# where: 迁移过滤（防御性）

def _robust_date(col):
    """yyyymm / yyyymmdd 混合整数 → DATE 的健壮转换表达式。"""
    c = f'CAST({col} AS CHAR)'
    return (f"IF(LENGTH({c})=6, STR_TO_DATE(CONCAT({c}, '01'), '%Y%m%d'), "
            f"STR_TO_DATE({c}, '%Y%m%d'))")


TABLES = [
    dict(old='ads_cst_bb_yk3300009', new='ads_cst_exp_cust_mon', cn='省内业扩表二_用电户数统计表（全口径）',
         period=dict(col='stat_period', type='VARCHAR(6)', expr="CAST(rpt_month AS CHAR)", uses=['rpt_month']),
         renames={'dept_id': 'mgt_org_code', 'dept_code': 'prov_org_code', 'date_ope_time': 'etl_time'},
         drops=[], overrides={
             'mgt_org_code': ('VARCHAR(32)', None), 'prov_org_code': ('VARCHAR(32)', None),
             'date_ope_type': ('VARCHAR(32)', None), 'bb_code': ('VARCHAR(64)', None),
             'item_title': ('VARCHAR(255)', None), 'etl_time': ('DATETIME', 'CAST(date_ope_time AS DATETIME)')},
         extra_cols=[('mgt_org_name', 'VARCHAR(128)', '管理单位名称',
                      "CASE CAST(dept_id AS CHAR) "
                      + ' '.join(f"WHEN '{k}' THEN '{v}'" for k, v in ORG_NAME_MAP.items())
                      + " ELSE '' END")]),
    dict(old='ads_cst_cust_elec_app_rec_and_cap_do', new='ads_cst_exp_app_cap_da', cn='业扩报装户数及容量',
         period=dict(col='stat_date', type='DATE', expr="STR_TO_DATE(CAST(data_period AS CHAR), '%Y%m%d')",
                     uses=['data_period']),
         renames={'write_time': 'etl_time'}, drops=[],
         overrides={'mgt_org_code': ('VARCHAR(32)', None), 'mgt_org_name': ('VARCHAR(128)', None),
                    'rate_time': ('DATE', _robust_date('rate_time')),
                    'etl_time': ('DATETIME', 'CAST(write_time AS DATETIME)')},
         extra_cols=[]),
    dict(old='ads_cst_deg_exp_stat_do', new='ads_cst_bill_fee_mon', cn='计费_电量电费统计',
         period=dict(col='stat_period', type='VARCHAR(6)', expr="CAST(data_period AS CHAR)", uses=['data_period']),
         renames={'write_time': 'etl_time'}, drops=[],
         overrides={'mgt_org_code': ('VARCHAR(32)', None), 'mgt_org_name': ('VARCHAR(128)', None),
                    'rate_time': ('DATE', _robust_date('rate_time')),
                    'etl_time': ('DATETIME', 'CAST(write_time AS DATETIME)')},
         extra_cols=[]),
    dict(old='ads_cst_rcvbl_acct_stat_do', new='ads_cst_rcvbl_acct_mon', cn='计费_应收台账统计',
         period=dict(col='stat_period', type='VARCHAR(6)', expr="CAST(data_period AS CHAR)", uses=['data_period']),
         renames={'write_time': 'etl_time'}, drops=[],
         overrides={'mgt_org_code': ('VARCHAR(32)', None),  # 值已损毁（int 溢出 2147483647），保留原值
                    'mgt_org_name': ('VARCHAR(128)', None),
                    'rate_time': ('DATE', _robust_date('rate_time')),
                    'etl_time': ('DATETIME', 'CAST(write_time AS DATETIME)')},
         extra_cols=[]),
    dict(old='ads_grid_t_sg_da_tpzb', new='ads_grid_carbon_idx_da', cn='PCEI双碳14个指标汇总表',
         period=dict(col='stat_date', type='DATE', expr='DATE(rq)', uses=['rq']),
         renames={}, drops=[],
         overrides={'mgt_org_code': ('VARCHAR(32)', None), 'mgt_org_name': ('VARCHAR(128)', None)},
         extra_cols=[]),
    dict(old='ads_grid_t_ts_data_proces_ts_sg_da_zrdl', new='ads_grid_prevday_power_da', cn='昨日电量',
         period=dict(col='stat_date', type='DATE', expr='DATE(create_date)', uses=['create_date']),
         renames={'id': 'src_id'}, drops=[],
         overrides={'mgt_org_code': ('VARCHAR(32)', None), 'mgt_org_name': ('VARCHAR(128)', None),
                    'src_id': ('BIGINT', None)},
         extra_cols=[]),
    dict(old='ads_grid_t_ts_sg_da_dszj', new='ads_grid_city_cap_da', cn='各地市装机容量',
         period=dict(col='stat_date', type='DATE', expr='DATE(rq)', uses=['rq']),
         renames={}, drops=[],
         overrides={'mgt_org_code': ('VARCHAR(32)', None), 'mgt_org_name': ('VARCHAR(128)', None),
                    'lx': ('VARCHAR(64)', None), 'owner': ('VARCHAR(64)', None)},
         extra_cols=[]),
    dict(old='ads_grid_t_ts_sg_da_fbsgf', new='ads_grid_pv_cap_da', cn='全社会分布式光伏装机',
         period=dict(col='stat_date', type='DATE', expr='DATE(rq)', uses=['rq']),
         renames={}, drops=[],
         overrides={'mgt_org_code': ('VARCHAR(32)', None), 'mgt_org_name': ('VARCHAR(128)', None),
                    'lx': ('VARCHAR(64)', None), 'owner': ('VARCHAR(64)', None)},
         extra_cols=[]),
    dict(old='ads_grid_t_ts_sg_da_glzjrl', new='ads_grid_type_cap_da', cn='各类型装机容量',
         period=dict(col='stat_date', type='DATE', expr='DATE(rq)', uses=['rq']),
         renames={}, drops=[],
         overrides={'mgt_org_code': ('VARCHAR(32)', None), 'mgt_org_name': ('VARCHAR(128)', None),
                    'lx': ('VARCHAR(64)', None), 'owner': ('VARCHAR(64)', None)},
         extra_cols=[]),
    dict(old='ads_grid_t_ts_sg_lc_con_pwrgrid_b', new='ads_grid_load_point_mi', cn='电网负荷量测数据',
         period=dict(col='stat_date', type='DATE', expr="STR_TO_DATE(CAST(create_time AS CHAR), '%Y%m%d')",
                     uses=['create_time']),
         renames={}, drops=[], reuse_id=True,  # 288 个 vHHMM 宽表列保留（规范豁免存量宽表）
         overrides={'mgt_org_code': ('VARCHAR(32)', None), 'mgt_org_name': ('VARCHAR(128)', None)},
         extra_cols=[]),
    dict(old='ads_grid_t_ts_sg_lc_sdxqsh', new='ads_grid_load_curve_mi', cn='省地县全社会负荷',
         period=dict(col='ts', type='DATETIME', expr='CAST(create_time AS DATETIME)', uses=['create_time']),
         renames={'id': 'src_id'}, drops=[],
         overrides={'mgt_org_code': ('VARCHAR(32)', None), 'mgt_org_name': ('VARCHAR(128)', None),
                    'src_id': ('BIGINT', None)},
         extra_cols=[]),
    dict(old='ads_grid_t_ts_sg_lc_sdxrdl', new='ads_grid_day_power_da', cn='省地县全社会日电量',
         period=dict(col='stat_date', type='DATE', expr='DATE(create_date)', uses=['create_date']),
         renames={}, drops=[], reuse_id=True,
         overrides={'mgt_org_code': ('VARCHAR(32)', None), 'mgt_org_name': ('VARCHAR(128)', None),
                    'name': ('VARCHAR(128)', None), 'name_abbreviation': ('VARCHAR(64)', None)},
         extra_cols=[]),
    dict(old='ads_prj_01_ngdis_st2b_pro_plant_summary_all', new='ads_prj_plant_summary_mon', cn='发电生产综合情况表',
         period=dict(col='stat_period', type='VARCHAR(6)',
                     expr="CONCAT(CAST(tab_year AS CHAR), LPAD(CAST(tab_month AS CHAR), 2, '0'))",
                     uses=['tab_year', 'tab_month']),
         renames={'id': 'src_id', 'extend_field_update_time': 'etl_time'},
         drops=['tab_year', 'tab_month'],
         overrides={'mgt_org_code': ('VARCHAR(32)', None), 'mgt_org_name': ('VARCHAR(128)', None),
                    'dept_code': ('VARCHAR(32)', None), 'sub_dept_code': ('VARCHAR(32)', None),
                    'extend_field_org_code': ('VARCHAR(32)', None), 'src_id': ('BIGINT', None),
                    'extend_field_time_stamp': ('DATETIME', 'CAST(extend_field_time_stamp AS DATETIME)'),
                    'etl_time': ('DATETIME', 'CAST(extend_field_update_time AS DATETIME)')},
         extra_cols=[]),
    dict(old='ads_prj_ngdis_st2d_trade_dq_1', new='ads_prj_society_power_mon', cn='全社会用电情况数据（全口径）',
         period=dict(col='stat_period', type='VARCHAR(6)', expr='CAST(`period` AS CHAR)', uses=['period']),
         renames={'dept_code__': 'dept_code', 'extend_field_update_time': 'etl_time'},
         drops=[],
         overrides={'mgt_org_code': ('VARCHAR(32)', None), 'mgt_org_name': ('VARCHAR(128)', None),
                    'dept_code': ('VARCHAR(32)', None), 'dept_name': ('VARCHAR(128)', None),
                    'extend_field_org_code': ('VARCHAR(32)', None),
                    'extend_field_time_stamp': ('DATETIME', 'CAST(extend_field_time_stamp AS DATETIME)'),
                    'etl_time': ('DATETIME', 'CAST(extend_field_update_time AS DATETIME)')},
         extra_cols=[]),
    dict(old='ads_prj_ngdis_ieq_te_trade_use_power_mon_pm', new='ads_prj_county_power_mon',
         cn='全社会用电情况数据（全口径）分区县',
         period=dict(col='stat_period', type='VARCHAR(6)', expr='CAST(periods AS CHAR)', uses=['periods']),
         renames={'data_date': 'etl_time'}, drops=[],
         where='dept_id_level = 4',  # 只保留区县数据（用户确认；当前 135 行已全是区县，防御性过滤）
         overrides={'mgt_org_code': ('VARCHAR(32)', None), 'mgt_org_name': ('VARCHAR(128)', None),
                    'dept_code': ('VARCHAR(32)', None),  # 值已损毁（int 溢出），保留原值
                    'dept_name': ('VARCHAR(128)', None),
                    'etl_time': ('DATETIME', 'CAST(data_date AS DATETIME)')},
         extra_cols=[]),
    dict(old='ads_prj_zb_fzgh_qshydl_mi', new='ads_prj_trade_power_mon', cn='全社会用电量-分行业-发展报表',
         period=dict(col='stat_period', type='VARCHAR(6)', expr='CAST(rate_time AS CHAR)', uses=['rate_time']),
         renames={}, drops=[],
         overrides={'mgt_org_code': ('VARCHAR(32)', None), 'mgt_org_name': ('VARCHAR(128)', None),
                    'org_no': ('VARCHAR(32)', None), 'org_name': ('VARCHAR(128)', None),
                    'rate': ('VARCHAR(16)', None), 'rate_name': ('VARCHAR(32)', None),
                    'item_id': ('VARCHAR(64)', None), 'item_name': ('VARCHAR(255)', None),
                    'data_id': ('VARCHAR(64)', None), 'trade_code': ('VARCHAR(64)', None),
                    'trade_name': ('VARCHAR(128)', None),
                    'zjrl': ('DECIMAL(20,4)', "IF(zjrl REGEXP '^[0-9.]+$', CAST(zjrl AS DECIMAL(20,4)), NULL)"),
                    'yhs': ('BIGINT', "IF(yhs REGEXP '^[0-9]+$', CAST(yhs AS SIGNED), NULL)")},
         extra_cols=[]),
]

DROP_TABLES = ['ads_prj_01_ngdis_st2d_trade_dq_1']  # 与 ads_prj_society_power_mon 数据零重叠（用户确认删除）

# 被删表 SQL 机械改写到保留表：dept_code__ 在 01 版注释为"当前单位名称"（qa227 LIKE '%浙江%'），映射 dept_name
DROPPED_TABLE_REWRITE = {
    'ads_prj_01_ngdis_st2d_trade_dq_1': {
        'table': 'ads_prj_society_power_mon',
        'cols': {'period': 'stat_period', 'dept_code__': 'dept_name'},
    },
}

DROP_SENTINEL = object()  # period/renames 中被消耗列的标记


def connect(db):
    return pymysql.connect(host=config.MYSQL_HOST, port=config.MYSQL_PORT,
                           user=config.MYSQL_USER, password=config.MYSQL_PASSWORD,
                           database=db, charset='utf8mb4', autocommit=False)


def fetch_cols(conn, table):
    """物理列：[(name, type, extra)]，按原始顺序。"""
    with conn.cursor() as cur:
        cur.execute('''SELECT column_name, UPPER(column_type), extra
                       FROM information_schema.columns
                       WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position''',
                    (DB_BIZ, table))
        return cur.fetchall()


def fetch_comments(gov, table):
    """治理库列注释：{column_name: (comment, is_pk)}。"""
    with gov.cursor() as cur:
        cur.execute('SELECT column_name, COALESCE(column_comment, ""), COALESCE(is_pk, 0) '
                    'FROM schema_column_docs WHERE table_name=%s', (table,))
        return {r[0]: (r[1], r[2]) for r in cur.fetchall()}


def fetch_table_comment(gov, table):
    with gov.cursor() as cur:
        cur.execute('SELECT COALESCE(table_comment, "") FROM schema_table_docs WHERE table_name=%s', (table,))
        row = cur.fetchone()
        return row[0] if row else ''


def generic_type(name, old_type):
    base = re.sub(r'\(.*\)', '', old_type)
    if base == 'DOUBLE':
        return 'DECIMAL(20,4)'
    if base == 'TEXT':
        return 'VARCHAR(255)'
    if base == 'INT':
        return 'INT'
    return old_type


def build_new_schema(spec, old_cols, comments):
    """计算新表列清单：[(new_name, new_type, comment, select_expr)]。失败抛 ValueError。"""
    old_names = {c[0] for c in old_cols}
    # 规格引用的旧列必须存在
    refs = set(spec['period']['uses']) | set(spec['renames']) | set(spec['drops'])
    for ov_col, (_t, expr) in spec['overrides'].items():
        if expr:
            expr_clean = re.sub(r"'[^']*'", '', expr)  # 去掉字符串字面量（含 %Y%m%d 格式串）
            refs |= set(re.findall(r'[a-zA-Z_][a-zA-Z0-9_]*', expr_clean)) - {
                'CAST', 'AS', 'CHAR', 'DATE', 'DATETIME', 'DECIMAL', 'SIGNED', 'STR_TO_DATE',
                'CONCAT', 'LPAD', 'CASE', 'WHEN', 'THEN', 'ELSE', 'END', 'IF', 'LENGTH',
                'REGEXP', 'NULL', 'NULLIF'}
    missing = [r for r in refs if r not in old_names and not re.fullmatch(r"'[^']*'|\d+", r)]
    if missing:
        raise ValueError(f"{spec['old']}: 规格引用了不存在的列 {missing}")

    consumed = set(spec['period']['uses']) | set(spec['drops'])
    rename_to = {v: k for k, v in spec['renames'].items()}
    cols = []  # (new_name, type, comment, expr)

    def comment_of(old_name, new_name):
        if new_name in STD_COMMENTS:
            return STD_COMMENTS[new_name]
        return comments.get(old_name, ('', 0))[0]

    # 1) id
    if spec.get('reuse_id'):
        cols.append(('id', 'BIGINT', STD_COMMENTS['id'], 'id'))
    else:
        cols.append(('id', 'BIGINT', STD_COMMENTS['id'], None))  # AUTO_INCREMENT 新建
    # 2) 期间列
    p = spec['period']
    cols.append((p['col'], p['type'], STD_COMMENTS[p['col']], p['expr']))
    # 3) 机构列 + 业务列（按旧表顺序）
    for name, old_type, extra in old_cols:
        if name in consumed:
            continue
        if name == 'id' and spec.get('reuse_id'):
            continue  # 已置首列
        new_name = spec['renames'].get(name, name)
        if name == 'id' and not spec.get('reuse_id'):
            new_name = 'src_id'  # 非 PK 的旧 id 统一收编为 src_id
        ov = spec['overrides'].get(new_name) or spec['overrides'].get(name)
        if ov:
            new_type, expr = ov
            expr = expr if expr else f'`{name}`'
        else:
            new_type, expr = generic_type(new_name, old_type), f'`{name}`'
        cols.append((new_name, new_type, comment_of(name, new_name), expr))
    # 4) 额外新列
    for name, typ, cmt, expr in spec.get('extra_cols', []):
        cols.append((name, typ, cmt, expr))
    # 5) 规范必备列兜底（datasource_id / etl_time 缺则补 NULL 列）
    have = {c[0] for c in cols}
    for need, typ in (('mgt_org_code', 'VARCHAR(32)'), ('mgt_org_name', 'VARCHAR(128)'),
                      ('datasource_id', 'INT'), ('etl_time', 'DATETIME')):
        if need not in have:
            cols.append((need, typ, STD_COMMENTS[need], None))
    return cols


def create_ddl(spec, cols, table_comment):
    lines = []
    for name, typ, cmt, _expr in cols:
        if name == 'id':
            lines.append(f'  `id` {typ} NOT NULL AUTO_INCREMENT COMMENT %s' % q(cmt))
        else:
            lines.append(f'  `{name}` {typ} NULL COMMENT %s' % q(cmt))
    pcol = spec['period']['col']
    lines.append('  PRIMARY KEY (`id`)')
    lines.append(f'  KEY `idx_org_period` (`mgt_org_code`, `{pcol}`)')
    return ('CREATE TABLE `%s` (\n%s\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT=%s'
            % (spec['new'], ',\n'.join(lines), q(table_comment)))


def q(s):
    return "'" + str(s).replace('\\', '\\\\').replace("'", "\\'") + "'"


def insert_sql(spec, cols):
    tgt, src = [], []
    for name, _typ, _cmt, expr in cols:
        if name == 'id' and not spec.get('reuse_id'):
            continue  # AUTO_INCREMENT 不插值
        tgt.append(f'`{name}`')
        src.append(expr if expr else 'NULL')
    where = f" WHERE {spec['where']}" if spec.get('where') else ''
    return f"INSERT INTO `{spec['new']}` ({', '.join(tgt)}) SELECT {', '.join(src)} FROM `{spec['old']}`{where}"


# ==================== 步骤一：物理迁移 ====================

def migrate_table(biz, spec, dry_run):
    old, new = spec['old'], spec['new']
    old_cols = fetch_cols(biz, old)
    if not old_cols:
        with biz.cursor() as cur:
            cur.execute('SELECT COUNT(*) FROM information_schema.tables '
                        'WHERE table_schema=%s AND table_name=%s', (DB_BIZ, new))
            if cur.fetchone()[0]:
                print(f'  [skip] {old} 已迁移为 {new}')
                return True
        raise RuntimeError(f'{old} 不存在且 {new} 也不存在')
    comments = fetch_comments(gov_conn(), old)
    cols = build_new_schema(spec, old_cols, comments)
    ddl = create_ddl(spec, cols, spec['cn'])
    ins = insert_sql(spec, cols)
    if dry_run:
        print(f'  [dry] {old} -> {new}\n{ddl}\n{ins[:400]}...\n')
        return True

    with biz.cursor() as cur:
        cur.execute(f'DROP TABLE IF EXISTS `{new}`')  # 幂等：重建半成品影子表
        cur.execute(ddl)
        cur.execute(ins)
        # 校验 1：行数
        cur.execute(f'SELECT COUNT(*) FROM `{new}`')
        new_cnt = cur.fetchone()[0]
        where = f" WHERE {spec['where']}" if spec.get('where') else ''
        cur.execute(f'SELECT COUNT(*) FROM `{old}`{where}')
        old_cnt = cur.fetchone()[0]
        if new_cnt != old_cnt:
            raise RuntimeError(f'{old}: 行数不一致 旧{old_cnt} != 新{new_cnt}')
        # 校验 2：期间列非空
        pcol = spec['period']['col']
        cur.execute(f'SELECT COUNT(*) FROM `{new}` WHERE `{pcol}` IS NULL')
        null_cnt = cur.fetchone()[0]
        if null_cnt:
            raise RuntimeError(f'{old}: 期间列 {pcol} 有 {null_cnt} 行为 NULL')
        # 校验 3：首个度量列合计比对（double→decimal 允许 1e-2 相对误差）
        meas = next((c for c in cols if 'DECIMAL' in c[1] and c[3] and c[3].startswith('`')), None)
        if meas:
            old_col = meas[3].strip('`')
            cur.execute(f'SELECT SUM(`{old_col}`) FROM `{old}`{where}')
            old_sum = float(cur.fetchone()[0] or 0)
            cur.execute(f'SELECT SUM(`{meas[0]}`) FROM `{new}`')
            new_sum = float(cur.fetchone()[0] or 0)
            if abs(old_sum - new_sum) > max(0.01, abs(old_sum) * 1e-4):
                raise RuntimeError(f'{old}: 度量列 {old_col} 合计漂移 旧{old_sum} 新{new_sum}')
        cur.execute(f'DROP TABLE `{old}`')
    biz.commit()
    print(f'  [ok] {old} -> {new}（{new_cnt} 行，{len(cols)} 列）')
    return True


_gov = None


def gov_conn():
    global _gov
    if _gov is None or not _gov.open:
        _gov = connect(DB_GOV)
    return _gov


# ==================== 步骤二：被删表 ====================

def drop_table(biz, dry_run):
    for t in DROP_TABLES:
        if dry_run:
            print(f'  [dry] DROP {t}')
            continue
        with biz.cursor() as cur:
            cur.execute(f'DROP TABLE IF EXISTS `{t}`')
        biz.commit()
        print(f'  [drop] {t}')


# ==================== 步骤三：治理库同步 ====================

def sync_governance(biz, dry_run):
    gov = gov_conn()
    rename_pairs = [(s['old'], s['new']) for s in TABLES]
    col_maps = {}  # new_table -> {old_col: new_col}（治理/改写用，剔除 id）
    for s in TABLES:
        m = dict(s['renames'])
        for u in s['period']['uses']:
            m[u] = s['period']['col']
        m.pop('id', None)  # id→src_id 不进 SQL 改写（词边界误伤风险）
        col_maps[s['new']] = m

    # 1) schema_table_docs：改名 + 刷新行/列数；删表行删除
    for old, new in rename_pairs:
        with biz.cursor() as cur:
            cur.execute('SELECT COUNT(*) FROM information_schema.tables '
                        'WHERE table_schema=%s AND table_name=%s', (DB_BIZ, new))
            exists = cur.fetchone()[0] > 0
            if exists:
                cur.execute(f'SELECT COUNT(*) FROM `{new}`')
                rc = cur.fetchone()[0]
                cur.execute('SELECT COUNT(*) FROM information_schema.columns '
                            'WHERE table_schema=%s AND table_name=%s', (DB_BIZ, new))
                cc = cur.fetchone()[0]
            else:
                rc = cc = -1  # dry-run（新表未建）或异常
        if dry_run:
            print(f'  [dry] schema_table_docs {old} -> {new}')
            continue
        if not exists:
            raise RuntimeError(f'治理库同步时新表 {new} 不存在（物理迁移未完成？）')
        with gov.cursor() as cur:
            cur.execute('UPDATE schema_table_docs SET table_name=%s, row_count=%s, column_count=%s, '
                        "doc_text=REPLACE(doc_text, %s, %s), updated_at=%s WHERE table_name=%s",
                        (new, rc, cc, old, new, datetime.now(), old))
    if not dry_run:
        for t in DROP_TABLES:
            with gov.cursor() as cur:
                cur.execute('DELETE FROM schema_table_docs WHERE table_name=%s', (t,))

    # 2) schema_column_docs：删旧表全部行，按新物理结构重建（注释沿用治理库旧值）
    for s in TABLES:
        old, new = s['old'], s['new']
        comments = fetch_comments(gov, old)
        old_is_pk = {c: pk for c, (_cm, pk) in comments.items()}
        with biz.cursor() as cur:
            cur.execute('SELECT column_name, UPPER(column_type), column_comment '
                        'FROM information_schema.columns WHERE table_schema=%s AND table_name=%s '
                        'ORDER BY ordinal_position', (DB_BIZ, new))
            new_cols = cur.fetchall()
        if dry_run:
            print(f'  [dry] schema_column_docs 重建 {new}: {len(new_cols)} 行')
            continue
        with gov.cursor() as cur:
            cur.execute('DELETE FROM schema_column_docs WHERE table_name=%s', (old,))
            cur.execute('DELETE FROM schema_column_docs WHERE table_name=%s', (new,))  # 幂等
            for name, typ, cmt in new_cols:
                # 注释来源：物理 COMMENT（迁移时双写）优先，其次旧映射
                is_pk = 1 if name == 'id' else 0
                # 保留旧业务主键标记（rq/owner/lx 等在旧文档中标 1 的）
                for old_col, new_col in col_maps[new].items():
                    if new_col == name and old_is_pk.get(old_col):
                        is_pk = 1
                doc = f'表 {new} 的字段 {name}，中文注释：{cmt or "（无）"}，类型：{typ}'
                cur.execute('INSERT INTO schema_column_docs (table_name, column_name, column_comment, '
                            'data_type, is_pk, doc_text) VALUES (%s,%s,%s,%s,%s,%s)',
                            (new, name, cmt or '', typ, is_pk, doc))
    if not dry_run:
        for t in DROP_TABLES:
            with gov.cursor() as cur:
                cur.execute('DELETE FROM schema_column_docs WHERE table_name=%s', (t,))
        gov.commit()

    # 3) schema_relationship_docs：表名 + 涉及表的列名改写；引用被删表的行删除
    with gov.cursor() as cur:
        cur.execute('SELECT id, title, path, join_conditions, doc_text FROM schema_relationship_docs')
        rels = cur.fetchall()
    for rid, title, path, joins, doc in rels:
        text_all = ''.join(str(x or '') for x in (title, path, joins, doc))
        hit_old = next((o for o, n in rename_pairs if o in text_all), None)
        hit_drop = next((t for t in DROP_TABLES if t in text_all), None)
        if not hit_old and not hit_drop:
            continue
        if dry_run:
            print(f'  [dry] schema_relationship_docs #{rid} 改写' if hit_old else f'  [dry] schema_relationship_docs #{rid} 删除（被删表）')
            continue
        with gov.cursor() as cur:
            if hit_drop:
                cur.execute('DELETE FROM schema_relationship_docs WHERE id=%s', (rid,))
                continue
            vals = []
            for v in (title, path, joins, doc):
                s2 = str(v or '')
                for old, new in rename_pairs:
                    s2 = s2.replace(old, new)
                vals.append(s2)
            # 列名改写（按字符串中实际出现的表名应用其列映射）
            for old, new in rename_pairs:
                if new in vals[2] or new in vals[3]:
                    for oc, nc in sorted(col_maps[new].items(), key=lambda kv: -len(kv[0])):
                        vals[2] = re.sub(rf'\b{re.escape(oc)}\b', nc, vals[2])
                        vals[3] = re.sub(rf'\b{re.escape(oc)}\b', nc, vals[3])
            cur.execute('UPDATE schema_relationship_docs SET title=%s, path=%s, join_conditions=%s, doc_text=%s '
                        'WHERE id=%s', (*vals, rid))
    if not dry_run:
        gov.commit()

    # 4) keyword_table_map：改名；被删表行删除
    if not dry_run:
        with gov.cursor() as cur:
            for old, new in rename_pairs:
                cur.execute('UPDATE keyword_table_map SET table_name=%s WHERE table_name=%s', (new, old))
            for t in DROP_TABLES:
                cur.execute('DELETE FROM keyword_table_map WHERE table_name=%s', (t,))
        gov.commit()
    else:
        print('  [dry] keyword_table_map 改名 ×16 + 删 1 表行')

    # 5) ingest_provenance：文本替换（被删表行保留作溯源历史）
    if not dry_run:
        with gov.cursor() as cur:
            for old, new in rename_pairs:
                cur.execute('UPDATE ingest_provenance SET target_key=REPLACE(target_key, %s, %s) '
                            'WHERE target_key LIKE %s', (old, new, f'%{old}%'))
                cur.execute('UPDATE ingest_provenance SET field_value=REPLACE(field_value, %s, %s) '
                            'WHERE field_value LIKE %s', (old, new, f'%{old}%'))
        gov.commit()
    else:
        print('  [dry] ingest_provenance REPLACE ×16 表')

    # 6) qa_pairs + report_templates：SQL 改写 + 只读验证
    from core.sql_exec import safe_execute_sql

    def rewrite_sql(sql):
        s2 = sql
        for old, new in rename_pairs:
            if old in s2:
                s2 = s2.replace(old, new)
                for oc, nc in sorted(col_maps[new].items(), key=lambda kv: -len(kv[0])):
                    s2 = re.sub(rf'\b{re.escape(oc)}\b', nc, s2)
        for dt, rw in DROPPED_TABLE_REWRITE.items():
            if dt in s2:
                s2 = s2.replace(dt, rw['table'])
                for oc, nc in rw['cols'].items():
                    s2 = re.sub(rf'\b{re.escape(oc)}\b', nc, s2)
        return s2

    with gov.cursor() as cur:
        cur.execute("SELECT id, standard_sql, is_usable FROM qa_pairs WHERE standard_sql LIKE '%ads\\_%'")
        qas = cur.fetchall()
    for qid, sql, usable in qas:
        new_sql = rewrite_sql(sql)
        if new_sql == sql:
            continue
        if dry_run:
            print(f'  [dry] qa#{qid} SQL 改写')
            continue
        check = safe_execute_sql(new_sql)
        ok = check.get('success')
        with gov.cursor() as cur:
            cur.execute('UPDATE qa_pairs SET standard_sql=%s, is_usable=%s WHERE id=%s',
                        (new_sql, 1 if (ok and usable) else (0 if not ok else usable), qid))
        gov.commit()
        print(f'  [qa#{qid}] 改写 + 验证{"通过" if ok else "失败→is_usable=0"}: {str(check.get("error"))[:80] if not ok else ""}')

    with gov.cursor() as cur:
        cur.execute("SELECT id, outline FROM report_templates WHERE outline LIKE '%ads\\_%'")
        tpls = cur.fetchall()
    for tid, outline in tpls:
        new_outline = rewrite_sql(outline)
        if new_outline == outline:
            continue
        if dry_run:
            print(f'  [dry] report_templates#{tid} outline 改写')
            continue
        # 校验 outline 内嵌 standard_sql
        fails = 0
        for m in re.finditer(r'"standard_sql":\s*"(.*?)"\s*[,}]', new_outline, re.DOTALL):
            import json as _json
            try:
                sql_txt = _json.loads('{"s": "' + m.group(1) + '"}')['s']
            except Exception:
                continue
            if sql_txt.strip() and not safe_execute_sql(sql_txt).get('success'):
                fails += 1
        with gov.cursor() as cur:
            cur.execute('UPDATE report_templates SET outline=%s, updated_at=%s WHERE id=%s',
                        (new_outline, datetime.now(), tid))
        gov.commit()
        print(f'  [tpl#{tid}] outline 改写，内嵌 SQL 验证失败数={fails}（失败问题由模板 SQL 再生成链路补齐）')


# ==================== 主流程 ====================

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--skip-ontology', action='store_true', help='只迁移物理+治理库，不动本体库')
    args = ap.parse_args()

    biz = connect(DB_BIZ)
    print(f'== 步骤 1/4：物理迁移（{"DRY-RUN" if args.dry_run else "执行"}）==')
    for spec in TABLES:
        migrate_table(biz, spec, args.dry_run)
    drop_table(biz, args.dry_run)

    print('== 步骤 2/4：治理库同步 ==')
    sync_governance(biz, args.dry_run)

    print('== 步骤 3/4：本体库同步 ==')
    if args.dry_run or args.skip_ontology:
        print('  [skip] dry-run 或 --skip-ontology')
    else:
        # entity_defs：member_tables 改名（被删表从数组剔除）+ name 改名（report 层实体以表名为实体名）
        ont = connect('database01_ontology')
        import json as _json
        name_map = {s['old']: s['new'] for s in TABLES}
        with ont.cursor() as cur:
            cur.execute('SELECT id, name, member_tables FROM ontology_entity_defs')
            defs = cur.fetchall()
            for did, dname, mt in defs:
                try:
                    arr = _json.loads(mt or '[]')
                except Exception:
                    arr = []
                arr2 = [t for t in (name_map.get(t, t) for t in arr) if t not in DROP_TABLES]
                new_name = name_map.get(dname, dname)
                if dname in DROP_TABLES:
                    cur.execute('DELETE FROM ontology_entity_defs WHERE id=%s', (did,))
                elif arr2 != arr or new_name != dname:
                    cur.execute('UPDATE ontology_entity_defs SET name=%s, member_tables=%s, updated_at=%s '
                                'WHERE id=%s', (new_name, _json.dumps(arr2, ensure_ascii=False),
                                                datetime.now(), did))
        ont.commit()
        print('  [ok] ontology_entity_defs（name + member_tables）已改名')
        # 走既有提案→审批路径重建换版
        from core.ontology.service import OntologyService
        svc = OntologyService()
        result = svc.rebuild_proposal()
        pid = result['proposal_id']
        ont_new = svc.approve(pid)
        print(f'  [ok] 本体提案 #{pid} 已批准生效，新版本 v{getattr(ont_new, "version", "?")}')

    print('== 步骤 4/4：残留校验 ==')
    if not args.dry_run:
        residue_check(biz)
    biz.close()
    print('[done] 标准化迁移完成')


def residue_check(biz):
    """治理库/本体库文本列扫描 17 个旧表名残留。

    预期留痕白名单：generation_logs / report_runs（运行历史不改）、
    ingest_provenance 中被删表行（溯源历史）、ontology_proposals（变更提案历史）。
    """
    old_names = [s['old'] for s in TABLES] + DROP_TABLES
    whitelist = {'generation_logs', 'report_runs', 'ontology_proposals'}
    bad = 0
    conns = [(DB_GOV, gov_conn()), ('database01_ontology', connect('database01_ontology'))]
    for db, conn in conns:
        with conn.cursor() as cur:
            cur.execute("SELECT table_name, column_name FROM information_schema.columns "
                        "WHERE table_schema=%s AND data_type IN ('text','mediumtext','varchar','char')", (db,))
            textcols = cur.fetchall()
            for t, c in textcols:
                if t in whitelist:
                    continue
                for old in old_names:
                    cur.execute(f"SELECT COUNT(*) FROM `{t}` WHERE `{c}` LIKE %s", (f'%{old}%',))
                    n = cur.fetchone()[0]
                    if n:
                        expected = (t == 'ingest_provenance' and old in DROP_TABLES)
                        tag = '（预期：被删表溯源历史）' if expected else '  <<< 异常残留'
                        if not expected:
                            bad += n
                        print(f'  [residue] {db}.{t}.{c} ~ {old}: {n}{tag}')
    print(f'  残留校验：{"通过" if bad == 0 else f"异常 {bad} 处"}')


if __name__ == '__main__':
    main()
