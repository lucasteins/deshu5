-- ============================================================
-- 共享层表重构版v2.0 精简版 - 仅保留主键、外键字段
-- 原文件: 35张营销共享层表_重构版v2.0_20260519.sql
-- ============================================================

CREATE TABLE IF NOT EXISTS dim_cst_elec_cons_cust (
    cust_id                   BIGINT        COMMENT '客户标识',
    mgt_org_code              varchar(50)        COMMENT '管理单位编码',
    primary key (cust_id)
) COMMENT '用电客户';

CREATE TABLE IF NOT EXISTS dim_cst_inst_elec_cons (
 `inst_id` BIGINT COMMENT '安装点标识',
 `cust_id` BIGINT COMMENT '客户标识',
 `cust_agrt_id` BIGINT COMMENT '客户协议标识',
 `dist_sta_id` BIGINT COMMENT '配送站标识',
 `mgt_org_code` varchar(255) COMMENT '管理单位编码',
    primary key (inst_id)
) COMMENT '安装点用电（计量点）';

CREATE TABLE IF NOT EXISTS dwd_cst_meter_run (
 `meter_id` BIGINT COMMENT '表计标识',
 `inst_id` BIGINT COMMENT '安装点标识',
 `cust_id` BIGINT COMMENT '客户标识',
 `meter_asset_no` varchar(50) COMMENT '资产编号',
 `mgt_org_code` varchar(255) COMMENT '管理单位编码',
    primary key (meter_asset_no)
) COMMENT '计量表计';

CREATE TABLE IF NOT EXISTS dim_cst_elec_meter (
 `dev_id` BIGINT COMMENT '设备标识',
 `mgt_org_code` varchar(255) COMMENT '管理单位编码',
    primary key (dev_id)
) COMMENT '电能表维表';

CREATE TABLE IF NOT EXISTS dim_cst_adj_volt_dev (
 `adj_volt_dev_id` BIGINT COMMENT '调压设备标识',
 `dist_sta_id` BIGINT COMMENT '配送站标识',
 `mgt_org_code` varchar(255) COMMENT '管理单位编码',
    primary key (adj_volt_dev_id)
) COMMENT '调压设备';

CREATE TABLE IF NOT EXISTS dim_cst_pipeline (
 `pipeline_id` BIGINT COMMENT '管线标识',
 `mgt_org_code` varchar(255) COMMENT '管理单位编码',
    primary key (pipeline_id)
) COMMENT '管线维表';

CREATE TABLE IF NOT EXISTS dim_cst_dist_sta (
 `dist_sta_id` BIGINT COMMENT '配送站标识',
 `mgt_org_code` varchar(255) COMMENT '管理单位编码',
    primary key (dist_sta_id)
) COMMENT '配送站';

CREATE TABLE IF NOT EXISTS dim_cst_pline_dist_sta_rela (
 `pipe_line_dist_sta_rela_id` BIGINT COMMENT '管线配送站关系标识',
 `pipeline_id` BIGINT COMMENT '管线标识',
 `dist_sta_id` BIGINT COMMENT '配送站标识',
    primary key (pipe_line_dist_sta_rela_id)
) COMMENT '管线配送站关系';

CREATE TABLE IF NOT EXISTS dim_cst_cntrl_sta (
 `cntrl_sta_id` BIGINT COMMENT '枢纽站标识',
 `mgt_org_code` varchar(255) COMMENT '管理单位编码',
    primary key (cntrl_sta_id)
) COMMENT '枢纽站_变电站';

CREATE TABLE IF NOT EXISTS dim_cst_cntrl_sta_pline_rela (
 `cntrl_sta_pipe_line_rela_id` BIGINT COMMENT '枢纽站管线关系标识',
 `cntrl_sta_id` BIGINT COMMENT '枢纽站标识',
 `pipeline_id` BIGINT COMMENT '管线标识',
    primary key (cntrl_sta_pipe_line_rela_id)
) COMMENT '枢纽站管线关系';

