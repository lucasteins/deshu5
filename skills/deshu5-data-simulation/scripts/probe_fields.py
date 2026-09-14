#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""针对性探针：打印给定 (表,列) 在真实库的取值分布与码表候选。

只读，不写库。用于修复码值口径前对齐"真实库到底存什么形态"。
用法：python probe_fields.py
"""
import os
import sys
import json
import pymysql

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
sys.path.insert(0, HERE)

CODEBOOK = os.path.join(HERE, '..', 'references', 'codebook.json')

DB = 'fz01'
TAG_MARK = '模拟'   # 仿真数据的标记串（用于排除仿真行，只看真实行）

FIELDS = [
    # (表, 列, 码表名 or None)
    ('dwd_cst_dev_rcpt_app_dtl_info', 'dev_use_stat', 'dev_use_stat'),
    ('dwd_cst_dev_rcpt_app_dtl_info', 'dev_use_stat_desc', None),
    ('dwd_cst_invest_order', 'arch_status', None),
    ('dwd_cst_invest_order', 'arch_status_desc', None),
    ('dwd_cst_invest_order', 'bld_mode', 'bld_mode'),
    ('dwd_cst_invest_order', 'bld_mode_desc', None),
    ('dwd_cst_invest_order', 'dist_lv', 'dist_lv'),
    ('dwd_cst_invest_order', 'dist_lv_desc', None),
    ('dwd_cst_invest_order', 'county_code', None),
    ('dwd_cst_invest_order', 'app_elec_categ', None),
    ('dwd_cst_invest_order', 'app_elec_categ_desc', None),
    ('dwd_cst_invest_order', 'app_ind_cls', None),
    ('dwd_cst_invest_order', 'land_char', None),
    ('dwd_cst_invest_order', 'voltage', None),
    ('dwd_cst_wkorder_affilmtrl_rec', 'wk_order_affil_mtrl_rec_cls', 'wk_order_affil_mtrl_rec_cls'),
    ('dwd_cst_wkorder_affilmtrl_rec', 'wk_order_affil_mtrl_rec_cls_desc', None),
    ('dwd_cst_wkorder_affilmtrl_rec', 'spot_chk_flag', 'spot_chk_flag'),
    ('dwd_cst_wkorder_affilmtrl_rec', 'spot_chk_flag_desc', None),
    ('dwd_cst_wkorder_affilmtrl_rec', 'cstr_rec', 'cstr_rec'),
    ('dwd_cst_wkorder_affilmtrl_rec', 'cstr_rec_desc', None),
    ('dwd_cst_wkorder_affilmtrl_rec', 'app_mode', 'app_mode'),
    ('dwd_cst_wkorder_affilmtrl_rec', 'app_mode_desc', None),
    ('dwd_cst_wkorder_affilmtrl_rec', 'valid_flag', None),
    ('dwd_cst_wkorder_affilmtrl_rec', 'veri_rslt', None),
    ('dwd_cst_wkorder_affilmtrl_rec', 'proj_categ', None),
    ('dwd_cst_gc_app_rec', 'cust_pscateg', 'cust_pscateg'),
    ('dwd_cst_gc_app_rec', 'cust_pscateg_desc', None),
    ('dwd_cst_gc_app_rec', 'plant_type', 'plant_type'),
    ('dwd_cst_gc_app_rec', 'plant_type_desc', None),
    ('dwd_cst_gc_app_rec', 'invest_mode', 'invest_mode'),
    ('dwd_cst_gc_app_rec', 'invest_mode_desc', None),
    ('dwd_cst_gc_app_rec', 'urbanrural_flag', 'urbanrural_flag'),
    ('dwd_cst_gc_app_rec', 'urbanrural_flag_desc', None),
    ('dwd_cst_gc_app_rec', 'srv_form', 'srv_form'),
    ('dwd_cst_gc_app_rec', 'srv_form_desc', None),
    ('dwd_cst_gc_app_rec', 'impt_lv', 'impt_lv'),
    ('dwd_cst_gc_app_rec', 'impt_lv_desc', None),
    ('dwd_cst_gc_app_rec', 'app_stat', 'app_stat'),
    ('dwd_cst_gc_app_rec', 'app_stat_desc', None),
    ('dwd_cst_gc_app_rec', 'proc_stat', 'proc_stat'),
    ('dwd_cst_gc_app_rec', 'proc_stat_desc', None),
    ('dwd_cst_gc_app_rec', 'wk_order_stat', 'wk_order_stat'),
    ('dwd_cst_gc_app_rec', 'wk_order_stat_desc', None),
    ('dwd_cst_gc_app_rec', 'bus_type', 'bus_type'),
    ('dwd_cst_gc_app_rec', 'bus_categ', 'bus_categ'),
    ('dwd_cst_gc_app_rec', 'arch_status', None),
    ('dwd_cst_gc_app_rec', 'arch_status_desc', None),
    ('dwd_cst_gc_app_rec', 'gc_stat', None),
    ('dwd_cst_gc_app_rec', 'gc_type', None),
    ('dwd_cst_conn_gen_power_app', 'lay_mode', 'lay_mode'),
    ('dwd_cst_conn_gen_power_app', 'lay_mode_desc', None),
    ('dwd_cst_conn_gen_power_app', 'run_mode', 'run_mode'),
    ('dwd_cst_conn_gen_power_app', 'run_mode_desc', None),
    ('dwd_cst_conn_gen_power_app', 'ps_dev_type', 'ps_dev_type'),
    ('dwd_cst_conn_gen_power_app', 'ps_dev_type_desc', None),
    ('dwd_cst_conn_gen_power_app', 'protect_mode', None),
    ('dwd_cst_conn_gen_power_app', 'supl_type', None),
    ('dwd_cst_bus_app_form', 'srv_kind', None),
    ('dwd_cst_bus_app_form', 'app_stat', None),
    ('dwd_cst_bus_app_form', 'county_code', None),
    ('dwd_cst_bus_app_form', 'pro_mgt_org_code', None),
    ('dim_cst_dev', 'dev_stat', 'dev_stat'),
    ('dim_cst_dev', 'dev_stat_desc', None),
    ('dim_equ_t_p_pd_linepsr', 'run_status', None),
    ('dim_equ_t_p_pd_linepsr', 'run_status_desc', None),
    ('dim_equ_t_p_pd_linepsr', 'dispatch_level', 'dispatch_level'),
    ('dim_equ_t_p_pd_linepsr', 'cable_layout_mode', None),
    ('dim_ast_t_p_pd_feederlinepsr', 'dispatch_level', 'dispatch_level'),
    ('dim_grid_t_ts_sg_da_dev_generator_b', 'fuel_type', 'fuel_type'),
    ('dim_grid_t_ts_sg_da_con_substation_b', 'type', None),
    ('dim_cst_pipeline', 'publ_clg_flag_desc', None),
    ('dim_cst_settle_acct', 'pay_mode_desc', None),
    ('dwd_cst_addl_charg', 'rpt_ec_categ', None),
    ('dwd_cst_mr_data', 'read_type', None),
    ('dwd_cst_a_ll_dist_det_day', 'inst_lv', None),
    ('dim_cst_business_partner', 'bp_type_desc', None),
    ('dim_cst_business_partner', 'bp_categ_desc', None),
    ('dwd_cst_step_rec', 'step_stat', 'step_stat'),
    ('dwd_cst_bilg_rs_rcpt', 'rs_cls', None),
    ('dwd_cst_connection_app_rec', 'voltage_desc', None),
]


def main():
    cb = json.load(open(CODEBOOK, encoding='utf-8'))
    codes = cb.get('codes', {})
    cols_map = cb.get('columns', {})
    wilds = cb.get('wildcards', {})

    pw = os.environ.get('MYSQL_PASSWORD', '')
    if not pw:
        for env in (os.path.join(ROOT, '.env'),
                    r'D:\codex\deshu5\.env'):
            if os.path.exists(env):
                for ln in open(env, encoding='utf-8'):
                    if ln.strip().startswith('MYSQL_PASSWORD'):
                        pw = ln.split('=', 1)[1].strip().strip('"').strip("'")
                break
    conn = pymysql.connect(host='localhost', port=3306, user='root', password=pw,
                           database=DB, charset='utf8mb4', cursorclass=pymysql.cursors.DictCursor)

    def real_vals(t, c, limit=8):
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT `{c}` v, COUNT(*) n FROM `{t}` WHERE `{c}` IS NOT NULL "
                f"AND `{c}`<>'' AND `{c}`<>'\\\\N' "
                f"AND (`{c}` NOT LIKE %s) GROUP BY `{c}` ORDER BY n DESC LIMIT {limit}",
                (f'%{TAG_MARK}%',))
            return cur.fetchall()

    for t, c, cn in FIELDS:
        print('=' * 78)
        print(f'{t}.{c}')
        refs, form = [], ''
        v = cols_map.get(f'{t}.{c}')
        if v:
            refs, form = v.get('codes', []), v.get('form', '')
        else:
            v = wilds.get(c)
            if v:
                refs, form = v.get('codes', []), v.get('form', '')
        print(f'  码表映射: {refs}  form={form or "-"}')
        for cn2 in (refs or ([cn] if cn else []))[:2]:
            it = codes.get(cn2, {}).get('items', [])
            print(f'    [{cn2}] {[(i["code"], i["name"]) for i in it[:10]]}')
        try:
            rv = real_vals(t, c)
        except Exception as e:
            print('  真实值: <查询失败>', e)
            continue
        if not rv:
            print('  真实值: <空>')
        else:
            print('  真实值:')
            for r in rv:
                print(f'    {r["v"]!r:<28} x{r["n"]}')
    conn.close()


if __name__ == '__main__':
    main()
