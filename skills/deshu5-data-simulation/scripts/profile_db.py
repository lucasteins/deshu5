# -*- coding: utf-8 -*-
"""profile_db.py — 从 deshu5 MySQL 反向勘探，产出 profiles.json 作为仿真拟合基准。

用法：
    python profile_db.py [--db fz01] [--out references/profiles.json]

用途：仿真引擎不硬编码"想当然"的取值，而是先读真实库的分布（区县清单、电压等级、
容量档位、倍率、电价码、类别占比、ID 形态），再按同样分布抽样。库结构演进后重跑本
脚本即可让仿真跟上，无需改代码。
"""
import argparse, json, os, sys
from collections import Counter, defaultdict

try:
    import pymysql
except ImportError:
    sys.exit("需要 pymysql：pip install pymysql")

CONN = dict(host=os.environ.get('MYSQL_HOST', 'localhost'),
            port=int(os.environ.get('MYSQL_PORT', '3306')),
            user=os.environ.get('MYSQL_USER', 'root'),
            password=os.environ.get('MYSQL_PASSWORD', ''), charset='utf8mb4')


def q(db, sql, args=None):
    c = pymysql.connect(database=db, **CONN)
    try:
        with c.cursor() as cur:
            cur.execute(sql, args)
            return cur.fetchall()
    finally:
        c.close()


def dist(db, table, col, limit=40):
    """返回 [(值, 计数, 占比)]，按计数降序。"""
    rows = q(db, f"SELECT `{col}`, COUNT(*) c FROM `{table}` WHERE `{col}` IS NOT NULL "
                 f"AND `{col}` <> '' GROUP BY `{col}` ORDER BY c DESC LIMIT {limit}")
    total = sum(r[1] for r in rows) or 1
    return [{"v": r[0], "n": r[1], "p": round(r[1] / total, 4)} for r in rows]