CREATE TABLE IF NOT EXISTS dim_cst_dev (
 `dev_id` BIGINT COMMENT '设备标识',
 `mgt_org_code` varchar(255) COMMENT '管理单位编码',
    primary key (dev_id)
) COMMENT '设备';

CREATE TABLE IF NOT EXISTS dim_cst_mgt_org (
 `mgt_org_code` varchar(50) COMMENT '管理单位编码',
    primary key (mgt_org_code)
) COMMENT '管理单位';

CREATE TABLE IF NOT EXISTS dim_cst_business_partner (
 `bp_id` BIGINT COMMENT '伙伴标识',
    primary key (bp_id)
) COMMENT '客户_业务伙伴';

CREATE TABLE IF NOT EXISTS dim_cst_settle_acct (
 `settle_acct_id` BIGINT COMMENT '结算账户标识',
    primary key (settle_acct_id)
) COMMENT '合同账户';

CREATE TABLE IF NOT EXISTS dim_cst_cust_agrt (
 `cust_agrt_id` BIGINT COMMENT '客户协议标识',
 `settle_acct_id` BIGINT COMMENT '结算账户标识',
    primary key (cust_agrt_id)
) COMMENT '客户协议';

CREATE TABLE IF NOT EXISTS dwd_cst_inst_bilg_card (
 `bilg_card_id` BIGINT COMMENT '计费卡标识',
 `calc_id` BIGINT COMMENT '计算标识',
 `inst_id` BIGINT COMMENT '安装点标识',
 `mgt_org_code` varchar(255) COMMENT '管理单位编码',
    primary key (bilg_card_id)
) COMMENT '安装点计费卡信息';

CREATE TABLE IF NOT EXISTS dwd_cst_acct_bal (
 `acct_bal_id` BIGINT COMMENT '账户余额标识',
 `cust_no` varchar(255) COMMENT '客户编号',
 `mgt_org_code` varchar(255) COMMENT '管理单位编码',
    primary key (acct_bal_id)
) COMMENT '余额表';

CREATE TABLE IF NOT EXISTS dwd_cst_es_meter_energy_day_l_xz (
 `data_date` DATETIME COMMENT '数据时间;数据时间',
 `meter_asset_no` varchar(50) COMMENT '表计资产编号',
 `mgt_org_code` varchar(255) COMMENT '管理单位编码',
    primary key (data_date, meter_asset_no)
) COMMENT '低压用户日电量（修正）';

CREATE TABLE IF NOT EXISTS dwd_cst_meter_energy_day_h_xz (
 `data_date` DATETIME COMMENT '数据时间',
 `meter_asset_no` varchar(50) COMMENT '表计资产编号',
 `mgt_org_code` varchar(255) COMMENT '管理单位编码',
    primary key (data_date, meter_asset_no)
) COMMENT '专变用户日电量（修正）';

CREATE TABLE IF NOT EXISTS dwd_cst_rcvbl_acct (
 `rcvbl_acct_id` BIGINT COMMENT '应收台账标识',
 `mgt_org_code` varchar(255) COMMENT '管理单位编码',
    primary key (rcvbl_acct_id)
) COMMENT '应收电费台账';

CREATE TABLE IF NOT EXISTS dwd_cst_rcvd_acct (
 `rcvd_acct_id` BIGINT COMMENT '实收台账标识',
 `charg_acct_id` BIGINT COMMENT '交费台账标识',
 `rcvbl_acct_id` BIGINT COMMENT '应收台账标识',
 `mgt_org_code` varchar(255) COMMENT '管理单位编码',
    primary key (rcvd_acct_id)
) COMMENT '实收电费台账';

CREATE TABLE IF NOT EXISTS dwd_cst_charg_acct (
 `charg_acct_id` BIGINT COMMENT '交费台账标识',
 `mgt_org_code` varchar(255) COMMENT '管理单位编码',
    primary key (charg_acct_id)
) COMMENT '交费台账';

