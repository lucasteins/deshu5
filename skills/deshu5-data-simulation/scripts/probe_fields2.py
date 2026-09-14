#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""探针 2：定位 A/B 级字段在真实库的真实形态。只读。"""
import os
import sys
import json
import pymysql

HERE = os.path.dirname(os.path.abspath(__file__))
CODEBOOK = os.path.join(HERE, '..', 'references', 'codebook.json')
DB = 'fz01'
TAG = '模拟'

FIELDS = [
    ('dwd_cst_bus_app_form', 'bus_type'),
    ('dwd_cst_bus_app_form', 'bus_type_desc'),
    ('dwd_cst_bus_app_form', 'bus_categ'),
    ('dwd_cst_bus_app_form', 'bus_categ_desc'),
    ('dwd_cst_bus_app_form', 'app_stat_desc'),
    ('dwd_cst_preapp_order', 'bus_type'),
    ('dwd_cst_preapp_order', 'bus_type_desc'),
    ('dwd_cst_cust_elec_app_rec', 'bus_type'),
    ('dwd_cst_wk_order', 'bus_type'),
    ('dwd_cst_wk_order', 'bus_type_desc'),
    ('dwd_cst_wk_order', 'wk_order_type'),
    ('dwd_cst_wk_order', 'wk_order_type_desc'),
    ('dwd_cst_wk_order', 'wk_order_stat_desc'),
    ('dwd_cst_inst_elec_sch', 'bus_type'),
    ('dwd_cst_step_rec', 'step_stat_desc'),
    ('dwd_cst_dev_inst_rmv_wk_rec', 'inst_rmv_categ'),
    ('dwd_cst_dev_inst_rmv_wk_rec', 'inst_rmv_categ_desc'),
    ('dwd_cst_conn_gen_power_app', 'county_code'),
    ('dwd_cst_gc_app_rec', 'county_code'),
    ('dwd_cst_invest_order', 'county_name'),
    ('dwd_cst_connection_app_rec', 'county_code'),
    ('dim_cst_pipeline', 'path_org_code'),
    ('dim_cst_pipeline', 'publ_clg_flag'),
    ('dim_cst_business_partner', 'bp_type'),
    ('dim_cst_business_partner', 'bp_categ'),
    ('dwd_cst_bilg_rs_rcpt', 'rs_cls_desc'),
    ('dwd_cst_mr_data', 'read_type'),
    ('dim_equ_t_p_pd_linepsr', 'cable_layout_mode_desc'),
    ('dim_ast_t_tg_linepsr', 'belong_type'),
    ('dwd_cst_invest_order', 'ind_cls_desc'),
    ('dwd_cst_invest_order', 'land_char_desc'),
    ('dwd_cst_dev_rcpt_app_dtl_info', 'data_src_desc'),
    ('dwd_cst_dev_rcpt_app_dtl_info', 'return_flag_desc'),
    ('dwd_cst_dev_rcpt_app', 'dev_cls_desc'),
    ('dwd_cst_dev_rcpt_app', 'valid_flag_desc'),
    ('dwd_cst_acc_sch', 'agrt_categ_desc'),
]

# 需要顺带确认 pro_mgt_org_code 在哪张表
SCAN = [('dwd_cst_mr_data', 'pro_mgt_org_code'), ('dim_cst_elec_cons_cust', 'pro_mgt_org_code'),
        ('dwd_cst_wk_order', 'pro_mgt_org_code'), ('dwd_cst_bus_app_form', 'mgt_org_code')]


def main():
    cb = json.load(open(CODEBOOK, encoding='utf-8'))
    codes = cb.get('codes', {})
    cols_map = cb.get('columns', {})
    wilds = cb.get('wildcards', {})
    pw = os.environ.get('MYSQL_PASSWORD', '')
    if not pw:
        for env in (r'D:\codex\deshu5\.env',):
            if os.path.exists(env):
                for ln in open(env, encoding='utf-8'):
                    if ln.strip().startswith('MYSQL_PASSWORD'):
                        pw = ln.split('=', 1)[1].strip()
    conn = pymysql.connect(host='localhost', port=3306, user='root', password=pw,
                           database=DB, charset='utf8mb4', cursorclass=pymysql.cursors.DictCursor)

    def real(t, c, limit=10):
        with conn.cursor() as cur:
            cur.execute(f"SELECT `{c}` v, COUNT(*) n FROM `{t}` WHERE `{c}` IS NOT NULL "
                        f"AND `{c}`<>'' AND `{c}`<>'\\\\N' AND `{c}` NOT LIKE %s "
                        f"GROUP BY `{c}` ORDER BY n DESC LIMIT {limit}", (f'%{TAG}%',))
            return cur.fetchall()

    for t, c in FIELDS + SCAN:
        print('-' * 74)
        print(f'{t}.{c}')
        refs, form = [], ''
        v = cols_map.get(f'{t}.{c}')
        if v:
            refs, form = v.get('codes', []), v.get('form', '')
        else:
            v = wilds.get(c)
            if v:
                refs, form = v.get('codes', []), v.get('form', '')
        print(f'  码表: {refs} form={form or "-"}')
        for r_ in refs[:1]:
            it = codes.get(r_, {}).get('items', [])
            print(f'    [{r_}] {[(i["code"], i["name"]) for i in it[:12]]}')
        try:
            for row in real(t, c):
                print(f'  real  {row["v"]!r:<30} x{row["n"]}')
        except Exception as e:
            print('  real  <失败>', e)
    conn.close()


if __name__ == '__main__':
    main()
