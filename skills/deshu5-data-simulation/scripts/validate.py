# -*- coding: utf-8 -*-
"""validate.py — 仿真数据质量与关联完整性校验

仿真数据只有"能跨表 JOIN、量费能勾稽"才有价值。本脚本按三组规则体检：

A. 规模体检     —— 各层生成量是否符合预期配比
B. 关联完整性   —— 主数据拓扑链路 + 业务量费链条是否条条可 JOIN（孤儿键清零）
C. 量费勾稽     —— 电量→电费→应收→实收 的金额与电量是否闭合

**关键：所有检查都以 --tag 圈定仿真范围**（真实主数据名称里不含该标记，业务数据
经 inst_id → 仿真计量点回溯圈定）。否则 MAX(账期) 会落在真实数据的月份上，体检
结论就会变成"在检查真实数据"——这正是早期版本的缺陷。

**坑：绝不要对含 LIKE '%xx%' 的 SQL 做 ''.replace('%s', ...) 拼接**。
tag 以 s 开头时 `'%smoke%'` 里的首个 `%` 紧跟 `s` 会被误当作 `%s` 占位符替换掉，
条件变成 `'202510smoke%'`，全部查空却不报错。账期应直接内联到 f-string。

用法：
    python validate.py [--db fz01] [--tag 模拟] [--ym 202606]
"""
import argparse, os, sys

try:
    import pymysql