CREATE TABLE IF NOT EXISTS dim_cst_cust (
 `cust_id` BIGINT COMMENT '客户标识',
 `cust_no` varchar(255) COMMENT '客户编号',
 `mgt_org_code` varchar(255) COMMENT '管理单位编码',
    primary key (cust_id)
) COMMENT '能源客户';

CREATE TABLE IF NOT EXISTS dwd_cst_sgmt_qty_charg (
 `sgmt_qty_charg_id` BIGINT COMMENT '分段量费标识',
 `bilg_card_id` BIGINT COMMENT '计费卡标识',
    primary key (sgmt_qty_charg_id)
) COMMENT '客户安装点分段量费信息';

CREATE TABLE IF NOT EXISTS dwd_cst_addl_charg (
 `addl_charg_id` BIGINT COMMENT '加收费标识',
 `bilg_card_id` BIGINT COMMENT '计费卡标识',
    primary key (addl_charg_id)
) COMMENT '客户安装点加收费信息';

CREATE TABLE IF NOT EXISTS dwd_cst_special_expense (
 `spcl_exp_id` BIGINT COMMENT '专属费用标识',
 `bilg_card_id` BIGINT COMMENT '计费卡标识',
    primary key (spcl_exp_id)
) COMMENT '客户安装点专属费信息';

CREATE TABLE IF NOT EXISTS dwd_cst_rs_inst_bilg_card (
 `rs_inst_bilg_card_id` BIGINT COMMENT '退补安装点计费卡标识',
 `bilg_rs_rcpt_id` BIGINT COMMENT '计费退补单标识',
    primary key (rs_inst_bilg_card_id)
) COMMENT '退补安装点计费卡明细';

CREATE TABLE IF NOT EXISTS dwd_cst_mr_data (
 `m_r_data_id` BIGINT COMMENT '抄表标识',
 `inst_id` BIGINT COMMENT '安装点标识',
 `meter_asset_no` varchar(255) COMMENT '资产编号',
    primary key (m_r_data_id)
) COMMENT '抄表数据';

CREATE TABLE IF NOT EXISTS dwd_cst_rcvbl_addl_acct (
 `rcvbl_addl_charg_acct_id` BIGINT COMMENT '应收加收台账标识',
 `rcvbl_acct_id` BIGINT COMMENT '应收台账标识',
    primary key (rcvbl_addl_charg_acct_id)
) COMMENT '应收加收台账';

CREATE TABLE IF NOT EXISTS dwd_cst_bilg_rs_rcpt (
 `bilg_rs_rcpt_id` BIGINT COMMENT '计费退补单标识',
 `cust_no` varchar(255) COMMENT '客户编号',
    primary key (bilg_rs_rcpt_id)
) COMMENT '计费退补单';

CREATE TABLE IF NOT EXISTS dwd_cst_es_meter_energy_day_p (
 `meter_asset_no` varchar(50) COMMENT '资产编号',
    primary key (meter_asset_no)
) COMMENT '公变日电量';

CREATE TABLE IF NOT EXISTS dwd_cst_a_ll_dist_det_day (
 `rec_id` varchar(50) COMMENT '记录标识',
 `dist_sta_id` BIGINT COMMENT '配送站标识',
    primary key (rec_id)
) COMMENT '配送站（台区）日电量';

CREATE TABLE IF NOT EXISTS dwd_cst_es_e_mp_comp_curve_h (
 `id` BIGINT COMMENT '标识(由表计资产编号转换)',
 `meter_asset_no` varchar(50) COMMENT '表计资产编号',
 `cust_no` varchar(50) COMMENT '客户编号',
 `data_type` BIGINT COMMENT '数据类型',
 `mgt_org_code` varchar(50) COMMENT '管理单位编码',
    primary key (id, cust_no, data_type, mgt_org_code)
) COMMENT '专变用户96点负荷 ';