def idshape(db, table, col):
    """返回 ID 的形态：最小/最大/长度分布。"""
    try:
        rows = q(db, f"SELECT MIN(`{col}`), MAX(`{col}`), COUNT(*) FROM `{table}` WHERE `{col}` IS NOT NULL")
        lens = q(db, f"SELECT CHAR_LENGTH(`{col}`) L, COUNT(*) c FROM `{table}` WHERE `{col}` IS NOT NULL "
                     f"GROUP BY L ORDER BY c DESC LIMIT 5")
        return {"min": rows[0][0], "max": rows[0][1], "n": rows[0][2],
                "lens": {str(r[0]): r[1] for r in lens}}
    except Exception as e:
        return {"error": str(e)[:80]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', default='fz01')
    ap.add_argument('--out', default=os.path.join(os.path.dirname(__file__),
                                                  '..', 'references', 'profiles.json'))
    a = ap.parse_args()
    db = a.db
    P = {"_source_db": db, "_note": "由 profile_db.py 从真实库反向勘探生成，仿真抽取时按此分布拟合"}

    # ---------- 1. 组织机构：区县供电公司（仿真锚点） ----------
    counties = []
    for r in q(db, "SELECT mgt_org_code, mgt_org_name, org_level, region_adcode, region_name "
                   "FROM dim_org_region_map WHERE org_level='区县' ORDER BY mgt_org_code"):
        counties.append({"mgt_org_code": r[0], "mgt_org_name": r[1],
                         "region_adcode": r[3], "region_name": r[4]})
    # 补地市/省信息（从 dim_cst_mgt_org 反查）
    orgrows = q(db, "SELECT mgt_org_code, mgt_org_name, city_code, city_name, county_code, "
                    "county_name, province_code, province_name FROM dim_cst_mgt_org "
                    "WHERE dist_lv_desc='区县' AND mgt_org_type_desc='单位'")
    bycode = {r[0]: r for r in orgrows}
    for cty in counties:
        r = bycode.get(cty['mgt_org_code'])
        if r:
            cty.update({"city_code": r[2], "city_name": r[3], "county_code": r[4],
                        "county_name": r[5], "province_code": r[6], "province_name": r[7]})
        else:
            cty.update({"city_code": None, "city_name": None, "county_code": None,
                        "county_name": cty['mgt_org_name'], "province_code": None,
                        "province_name": None})
    P['counties'] = counties
    P['county_count'] = len(counties)

    # ---------- 2. 电网侧主数据取值分布 ----------
    P['voltage'] = {
        'cntrl_sta': dist(db, 'dim_cst_cntrl_sta', 'voltage_desc'),
        'pipeline': dist(db, 'dim_cst_pipeline', 'voltage_desc'),
        'dist_sta': dist(db, 'dim_cst_dist_sta', 'voltage_desc'),
        'adj_volt_dev_primary': dist(db, 'dim_cst_adj_volt_dev', 'primary_side_bearing_desc'),
        'adj_volt_dev_secondary': dist(db, 'dim_cst_adj_volt_dev', 'secondary_side_bearing_desc'),
    }
    P['capacity'] = {
        'cntrl_sta_kva': dist(db, 'dim_cst_cntrl_sta', 'dev_cap'),
        'dist_sta_kva': dist(db, 'dim_cst_dist_sta', 'dist_stacap'),
        'transformer_kva': dist(db, 'dim_cst_adj_volt_dev', 'np_cap'),
    }
    P['flags'] = {
        'cntrl_sta_status': dist(db, 'dim_cst_cntrl_sta', 'resrc_supl_stat_desc'),
        'pipeline_run_stat': dist(db, 'dim_cst_pipeline', 'pip_line_run_stat_desc'),
        'pipeline_publ_clg': dist(db, 'dim_cst_pipeline', 'publ_clg_flag_desc'),
        'dist_sta_publ_clg': dist(db, 'dim_cst_dist_sta', 'publ_clg_flag_desc'),
        'dist_sta_status': dist(db, 'dim_cst_dist_sta', 'resrc_supl_stat_desc'),
        'adj_volt_dev_type': dist(db, 'dim_cst_adj_volt_dev', 'dev_type_desc'),
        'srv_kind': dist(db, 'dim_cst_cntrl_sta', 'srv_kind_desc'),
    }

    # ---------- 3. 客户侧主数据取值分布 ----------
    P['customer'] = {
        'cust_cls': dist(db, 'dim_cst_cust_agrt', 'cust_cls_desc'),
        'ec_categ': dist(db, 'dim_cst_cust_agrt', 'ec_categ_desc'),
        'cust_volt': dist(db, 'dim_cst_cust_agrt', 'cust_volt_desc'),
        'prc_code': dist(db, 'dim_cst_cust_agrt', 'prc_code'),
        'ctlg_prc_name': dist(db, 'dim_cst_cust_agrt', 'ctlg_prc_name', 20),
        'urbanrural': dist(db, 'dim_cst_cust_agrt', 'urbanrural_flag_desc'),
        'agrt_categ': dist(db, 'dim_cst_cust_agrt', 'agrt_categ_desc'),
        'urbanrural_categ': dist(db, 'dim_cst_cust', 'urbanrural_categ_desc'),
        'cust_ind_cls_desc': dist(db, 'dim_cst_cust', 'cust_ind_cls_desc'),
        'cust_ind_ustry_cls': dist(db, 'dim_cst_cust', 'cust_ind_ustry_cls'),
        'impt_lv': dist(db, 'dim_cst_cust', 'impt_lv_desc'),
        'settle_acct_categ': dist(db, 'dim_cst_settle_acct', 'settle_acct_categ_desc'),
        'pay_mode': dist(db, 'dim_cst_settle_acct', 'pay_mode_desc'),
        'bill_mode': dist(db, 'dim_cst_settle_acct', 'bill_mode_desc'),
        'inv_mode': dist(db, 'dim_cst_settle_acct', 'inv_mode_desc'),
        'note_type': dist(db, 'dim_cst_settle_acct', 'note_type_desc'),
    }

    # ---------- 4. 计量/设备 ----------
    P['metering'] = {
        'inst_cls_desc': dist(db, 'dim_cst_inst_elec_cons', 'inst_cls_desc'),
        'inst_char_desc': dist(db, 'dim_cst_inst_elec_cons', 'inst_char_desc'),
        'inst_usage_cls_desc': dist(db, 'dim_cst_inst_elec_cons', 'inst_usage_cls_desc'),
        'inst_affil_side_desc': dist(db, 'dim_cst_inst_elec_cons', 'inst_affil_side_desc'),
        'inst_cap': dist(db, 'dim_cst_inst_elec_cons', 'inst_cap', 20),
        'wire_mode_desc': dist(db, 'dim_cst_elec_meter', 'wire_mode_desc'),
        'rv_desc': dist(db, 'dim_cst_elec_meter', 'rv_desc'),
        'cali_cur_desc': dist(db, 'dim_cst_elec_meter', 'cali_cur_desc'),
        'bidi_flag': dist(db, 'dim_cst_elec_meter', 'bidi_meter_flag_desc'),
        'meter_categ': dist(db, 'dim_cst_dev', 'categ_desc'),
        'meter_dev_cls': dist(db, 'dim_cst_dev', 'dev_cls_desc'),
        'comp_rto': dist(db, 'dwd_cst_meter_run', 'comp_rto'),
        'meter_mode_desc': dist(db, 'dwd_cst_inst_elec_sch', 'meter_mode_desc'),
    }

    # ---------- 5. ID 形态 ----------
    P['id_shape'] = {
        'cntrl_sta_id': idshape(db, 'dim_cst_cntrl_sta', 'cntrl_sta_id'),
        'pipeline_id': idshape(db, 'dim_cst_pipeline', 'pipeline_id'),
        'dist_sta_id': idshape(db, 'dim_cst_dist_sta', 'dist_sta_id'),
        'cust_id': idshape(db, 'dim_cst_cust', 'cust_id'),
        'inst_id': idshape(db, 'dim_cst_inst_elec_cons', 'inst_id'),
        'dev_id': idshape(db, 'dim_cst_dev', 'dev_id'),
        'bilg_card_id': idshape(db, 'dwd_cst_inst_bilg_card', 'bilg_card_id'),
        'rcvbl_acct_id': idshape(db, 'dwd_cst_rcvbl_acct', 'rcvbl_acct_id'),
        'calc_id': idshape(db, 'dwd_cst_inst_bilg_card', 'calc_id'),
    }

    # ---------- 6. 业务数据口径（时间范围、费率档数、倍率） ----------
    P['business_span'] = {}
    for t, col in [('dwd_cst_inst_bilg_card', 'qty_charg_ym'), ('dwd_cst_rcvbl_acct', 'rcvbl_ym'),
                   ('dwd_cst_rcvd_acct', 'rcvd_ym'), ('dwd_cst_mr_data', 'qty_charg_ym'),
                   ('dwd_cst_charg_acct', 'charg_ym'), ('dwd_cst_es_meter_energy_day_l_xz', 'data_date'),
                   ('dwd_cst_meter_energy_day_h_xz', 'data_date'), ('dwd_cst_es_meter_energy_day_p', 'data_date'),
                   ('dwd_cst_a_ll_dist_det_day', 'stat_date'), ('dwd_cst_es_e_mp_comp_curve_h', 'data_date')]:
        try:
            r = q(db, f"SELECT MIN(`{col}`), MAX(`{col}`), COUNT(*) FROM `{t}` WHERE `{col}` IS NOT NULL")[0]
            P['business_span'][t] = {"col": col, "min": str(r[0]), "max": str(r[1]), "n": r[2]}
        except Exception as e:
            P['business_span'][t] = {"error": str(e)[:60]}
    P['bill_ratio'] = {
        'addl_over_settle': None,
        'settle_qty_buckets': ['settle_qty_01', 'settle_qty_02', 'settle_qty_03', 'settle_qty_04', 'settle_qty_05'],
        'prc_ec_categ': dist(db, 'dwd_cst_inst_bilg_card', 'prc_ec_categ'),
        'read_type': dist(db, 'dwd_cst_mr_data', 'read_type'),
        'data_src': dist(db, 'dwd_cst_mr_data', 'data_src'),
        'sgmt_exp_attr_cls': dist(db, 'dwd_cst_sgmt_qty_charg', 'sgmt_exp_attr_cls', 20),
        'ctlg_cls': dist(db, 'dwd_cst_sgmt_qty_charg', 'ctlg_cls'),
        'rcvbl_addl_charg_ctlg': dist(db, 'dwd_cst_rcvbl_addl_acct', 'addl_charg_ctlg_code', 20),
    }

    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, 'w', encoding='utf-8') as f:
        json.dump(P, f, ensure_ascii=False, indent=1)
    print(f"[ok] profiles -> {os.path.abspath(a.out)}")
    print(f"     区县供电公司 {P['county_count']} 个；变电站电压 {len(P['voltage']['cntrl_sta'])} 档；"
          f"容量档 {len(P['capacity']['cntrl_sta_kva'])} 档")


if __name__ == '__main__':
    main()