except ImportError:
    sys.exit("需要 pymysql：pip install pymysql")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', default=os.environ.get('SIM_DB', 'fz01'))
    ap.add_argument('--host', default=os.environ.get('MYSQL_HOST', 'localhost'))
    ap.add_argument('--port', type=int, default=int(os.environ.get('MYSQL_PORT', '3306')))
    ap.add_argument('--user', default=os.environ.get('MYSQL_USER', 'root'))
    ap.add_argument('--password', default=os.environ.get('MYSQL_PASSWORD', ''))
    ap.add_argument('--tag', default='模拟')
    ap.add_argument('--ym', type=int, default=0, help='抽查的账期 YYYYMM（0=自动取仿真最新）')
    a = ap.parse_args()
    if not a.password:
        sys.exit('缺少 MySQL 密码：设置 MYSQL_PASSWORD 环境变量或传 --password')

    conn = pymysql.connect(host=a.host, port=a.port, user=a.user, password=a.password,
                           database=a.db, charset='utf8mb4')

    def one(sql, args=None):
        with conn.cursor() as cur:
            cur.execute(sql, args)
            r = cur.fetchone()
            return r[0] if r else None

    T = a.tag
    LK = f"'%{T}%'"

    # ---- 仿真范围圈定（逐层回溯，全部限定在本次生成的数据内）----
    S_STA = f"SELECT cntrl_sta_id FROM dim_cst_cntrl_sta WHERE resrc_supl_name LIKE {LK}"
    S_PIPE = f"SELECT pipeline_id FROM dim_cst_pipeline WHERE pipeline_name LIKE {LK}"
    S_DIST = f"SELECT dist_sta_id FROM dim_cst_dist_sta WHERE resrc_supl_name LIKE {LK}"
    S_BP = f"SELECT bp_id FROM dim_cst_business_partner WHERE bp_name LIKE {LK}"
    S_CUST = f"SELECT cust_id FROM dim_cst_cust WHERE cust_name LIKE {LK}"
    S_CUSTNO = f"SELECT cust_no FROM dim_cst_cust WHERE cust_name LIKE {LK}"
    S_INST = f"SELECT inst_id FROM dim_cst_inst_elec_cons WHERE inst_name LIKE {LK}"
    S_BILG = f"SELECT bilg_card_id FROM dwd_cst_inst_bilg_card WHERE inst_id IN ({S_INST})"
    S_CALC = f"SELECT calc_id FROM dwd_cst_inst_bilg_card WHERE inst_id IN ({S_INST})"
    S_RCVBL = f"SELECT rcvbl_acct_id FROM dwd_cst_rcvbl_acct WHERE qty_charg_calc_id IN ({S_CALC})"
    S_METER = f"SELECT DISTINCT meter_asset_no FROM dwd_cst_meter_run WHERE inst_id IN ({S_INST})"
    S_APPF = f"SELECT bus_app_form_id FROM dwd_cst_bus_app_form WHERE bp_id IN ({S_BP})"
    # 设备域（PMS 侧）：经营销 → PSR 桥接字段回溯，绝不按名称猜
    S_PMS_STA = f"SELECT pms_cntrl_sta_id FROM dim_cst_cntrl_sta WHERE resrc_supl_name LIKE {LK}"
    S_PMS_LINE = f"SELECT pms_pipeline_id FROM dim_cst_pipeline WHERE pipeline_name LIKE {LK}"
    S_PMS_TF = ("SELECT id FROM dim_equ_t_p_pdzn_transformerpsr "
                f"WHERE pwline_id IN ({S_PMS_LINE})")
    S_PMS_OTF = ("SELECT id FROM dim_equ_t_p_pd_optransformerpsr "
                 f"WHERE customer_no IN ({S_CUSTNO})")
    # 电网模型（D5000 侧）：id = 3 位表类型码 + 6 位 adcode + 9 位流水，
    # adcode 由站 PSR 的 georegion（形如 3301-02-...）回读，故只圈定本次区县
    S_ADC = ("SELECT DISTINCT CONCAT(LEFT(p.georegion,4),SUBSTRING(p.georegion,6,2)) "
             "FROM dim_equ_t_p_pd_stationpsr p "
             "JOIN dim_cst_cntrl_sta s ON s.pms_cntrl_sta_id=p.id "
             f"WHERE s.resrc_supl_name LIKE {LK}")

    n_inst = one(f"SELECT COUNT(*) FROM dim_cst_inst_elec_cons WHERE inst_name LIKE {LK}") or 0
    if not n_inst:
        print(f"\n未找到 tag={T} 的仿真数据（按计量点名称匹配），无需体检。")
        return

    # ---- 把圈定范围物化成带索引的临时表 ----
    # 库内业务表**只有主键、没有任何二级索引**（真实库如此，不要为了体检去 ALTER 它）。
    # 因此 "IN (子查询)" 会被优化器反复全表扫描：80 万行的加收勾稽曾跑 9 分钟。
    # 物化到临时表 + 建(前缀)索引后，同样的校验 1.6 秒完成，结论完全一致。
    # 注意：MySQL 不允许同一查询里两次引用同一张临时表（Can't reopen table），
    # 所以必须**逐层物化**，每张临时表在每条 SQL 里只出现一次。
    def mk(tname, idx, sql):
        cur = conn.cursor()
        cur.execute(f"DROP TEMPORARY TABLE IF EXISTS {tname}")
        cur.execute(f"CREATE TEMPORARY TABLE {tname} AS {sql}")
        if idx:
            try:
                cur.execute(f"ALTER TABLE {tname} ADD INDEX k ({idx})")
            except Exception:
                pass   # TEXT 列在联合索引里必须带长度，失败则退回全表（不影响正确性）
        cur.execute(f"SELECT COUNT(*) FROM {tname}")
        return cur.fetchone()[0]

    mk('_sim_sta', 'cntrl_sta_id', "SELECT cntrl_sta_id, pms_cntrl_sta_id "
       f"FROM dim_cst_cntrl_sta WHERE resrc_supl_name LIKE {LK}")
    mk('_sim_pipe', 'pipeline_id', "SELECT pipeline_id, pms_pipeline_id "
       f"FROM dim_cst_pipeline WHERE pipeline_name LIKE {LK}")
    mk('_sim_dist', 'dist_sta_id', S_DIST)
    mk('_sim_bp', 'bp_id', S_BP)
    mk('_sim_cust', 'cust_id', f"SELECT cust_id, cust_no FROM dim_cst_cust WHERE cust_name LIKE {LK}")
    mk('_sim_inst', 'inst_id', S_INST)
    mk('_sim_pmstf', 'pwline_id', "SELECT id, pwline_id FROM dim_equ_t_p_pdzn_transformerpsr "
       "WHERE pwline_id IN (SELECT pms_pipeline_id FROM _sim_pipe)")
    mk('_sim_pmsotf', 'customer_no', "SELECT id, customer_no FROM dim_equ_t_p_pd_optransformerpsr "
       "WHERE customer_no IN (SELECT cust_no FROM _sim_cust)")
    mk('_sim_meter', 'meter_asset_no', "SELECT DISTINCT meter_asset_no FROM dwd_cst_meter_run "
       "WHERE inst_id IN (SELECT inst_id FROM _sim_inst)")
    mk('_sim_bilg', 'bilg_card_id', "SELECT bilg_card_id, calc_id, t_addl_charg, t_settle_exp "
       "FROM dwd_cst_inst_bilg_card WHERE inst_id IN (SELECT inst_id FROM _sim_inst)")
    mk('_sim_appf', 'bus_app_form_id', "SELECT bus_app_form_id, bp_id, mgt_org_code "
       "FROM dwd_cst_bus_app_form WHERE bp_id IN (SELECT bp_id FROM _sim_bp)")

    # 覆盖：此后所有查询都只查临时表
    S_STA = "SELECT cntrl_sta_id FROM _sim_sta"
    S_PIPE = "SELECT pipeline_id FROM _sim_pipe"
    S_DIST = "SELECT dist_sta_id FROM _sim_dist"
    S_BP = "SELECT bp_id FROM _sim_bp"
    S_CUST = "SELECT cust_id FROM _sim_cust"
    S_CUSTNO = "SELECT cust_no FROM _sim_cust"
    S_INST = "SELECT inst_id FROM _sim_inst"
    S_BILG = "SELECT bilg_card_id FROM _sim_bilg"
    S_CALC = "SELECT calc_id FROM _sim_bilg"
    S_RCVBL = ("SELECT rcvbl_acct_id FROM dwd_cst_rcvbl_acct "
               "WHERE qty_charg_calc_id IN (SELECT calc_id FROM _sim_bilg)")
    S_METER = "SELECT meter_asset_no FROM _sim_meter"
    S_APPF = "SELECT bus_app_form_id FROM _sim_appf"
    S_PMS_STA = "SELECT pms_cntrl_sta_id FROM _sim_sta"
    S_PMS_LINE = "SELECT pms_pipeline_id FROM _sim_pipe"
    S_PMS_TF = "SELECT id FROM _sim_pmstf"
    S_PMS_OTF = "SELECT id FROM _sim_pmsotf"
    # 电网模型 adcode：由 _sim_sta 桥接到站 PSR 的 georegion 回读
    S_ADC = ("SELECT DISTINCT CONCAT(LEFT(p.georegion,4),SUBSTRING(p.georegion,6,2)) "
             "FROM dim_equ_t_p_pd_stationpsr p "
             "JOIN _sim_sta s ON s.pms_cntrl_sta_id=p.id")

    ym = a.ym
    if not ym:
        ym = one(f"SELECT MAX(qty_charg_ym) FROM dwd_cst_inst_bilg_card WHERE inst_id IN ({S_INST})") or 0

    print(f"\n{'='*72}\n仿真数据体检｜库 {a.db}｜标记 {T}｜抽查账期 {ym}\n{'='*72}")

    # ---------- A. 规模 ----------
    print("\n【A】生成规模")
    SCALE = [
        ('220kV 变电站', f"SELECT COUNT(*) FROM dim_cst_cntrl_sta WHERE resrc_supl_name LIKE {LK} AND voltage_desc='交流220kV'"),
        ('110kV 变电站', f"SELECT COUNT(*) FROM dim_cst_cntrl_sta WHERE resrc_supl_name LIKE {LK} AND voltage_desc='交流110kV'"),
        ('110kV 线路', f"SELECT COUNT(*) FROM dim_cst_pipeline WHERE pipeline_name LIKE {LK} AND voltage_desc='交流110kV'"),
        ('10kV 线路', f"SELECT COUNT(*) FROM dim_cst_pipeline WHERE pipeline_name LIKE {LK} AND voltage_desc='交流10kV'"),
        ('10kV 台区', f"SELECT COUNT(*) FROM dim_cst_dist_sta WHERE resrc_supl_name LIKE {LK}"),
        ('配电变压器', f"SELECT COUNT(*) FROM dim_cst_adj_volt_dev WHERE dev_name LIKE {LK}"),
        ('客户', f"SELECT COUNT(*) FROM dim_cst_cust WHERE cust_name LIKE {LK}"),
        ('计量点', f"SELECT COUNT(*) FROM dim_cst_inst_elec_cons WHERE inst_name LIKE {LK}"),
        ('电能表', f"SELECT COUNT(DISTINCT e.dev_id) FROM dim_cst_elec_meter e "
                 f"WHERE e.dev_id IN (SELECT meter_id FROM dwd_cst_meter_run WHERE inst_id IN ({S_INST}))"),
        ('月计费卡', f"SELECT COUNT(*) FROM dwd_cst_inst_bilg_card WHERE inst_id IN ({S_INST}) AND qty_charg_ym={ym}"),
        ('月抄表', f"SELECT COUNT(*) FROM dwd_cst_mr_data WHERE inst_id IN ({S_INST}) AND qty_charg_ym={ym}"),
        ('月应收', f"SELECT COUNT(*) FROM dwd_cst_rcvbl_acct WHERE qty_charg_calc_id IN ({S_CALC}) AND rcvbl_ym={ym}"),
        ('月实收', f"SELECT COUNT(*) FROM dwd_cst_rcvd_acct WHERE rcvbl_acct_id IN ({S_RCVBL}) AND rcvd_ym={ym}"),
    ]
    for name, sql in SCALE:
        try:
            print(f"  {name:<14} {one(sql) or 0:>9,}")
        except Exception as e:
            print(f"  {name:<14} —  {str(e)[:60]}")

    # ---------- B. 关联完整性 ----------
    print("\n【B】关联完整性（显示可 JOIN 行数；异常见末尾）")
    JOIN_CHECKS = [
        ('220/110站→线路 关系可 JOIN',
         f"SELECT COUNT(*) FROM dim_cst_cntrl_sta_pline_rela r "
         f"JOIN dim_cst_cntrl_sta s ON r.cntrl_sta_id=s.cntrl_sta_id "
         f"JOIN dim_cst_pipeline p ON r.pipeline_id=p.pipeline_id "
         f"WHERE r.cntrl_sta_id IN ({S_STA})"),
        ('线路→台区 关系可 JOIN',
         f"SELECT COUNT(*) FROM dim_cst_pline_dist_sta_rela r "
         f"JOIN dim_cst_pipeline p ON r.pipeline_id=p.pipeline_id "
         f"JOIN dim_cst_dist_sta d ON r.dist_sta_id=d.dist_sta_id "
         f"WHERE r.dist_sta_id IN ({S_DIST})"),
        ('计量点→客户/台区/线路/变电站 全链可 JOIN',
         f"SELECT COUNT(*) FROM dim_cst_inst_elec_cons i "
         f"JOIN dim_cst_cust c ON i.cust_id=c.cust_id "
         f"JOIN dim_cst_dist_sta d ON i.dist_sta_id=d.dist_sta_id "
         f"JOIN dim_cst_pipeline p ON i.pipeline_id=p.pipeline_id "
         f"JOIN dim_cst_cntrl_sta s ON i.cntrl_sta_id=s.cntrl_sta_id "
         f"WHERE i.inst_name LIKE {LK}"),
        ('客户→业务伙伴/协议/结算账户 可 JOIN',
         f"SELECT COUNT(*) FROM dim_cst_cust c "
         f"JOIN dim_cst_business_partner b ON c.bp_id=b.bp_id "
         f"JOIN dim_cst_cust_agrt a ON a.cust_id=c.cust_id "
         f"JOIN dim_cst_settle_acct s ON a.settle_acct_id=s.settle_acct_id "
         f"WHERE c.cust_name LIKE {LK}"),
        ('计量点运行→电能表台账 可 JOIN',
         f"SELECT COUNT(*) FROM dwd_cst_meter_run m "
         f"JOIN dim_cst_elec_meter e ON m.meter_id=e.dev_id WHERE m.inst_id IN ({S_INST})"),
        ('抄表→计费卡（calc_id）可 JOIN',
         f"SELECT COUNT(*) FROM dwd_cst_mr_data m "
         f"JOIN dwd_cst_inst_bilg_card b ON m.calc_id=b.calc_id "
         f"WHERE m.inst_id IN ({S_INST}) AND m.qty_charg_ym={ym}"),
        ('计费卡→分段量费 可 JOIN',
         f"SELECT COUNT(*) FROM dwd_cst_inst_bilg_card b "
         f"JOIN dwd_cst_sgmt_qty_charg s ON s.bilg_card_id=b.bilg_card_id "
         f"WHERE b.inst_id IN ({S_INST}) AND b.qty_charg_ym={ym}"),
        ('计费卡→应收 可 JOIN',
         f"SELECT COUNT(*) FROM dwd_cst_inst_bilg_card b "
         f"JOIN dwd_cst_rcvbl_acct r ON b.calc_id=r.qty_charg_calc_id "
         f"WHERE b.inst_id IN ({S_INST}) AND b.qty_charg_ym={ym}"),
        ('应收→实收→交费 可 JOIN',
         f"SELECT COUNT(*) FROM dwd_cst_rcvbl_acct r "
         f"JOIN dwd_cst_rcvd_acct v ON v.rcvbl_acct_id=r.rcvbl_acct_id "
         f"JOIN dwd_cst_charg_acct g ON v.charg_acct_id=g.charg_acct_id "
         f"WHERE r.qty_charg_calc_id IN ({S_CALC}) AND r.rcvbl_ym={ym}"),
        ('日电量→计量点运行（asset_no）可 JOIN',
         f"SELECT COUNT(*) FROM dwd_cst_es_meter_energy_day_l_xz e "
         f"JOIN dwd_cst_meter_run m ON e.meter_asset_no=m.meter_asset_no "
         f"WHERE m.inst_id IN ({S_INST})"),
        ('业扩申请→方案→工单→环节 可 JOIN',
         f"SELECT COUNT(*) FROM dwd_cst_bus_app_form f "
         f"JOIN dwd_cst_acc_sch a ON a.bus_app_form_id=f.bus_app_form_id "
         f"JOIN dwd_cst_meter_sch ms ON ms.bus_app_form_id=f.bus_app_form_id "
         f"JOIN dwd_cst_wk_order w ON w.bus_attr_no=CAST(f.bus_app_form_id AS CHAR) "
         f"WHERE f.bp_id IN ({S_BP})"),
    ]
    orphan = 0
    for name, sql in JOIN_CHECKS:
        try:
            v = one(sql)
            if not v:
                orphan += 1     # 一条都连不上 → 视为异常
            print(f"  {name:<38} {v or 0:>8,}")
        except Exception as e:
            orphan += 1
            print(f"  {name:<38} ERR {str(e)[:60]}")

    # ---------- C. 量费勾稽 ----------
    print("\n【C】量费勾稽（期望：偏差记录数为 0）")
    RECON = [
        ('分段结算量之和 = 计费卡总结算量',
         f"SELECT COUNT(*) FROM dwd_cst_inst_bilg_card b WHERE b.inst_id IN ({S_INST}) "
         f"AND b.qty_charg_ym={ym} "
         f"AND ABS(COALESCE(b.settle_qty_01,0)+COALESCE(b.settle_qty_02,0)+COALESCE(b.settle_qty_03,0)"
         f"+COALESCE(b.settle_qty_04,0)+COALESCE(b.settle_qty_05,0) - b.t_settle_qty) > 1"),
        ('计费卡总结算费 = 目录电费 + 加收',
         f"SELECT COUNT(*) FROM dwd_cst_inst_bilg_card b WHERE b.inst_id IN ({S_INST}) "
         f"AND b.qty_charg_ym={ym} "
         f"AND ABS(b.t_settle_exp - (COALESCE(b.t_ctlg_exp,0) + COALESCE(b.t_addl_charg,0))) > 0.02"),
        ('应收金额 = 计费卡总结算费',
         f"SELECT COUNT(*) FROM dwd_cst_rcvbl_acct r "
         f"JOIN dwd_cst_inst_bilg_card b ON b.calc_id=r.qty_charg_calc_id "
         f"WHERE r.qty_charg_calc_id IN ({S_CALC}) AND r.rcvbl_ym={ym} "
         f"AND ABS(r.rcvbl_amt - b.t_settle_exp) > 0.02"),
        ('应收电量 = 计费卡总结算量',
         f"SELECT COUNT(*) FROM dwd_cst_rcvbl_acct r "
         f"JOIN dwd_cst_inst_bilg_card b ON b.calc_id=r.qty_charg_calc_id "
         f"WHERE r.qty_charg_calc_id IN ({S_CALC}) AND r.rcvbl_ym={ym} "
         f"AND r.ec_qty <> b.t_settle_qty"),
        ('实收金额 = 交费金额',
         f"SELECT COUNT(*) FROM dwd_cst_rcvd_acct v "
         f"JOIN dwd_cst_charg_acct g ON v.charg_acct_id=g.charg_acct_id "
         f"WHERE v.rcvbl_acct_id IN ({S_RCVBL}) AND v.rcvd_ym={ym} "
         f"AND ABS(v.rcvd_amt - g.charg_amt) > 0.02"),
    ]
    bad = 0
    for name, sql in RECON:
        try:
            v = one(sql)
            bad += v or 0
            print(f"  偏差记录数 {name:<34} {v:>8,}")
        except Exception as e:
            bad += 1
            print(f"  偏差记录数 {name:<34} ERR {str(e)[:60]}")

    # ---------- D. 设备域（PMS 台账 + D5000 电网模型）关联 ----------
    print("\n【D】设备域关联（营销域 ↔ 设备域桥接 + 设备内部拓扑）")
    P96 = '+'.join(f"COALESCE(CAST(p{i} AS DECIMAL(20,6)),0)" for i in range(1, 97))
    DEV_CHECKS = [
        ('220/110站 → PMS 站 PSR（pms_cntrl_sta_id）',
         f"SELECT COUNT(*) FROM dim_cst_cntrl_sta s "
         f"JOIN dim_equ_t_p_pd_stationpsr p ON s.pms_cntrl_sta_id=p.id "
         f"WHERE s.cntrl_sta_id IN ({S_STA})"),
        ('线路 → PMS 馈线 PSR（pms_pipeline_id）',
         f"SELECT COUNT(*) FROM dim_cst_pipeline l "
         f"JOIN dim_ast_t_p_pd_feederlinepsr p ON l.pms_pipeline_id=p.id "
         f"WHERE l.pipeline_id IN ({S_PIPE})"),
        ('配变 PSR → 馈线 PSR',
         f"SELECT COUNT(*) FROM dim_equ_t_p_pdzn_transformerpsr t "
         f"JOIN dim_ast_t_p_pd_feederlinepsr f ON t.pwline_id=f.id "
         f"WHERE t.id IN ({S_PMS_TF})"),
        ('配变-馈线关系 → 配变 & 馈线 双向可 JOIN',
         f"SELECT COUNT(*) FROM dim_equ_m_p_rel_pdtransformer_feederline r "
         f"JOIN dim_equ_t_p_pdzn_transformerpsr t ON r.transformer_psr_id=t.id "
         f"JOIN dim_ast_t_p_pd_feederlinepsr f ON r.feeder_psr_id=f.id "
         f"WHERE r.transformer_psr_id IN ({S_PMS_TF})"),
        ('公变台账关联 → 配变 PSR',
         f"SELECT COUNT(*) FROM dim_equ_t_p_rel_transformer_publ r "
         f"JOIN dim_equ_t_p_pdzn_transformerpsr t ON r.dev_id=t.id "
         f"WHERE r.dev_id IN ({S_PMS_TF})"),
        ('专变台账关联 → 专变 PSR',
         f"SELECT COUNT(*) FROM dim_equ_t_p_rel_transformer_priv r "
         f"JOIN dim_equ_t_p_pd_optransformerpsr t ON r.dev_id=t.id "
         f"WHERE r.dev_id IN ({S_PMS_OTF})"),
        ('变电站-线路关系表 双向可 JOIN',
         f"SELECT COUNT(*) FROM dim_cst_cntrl_sta_pline_rela r "
         f"JOIN dim_cst_pipeline p ON r.pipeline_id=p.pipeline_id "
         f"JOIN dim_cst_cntrl_sta s ON r.cntrl_sta_id=s.cntrl_sta_id "
         f"WHERE r.pipeline_id IN ({S_PIPE})"),
    ]
    # D5000 电网模型：按 id 内嵌 adcode 圈定本次区县
    GM = ['dim_grid_t_ts_sg_da_acline_b', 'dim_grid_t_ts_sg_da_aclineend_b',
          'dwd_grid_t_ts_sg_da_feederline_b_new', 'dwd_grid_t_ts_sg_da_con_jlxdjbxx',
          'dim_grid_t_ts_sg_da_tline_b', 'dim_grid_t_ts_sg_da_breaker_b',
          'dim_grid_t_ts_sg_da_busbar_b', 'dim_grid_t_ts_sg_da_dev_pwrtransfm_b',
          'dim_grid_t_ts_sg_da_transfmwd_b', 'dim_grid_t_ts_sg_da_dev_generator_b']
    for t in GM:
        DEV_CHECKS.append((f'  · D5000 {t[9:]}', 
                           f"SELECT COUNT(*) FROM {t} WHERE SUBSTRING(id,4,6) IN ({S_ADC})"))
    for name, sql in DEV_CHECKS:
        try:
            v = one(sql)
            print(f"  {name:<44} {v or 0:>8,}")
        except Exception as e:
            orphan += 1
            print(f"  {name:<44} ERR {str(e)[:60]}")

    # ---------- E. 派生业务表勾稽 ----------
    print("\n【E】派生业务勾稽（期望：偏差记录数为 0）")
    # 先把几张"要被反复比对"的大表物化，避免在同一查询里重复扫描（见开头 mk() 说明）
    mk('_addl', 'bilg_card_id',
       "SELECT addl_charg_id, bilg_card_id, addl_num, addl_prc, addl_amt "
       "FROM dwd_cst_addl_charg WHERE bilg_card_id IN (SELECT bilg_card_id FROM _sim_bilg)")
    mk('_addl_agg', 'bilg_card_id',
       "SELECT bilg_card_id, SUM(addl_amt) s FROM _addl GROUP BY bilg_card_id")
    mk('_spcl', 'bilg_card_id',
       "SELECT s.bilg_card_id, s.settle_exp_qty_val, s.settle_prc, s.settle_exp "
       "FROM dwd_cst_special_expense s WHERE s.bilg_card_id IN "
       "(SELECT bilg_card_id FROM _sim_bilg)")
    mk('_hxz', 'meter_asset_no(24),data_date(19)',
       "SELECT meter_asset_no, data_date, pap_e FROM dwd_cst_meter_energy_day_h_xz "
       "WHERE meter_asset_no IN (SELECT meter_asset_no FROM _sim_meter)")
    mk('_mp_e', 'meter_asset_no(24),data_date(19)',
       "SELECT meter_asset_no, data_date, pap_e FROM dwd_cst_es_meter_energy_day_p "
       "WHERE meter_asset_no IN (SELECT meter_asset_no FROM _sim_meter)")
    mk('_curve', 'meter_asset_no(24),data_date(19)',
       f"SELECT meter_asset_no, data_date, ({P96}) * 0.25 AS curve_e, tv, ta "
       "FROM dwd_cst_es_e_mp_comp_curve_h "
       "WHERE meter_asset_no IN (SELECT meter_asset_no FROM _sim_meter)")
    mk('_lss', 'dist_sta_id',
       "SELECT dist_sta_id, stat_date, last_read, this_read, coll_pq "
       "FROM dwd_cst_a_ll_dist_det_day WHERE dist_sta_id IN (SELECT dist_sta_id FROM _sim_dist)")

    DERIVED = [
        ('加收明细 → 计费卡 可 JOIN',
         "SELECT COUNT(*) FROM _addl a JOIN _sim_bilg b ON b.bilg_card_id=a.bilg_card_id", None),
        ('加收明细合计 = 计费卡 t_addl_charg',
         "SELECT COUNT(*) FROM _sim_bilg b LEFT JOIN _addl_agg a ON a.bilg_card_id=b.bilg_card_id "
         "WHERE ABS(COALESCE(b.t_addl_charg,0) - COALESCE(a.s,0)) > 0.02", 'zero'),
        ('加收明细：电量 × 单价 ≠ 金额（应为 0 行）',
         "SELECT COUNT(*) FROM _addl WHERE ABS(addl_num * addl_prc - addl_amt) > 0.01", 'zero'),
        ('力调明细：电量 × 单价 ≠ 金额（应为 0 行）',
         "SELECT COUNT(*) FROM _spcl "
         "WHERE ABS(settle_exp_qty_val * settle_prc - settle_exp) > 0.01", 'zero'),
        ('台区日线损：coll_pq ≤ 0（应为 0 行）',
         "SELECT COUNT(*) FROM _lss WHERE coll_pq <= 0", 'zero'),
        ('台区日线损：读数差 ≠ 采集电量（应为 0 行）',
         "SELECT COUNT(*) FROM _lss WHERE ABS((this_read - last_read) - coll_pq) > 0.02", 'zero'),
        ('台区日线损 命中的仿真台区数',
         "SELECT COUNT(DISTINCT dist_sta_id) FROM _lss", None),
        ('表计正反向电量 = 抄表日电量（应为 0 行）',
         "SELECT COUNT(*) FROM _mp_e p JOIN _hxz h "
         "ON h.meter_asset_no=p.meter_asset_no AND h.data_date=p.data_date "
         "WHERE ABS(p.pap_e - h.pap_e) > 0.05", 'zero'),
        ('96 点曲线：Σp × 0.25h = 该表当日电量（应为 0 行）',
         "SELECT COUNT(*) FROM _curve z JOIN _hxz h "
         "ON h.meter_asset_no=z.meter_asset_no AND h.data_date=z.data_date "
         "WHERE ABS(z.curve_e - h.pap_e) > 0.1", 'zero'),
        ('96 点曲线：tv/ta 非空（应为 0 行）',
         "SELECT COUNT(*) FROM _curve WHERE tv IS NULL OR ta IS NULL", 'zero'),
        ('投资工单 → 业扩申请单 可 JOIN',
         "SELECT COUNT(*) FROM dwd_cst_invest_order i "
         "JOIN _sim_appf f ON f.bus_app_form_id=i.bus_app_form_id", None),
        ('工单附属材料 → 业扩申请单 可 JOIN',
         "SELECT COUNT(*) FROM dwd_cst_wkorder_affilmtrl_rec m "
         "JOIN _sim_appf f ON f.bus_app_form_id=m.bus_app_form_id", None),
        ('发电并网申请 → 客户 可 JOIN',
         "SELECT COUNT(*) FROM dwd_cst_conn_gen_power_app g "
         "JOIN _sim_cust c ON c.cust_id=g.cust_id", None),
        ('投资工单 机构码与申请单一致（应为 0 行）',
         "SELECT COUNT(*) FROM dwd_cst_invest_order i "
         "JOIN _sim_appf f ON f.bus_app_form_id=i.bus_app_form_id "
         "WHERE CAST(i.mgt_org_code AS CHAR) <> CAST(f.mgt_org_code AS CHAR)", 'zero'),
    ]
    for name, sql, mode in DERIVED:
        try:
            v = one(sql)
            bad += (v or 0) if mode == 'zero' else 0
            if not v and mode is None:
                orphan += 1
            flag = ' ← 偏差' if (mode == 'zero' and v) else ''
            print(f"  {name:<44} {v or 0:>8,}{flag}")
        except Exception as e:
            bad += 1
            print(f"  {name:<44} ERR {str(e)[:60]}")

    print(f"\n{'='*72}")
    print(f"结论：关联校验异常组 {orphan}｜勾稽异常记录 {bad}")
    print("      两者均为 0 表示仿真数据可安全用于问数/治理场景。")
    conn.close()


if __name__ == '__main__':
    main()