CREATE TABLE IF NOT EXISTS dwd_cst_es_e_mp_comp_curve_h_v (
 `id` BIGINT COMMENT '标识(由表计资产编号转换)',
 `meter_asset_no` varchar(50) COMMENT '资产编号',
 `cust_no` varchar(50) COMMENT '客户编号',
 `phase_flag` varchar(50) COMMENT '电压标志',
 `mgt_org_code` varchar(50) COMMENT '管理单位编码',
    primary key (id, cust_no, phase_flag, mgt_org_code)
) COMMENT '专变用户96点电压';

CREATE TABLE IF NOT EXISTS dwd_cst_es_e_mp_comp_curve_h_a (
 `id` BIGINT COMMENT '标识(由表计资产编号转换)',
 `meter_asset_no` varchar(50) COMMENT '表计资产编号',
 `cust_no` varchar(50) COMMENT '客户编号',
 `phase_flag` varchar(50) COMMENT '电流标志',
 `mgt_org_code` varchar(50) COMMENT '管理单位编码',
    primary key (id, cust_no, phase_flag, mgt_org_code)
) COMMENT '专变用户96点电流';

-- ============================================================
-- 外键约束（精简后仍然有效的外键关系）
-- ============================================================

ALTER TABLE dim_cst_adj_volt_dev ADD CONSTRAINT fk_dim_cst_adj_volt_dev_dist_sta_id FOREIGN KEY (dist_sta_id) REFERENCES dim_cst_dist_sta(dist_sta_id);
ALTER TABLE dim_cst_adj_volt_dev ADD CONSTRAINT fk_dim_cst_adj_volt_dev_mgt_org_code FOREIGN KEY (mgt_org_code) REFERENCES dim_cst_mgt_org(mgt_org_code);
ALTER TABLE dim_cst_cntrl_sta ADD CONSTRAINT fk_dim_cst_cntrl_sta_mgt_org_code FOREIGN KEY (mgt_org_code) REFERENCES dim_cst_mgt_org(mgt_org_code);
ALTER TABLE dim_cst_cntrl_sta_pline_rela ADD CONSTRAINT fk_dim_cst_cntrl_sta_pline_rela_cntrl_sta_id FOREIGN KEY (cntrl_sta_id) REFERENCES dim_cst_cntrl_sta(cntrl_sta_id);
ALTER TABLE dim_cst_cntrl_sta_pline_rela ADD CONSTRAINT fk_dim_cst_cntrl_sta_pline_rela_pipeline_id FOREIGN KEY (pipeline_id) REFERENCES dim_cst_pipeline(pipeline_id);
ALTER TABLE dim_cst_cust_agrt ADD CONSTRAINT fk_dim_cst_cust_agrt_settle_acct_id FOREIGN KEY (settle_acct_id) REFERENCES dim_cst_settle_acct(settle_acct_id);
ALTER TABLE dim_cst_dev ADD CONSTRAINT fk_dim_cst_dev_mgt_org_code FOREIGN KEY (mgt_org_code) REFERENCES dim_cst_mgt_org(mgt_org_code);
ALTER TABLE dim_cst_dist_sta ADD CONSTRAINT fk_dim_cst_dist_sta_mgt_org_code FOREIGN KEY (mgt_org_code) REFERENCES dim_cst_mgt_org(mgt_org_code);
ALTER TABLE dim_cst_elec_cons_cust ADD CONSTRAINT fk_dim_cst_elec_cons_cust_cust_id FOREIGN KEY (cust_id) REFERENCES dim_cst_cust(cust_id);
ALTER TABLE dim_cst_elec_cons_cust ADD CONSTRAINT fk_dim_cst_elec_cons_cust_mgt_org_code FOREIGN KEY (mgt_org_code) REFERENCES dim_cst_mgt_org(mgt_org_code);
ALTER TABLE dim_cst_elec_meter ADD CONSTRAINT fk_dim_cst_elec_meter_dev_id FOREIGN KEY (dev_id) REFERENCES dim_cst_dev(dev_id);
ALTER TABLE dim_cst_elec_meter ADD CONSTRAINT fk_dim_cst_elec_meter_mgt_org_code FOREIGN KEY (mgt_org_code) REFERENCES dim_cst_mgt_org(mgt_org_code);
ALTER TABLE dim_cst_inst_elec_cons ADD CONSTRAINT fk_dim_cst_inst_elec_cons_cust_agrt_id FOREIGN KEY (cust_agrt_id) REFERENCES dim_cst_cust_agrt(cust_agrt_id);
ALTER TABLE dim_cst_inst_elec_cons ADD CONSTRAINT fk_dim_cst_inst_elec_cons_cust_id FOREIGN KEY (cust_id) REFERENCES dim_cst_elec_cons_cust(cust_id);
ALTER TABLE dim_cst_inst_elec_cons ADD CONSTRAINT fk_dim_cst_inst_elec_cons_dist_sta_id FOREIGN KEY (dist_sta_id) REFERENCES dim_cst_dist_sta(dist_sta_id);
ALTER TABLE dim_cst_inst_elec_cons ADD CONSTRAINT fk_dim_cst_inst_elec_cons_mgt_org_code FOREIGN KEY (mgt_org_code) REFERENCES dim_cst_mgt_org(mgt_org_code);
ALTER TABLE dim_cst_pipeline ADD CONSTRAINT fk_dim_cst_pipeline_mgt_org_code FOREIGN KEY (mgt_org_code) REFERENCES dim_cst_mgt_org(mgt_org_code);
ALTER TABLE dim_cst_pline_dist_sta_rela ADD CONSTRAINT fk_dim_cst_pline_dist_sta_rela_dist_sta_id FOREIGN KEY (dist_sta_id) REFERENCES dim_cst_dist_sta(dist_sta_id);
ALTER TABLE dim_cst_pline_dist_sta_rela ADD CONSTRAINT fk_dim_cst_pline_dist_sta_rela_pipeline_id FOREIGN KEY (pipeline_id) REFERENCES dim_cst_pipeline(pipeline_id);
ALTER TABLE dwd_cst_a_ll_dist_det_day ADD CONSTRAINT fk_dwd_cst_a_ll_dist_det_day_dist_sta_id FOREIGN KEY (dist_sta_id) REFERENCES dim_cst_dist_sta(dist_sta_id);
ALTER TABLE dwd_cst_acct_bal ADD CONSTRAINT fk_dwd_cst_acct_bal_cust_no FOREIGN KEY (cust_no) REFERENCES dim_cst_elec_cons_cust(cust_no);
ALTER TABLE dwd_cst_acct_bal ADD CONSTRAINT fk_dwd_cst_acct_bal_mgt_org_code FOREIGN KEY (mgt_org_code) REFERENCES dim_cst_mgt_org(mgt_org_code);
ALTER TABLE dwd_cst_addl_charg ADD CONSTRAINT fk_dwd_cst_addl_charg_bilg_card_id FOREIGN KEY (bilg_card_id) REFERENCES dwd_cst_inst_bilg_card(bilg_card_id);
ALTER TABLE dwd_cst_bilg_rs_rcpt ADD CONSTRAINT fk_dwd_cst_bilg_rs_rcpt_cust_no FOREIGN KEY (cust_no) REFERENCES dim_cst_elec_cons_cust(cust_no);
ALTER TABLE dwd_cst_charg_acct ADD CONSTRAINT fk_dwd_cst_charg_acct_mgt_org_code FOREIGN KEY (mgt_org_code) REFERENCES dim_cst_mgt_org(mgt_org_code);
ALTER TABLE dwd_cst_es_e_mp_comp_curve_h ADD CONSTRAINT fk_dwd_cst_es_e_mp_comp_curve_h_meter_asset_no FOREIGN KEY (meter_asset_no) REFERENCES dwd_cst_meter_run(meter_asset_no);
ALTER TABLE dwd_cst_es_e_mp_comp_curve_h_a ADD CONSTRAINT fk_dwd_cst_es_e_mp_comp_curve_h_a_meter_asset_no FOREIGN KEY (meter_asset_no) REFERENCES dwd_cst_meter_run(meter_asset_no);
ALTER TABLE dwd_cst_es_e_mp_comp_curve_h_v ADD CONSTRAINT fk_dwd_cst_es_e_mp_comp_curve_h_v_meter_asset_no FOREIGN KEY (meter_asset_no) REFERENCES dwd_cst_meter_run(meter_asset_no);
ALTER TABLE dwd_cst_es_meter_energy_day_l_xz ADD CONSTRAINT fk_dwd_cst_es_meter_energy_day_l_xz_meter_asset_no FOREIGN KEY (meter_asset_no) REFERENCES dwd_cst_meter_run(meter_asset_no);
ALTER TABLE dwd_cst_es_meter_energy_day_l_xz ADD CONSTRAINT fk_dwd_cst_es_meter_energy_day_l_xz_mgt_org_code FOREIGN KEY (mgt_org_code) REFERENCES dim_cst_mgt_org(mgt_org_code);
ALTER TABLE dwd_cst_es_meter_energy_day_p ADD CONSTRAINT fk_dwd_cst_es_meter_energy_day_p_meter_asset_no FOREIGN KEY (meter_asset_no) REFERENCES dwd_cst_meter_run(meter_asset_no);
ALTER TABLE dwd_cst_inst_bilg_card ADD CONSTRAINT fk_dwd_cst_inst_bilg_card_calc_id FOREIGN KEY (calc_id) REFERENCES dwd_cst_rcvbl_acct(calc_id);
ALTER TABLE dwd_cst_inst_bilg_card ADD CONSTRAINT fk_dwd_cst_inst_bilg_card_inst_id FOREIGN KEY (inst_id) REFERENCES dim_cst_inst_elec_cons(inst_id);
ALTER TABLE dwd_cst_inst_bilg_card ADD CONSTRAINT fk_dwd_cst_inst_bilg_card_mgt_org_code FOREIGN KEY (mgt_org_code) REFERENCES dim_cst_mgt_org(mgt_org_code);
ALTER TABLE dwd_cst_meter_energy_day_h_xz ADD CONSTRAINT fk_dwd_cst_meter_energy_day_h_xz_meter_asset_no FOREIGN KEY (meter_asset_no) REFERENCES dwd_cst_meter_run(meter_asset_no);
ALTER TABLE dwd_cst_meter_energy_day_h_xz ADD CONSTRAINT fk_dwd_cst_meter_energy_day_h_xz_mgt_org_code FOREIGN KEY (mgt_org_code) REFERENCES dim_cst_mgt_org(mgt_org_code);
ALTER TABLE dwd_cst_meter_run ADD CONSTRAINT fk_dwd_cst_meter_run_inst_id FOREIGN KEY (inst_id) REFERENCES dim_cst_inst_elec_cons(inst_id);
ALTER TABLE dwd_cst_meter_run ADD CONSTRAINT fk_dwd_cst_meter_run_meter_id FOREIGN KEY (meter_id) REFERENCES dim_cst_elec_meter(meter_id);
ALTER TABLE dwd_cst_meter_run ADD CONSTRAINT fk_dwd_cst_meter_run_mgt_org_code FOREIGN KEY (mgt_org_code) REFERENCES dim_cst_mgt_org(mgt_org_code);
ALTER TABLE dwd_cst_mr_data ADD CONSTRAINT fk_dwd_cst_mr_data_inst_id_meter_asset_no FOREIGN KEY (inst_id, meter_asset_no) REFERENCES dwd_cst_meter_run(inst_id, meter_asset_no);
ALTER TABLE dwd_cst_rcvbl_acct ADD CONSTRAINT fk_dwd_cst_rcvbl_acct_mgt_org_code FOREIGN KEY (mgt_org_code) REFERENCES dim_cst_mgt_org(mgt_org_code);
ALTER TABLE dwd_cst_rcvbl_addl_acct ADD CONSTRAINT fk_dwd_cst_rcvbl_addl_acct_rcvbl_acct_id FOREIGN KEY (rcvbl_acct_id) REFERENCES dwd_cst_rcvbl_acct(rcvbl_acct_id);
ALTER TABLE dwd_cst_rcvd_acct ADD CONSTRAINT fk_dwd_cst_rcvd_acct_charg_acct_id FOREIGN KEY (charg_acct_id) REFERENCES dwd_cst_charg_acct(charg_acct_id);
ALTER TABLE dwd_cst_rcvd_acct ADD CONSTRAINT fk_dwd_cst_rcvd_acct_mgt_org_code FOREIGN KEY (mgt_org_code) REFERENCES dim_cst_mgt_org(mgt_org_code);
ALTER TABLE dwd_cst_rcvd_acct ADD CONSTRAINT fk_dwd_cst_rcvd_acct_rcvbl_acct_id FOREIGN KEY (rcvbl_acct_id) REFERENCES dwd_cst_rcvbl_acct(rcvbl_acct_id);
ALTER TABLE dwd_cst_rs_inst_bilg_card ADD CONSTRAINT fk_dwd_cst_rs_inst_bilg_card_bilg_rs_rcpt_id FOREIGN KEY (bilg_rs_rcpt_id) REFERENCES dwd_cst_bilg_rs_rcpt(bilg_rs_rcpt_id);
ALTER TABLE dwd_cst_sgmt_qty_charg ADD CONSTRAINT fk_dwd_cst_sgmt_qty_charg_bilg_card_id FOREIGN KEY (bilg_card_id) REFERENCES dwd_cst_inst_bilg_card(bilg_card_id);
ALTER TABLE dwd_cst_special_expense ADD CONSTRAINT fk_dwd_cst_special_expense_bilg_card_id FOREIGN KEY (bilg_card_id) REFERENCES dwd_cst_inst_bilg_card(bilg_card_id);
-- ============================================================
-- 补充外键关系（2026-07 问答对库治理：业务确认有效的实际业务连接，
-- 对应问答对库主外键治理报告模式1-5）
-- ============================================================

