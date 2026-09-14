#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""真实值 vs 仿真值 对照（严格排除仿真行）。

按台账主键集合建临时表，用 NOT EXISTS 排除仿真行，取真实库去重取值；
再打印码表候选与仿真值，供修复时逐字段定口径。

用法：python real_vs_sim.py [--godb ...] [--manifest ...]
"""
import os
import re
import sys
import json
import argparse
import pymysql

HERE = os.path.dirname(os.path.abspath(__file__))
CODEBOOK = os.path.join(HERE, '..', 'references', 'codebook.json')
DEFAULT_MANIFEST = os.path.join(HERE, 'manifest', 'fz01__模拟__20260912_185637.json')

# 待核查字段：(表, 列)  只列不合规字段
TARGETS = [
    ('dwd_cst_a_ll_dist_det_day', 'pro_mgt_org_code'),
    ('dwd_cst_bus_app_form', 'county_code'), ('dwd_cst_bus_app_form', 'county_name'),
    ('dwd_cst_bus_app_form', 'bus_type'), ('dwd_cst_bus_app_form', 'bus_type_desc'),
    ('dwd_cst_bus_app_form', 'bus_categ'), ('dwd_cst_bus_app_form', 'bus_categ_desc'),
    ('dwd_cst_bus_app_form', 'app_stat_desc'),
    ('dwd_cst_cust_elec_app_rec', 'bus_type'), ('dwd_cst_cust_elec_app_rec', 'bus_type_desc'),
    ('dwd_cst_preapp_order', 'bus_type'), ('dwd_cst_preapp_order', 'bus_type_desc'),
    ('dwd_cst_preapp_order', 'bus_categ_desc'),
    ('dwd_cst_inst_elec_sch', 'county_code'), ('dwd_cst_inst_elec_sch', 'county_name'),
    ('dwd_cst_wk_order', 'county_code'), ('dwd_cst_wk_order', 'county_name'),
    ('dwd_cst_wk_order', 'wk_order_type'), ('dwd_cst_wk_order', 'wk_order_type_desc'),
    ('dwd_cst_wk_order', 'wk_order_stat_desc'),
    ('dwd_cst_invest_order', 'county_code'), ('dwd_cst_invest_order', 'county_name'),
    ('dwd_cst_invest_order', 'bld_mode'), ('dwd_cst_invest_order', 'app_elec_categ'),
    ('dwd_cst_invest_order', 'app_elec_categ_desc'), ('dwd_cst_invest_order', 'app_ind_cls'),
    ('dwd_cst_invest_order', 'land_char'),
    ('dwd_cst_conn_gen_power_app', 'county_code'), ('dwd_cst_conn_gen_power_app', 'county_name'),
    ('dwd_cst_conn_gen_power_app', 'lay_mode'), ('dwd_cst_conn_gen_power_app', 'lay_mode_desc'),
    ('dwd_cst_conn_gen_power_app', 'run_mode'), ('dwd_cst_conn_gen_power_app', 'run_mode_desc'),
    ('dwd_cst_conn_gen_power_app', 'ps_dev_type'), ('dwd_cst_conn_gen_power_app', 'ps_dev_type_desc'),
    ('dwd_cst_gc_app_rec', 'county_code'), ('dwd_cst_gc_app_rec', 'county_name'),
    ('dwd_cst_gc_app_rec', 'bus_type'), ('dwd_cst_gc_app_rec', 'bus_type_desc'),
    ('dwd_cst_gc_app_rec', 'bus_categ'), ('dwd_cst_gc_app_rec', 'bus_categ_desc'),
    ('dwd_cst_gc_app_rec', 'cust_pscateg'), ('dwd_cst_gc_app_rec', 'cust_pscateg_desc'),
    ('dwd_cst_gc_app_rec', 'plant_type'), ('dwd_cst_gc_app_rec', 'plant_type_desc'),
    ('dwd_cst_gc_app_rec', 'invest_mode'), ('dwd_cst_gc_app_rec', 'invest_mode_desc'),
    ('dwd_cst_gc_app_rec', 'urbanrural_flag'), ('dwd_cst_gc_app_rec', 'urbanrural_flag_desc'),
    ('dwd_cst_gc_app_rec', 'srv_form'), ('dwd_cst_gc_app_rec', 'srv_form_desc'),
    ('dwd_cst_gc_app_rec', 'impt_lv'), ('dwd_cst_gc_app_rec', 'impt_lv_desc'),
    ('dwd_cst_gc_app_rec', 'app_stat_desc'), ('dwd_cst_gc_app_rec', 'proc_stat'),
    ('dwd_cst_gc_app_rec', 'proc_stat_desc'), ('dwd_cst_gc_app_rec', 'wk_order_stat_desc'),
    ('dwd_cst_gc_app_rec', 'dist_lv_desc'), ('dwd_cst_gc_app_rec', 'app_mode'),
    ('dwd_cst_gc_app_rec', 'app_mode_desc'), ('dwd_cst_gc_app_rec', 'arch_status'),
    ('dwd_cst_gc_app_rec', 'arch_status_desc'),
    ('dwd_cst_conn_gen_power_app', 'arch_status'),
    ('dwd_cst_wkorder_affilmtrl_rec', 'wk_order_affil_mtrl_rec_cls_desc'),
    ('dwd_cst_wkorder_affilmtrl_rec', 'proj_categ_desc'),
    ('dwd_cst_wkorder_affilmtrl_rec', 'app_mode'), ('dwd_cst_wkorder_affilmtrl_rec', 'app_mode_desc'),
    ('dwd_cst_wkorder_affilmtrl_rec', 'cstr_rec'), ('dwd_cst_wkorder_affilmtrl_rec', 'cstr_rec_desc'),
    ('dwd_cst_wkorder_affilmtrl_rec', 'spot_chk_flag'), ('dwd_cst_wkorder_affilmtrl_rec', 'spot_chk_flag_desc'),
    ('dwd_cst_stop_rcvr_supl_app', 'stop_rcvr_supl_categ_desc'),
    ('dwd_cst_dev_inst_rmv_wk_rec', 'inst_rmv_categ_desc'),
    ('dwd_cst_dev_rcpt_app_dtl_info', 'dev_use_stat_desc'),
    ('dwd_cst_dev_rcpt_app', 'rcv_ret_flag'),
    ('dwd_cst_step_rec', 'step_stat'),
    ('dwd_cst_bilg_rs_rcpt', 'rs_cls'), ('dwd_cst_bilg_rs_rcpt', 'hndl_stat'),
    ('dim_cst_pipeline', 'path_org_code'),
    ('dim_cst_business_partner', 'bp_type_desc'), ('dim_cst_business_partner', 'bp_categ_desc'),
    ('dim_cst_dev', 'dev_stat_desc'),
    ('dim_equ_t_p_pd_linepsr', 'run_status_desc'), ('dim_equ_t_p_pd_linepsr', 'dispatch_level'),
    ('dim_equ_t_p_pd_linepsr', 'cable_layout_mode'), ('dim_equ_t_p_pd_linepsr', 'conductor_layout_mode'),
    ('dim_ast_t_p_pd_feederlinepsr', 'dispatch_level'),
    ('dim_ast_t_p_dy_stationzonepsr', 'belong_type'),
    ('dim_ast_t_odps_sync_v_p_pd_polesitepsr', 'belong_type'),
    ('dim_ast_t_odps_sync_v_p_pd_polesitepsr', 'conductor_layout_mode'),
    ('dim_equ_t_p_pd_stationpsr', 'power_supply_type'),
    ('dim_grid_t_ts_sg_da_dev_generator_b', 'fuel_type'),
    ('dim_grid_t_ts_sg_da_con_substation_b', 'type'),
    ('dim_grid_t_ts_sg_da_acline_b', 'linetype'),
]


def load_env():
    pw = os.environ.get('MYSQL_PASSWORD', '')
    if not pw:
        for p in (r'D:\codex\deshu5\.env', os.path.join(HERE, '..', '..', '..', '..', '.env')):
            if os.path.exists(p):
                for ln in open(p, encoding='utf-8'):
                    m = re.match(r'\s*MYSQL_PASSWORD\s*=\s*(.*)', ln)
                    if m:
                        pw = m.group(1).strip().strip('"').strip("'")
                break
    return pw


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', default='fz01')
    ap.add_argument('--manifest', default=DEFAULT_MANIFEST)
    ap.add_argument('--ont-db', default='fz01_ontology')
    ap.add_argument('--gov-db', default='fz01_governance')
    ap.add_argument('--out', default=None)
    args = ap.parse_args()

    man = json.load(open(args.manifest, encoding='utf-8'))
    tabs = man['tables']
    cb = json.load(open(CODEBOOK, encoding='utf-8'))
    codes = cb['codes']
    cols_map = cb['columns']
    wilds = cb['wildcards']

    conn = pymysql.connect(host='localhost', port=3306, user='root', password=load_env(),
                           database=args.db, charset='utf8mb4',
                           cursorclass=pymysql.cursors.DictCursor)
    cur = conn.cursor()
    out = []
    for t, c in TARGETS:
        if t not in tabs:
            print(f'!! 台账无此表: {t}')
            continue
        info = tabs[t]
        pk = info['pk']
        pks = info.get('pks') or []
        coll = 'utf8mb4_general_ci'
        cur.execute("SELECT DATA_TYPE FROM information_schema.COLUMNS WHERE "
                    "TABLE_SCHEMA=%s AND TABLE_NAME=%s AND COLUMN_NAME=%s",
                    (args.db, t, pk))
        r0 = cur.fetchone()
        num = (r0['DATA_TYPE'] if r0 else 'varchar') in (
            'int', 'bigint', 'smallint', 'tinyint', 'mediumint', 'decimal')
        cur.execute("DROP TEMPORARY TABLE IF EXISTS `_pk2_`")
        cur.execute("CREATE TEMPORARY TABLE `_pk2_` (k %s %s PRIMARY KEY)"
                    % ('BIGINT' if num else 'VARCHAR(96)', '' if num else 'COLLATE ' + coll))
        for s in range(0, len(pks), 5000):
            cur.executemany("INSERT IGNORE INTO `_pk2_` (k) VALUES (%s)",
                            [(x if num else str(x),) for x in pks[s:s + 5000]])
        cond = (f"t.`{pk}` = p.k" if num
                else f"CAST(t.`{pk}` AS CHAR) COLLATE {coll} = p.k")
        cur.execute(f"SELECT CAST(t.`{c}` AS CHAR) v, COUNT(*) n FROM `{t}` t "
                    f"WHERE NOT EXISTS(SELECT 1 FROM `_pk2_` p WHERE {cond}) "
                    f"GROUP BY t.`{c}` ORDER BY n DESC LIMIT 40")
        real = [(x['v'], x['n']) for x in cur.fetchall()]
        cur.execute(f"SELECT CAST(t.`{c}` AS CHAR) v, COUNT(*) n FROM `{t}` t "
                    f"WHERE EXISTS(SELECT 1 FROM `_pk2_` p WHERE {cond}) "
                    f"GROUP BY t.`{c}` ORDER BY n DESC LIMIT 40")
        sim = [(x['v'], x['n']) for x in cur.fetchall()]
        refs, form = [], ''
        v = cols_map.get(f'{t}.{c}')
        if v:
            refs, form = v.get('codes', []), v.get('form', '')
        else:
            v = wilds.get(c)
            if v:
                refs, form = v.get('codes', []), v.get('form', '')
        out.append({'table': t, 'column': c, 'refs': refs, 'form': form,
                    'real': real, 'sim': sim})

    conn.close()
    for r in out:
        print('=' * 90)
        print(f"{r['table']}.{r['column']}   码表={r['refs']} form={r['form'] or '-'}")
        for cn in r['refs'][:1]:
            it = codes.get(cn, {}).get('items', [])
            print(f"  [{cn}] n={len(it)}  {[(i['code'], i['name']) for i in it[:14]]}")
        print('  REAL:', [(v, n) for v, n in r['real'][:20]])
        print('  SIM :', [(v, n) for v, n in r['sim'][:20]])
    if args.out:
        json.dump(out, open(args.out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        print('written', args.out)


if __name__ == '__main__':
    main()