ALTER TABLE dwd_cst_meter_run ADD CONSTRAINT fk_dwd_cst_meter_run_meter_id_dev FOREIGN KEY (meter_id) REFERENCES dim_cst_dev(dev_id);
ALTER TABLE dwd_cst_meter_run ADD CONSTRAINT fk_dwd_cst_meter_run_cust_id FOREIGN KEY (cust_id) REFERENCES dim_cst_elec_cons_cust(cust_id);
ALTER TABLE dim_cst_cust ADD CONSTRAINT fk_dim_cst_cust_mgt_org_code FOREIGN KEY (mgt_org_code) REFERENCES dim_cst_mgt_org(mgt_org_code);
ALTER TABLE dwd_cst_acct_bal ADD CONSTRAINT fk_dwd_cst_acct_bal_cust_no_cust FOREIGN KEY (cust_no) REFERENCES dim_cst_cust(cust_no);
ALTER TABLE dim_cst_pline_dist_sta_rela ADD CONSTRAINT fk_dwd_cst_pline_dist_sta_rela_dist_sta_adj FOREIGN KEY (dist_sta_id) REFERENCES dim_cst_adj_volt_dev(dist_sta_id);

-- 2026-08-07 补充：业务伙伴关联（bp_id 在两库物理列上均存在，精简版遗漏）
ALTER TABLE dim_cst_cust ADD CONSTRAINT fk_dim_cst_cust_bp_id FOREIGN KEY (bp_id) REFERENCES dim_cst_business_partner(bp_id);
ALTER TABLE dim_cst_elec_cons_cust ADD CONSTRAINT fk_dim_cst_elec_cons_cust_bp_id FOREIGN KEY (bp_id) REFERENCES dim_cst_business_partner(bp_id);

-- 2026-08-07 补充：电能表与计量点运行关联（MySQL 物理外键，用户确认正确）
ALTER TABLE dim_cst_elec_meter ADD CONSTRAINT fk_dim_cst_elec_meter_dev_id_meter_run FOREIGN KEY (dev_id) REFERENCES dwd_cst_meter_run(meter_id);
