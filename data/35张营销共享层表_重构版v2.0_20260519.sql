-- ============================================================
-- 共享层表重构后建表语句
-- 基于智能问数友好型重构分析结果生成
-- 仅保留主键、外键及核心业务字段
-- 生成时间: 2026-05-11
-- 更新记录：2026-05-19
-- 1、用电客户dim_cst_elec_cons_cust表结构重构，基于共享层完整结构重新删减，保留大部分维度2、主键字段长度从varchar(255)调整为varchar(50)
-- ============================================================
-- 其中主数据表清单：
-- dim_cst_elec_cons_cust
-- dim_cst_inst_elec_cons
-- dwd_cst_meter_run
-- dim_cst_elec_meter
-- dim_cst_adj_volt_dev
-- dim_cst_pipeline
-- dim_cst_dist_sta
-- dim_cst_cntrl_sta
-- dim_cst_dev
-- dim_cst_mgt_org
-- dim_cst_business_partner
-- dim_cst_settle_acct
-- dim_cst_cust_agrt
-- dim_cst_cust

CREATE TABLE IF NOT EXISTS dim_cst_elec_cons_cust
(
    elec_cons_cust_id         BIGINT        COMMENT '客户用电标识',
    bp_id                     BIGINT        COMMENT '伙伴标识',
    cust_id                   BIGINT        COMMENT '客户标识',
    cust_no                   varchar(50)        COMMENT '客户编号',
    cust_name                 varchar(255)        COMMENT '客户名称',
    estab_acct_date           DATETIME      COMMENT '立户日期',
    cncl_date                 DATETIME      COMMENT '销户日期',
    ecc_stat_desc             varchar(255)        COMMENT '用电户状态描述',
    ctrt_cap                  DECIMAL(20,6) COMMENT '合同容量',
    run_cap                   DECIMAL(20,6) COMMENT '运行容量',
    cust_cls_desc             varchar(255)        COMMENT '用电客户分类描述',
    cust_ind_cls_desc         varchar(255)        COMMENT '行业分类描述',
    cust_ind_cls_desc_1       varchar(255)        COMMENT '一级行业分类',
    ec_categ_desc             varchar(255)        COMMENT '用能类别描述',
    cust_volt_desc            varchar(255)        COMMENT '基础承压描述',
    volt_cls01                varchar(255)        COMMENT '电压分类标识1',
    volt_cls02                varchar(255)        COMMENT '电压分类标识2',
    volt_cls03                varchar(255)        COMMENT '电压分类标识3',
    volt_cls04                varchar(255)        COMMENT '电压分类标识4',
    high_ec_ind_cls_desc      varchar(255)        COMMENT '高耗能行业分类描述',
    impt_lv_desc              varchar(255)        COMMENT '重要性等级描述',
    dereg_attr_cls_desc       varchar(255)        COMMENT '市场化属性分类描述',
    cost_ctrl_flag_desc       varchar(255)        COMMENT '费控标志描述',
    graded_settle_flag_desc   varchar(255)        COMMENT '分次结算标志描述',
    load_char_desc            varchar(255)        COMMENT '负荷性质描述',
    load_charts_desc          varchar(255)        COMMENT '负荷特性描述',
    transfer_flag_desc        varchar(255)        COMMENT '转供户标志描述',
    cstm_query_no             varchar(255)        COMMENT '自定义查询号',
    holiday                   varchar(255)        COMMENT '厂休日',
    tmp_ec_flag_desc          varchar(255)        COMMENT '临时用能标志描述',
    tmp_expr_date             DATETIME      COMMENT '临时到期日期',
    main_hshd_flag_desc       varchar(255)        COMMENT '主户标志描述',
    chk_cyc                   varchar(255)        COMMENT '检查周期',
    last_insp_date            DATETIME      COMMENT '上次检查日期',
    chkr_no                   varchar(255)        COMMENT '用电检查人员',
    e_sdate                   DATETIME      COMMENT '送能日期',
    prod_shift_desc           varchar(255)        COMMENT '生产班次描述',
    stop_supl_flag_desc       varchar(255)        COMMENT '停供标志描述',
    stop_supl_mode_desc       varchar(255)        COMMENT '停供方式描述',
    pwr_off_reason            varchar(255)        COMMENT '停电原因',
    rcvr_supl_mode_desc       varchar(255)        COMMENT '复供方式描述',
    e_carea                   DOUBLE        COMMENT '用能面积',
    ec_stf_num                BIGINT        COMMENT '用能人数',
    recent_chg_date           DATETIME      COMMENT '最近变更日期',
    ec_addr                   varchar(255)        COMMENT '用能地址',
    valid_date                DATETIME      COMMENT '生效日期',
    usage_dur                 varchar(255)        COMMENT '使用期限',
    invalid_date              DATETIME      COMMENT '失效日期',
    lock_stat_desc            varchar(255)        COMMENT '锁定状态描述',
    attach_id                 BIGINT        COMMENT '附件标识',
    charg_remind_unit_no      varchar(255)        COMMENT '催费单元编号',
    scy_cap                   DECIMAL(20,6) COMMENT '保安负荷容量',
    hndl_time                 DATETIME      COMMENT '办理时间',
    urbanrural_flag_desc      varchar(255)        COMMENT '城乡类别描述',
    billing_unit_no           varchar(255)        COMMENT '计费单元编号',
    mr_unit_no                varchar(255)        COMMENT '抄表单元编号',
    bus_srv_addr_id           BIGINT        COMMENT '业务服务地址标识',
    bus_prov_code             varchar(255)        COMMENT '省码',
    bus_city_code             varchar(255)        COMMENT '市码',
    bus_county_code           varchar(255)        COMMENT '区县码',
    bus_st_code               varchar(255)        COMMENT '街道码（乡镇）',
    bus_neighbor_comm_code    varchar(255)        COMMENT '社区码（村）',
    bus_rd_code               varchar(255)        COMMENT '道路码',
    bus_cmny_code             varchar(255)        COMMENT '小区码',
    bus_prov_name             varchar(255)        COMMENT '省名称',
    bus_city_name             varchar(255)        COMMENT '市名称',
    bus_county_name           varchar(255)        COMMENT '区县名称',
    bus_st_name               varchar(255)        COMMENT '街道名称（乡镇）',
    bus_neighbor_comm_name    varchar(255)        COMMENT '社区名称（村）',
    bus_rd_name               varchar(255)        COMMENT '道路名称',
    bus_cmny_name             varchar(255)        COMMENT '小区名称',
    mgt_org_code              varchar(50)        COMMENT '管理单位编码',
    is_cdzh                   BIGINT        COMMENT '是否充电桩户',
    is_yqh                    BIGINT        COMMENT '是否园区户',
    is_zgh                    BIGINT        COMMENT '是否转供户',
    is_bzgh                   BIGINT        COMMENT '是否被转供户',
    is_dbh                    BIGINT        COMMENT '是否低保户',
    is_wbh                    BIGINT        COMMENT '是否五保户',
    id_card                   varchar(255)        COMMENT '身份证',
    vat_tax_no                varchar(255)        COMMENT '增值税编号',
    credit_code               varchar(255)        COMMENT '统一社会信用代码',
    vat_no                    varchar(255)        COMMENT '增值税号',
    is_gs                     BIGINT        COMMENT '是否规上企业',
    is_xw                     BIGINT        COMMENT '是否小微企业',
    cust_contact_id           BIGINT        COMMENT '客户联系信息标识',
    contact_name_zw           varchar(255)        COMMENT '账务联系人名称',
    contact_mobile_zw         varchar(255)        COMMENT '账务联系人电话',
    id_card_new               varchar(255)        COMMENT '最新身份证号码',
    credit_code_new           varchar(255)        COMMENT '最新统一社会信用代码',
    vat_no_new                varchar(255)        COMMENT '最新增值税号',
    is_credit_code_new        varchar(255)        COMMENT '统一社会信用代码是否通过验证',
    write_time                DATETIME      COMMENT '入库时间',
 primary key (cust_id)
) 
COMMENT '用电客户'
;


CREATE TABLE IF NOT EXISTS dim_cst_inst_elec_cons
(
 `inst_id` BIGINT COMMENT '安装点标识',
 `inst_no` varchar(255) COMMENT '安装点编号',
 `inst_name` varchar(255) COMMENT '安装点名称',
 `cust_id` BIGINT COMMENT '客户标识',
 `inst_cls_desc` varchar(255) COMMENT '安装点分类描述',
 `inst_char_desc` varchar(255) COMMENT '安装点性质描述',
 `inst_affil_side_desc` varchar(255) COMMENT '安装点所属侧描述',
 `inst_usage_cls_desc` varchar(255) COMMENT '安装点用途分类描述',
 `inst_cap` DECIMAL(20,6) COMMENT '安装点容量',
 `prc_code` varchar(255) COMMENT '价格码',
 `ctlg_prc_name` varchar(255) COMMENT '目录定价名称',
 `cust_agrt_id` BIGINT COMMENT '客户协议标识',
 `prem_id` BIGINT COMMENT '房产标识',
 `srv_loc_id` BIGINT COMMENT '服务位置标识',
 `iot_point_id` BIGINT COMMENT '物联点标识',
 `pipeline_id` BIGINT COMMENT '管线标识',
 `dist_sta_id` BIGINT COMMENT '配送站标识',
 `cntrl_sta_id` BIGINT COMMENT '枢纽站标识',
 `mgt_org_code` varchar(255) COMMENT '管理单位编码',
 `loc_id` BIGINT COMMENT '设备位置标识',
 primary key (inst_id)
)
COMMENT '安装点用电（计量点）'
;

CREATE TABLE IF NOT EXISTS dwd_cst_meter_run
(
 `meter_id` BIGINT COMMENT '表计标识',
 `ref_meter_id` BIGINT COMMENT '参考表计标识',
 `comp_rto` BIGINT COMMENT '综合倍率',
 `ref_meter_flag_desc` varchar(255) COMMENT '参考表标志描述',
 `dev_cls_desc` varchar(255) COMMENT '设备分类描述',
 `share_flag_desc` varchar(255) COMMENT '共用标志描述',
 `share_met_logic_id` BIGINT COMMENT '共用计量表计逻辑标识',
 `loc_id` BIGINT COMMENT '设备位置标识',
 `meter_logic_id` BIGINT COMMENT '计量表计逻辑标识',
 `inst_id` BIGINT COMMENT '安装点标识',
 `cust_id` BIGINT COMMENT '客户标识',
 `dev_no` varchar(255) COMMENT '设备编号',
 `bar_code` varchar(255) COMMENT '条形码',
 `meter_asset_no` varchar(50) COMMENT '资产编号',
 `mgt_org_code` varchar(255) COMMENT '管理单位编码',
 primary key (meter_asset_no)
)
COMMENT '计量表计'
;

CREATE TABLE IF NOT EXISTS dim_cst_elec_meter
(
 `dev_id` BIGINT COMMENT '设备标识',
 `bar_code` varchar(255) COMMENT '条形码  ',
 `rv_desc` varchar(255) COMMENT '额定电压描述',
 `cali_cur_desc` varchar(255) COMMENT '标定电流描述',
 `wire_mode_desc` varchar(255) COMMENT '接线方式描述',
 `self_rto` BIGINT COMMENT '自身倍率',
 `bidi_meter_flag_desc` varchar(255) COMMENT '双向计量标志描述',
 `prc_id` BIGINT COMMENT '电价标识',
 `asset_no` varchar(255) COMMENT '资产编号',
 `mgt_org_code` varchar(255) COMMENT '管理单位编码',
 `wh_id` BIGINT COMMENT '库房标识',
 `wh_area_id` BIGINT COMMENT '库区标识',
 `stor_area_id` BIGINT COMMENT '存放区标识',
 `stor_loc_id` BIGINT COMMENT '储位标识',
 primary key (dev_id)
)
COMMENT '电能表维表'
;

CREATE TABLE IF NOT EXISTS dim_cst_adj_volt_dev
(
 `adj_volt_dev_id` BIGINT COMMENT '调压设备标识',
 `dist_sta_id` BIGINT COMMENT '配送站标识',
 `dev_no` varchar(255) COMMENT '调压设备编码',
 `dev_name` varchar(255) COMMENT '调压设备名称',
 `dev_type_desc` varchar(255) COMMENT '设备类型描述',
 `publ_clg_flag_desc` varchar(255) COMMENT '公专标志描述',
 `np_cap` DECIMAL(20,6) COMMENT '铭牌容量',
 `run_stat_desc` varchar(255) COMMENT '运行状态描述',
 `voltage_desc` varchar(255) COMMENT '承压描述',
 `primary_side_bearing_desc` varchar(255) COMMENT '一次侧承压描述',
 `secondary_side_bearing_desc` varchar(255) COMMENT '二次侧承压描述',
 `adj_volt_dev_asset_id` BIGINT COMMENT '调压设备资产标识',
 `cust_id` BIGINT COMMENT '客户标识',
 `loc_id` BIGINT COMMENT '设备位置标识',
 `mgt_org_code` varchar(255) COMMENT '管理单位编码',
 primary key (adj_volt_dev_id)
)
COMMENT '调压设备'
;

CREATE TABLE IF NOT EXISTS dim_cst_pipeline
(
 `pipeline_id` BIGINT COMMENT '管线标识',
 `pipeline_no` varchar(255) COMMENT '管线编码',
 `pipeline_name` varchar(255) COMMENT '管线名称',
 `branch_flag_desc` varchar(255) COMMENT '分支标志描述',
 `line_publ_clg_flag` varchar(255) COMMENT '线路公专标志',
 `line_publ_clg_flag_desc` varchar(255) COMMENT '线路公专标志描述',
 `pipeline_len` DECIMAL(20,6) COMMENT '管线长度',
 `voltage_desc` varchar(255) COMMENT '承压描述',
 `pip_line_run_stat_desc` varchar(255) COMMENT '管线运行状态描述',
 `bus_srv_addr_id` BIGINT COMMENT '业务服务地址标识',
 `pms_pipeline_id` varchar(255) COMMENT '电网线路标识',
 `path_org_code` varchar(255) COMMENT '途径单位',
 `path_org_name` varchar(255) COMMENT '途径单位名称',
 `cust_id` BIGINT COMMENT '客户标识',
 `mgt_org_code` varchar(255) COMMENT '管理单位编码',
 primary key (pipeline_id)
)
COMMENT '管线维表'
;

CREATE TABLE IF NOT EXISTS dim_cst_dist_sta
(
 `dist_sta_id` BIGINT COMMENT '配送站标识',
 `resrc_supl_code` varchar(255) COMMENT '配送站编码',
 `resrc_supl_name` varchar(255) COMMENT '配送站名称',
 `publ_clg_flag_desc` varchar(255) COMMENT '公专标志描述',
 `resrc_supl_stat_desc` varchar(255) COMMENT '配送站状态描述',
 `srv_kind_desc` varchar(255) COMMENT '服务种类描述',
 `voltage_desc` varchar(255) COMMENT '承压描述',
 `dist_stacap` DECIMAL(20,6) COMMENT '配送站容量',
 `mgt_org_code` varchar(255) COMMENT '管理单位编码',
 primary key (dist_sta_id)
)
COMMENT '配送站'
;

CREATE TABLE IF NOT EXISTS dim_cst_pline_dist_sta_rela
(
 `pipe_line_dist_sta_rela_id` BIGINT COMMENT '管线配送站关系标识',
 `pipeline_id` BIGINT COMMENT '管线标识',
 `dist_sta_id` BIGINT COMMENT '配送站标识',
 primary key (pipe_line_dist_sta_rela_id)
)
COMMENT '管线配送站关系'
;

CREATE TABLE IF NOT EXISTS dim_cst_cntrl_sta
(
 `cntrl_sta_id` BIGINT COMMENT '枢纽站标识',
 `resrc_supl_code` varchar(255) COMMENT '枢纽站编码',
 `resrc_supl_name` varchar(255) COMMENT '枢纽站名称',
 `resrc_supl_stat_desc` varchar(255) COMMENT '枢纽站状态描述',
 `srv_kind_desc` varchar(255) COMMENT '服务种类描述',
 `det_addr` varchar(255) COMMENT '详细地址',
 `voltage_desc` varchar(255) COMMENT '承压描述',
 `dev_cap` DECIMAL(20,6) COMMENT '设备容量',
 `pms_cntrl_sta_id` varchar(255) COMMENT '电网枢纽站标识',
 `conn_line_id` BIGINT COMMENT '入线标识',
 `mgt_org_code` varchar(255) COMMENT '管理单位编码',
 primary key (cntrl_sta_id)
)
COMMENT '枢纽站_变电站'
;

CREATE TABLE IF NOT EXISTS dim_cst_cntrl_sta_pline_rela
(
 `cntrl_sta_pipe_line_rela_id` BIGINT COMMENT '枢纽站管线关系标识',
 `cntrl_sta_id` BIGINT COMMENT '枢纽站标识',
 `pipeline_id` BIGINT COMMENT '管线标识',
 primary key (cntrl_sta_pipe_line_rela_id)
)
COMMENT '枢纽站管线关系'
;

CREATE TABLE IF NOT EXISTS dim_cst_dev
(
 `dev_id` BIGINT COMMENT '设备标识',
 `dev_code_id` BIGINT COMMENT '设备码标识',
 `dev_code_no` varchar(255) COMMENT '设备码编号',
 `dev_code_name` varchar(255) COMMENT '设备码名称',
 `bar_code` varchar(255) COMMENT '条形码',
 `asset_no` varchar(255) COMMENT '资产编号',
 `dev_cls_desc` varchar(255) COMMENT '设备分类描述',
 `categ_desc` varchar(255) COMMENT '设备类别描述',
 `dev_type_desc` varchar(255) COMMENT '设备类型描述',
 `dev_stat_desc` varchar(255) COMMENT '设备状态描述',
 `wh_id` BIGINT COMMENT '库房标识',
 `wh_area_id` BIGINT COMMENT '库区标识',
 `stor_area_id` BIGINT COMMENT '存放区标识',
 `stor_loc_id` BIGINT COMMENT '储位标识',
 `instal_date` DATETIME COMMENT '安装日期',
 `creator` varchar(255) COMMENT '创建人',
 `mgt_org_code` varchar(255) COMMENT '管理单位编码',
 primary key (dev_id)
)
COMMENT '设备'
;

CREATE TABLE IF NOT EXISTS dim_cst_mgt_org
(
 `mgt_org_id` BIGINT COMMENT '管理单位标识',
 `mgt_org_code` varchar(50) COMMENT '管理单位编码',
 `mgt_org_name` varchar(255) COMMENT '管理单位名称',
 `prnt_mgt_org_code` varchar(255) COMMENT '上级管理单位编码',
 `mgt_org_type_desc` varchar(255) COMMENT '管理单位类型描述',
 `dist_lv_desc` varchar(255) COMMENT '区域层级描述',
 `valid_flag_desc` varchar(255) COMMENT '有效标志描述',
 `mgt_org_chn_abbr1` varchar(255) COMMENT '管理单位名称简称1',
 `mgt_org_chn_abbr2` varchar(255) COMMENT '管理单位名称简称2',
 `mgt_org_char_desc` varchar(255) COMMENT '管理单位性质描述',
 `maj_attr_desc` varchar(255) COMMENT '专业属性描述',
 `valid_date` DATETIME COMMENT '生效日期',
 `invalid_date` DATETIME COMMENT '失效日期',
 `relamgt_org_id` BIGINT COMMENT '关联管理单位标识',
 `sys_mgt_org_id` varchar(255) COMMENT '系统管理单位标识',
 `srv_kind` varchar(255) COMMENT '服务种类',
 `srv_kind_desc` varchar(255) COMMENT '服务种类描述',
 `area` varchar(255) COMMENT '所属区域',
 `province_code` varchar(255) COMMENT '所属省公司代码',
 `province_name` varchar(255) COMMENT '所属省公司名称',
 `province_abbr` varchar(255) COMMENT '所属省公司简称',
 `city_code` varchar(255) COMMENT '所属市公司代码',
 `city_name` varchar(255) COMMENT '所属市公司名称',
 `city_abbr` varchar(255) COMMENT '所属市公司简称',
 `county_code` varchar(255) COMMENT '所属县公司代码',
 `county_name` varchar(255) COMMENT '所属县公司名称',
 `county_abbr` varchar(255) COMMENT '所属县公司简称',
 `station_code` varchar(255) COMMENT '所属县供电所代码',
 `station_name` varchar(255) COMMENT '所属县供电所名称',
 primary key (mgt_org_code)
)
COMMENT '管理单位'
;

CREATE TABLE IF NOT EXISTS dim_cst_business_partner
(
 `bp_id` BIGINT COMMENT '伙伴标识',
 `prtr_no` varchar(255) COMMENT '伙伴编号',
 `bp_name` varchar(255) COMMENT '伙伴名称',
 `bp_categ_desc` varchar(255) COMMENT '伙伴类别描述',
 `bp_type_desc` varchar(255) COMMENT '伙伴类型描述',
 `bp_stat` varchar(255) COMMENT '业务伙伴状态',
 `query_pwd` varchar(255) COMMENT '查询密码',
 `industry_cls_desc` varchar(255) COMMENT '产业分类描述',
 `prtr_srch_word` varchar(255) COMMENT '业务伙伴搜索词',
 `bus_prtr_grp` varchar(255) COMMENT '业务伙伴分组',
 `auth_grp` varchar(255) COMMENT '权限组',
 `greeting` varchar(255) COMMENT '称谓',
 `bus_srv_addr_id` BIGINT COMMENT '业务地址标识',
 `risk_lv_desc` varchar(255) COMMENT '风险等级描述',
 `credit_lv_desc` varchar(255) COMMENT '信用等级描述',
 `cr_score` BIGINT COMMENT '信用分',
 `not_type_desc` varchar(255) COMMENT '票据类型描述',
 primary key (bp_id)
)
COMMENT '客户_业务伙伴'
;

CREATE TABLE IF NOT EXISTS dim_cst_settle_acct
(
 `settle_acct_id` BIGINT COMMENT '结算账户标识',
 `settle_acct_no` varchar(255) COMMENT '结算账户编号',
 `settle_acct_name` varchar(255) COMMENT '结算账户名称',
 `settle_acct_alias` varchar(255) COMMENT '合同账户别名',
 `settle_acct_categ_desc` varchar(255) COMMENT '结算账户类别描述',
 `setl_agrt_categ_desc` varchar(255) COMMENT '结算协议类别描述',
 `settle_categ_desc` varchar(255) COMMENT '结算类别描述',
 `bill_mode_desc` varchar(255) COMMENT '账单方式描述',
 `pay_chan_desc` varchar(255) COMMENT '交费渠道描述',
 `prepay_mode_desc` varchar(255) COMMENT '付费方式描述',
 `inv_mode_desc` varchar(255) COMMENT '开票模式描述',
 `note_type_desc` varchar(255) COMMENT '票据类型描述',
 `ntce_mode_desc` varchar(255) COMMENT '通知方式描述',
 `valid_flag_desc` varchar(255) COMMENT '生效标志',
 `rela_settle_acct_no` varchar(255) COMMENT '关联结算账号',
 `mgt_org_code` varchar(255) COMMENT '管理单位编码',
 `bp_id` BIGINT COMMENT '伙伴标识',
 `settle_acct_stat` varchar(255) COMMENT '合同账户状态',
 `cust_no` varchar(255) COMMENT '客户编号:默认客户的编号',
 `acct_usage_type` varchar(255) COMMENT '账户使用类型',
 primary key (settle_acct_id)
)
COMMENT '合同账户'
;

CREATE TABLE IF NOT EXISTS dim_cst_cust_agrt
(
 `cust_agrt_id` BIGINT COMMENT '客户协议标识',
 `cust_id` BIGINT COMMENT '客户标识',
 `ctrt_cap` DECIMAL(20,6) COMMENT '合同容量',
 `run_cap` DECIMAL(20,6) COMMENT '运行容量',
 `cust_cls_desc` varchar(255) COMMENT '用户分类描述',
 `ec_categ_desc` varchar(255) COMMENT '用能类别描述',
 `cust_volt_desc` varchar(255) COMMENT '基础承压描述',
 `urbanrural_flag_desc` varchar(255) COMMENT '城乡类别描述',
 `agrt_categ_desc` varchar(255) COMMENT '协议类别描述',
 `prc_code` varchar(255) COMMENT '价格码',
 `bilg_std_ver_no` varchar(255) COMMENT '计费标准版本号',
 `ctlg_prc_name` varchar(255) COMMENT '目录定价名称',
 `disc_mode_cls_desc` varchar(255) COMMENT '优惠方式分类描述',
 `fix_rto` DECIMAL(20,6) COMMENT '固定力率值',
 `time_sec_num` varchar(255) COMMENT '时段数目',
 `timesec_flag_desc` varchar(255) COMMENT '执行分时标志描述',
 `srv_loc_id` BIGINT COMMENT '服务位置标识',
 `agrt_prc` DECIMAL(28,8) COMMENT '协议价格',
 `settle_acct_id` BIGINT COMMENT '结算账户标识',
 `mgt_org_code` varchar(255) COMMENT '管理单位编码',
 primary key (cust_agrt_id)
)
COMMENT '客户协议'
;

CREATE TABLE IF NOT EXISTS dwd_cst_inst_bilg_card
(
 `bilg_card_id` BIGINT COMMENT '计费卡标识',
 `calc_id` BIGINT COMMENT '计算标识',
 `qty_charg_ym` varchar(255) COMMENT '量费年月',
 `calc_time` DATETIME COMMENT '计算时间',
 `prc_ec_categ` varchar(255) COMMENT '电价用能类别',
 `t_settle_qty` BIGINT COMMENT '总结算量',
 `t_settle_exp` DECIMAL(20,6) COMMENT '总结算费',
 `t_ctlg_exp` DECIMAL(20,6) COMMENT '总目录费用',
 `deg_exp` DECIMAL(20,6) COMMENT '度数费用',
 `t_addl_charg` DECIMAL(20,6) COMMENT '总加收费',
 `ext_settle_qty` BIGINT COMMENT '扩展结算量',
 `t_self_cons_elec_qty` BIGINT COMMENT '总自发自用电量',
 `t_self_cons_elec_charg` DECIMAL(20,6) COMMENT '总自发自用加收电费',
 `settle_qty_01` BIGINT COMMENT '尖峰结算量',
 `settle_qty_02` BIGINT COMMENT '峰结算量',
 `settle_qty_03` BIGINT COMMENT '平结算量',
 `settle_qty_04` BIGINT COMMENT '谷结算量',
 `settle_qty_05` BIGINT COMMENT '其他结算量',
 `inst_snap_id` BIGINT COMMENT '安装点快照标识',
 `inst_id` BIGINT COMMENT '安装点标识',
 `mgt_org_code` varchar(255) COMMENT '管理单位编码',
 primary key (bilg_card_id)
)
COMMENT '安装点计费卡信息'
;

CREATE TABLE IF NOT EXISTS dwd_cst_acct_bal
(
 `acct_bal_id` BIGINT COMMENT '账户余额标识',
 `cust_no` varchar(255) COMMENT '客户编号',
 `t_bal` DECIMAL(20,6) COMMENT '总余额',
 `frz_amt` DECIMAL(20,6) COMMENT '冻结金额',
 `tmp_frz_amt` DECIMAL(20,6) COMMENT '临时冻结金额',
 `cur_avail_bal` DECIMAL(20,6) COMMENT '当前可用余额',
 `rcvd_adv_bal` DECIMAL(20,6) COMMENT '预收余额',
 `rchg_card_bal` DECIMAL(20,6) COMMENT '充值卡余额',
 `spcl_bal` DECIMAL(20,6) COMMENT '专用余额',
 `thrd_prty_bal` DECIMAL(20,6) COMMENT '第三方余额',
 `mgt_org_code` varchar(255) COMMENT '管理单位编码',
 primary key (acct_bal_id)
)
COMMENT '余额表'
;

CREATE TABLE IF NOT EXISTS dwd_cst_es_meter_energy_day_l_xz
(
 `pap_e` DECIMAL(20,6) COMMENT '正向有功总电能量',
 `rap_e` DECIMAL(16,4) COMMENT '反向有功总电能量',
 `data_date` DATETIME COMMENT '数据时间;数据时间',
 `meter_asset_no` varchar(50) COMMENT '表计资产编号',
 `mgt_org_code` varchar(255) COMMENT '管理单位编码',
 primary key (data_date, meter_asset_no)
)
COMMENT '低压用户日电量（修正）'
;

CREATE TABLE IF NOT EXISTS dwd_cst_meter_energy_day_h_xz
(
 `data_date` DATETIME COMMENT '数据时间',
 `meter_asset_no` varchar(50) COMMENT '表计资产编号',
 `t_factor` DECIMAL(20,6) COMMENT '综合倍率',
 `pap_e` DECIMAL(20,6) COMMENT '正向有功总电能量',
 `rap_e` DECIMAL(20,6) COMMENT '反向有功总电能量',
 `prp_e` DECIMAL(20,6) COMMENT '正向无功总电能量',
 `mgt_org_code` varchar(255) COMMENT '管理单位编码',
 primary key (data_date, meter_asset_no)
)
COMMENT '专变用户日电量（修正）'
;

CREATE TABLE IF NOT EXISTS dwd_cst_rcvbl_acct
(
 `rcvbl_acct_id` BIGINT COMMENT '应收台账标识',
 `qty_charg_calc_id` BIGINT COMMENT '量费计算标识',
 `rcvbl_ym` varchar(255) COMMENT '应收年月',
 `acct_no` varchar(255) COMMENT '记账编号',
 `ec_qty` BIGINT COMMENT '能源量',
 `rcvbl_amt` DECIMAL(20,6) COMMENT '应收金额',
 `rcvd_amt` DECIMAL(20,6) COMMENT '实收金额',
 `in_trnst_amt` DECIMAL(20,6) COMMENT '在途金额',
 `arer_bal` DECIMAL(20,6) COMMENT '欠费余额',
 `write_off_amt` DECIMAL(20,6) COMMENT '核销金额',
 `rcvbl_lqd_damg` DECIMAL(20,6) COMMENT '应收违约金',
 `rcvd_lqd_damg` DECIMAL(20,6) COMMENT '实收违约金',
 `issu_date` DATETIME COMMENT '发行日期',
 `mgt_org_code` varchar(255) COMMENT '管理单位编码',
 primary key (rcvbl_acct_id)
)
COMMENT '应收电费台账'
;

CREATE TABLE IF NOT EXISTS dwd_cst_rcvd_acct
(
 `rcvd_acct_id` BIGINT COMMENT '实收台账标识',
 `charg_acct_id` BIGINT COMMENT '交费台账标识',
 `rcvbl_acct_id` BIGINT COMMENT '应收台账标识',
 `rcvbl_ym` varchar(255) COMMENT '应收年月',
 `rcvd_ym` varchar(255) COMMENT '实收年月',
 `rcvd_amt` DECIMAL(20,6) COMMENT '实收金额',
 `rcvd_date` DATETIME COMMENT '实收日期',
 `arer_bal` DECIMAL(20,6) COMMENT '欠费余额',
 `acct_no` varchar(255) COMMENT '记账编号',
 `mgt_org_code` varchar(255) COMMENT '管理单位编码',
 primary key (rcvd_acct_id)
)
COMMENT '实收电费台账'
;

CREATE TABLE IF NOT EXISTS dwd_cst_charg_acct
(
 `charg_acct_id` BIGINT COMMENT '交费台账标识',
 `charg_ym` varchar(255) COMMENT '收费年月',
 `charg_date` DATETIME COMMENT '收费日期',
 `charg_amt` DECIMAL(20,6) COMMENT '收费金额',
 `chan_no` varchar(255) COMMENT '渠道编号',
 `rela_id` BIGINT COMMENT '关联标识',
 `acct_no` varchar(255) COMMENT '记账编号',
 `acct_ym` varchar(255) COMMENT '记账年月',
 `acct_date` DATETIME COMMENT '记账日期',
 `cap_no` varchar(255) COMMENT '资金编号',
 `trans_run_acct_no` varchar(255) COMMENT '交易流水号',
 `pay_order_id` BIGINT COMMENT '支付单标识',
 `cash_chk_rec_id` BIGINT COMMENT '解款记录标识',
 `mgt_chan_org` varchar(255) COMMENT '渠道供电单位',
 `mgt_org_code` varchar(255) COMMENT '管理单位编码',
 primary key (charg_acct_id)
)
COMMENT '交费台账'
;

CREATE TABLE IF NOT EXISTS dim_cst_cust
(
 `cust_id` BIGINT COMMENT '客户标识',
 `cust_no` varchar(255) COMMENT '客户编号',
 `cust_name` varchar(255) COMMENT '客户名称',
 `bp_id` BIGINT COMMENT '伙伴标识',
 `creat_date` DATETIME COMMENT '创建日期',
 `cncl_date` DATETIME COMMENT '注销日期',
 `ec_addr` varchar(255) COMMENT '用能地址',
 `urbanrural_categ` varchar(255) COMMENT '城乡类别',
 `urbanrural_categ_desc` varchar(255) COMMENT '城乡类别描述',
 `cust_ind_cls` varchar(255) COMMENT '行业分类',
 `cust_ind_cls_desc` varchar(255) COMMENT '行业分类描述',
 `cust_ind_ustry_cls` varchar(255) COMMENT '产业分类',
 `cust_ind_cls_desc_1` varchar(255) COMMENT '一级行业分类',
 `impt_lv_desc` varchar(255) COMMENT '重要性等级描述',
 `ppy_auth_flag` varchar(255) COMMENT '户主认证标志',
 `billing_unit_no` varchar(255) COMMENT '计费单元编号',
 `mr_unit_no` varchar(255) COMMENT '抄表单元编号',
 `bus_srv_addr_id` BIGINT COMMENT '业务服务地址标识',
 `mgt_org_code` varchar(255) COMMENT '管理单位编码',
 `write_time` DATETIME COMMENT '入库时间',
 `within_city_plan_range_desc` varchar(255) COMMENT '属性描述',
 primary key (cust_id)
)
COMMENT '能源客户'
;

CREATE TABLE IF NOT EXISTS dwd_cst_sgmt_qty_charg
(
 `sgmt_qty_charg_id` BIGINT COMMENT '分段量费标识',
 `calc_id` BIGINT COMMENT '计算标识',
 `qty_charg_ym` varchar(255) COMMENT '量费年月',
 `sgmt_exp_attr_cls` varchar(255) COMMENT '费用属性分类',
 `settle_qty` BIGINT COMMENT '结算量',
 `deg_up` DECIMAL(20,6) COMMENT '度数单价',
 `deg_exp` DECIMAL(20,6) COMMENT '度数费用',
 `exec_ctlg_up` DECIMAL(20,6) COMMENT '目录单价',
 `ctlg_exp` DECIMAL(20,6) COMMENT '目录费用',
 `ctlg_cls` varchar(255) COMMENT '目录分类',
 `lvling_diff` DECIMAL(20,6) COMMENT '调平差额',
 `ext_settle_qty` BIGINT COMMENT '扩展结算量',
 `before_ctlg_exp` DECIMAL(20,6) COMMENT '调尾前目录费用',
 `before_deg_exp` DECIMAL(20,6) COMMENT '调尾前度数费用',
 `plan_no` varchar(255) COMMENT '计划编号',
 `bilg_card_id` BIGINT COMMENT '计费卡标识',
 `prc_ind_cls` varchar(255) COMMENT '行业分类_电价',
 `prc_ind_ustry_cls` varchar(255) COMMENT '产业分类',
 `prc_ind_cls_desc` varchar(255) COMMENT '一级行业分类',
 `dereg_attr_cls` varchar(255) COMMENT '市场化属性分类',
 `exp_attr_cls` varchar(255) COMMENT '费用属性分类',
 `exec_rng_type` varchar(255) COMMENT '执行范围分类',
 `disc_mode_cls` varchar(255) COMMENT '优惠方式分类',
 `prc_volt_code` varchar(255) COMMENT '承压等级_电价',
 `prc_ec_categ` varchar(255) COMMENT '用能类别_电价',
 `prc_type` varchar(255) COMMENT '电价类别',
 `inst_snap_id` BIGINT COMMENT '安装点快照标识',
 `meter_mode` varchar(255) COMMENT '计量方式',
 `inst_usage_type` varchar(255) COMMENT '安装点用途类型',
 `gen_cons_type` varchar(255) COMMENT '发用电户类型',
 `gen_mode` varchar(255) COMMENT '发电方式',
 `cust_pscateg` varchar(255) COMMENT '客户电源类别',
 `gc_type` varchar(255) COMMENT '发电客户类型',
 `e_consp_mode` varchar(255) COMMENT '能源消纳方式',
 `cust_cls` varchar(255) COMMENT '客户分类',
 `high_ec_ind_cls` varchar(255) COMMENT '高耗能行业类别',
 `cust_volt_code` varchar(255) COMMENT '承压',
 `cust_ec_categ` varchar(255) COMMENT '用能类别',
 `urbanruran_categ` varchar(255) COMMENT '城乡类别',
 `esell_co_no` varchar(255) COMMENT '售能公司编号',
 `as_ym` varchar(255) COMMENT '追补年月',
 primary key (sgmt_qty_charg_id)
)
COMMENT '客户安装点分段量费信息'
;

CREATE TABLE IF NOT EXISTS dwd_cst_addl_charg
(
 `addl_charg_id` BIGINT COMMENT '加收费标识',
 `calc_id` BIGINT COMMENT '计算标识',
 `qty_charg_ym` varchar(255) COMMENT '量费年月',
 `addl_num` BIGINT COMMENT '加收量',
 `addl_prc` DECIMAL(20,6) COMMENT '加收单价',
 `addl_amt` DECIMAL(20,6) COMMENT '加收费用',
 `addl_charg_ctlg` varchar(255) COMMENT '加收项',
 `ctlg_cls` varchar(255) COMMENT '目录分类',
 `plan_no` varchar(255) COMMENT '计划编号;属性描述',
 `bilg_card_id` BIGINT COMMENT '计费卡标识',
 `prc_ind_cls` varchar(255) COMMENT '行业分类_电价',
 `prc_ind_ustry_cls` varchar(255) COMMENT '产业分类',
 `prc_ind_cls_desc` varchar(255) COMMENT '一级行业分类',
 `exp_attr_cls` varchar(255) COMMENT '费用属性分类',
 `exec_rng_type` varchar(255) COMMENT '执行范围分类',
 `disc_mode_cls` varchar(255) COMMENT '优惠方式分类',
 `prc_volt_code` varchar(255) COMMENT '电压等级',
 `prc_ec_categ` varchar(255) COMMENT '用能类别_电价',
 `prc_type` varchar(255) COMMENT '电价类别',
 `inst_snap_id` BIGINT COMMENT '安装点快照标识',
 `meter_mode` varchar(255) COMMENT '计量方式',
 `inst_usage_type` varchar(255) COMMENT '安装点用途类型',
 `gen_cons_type` varchar(255) COMMENT '发用电户类型',
 `gen_mode` varchar(255) COMMENT '发电方式',
 `cust_pscateg` varchar(255) COMMENT '客户电源类别',
 `gc_type` varchar(255) COMMENT '发电客户类型',
 `e_consp_mode` varchar(255) COMMENT '能源消纳方式',
 `cust_cls` varchar(255) COMMENT '客户分类',
 `high_ec_ind_cls` varchar(255) COMMENT '高耗能行业类别',
 `cust_volt_code` varchar(255) COMMENT '承压',
 `cust_ec_categ` varchar(255) COMMENT '用能类别',
 `urbanruran_categ` varchar(255) COMMENT '城乡类别',
 `as_ym` varchar(255) COMMENT '追补年月',
 `calc_bus_type` varchar(255) COMMENT '量费计算业务类型',
 primary key (addl_charg_id)
)
COMMENT '客户安装点加收费信息'
;

CREATE TABLE IF NOT EXISTS dwd_cst_special_expense
(
 `spcl_exp_id` BIGINT COMMENT '专属费用标识',
 `calc_id` BIGINT COMMENT '计算标识',
 `qty_charg_ym` varchar(255) COMMENT '量费年月',
 `scpl_fee_categ` varchar(255) COMMENT '专属费用类别',
 `spcl_exp_scnd_lv_cls` varchar(255) COMMENT '专属费用二级分类',
 `settle_exp_qty_val` DECIMAL(20,6) COMMENT '结算费用量值',
 `settle_prc` DECIMAL(20,6) COMMENT '结算价格',
 `settle_exp` DECIMAL(20,6) COMMENT '结算费用',
 `calc_actl_pf_ap_q` BIGINT COMMENT '计算实际功率因数有功电量',
 `calc_actl_pf_rp_q` BIGINT COMMENT '计算实际功率因数无功电量',
 `actl_p_f` DECIMAL(20,6) COMMENT '实际功率因数',
 `pf_std_code` varchar(255) COMMENT '功率因数考核标准代码',
 `pf_std_value` varchar(255) COMMENT '功率因数考核标准',
 `plan_no` varchar(255) COMMENT '计划编号',
 `bilg_card_id` BIGINT COMMENT '计费卡标识',
 `prc_ind_cls` varchar(255) COMMENT '行业分类_电价',
 `prc_ind_ustry_cls` varchar(255) COMMENT '产业分类',
 `prc_ind_cls_desc` varchar(255) COMMENT '一级行业分类',
 `prc_volt_code` varchar(255) COMMENT '承压等级_电价',
 `prc_ec_categ` varchar(255) COMMENT '用能类别_电价',
 `prc_type` varchar(255) COMMENT '电价类别',
 `inst_snap_id` BIGINT COMMENT '安装点快照标识',
 `meter_mode` varchar(255) COMMENT '计量方式',
 `inst_usage_type` varchar(255) COMMENT '安装点用途类型',
 `gen_cons_type` varchar(255) COMMENT '发用电户类型',
 `gen_mode` varchar(255) COMMENT '发电方式',
 `cust_pscateg` varchar(255) COMMENT '客户电源类别',
 `gc_type` varchar(255) COMMENT '发电客户类型',
 `e_consp_mode` varchar(255) COMMENT '能源消纳方式',
 `cust_cls` varchar(255) COMMENT '客户分类',
 `high_ec_ind_cls` varchar(255) COMMENT '高耗能行业类别',
 `cust_volt_code` varchar(255) COMMENT '承压',
 `cust_ec_categ` varchar(255) COMMENT '用能类别',
 `as_ym` varchar(255) COMMENT '追补年月',
 `per_mon_kva_qty` DECIMAL(22,6) COMMENT '属性描述',
 `unit_eval_times` DECIMAL(20,6) COMMENT '属性描述',
 `power_ratio` DECIMAL(20,6) COMMENT '属性描述',
 `exp_ymd` varchar(255) COMMENT '属性描述',
 primary key (spcl_exp_id)
)
COMMENT '客户安装点专属费信息'
;

CREATE TABLE IF NOT EXISTS dwd_cst_rs_inst_bilg_card
(
 `rs_inst_bilg_card_id` BIGINT COMMENT '退补安装点计费卡标识',
 `rs_qty_charg_calc_id` BIGINT COMMENT '退补量费计算标识',
 `orgn_qty_charg_calc_id` BIGINT COMMENT '原量费计算标识',
 `exp_attr_cls` varchar(255) COMMENT '费用属性分类',
 `actl_out_acct_mon` varchar(255) COMMENT '实际出账月',
 `t_mr_qty` BIGINT COMMENT '总抄见量',
 `t_settle_qty` BIGINT COMMENT '总结算量',
 `t_settle_exp` DECIMAL(20,6) COMMENT '总结算费',
 `t_ctlg_exp` DECIMAL(20,6) COMMENT '总目录费',
 `t_addl_charg` DECIMAL(20,6) COMMENT '总加收费',
 `t_ecc` DECIMAL(20,6) COMMENT '总电度电费',
 `ind_categ` varchar(255) COMMENT '行业类别',
 `rp_tl` DECIMAL(20,6) COMMENT '无功变损',
 `ap_tl` DECIMAL(20,6) COMMENT '有功变损',
 `ap_ll` DECIMAL(20,6) COMMENT '有功线损',
 `rp_ll` DECIMAL(20,6) COMMENT '无功线损',
 `rpq` DECIMAL(20,6) COMMENT '无功电量',
 `ext_settle_qty` DECIMAL(20,6) COMMENT '扩展结算量',
 `pic` varchar(255) COMMENT '责任人',
 `app_no` varchar(255) COMMENT '申请编号',
 `exec_rng_id` BIGINT COMMENT '政策执行范围标识',
 `is_inc_addl_charg_ctlg` varchar(255) COMMENT '是否包含代征',
 `is_con_qty` varchar(255) COMMENT '是否折算电量',
 `bilg_rs_rcpt_id` BIGINT COMMENT '计费退补单标识',
 `qty_charg_ym` varchar(255) COMMENT '量费年月',
 `inst_snap_acct_id` BIGINT COMMENT '安装点快照台账标识',
 `rs_prc_src` varchar(255) COMMENT '退补价格来源',
 `qty_charg_acct_ym` varchar(255) COMMENT '量费台账年月',
 `calc_acct_id` BIGINT COMMENT '计算台账标识',
 `dereg_attr_cls` varchar(255) COMMENT '市场化属性',
 `prc_ec_categ` varchar(255) COMMENT '电价用能类别',
 `t_self_cons_elec_qty` BIGINT COMMENT '属性描述',
 `t_self_cons_elec_charg` DECIMAL(20,6) COMMENT '属性描述',
 `trnsm_dist_std_ver_no` varchar(255) COMMENT '属性描述',
 `exec_rng_type` varchar(255) COMMENT '属性描述',
 primary key (rs_inst_bilg_card_id)
)
COMMENT '退补安装点计费卡明细'
;

CREATE TABLE IF NOT EXISTS dwd_cst_mr_data
(
 `m_r_data_id` BIGINT COMMENT '抄表标识',
 `calc_id` BIGINT COMMENT '计算标识',
 `qty_charg_ym` varchar(255) COMMENT '量费年月',
 `plan_no` varchar(255) COMMENT '计划编号',
 `inst_id` BIGINT COMMENT '安装点标识',
 `meter_asset_no` varchar(255) COMMENT '资产编号',
 `mr_sn` BIGINT COMMENT '抄表顺序号',
 `read_type` varchar(255) COMMENT '计度器类型',
 `last_read_frz_date` DATETIME COMMENT '上次计划抄表日期',
 `last_read_act_date` DATETIME COMMENT '上次实际抄表日期',
 `last_m_r` DECIMAL(20,6) COMMENT '上次抄见示数',
 `last_mr_qty` BIGINT COMMENT '上次抄见量',
 `this_read_frz_date` DATETIME COMMENT '本次计划抄表日期',
 `this_read_act_date` DATETIME COMMENT '本次实际抄表日期',
 `this_m_r` DECIMAL(20,6) COMMENT '本次抄见示数',
 `this_mr_qty` BIGINT COMMENT '本次抄见量',
 `comp_rati` BIGINT COMMENT '综合倍率',
 `mr_digit` varchar(255) COMMENT '抄见位数',
 `mr_stat` varchar(255) COMMENT '抄表状态',
 `data_src` varchar(255) COMMENT '数据来源',
 `mr_abnor_categ` varchar(255) COMMENT '抄表异常类别',
 `actl_mr_mode` varchar(255) COMMENT '实际抄表方式',
 `m_r_coef` DECIMAL(20,6) COMMENT '抄表系数',
 `rela_app_no` varchar(255) COMMENT '关联申请编号',
 `chg_date` DATETIME COMMENT '变更日期',
 `exp_ymd` varchar(255) COMMENT '费用年月日',
 `time_slot` varchar(255) COMMENT '现货交易时段（枚举值）',
 `reg_read_vou_id` BIGINT COMMENT '计度器示数凭证标识',
 `sgmt_no` varchar(255) COMMENT '分段编号',
 `mr_unit_id` BIGINT COMMENT '抄表单元标识',
 `mr_unit_stat` varchar(255) COMMENT '抄表单元状态',
 `stat_time` DATETIME COMMENT '状态时间',
 `mgt_team` varchar(255) COMMENT '管理班组',
 `mr_pic` varchar(255) COMMENT '抄表责任人',
 `tmp_mr_pic` varchar(255) COMMENT '临时抄表责任人',
 `cls_dim` varchar(255) COMMENT '分类维度',
 `grab_order_open_coef` varchar(255) COMMENT '抢单开放系数',
 `mr_unit_attr` varchar(255) COMMENT '抄表单元属性',
 `e_sell_co_id` varchar(255) COMMENT '售能公司标识',
 `cr_pic` varchar(255) COMMENT '催费责任人',
 `dist_sta_no` varchar(255) COMMENT '配送站编号',
 `read_chk_stf` varchar(255) COMMENT '示数复核人',
 `cust_cls` varchar(255) COMMENT '客户分类',
 `cust_volt_code` varchar(255) COMMENT '承压',
 primary key (m_r_data_id)
)
COMMENT '抄表数据'
;

CREATE TABLE IF NOT EXISTS dwd_cst_rcvbl_addl_acct
(
 `rcvbl_addl_charg_acct_id` BIGINT COMMENT '应收加收台账标识',
 `rcvbl_acct_id` BIGINT COMMENT '应收台账标识',
 `rcvbl_ym` varchar(255) COMMENT '应收年月',
 `acct_no` varchar(255) COMMENT '记账编号',
 `qty_charg_calc_id` BIGINT COMMENT '量费计算标识',
 `cust_cls` varchar(255) COMMENT '客户分类',
 `dereg_attr_cls` varchar(255) COMMENT '市场化属性分类',
 `ec_categ` varchar(255) COMMENT '用能类别',
 `volt_code` varchar(255) COMMENT '承压等级',
 `ec_qty` BIGINT COMMENT '应收能源量',

 
 `addl_charg_ctlg_code` varchar(255) COMMENT '加收项代码',
 `rcvbl_addl_charg_amt` DECIMAL(20,6) COMMENT '应收加收金额',
 `rcvd_addl_charg_amt` DECIMAL(20,6) COMMENT '实收加收金额',
 primary key (rcvbl_addl_charg_acct_id)
)
COMMENT '应收加收台账'
;

CREATE TABLE IF NOT EXISTS dwd_cst_bilg_rs_rcpt
(
 `bilg_rs_rcpt_id` BIGINT COMMENT '计费退补单标识',
 `cust_no` varchar(255) COMMENT '客户编号',
 `cust_rs_stat` varchar(255) COMMENT '客户退补状态',
 `rs_ym` varchar(255) COMMENT '退补应收应付年月',
 `time_rng_beg_date` DATETIME COMMENT '时间范围开始日期',
 `time_rng_end_date` DATETIME COMMENT '时间范围截止日期',
 `err_occur_date` DATETIME COMMENT '错误发生日期',
 `dereg_attr_cls` varchar(255) COMMENT '市场化属性分类',
 `gen_cons_type` varchar(255) COMMENT '发电用户类型',
 `merg_out_acct_mon` varchar(255) COMMENT '合并出账月',
 `actl_out_acct_mon` varchar(255) COMMENT '实际出账月份',
 `orgn_calc_id` BIGINT COMMENT '原计算标识',
 `hndl_stat` varchar(255) COMMENT '处理状态',
 `rs_reason_cls` varchar(255) COMMENT '退补原因分类',
 `site_chk_stf` varchar(255) COMMENT '现场核查人',
 `chk_date` DATETIME COMMENT '核查时间',
 `chk_rslt` varchar(255) COMMENT '核查结果',
 `rs_reason` varchar(255) COMMENT '退补原因',
 `attach_id` BIGINT COMMENT '附件标识',
 `bilg_unit_no` varchar(255) COMMENT '计费单元编号',
 `rs_app_sch_id` BIGINT COMMENT '退补申请方案标识',
 `applnt` varchar(255) COMMENT '申请人',
 `app_date` DATETIME COMMENT '申请日期',
 `rcvbl_paybl_type` varchar(255) COMMENT '应收应付类型',
 `rs_hndl_cls` varchar(255) COMMENT '退补处理分类',
 `app_no` varchar(255) COMMENT '申请编号',
 `rs_rcvbl_ym` varchar(255) COMMENT '退补应收年月',
 `rs_cls` varchar(255) COMMENT '退补类别',
 primary key (bilg_rs_rcpt_id)
)
COMMENT '计费退补单'
;

CREATE TABLE IF NOT EXISTS dwd_cst_es_meter_energy_day_p
(
 `data_date` DATETIME COMMENT '数据时间',
 `meter_asset_no` varchar(50) COMMENT '资产编号',
 `t_factor` DECIMAL(20,6) COMMENT '综合倍率',
 `pap_e` DECIMAL(20,6) COMMENT '正向有功总电能量',
 `pap_e1` DECIMAL(20,6) COMMENT '正向有功费率1电能量',
 `pap_e2` DECIMAL(20,6) COMMENT '正向有功费率2电能量',
 `pap_e3` DECIMAL(20,6) COMMENT '正向有功费率3电能量',
 `pap_e4` DECIMAL(20,6) COMMENT '正向有功费率4电能量',
 `rap_e` DECIMAL(20,6) COMMENT '反向有功总电能量',
 `pap_e_quality` varchar(255) COMMENT '正向有功总电能量质量标识',
 `rap_e_quality` varchar(255) COMMENT '反向有功总电能量质量标识',
 `pap_e_fix` DECIMAL(16,4) COMMENT '修正正向有功总电量',
 `pap_r_fix` DECIMAL(16,4) COMMENT '修正正向有功总电能示值止码',
 `rap_e_fix` DECIMAL(16,4) COMMENT '修正反向有功总电量',
 `rap_r_fix` DECIMAL(16,4) COMMENT '修正反向有功总电能示值止码',
 primary key (meter_asset_no)
)
COMMENT '公变日电量'
;

CREATE TABLE IF NOT EXISTS dwd_cst_a_ll_dist_det_day
(
 `rec_id` varchar(50) COMMENT '记录标识',
 `stat_date` DATETIME COMMENT '统计日期',
 `pro_mgt_org_code` varchar(255) COMMENT '省管理单位编码',
 `dist_sta_id` BIGINT COMMENT '配送站标识',
 `energy_type` varchar(255) COMMENT '电量类型',
 `meter_dev_id` BIGINT COMMENT '电能表设备标识',
 `meter_asset_no` varchar(255) COMMENT '电能表资产编号',
 `iot_acq_obj_id` BIGINT COMMENT '物联采集对象标识',
 `comp_ratio` DECIMAL(20,6) COMMENT '综合倍率',
 `inst_lv` BIGINT COMMENT '安装点级数',
 `pq_data_type` varchar(255) COMMENT '电量数据类型',
 `last_read` DECIMAL(20,6) COMMENT '上次示数',
 `this_read` DECIMAL(20,6) COMMENT '本次示数',
 `coll_pq` DECIMAL(20,6) COMMENT '采集电量',
 `est_pq` DECIMAL(20,6) COMMENT '估算电量',
 `mr_pq` DECIMAL(20,6) COMMENT '抄见电量',
 `adj_pq` DECIMAL(20,6) COMMENT '特殊调整电量',
 `pq_abnor_ctn_days` DECIMAL(20,6) COMMENT '连续异常天数',
 `pq_abnor_days` DECIMAL(20,6) COMMENT '异常天数',
 primary key (rec_id)
)
COMMENT '配送站（台区）日电量'
;

CREATE TABLE IF NOT EXISTS dwd_cst_es_e_mp_comp_curve_h
(
 `id` BIGINT COMMENT '标识(由表计资产编号转换)',
 `meter_asset_no` varchar(50) COMMENT '表计资产编号',
 `cust_no` varchar(50) COMMENT '客户编号',
 `cust_cls` varchar(255) COMMENT '用电客户分类',
 `ec_categ` varchar(255) COMMENT '用能类别',
 `cust_volt_code` varchar(255) COMMENT '基础承压',
 `data_date` DATETIME COMMENT '数据日期。YYYY-MM-DD',
 `data_type` BIGINT COMMENT '数据类型',
 `data_whole_flag` varchar(255) COMMENT '数据完整性标志',
 `data_point_flag` varchar(255) COMMENT '数据点数标志',
 `p1` DECIMAL(20,6) COMMENT '功率1',
 `p2` DECIMAL(20,6) COMMENT '功率2',
 `p3` DECIMAL(20,6) COMMENT '功率3',
 `p4` DECIMAL(20,6) COMMENT '功率4',
 `p5` DECIMAL(20,6) COMMENT '功率5',
 `p6` DECIMAL(20,6) COMMENT '功率6',
 `p7` DECIMAL(20,6) COMMENT '功率7',
 `p8` DECIMAL(20,6) COMMENT '功率8',
 `p9` DECIMAL(20,6) COMMENT '功率9',
 `p10` DECIMAL(20,6) COMMENT '功率10',
 `p11` DECIMAL(20,6) COMMENT '功率11',
 `p12` DECIMAL(20,6) COMMENT '功率12',
 `p13` DECIMAL(20,6) COMMENT '功率13',
 `p14` DECIMAL(20,6) COMMENT '功率14',
 `p15` DECIMAL(20,6) COMMENT '功率15',
 `p16` DECIMAL(20,6) COMMENT '功率16',
 `p17` DECIMAL(20,6) COMMENT '功率17',
 `p18` DECIMAL(20,6) COMMENT '功率18',
 `p19` DECIMAL(20,6) COMMENT '功率19',
 `p20` DECIMAL(20,6) COMMENT '功率20',
 `p21` DECIMAL(20,6) COMMENT '功率21',
 `p22` DECIMAL(20,6) COMMENT '功率22',
 `p23` DECIMAL(20,6) COMMENT '功率23',
 `p24` DECIMAL(20,6) COMMENT '功率24',
 `p25` DECIMAL(20,6) COMMENT '功率25',
 `p26` DECIMAL(20,6) COMMENT '功率26',
 `p27` DECIMAL(20,6) COMMENT '功率27',
 `p28` DECIMAL(20,6) COMMENT '功率28',
 `p29` DECIMAL(20,6) COMMENT '功率29',
 `p30` DECIMAL(20,6) COMMENT '功率30',
 `p31` DECIMAL(20,6) COMMENT '功率31',
 `p32` DECIMAL(20,6) COMMENT '功率32',
 `p33` DECIMAL(20,6) COMMENT '功率33',
 `p34` DECIMAL(20,6) COMMENT '功率34',
 `p35` DECIMAL(20,6) COMMENT '功率35',
 `p36` DECIMAL(20,6) COMMENT '功率36',
 `p37` DECIMAL(20,6) COMMENT '功率37',
 `p38` DECIMAL(20,6) COMMENT '功率38',
 `p39` DECIMAL(20,6) COMMENT '功率39',
 `p40` DECIMAL(20,6) COMMENT '功率40',
 `p41` DECIMAL(20,6) COMMENT '功率41',
 `p42` DECIMAL(20,6) COMMENT '功率42',
 `p43` DECIMAL(20,6) COMMENT '功率43',
 `p44` DECIMAL(20,6) COMMENT '功率44',
 `p45` DECIMAL(20,6) COMMENT '功率45',
 `p46` DECIMAL(20,6) COMMENT '功率46',
 `p47` DECIMAL(20,6) COMMENT '功率47',
 `p48` DECIMAL(20,6) COMMENT '功率48',
 `p49` DECIMAL(20,6) COMMENT '功率49',
 `p50` DECIMAL(20,6) COMMENT '功率50',
 `p51` DECIMAL(20,6) COMMENT '功率51',
 `p52` DECIMAL(20,6) COMMENT '功率52',
 `p53` DECIMAL(20,6) COMMENT '功率53',
 `p54` DECIMAL(20,6) COMMENT '功率54',
 `p55` DECIMAL(20,6) COMMENT '功率55',
 `p56` DECIMAL(20,6) COMMENT '功率56',
 `p57` DECIMAL(20,6) COMMENT '功率57',
 `p58` DECIMAL(20,6) COMMENT '功率58',
 `p59` DECIMAL(20,6) COMMENT '功率59',
 `p60` DECIMAL(20,6) COMMENT '功率60',
 `p61` DECIMAL(20,6) COMMENT '功率61',
 `p62` DECIMAL(20,6) COMMENT '功率62',
 `p63` DECIMAL(20,6) COMMENT '功率63',
 `p64` DECIMAL(20,6) COMMENT '功率64',
 `p65` DECIMAL(20,6) COMMENT '功率65',
 `p66` DECIMAL(20,6) COMMENT '功率66',
 `p67` DECIMAL(20,6) COMMENT '功率67',
 `p68` DECIMAL(20,6) COMMENT '功率68',
 `p69` DECIMAL(20,6) COMMENT '功率69',
 `p70` DECIMAL(20,6) COMMENT '功率70',
 `p71` DECIMAL(20,6) COMMENT '功率71',
 `p72` DECIMAL(20,6) COMMENT '功率72',
 `p73` DECIMAL(20,6) COMMENT '功率73',
 `p74` DECIMAL(20,6) COMMENT '功率74',
 `p75` DECIMAL(20,6) COMMENT '功率75',
 `p76` DECIMAL(20,6) COMMENT '功率76',
 `p77` DECIMAL(20,6) COMMENT '功率77',
 `p78` DECIMAL(20,6) COMMENT '功率78',
 `p79` DECIMAL(20,6) COMMENT '功率79',
 `p80` DECIMAL(20,6) COMMENT '功率80',
 `p81` DECIMAL(20,6) COMMENT '功率81',
 `p82` DECIMAL(20,6) COMMENT '功率82',
 `p83` DECIMAL(20,6) COMMENT '功率83',
 `p84` DECIMAL(20,6) COMMENT '功率84',
 `p85` DECIMAL(20,6) COMMENT '功率85',
 `p86` DECIMAL(20,6) COMMENT '功率86',
 `p87` DECIMAL(20,6) COMMENT '功率87',
 `p88` DECIMAL(20,6) COMMENT '功率88',
 `p89` DECIMAL(20,6) COMMENT '功率89',
 `p90` DECIMAL(20,6) COMMENT '功率90',
 `p91` DECIMAL(20,6) COMMENT '功率91',
 `p92` DECIMAL(20,6) COMMENT '功率92',
 `p93` DECIMAL(20,6) COMMENT '功率93',
 `p94` DECIMAL(20,6) COMMENT '功率94',
 `p95` DECIMAL(20,6) COMMENT '功率95',
 `p96` DECIMAL(20,6) COMMENT '功率96',
 `mgt_org_code` varchar(50) COMMENT '管理单位编码',
 `tv` DECIMAL(20,6) COMMENT '电压变比',
 `ta` DECIMAL(20,6) COMMENT '电流变比',
 primary key (id, cust_no, data_type, mgt_org_code)
)
COMMENT '专变用户96点负荷 '
;

CREATE TABLE IF NOT EXISTS dwd_cst_es_e_mp_comp_curve_h_v
(
 `id` BIGINT COMMENT '标识(由表计资产编号转换)',
 `meter_asset_no` varchar(50) COMMENT '资产编号',
 `cust_no` varchar(50) COMMENT '客户编号',
 `cust_cls` varchar(255) COMMENT '用电客户分类',
 `ec_categ` varchar(255) COMMENT '用能类别',
 `cust_volt_code` varchar(255) COMMENT '基础承压',
 `data_date` DATETIME COMMENT '数据日期YYYY-MM-DD',
 `data_type` BIGINT COMMENT '数据类型',
 `data_whole_flag` varchar(255) COMMENT '数据完整性标志',
 `data_point_flag` varchar(255) COMMENT '数据点数标志',
 `phase_flag` varchar(50) COMMENT '电压标志',
 `v1` DECIMAL(20,6) COMMENT '电压1',
 `v2` DECIMAL(20,6) COMMENT '电压2',
 `v3` DECIMAL(20,6) COMMENT '电压3',
 `v4` DECIMAL(20,6) COMMENT '电压4',
 `v5` DECIMAL(20,6) COMMENT '电压5',
 `v6` DECIMAL(20,6) COMMENT '电压6',
 `v7` DECIMAL(20,6) COMMENT '电压7',
 `v8` DECIMAL(20,6) COMMENT '电压8',
 `v9` DECIMAL(20,6) COMMENT '电压9',
 `v10` DECIMAL(20,6) COMMENT '电压10',
 `v11` DECIMAL(20,6) COMMENT '电压11',
 `v12` DECIMAL(20,6) COMMENT '电压12',
 `v13` DECIMAL(20,6) COMMENT '电压13',
 `v14` DECIMAL(20,6) COMMENT '电压14',
 `v15` DECIMAL(20,6) COMMENT '电压15',
 `v16` DECIMAL(20,6) COMMENT '电压16',
 `v17` DECIMAL(20,6) COMMENT '电压17',
 `v18` DECIMAL(20,6) COMMENT '电压18',
 `v19` DECIMAL(20,6) COMMENT '电压19',
 `v20` DECIMAL(20,6) COMMENT '电压20',
 `v21` DECIMAL(20,6) COMMENT '电压21',
 `v22` DECIMAL(20,6) COMMENT '电压22',
 `v23` DECIMAL(20,6) COMMENT '电压23',
 `v24` DECIMAL(20,6) COMMENT '电压24',
 `v25` DECIMAL(20,6) COMMENT '电压25',
 `v26` DECIMAL(20,6) COMMENT '电压26',
 `v27` DECIMAL(20,6) COMMENT '电压27',
 `v28` DECIMAL(20,6) COMMENT '电压28',
 `v29` DECIMAL(20,6) COMMENT '电压29',
 `v30` DECIMAL(20,6) COMMENT '电压30',
 `v31` DECIMAL(20,6) COMMENT '电压31',
 `v32` DECIMAL(20,6) COMMENT '电压32',
 `v33` DECIMAL(20,6) COMMENT '电压33',
 `v34` DECIMAL(20,6) COMMENT '电压34',
 `v35` DECIMAL(20,6) COMMENT '电压35',
 `v36` DECIMAL(20,6) COMMENT '电压36',
 `v37` DECIMAL(20,6) COMMENT '电压37',
 `v38` DECIMAL(20,6) COMMENT '电压38',
 `v39` DECIMAL(20,6) COMMENT '电压39',
 `v40` DECIMAL(20,6) COMMENT '电压40',
 `v41` DECIMAL(20,6) COMMENT '电压41',
 `v42` DECIMAL(20,6) COMMENT '电压42',
 `v43` DECIMAL(20,6) COMMENT '电压43',
 `v44` DECIMAL(20,6) COMMENT '电压44',
 `v45` DECIMAL(20,6) COMMENT '电压45',
 `v46` DECIMAL(20,6) COMMENT '电压46',
 `v47` DECIMAL(20,6) COMMENT '电压47',
 `v48` DECIMAL(20,6) COMMENT '电压48',
 `v49` DECIMAL(20,6) COMMENT '电压49',
 `v50` DECIMAL(20,6) COMMENT '电压50',
 `v51` DECIMAL(20,6) COMMENT '电压51',
 `v52` DECIMAL(20,6) COMMENT '电压52',
 `v53` DECIMAL(20,6) COMMENT '电压53',
 `v54` DECIMAL(20,6) COMMENT '电压54',
 `v55` DECIMAL(20,6) COMMENT '电压55',
 `v56` DECIMAL(20,6) COMMENT '电压56',
 `v57` DECIMAL(20,6) COMMENT '电压57',
 `v58` DECIMAL(20,6) COMMENT '电压58',
 `v59` DECIMAL(20,6) COMMENT '电压59',
 `v60` DECIMAL(20,6) COMMENT '电压60',
 `v61` DECIMAL(20,6) COMMENT '电压61',
 `v62` DECIMAL(20,6) COMMENT '电压62',
 `v63` DECIMAL(20,6) COMMENT '电压63',
 `v64` DECIMAL(20,6) COMMENT '电压64',
 `v65` DECIMAL(20,6) COMMENT '电压65',
 `v66` DECIMAL(20,6) COMMENT '电压66',
 `v67` DECIMAL(20,6) COMMENT '电压67',
 `v68` DECIMAL(20,6) COMMENT '电压68',
 `v69` DECIMAL(20,6) COMMENT '电压69',
 `v70` DECIMAL(20,6) COMMENT '电压70',
 `v71` DECIMAL(20,6) COMMENT '电压71',
 `v72` DECIMAL(20,6) COMMENT '电压72',
 `v73` DECIMAL(20,6) COMMENT '电压73',
 `v74` DECIMAL(20,6) COMMENT '电压74',
 `v75` DECIMAL(20,6) COMMENT '电压75',
 `v76` DECIMAL(20,6) COMMENT '电压76',
 `v77` DECIMAL(20,6) COMMENT '电压77',
 `v78` DECIMAL(20,6) COMMENT '电压78',
 `v79` DECIMAL(20,6) COMMENT '电压79',
 `v80` DECIMAL(20,6) COMMENT '电压80',
 `v81` DECIMAL(20,6) COMMENT '电压81',
 `v82` DECIMAL(20,6) COMMENT '电压82',
 `v83` DECIMAL(20,6) COMMENT '电压83',
 `v84` DECIMAL(20,6) COMMENT '电压84',
 `v85` DECIMAL(20,6) COMMENT '电压85',
 `v86` DECIMAL(20,6) COMMENT '电压86',
 `v87` DECIMAL(20,6) COMMENT '电压87',
 `v88` DECIMAL(20,6) COMMENT '电压88',
 `v89` DECIMAL(20,6) COMMENT '电压89',
 `v90` DECIMAL(20,6) COMMENT '电压90',
 `v91` DECIMAL(20,6) COMMENT '电压91',
 `v92` DECIMAL(20,6) COMMENT '电压92',
 `v93` DECIMAL(20,6) COMMENT '电压93',
 `v94` DECIMAL(20,6) COMMENT '电压94',
 `v95` DECIMAL(20,6) COMMENT '电压95',
 `v96` DECIMAL(20,6) COMMENT '电压96',
 `mgt_org_code` varchar(50) COMMENT '管理单位编码',
 primary key (id, cust_no, phase_flag, mgt_org_code)
)
COMMENT '专变用户96点电压'
;

CREATE TABLE IF NOT EXISTS dwd_cst_es_e_mp_comp_curve_h_a
(
 `id` BIGINT COMMENT '标识(由表计资产编号转换)',
 `meter_asset_no` varchar(50) COMMENT '表计资产编号',
 `cust_no` varchar(50) COMMENT '客户编号',
 `cust_cls` varchar(255) COMMENT '用电客户分类',
 `ec_categ` varchar(255) COMMENT '用能类别',
 `cust_volt_code` varchar(255) COMMENT '基础承压',
 `data_date` DATETIME COMMENT '数据日期YYYY-MM-DD',
 `data_type` BIGINT COMMENT '数据类型',
 `data_whole_flag` varchar(255) COMMENT '数据完整性标志',
 `data_point_flag` varchar(255) COMMENT '数据点数标志',
 `phase_flag` varchar(50) COMMENT '电流标志',
 `a1` DECIMAL(20,6) COMMENT '电流1',
 `a2` DECIMAL(20,6) COMMENT '电流2',
 `a3` DECIMAL(20,6) COMMENT '电流3',
 `a4` DECIMAL(20,6) COMMENT '电流4',
 `a5` DECIMAL(20,6) COMMENT '电流5',
 `a6` DECIMAL(20,6) COMMENT '电流6',
 `a7` DECIMAL(20,6) COMMENT '电流7',
 `a8` DECIMAL(20,6) COMMENT '电流8',
 `a9` DECIMAL(20,6) COMMENT '电流9',
 `a10` DECIMAL(20,6) COMMENT '电流10',
 `a11` DECIMAL(20,6) COMMENT '电流11',
 `a12` DECIMAL(20,6) COMMENT '电流12',
 `a13` DECIMAL(20,6) COMMENT '电流13',
 `a14` DECIMAL(20,6) COMMENT '电流14',
 `a15` DECIMAL(20,6) COMMENT '电流15',
 `a16` DECIMAL(20,6) COMMENT '电流16',
 `a17` DECIMAL(20,6) COMMENT '电流17',
 `a18` DECIMAL(20,6) COMMENT '电流18',
 `a19` DECIMAL(20,6) COMMENT '电流19',
 `a20` DECIMAL(20,6) COMMENT '电流20',
 `a21` DECIMAL(20,6) COMMENT '电流21',
 `a22` DECIMAL(20,6) COMMENT '电流22',
 `a23` DECIMAL(20,6) COMMENT '电流23',
 `a24` DECIMAL(20,6) COMMENT '电流24',
 `a25` DECIMAL(20,6) COMMENT '电流25',
 `a26` DECIMAL(20,6) COMMENT '电流26',
 `a27` DECIMAL(20,6) COMMENT '电流27',
 `a28` DECIMAL(20,6) COMMENT '电流28',
 `a29` DECIMAL(20,6) COMMENT '电流29',
 `a30` DECIMAL(20,6) COMMENT '电流30',
 `a31` DECIMAL(20,6) COMMENT '电流31',
 `a32` DECIMAL(20,6) COMMENT '电流32',
 `a33` DECIMAL(20,6) COMMENT '电流33',
 `a34` DECIMAL(20,6) COMMENT '电流34',
 `a35` DECIMAL(20,6) COMMENT '电流35',
 `a36` DECIMAL(20,6) COMMENT '电流36',
 `a37` DECIMAL(20,6) COMMENT '电流37',
 `a38` DECIMAL(20,6) COMMENT '电流38',
 `a39` DECIMAL(20,6) COMMENT '电流39',
 `a40` DECIMAL(20,6) COMMENT '电流40',
 `a41` DECIMAL(20,6) COMMENT '电流41',
 `a42` DECIMAL(20,6) COMMENT '电流42',
 `a43` DECIMAL(20,6) COMMENT '电流43',
 `a44` DECIMAL(20,6) COMMENT '电流44',
 `a45` DECIMAL(20,6) COMMENT '电流45',
 `a46` DECIMAL(20,6) COMMENT '电流46',
 `a47` DECIMAL(20,6) COMMENT '电流47',
 `a48` DECIMAL(20,6) COMMENT '电流48',
 `a49` DECIMAL(20,6) COMMENT '电流49',
 `a50` DECIMAL(20,6) COMMENT '电流50',
 `a51` DECIMAL(20,6) COMMENT '电流51',
 `a52` DECIMAL(20,6) COMMENT '电流52',
 `a53` DECIMAL(20,6) COMMENT '电流53',
 `a54` DECIMAL(20,6) COMMENT '电流54',
 `a55` DECIMAL(20,6) COMMENT '电流55',
 `a56` DECIMAL(20,6) COMMENT '电流56',
 `a57` DECIMAL(20,6) COMMENT '电流57',
 `a58` DECIMAL(20,6) COMMENT '电流58',
 `a59` DECIMAL(20,6) COMMENT '电流59',
 `a60` DECIMAL(20,6) COMMENT '电流60',
 `a61` DECIMAL(20,6) COMMENT '电流61',
 `a62` DECIMAL(20,6) COMMENT '电流62',
 `a63` DECIMAL(20,6) COMMENT '电流63',
 `a64` DECIMAL(20,6) COMMENT '电流64',
 `a65` DECIMAL(20,6) COMMENT '电流65',
 `a66` DECIMAL(20,6) COMMENT '电流66',
 `a67` DECIMAL(20,6) COMMENT '电流67',
 `a68` DECIMAL(20,6) COMMENT '电流68',
 `a69` DECIMAL(20,6) COMMENT '电流69',
 `a70` DECIMAL(20,6) COMMENT '电流70',
 `a71` DECIMAL(20,6) COMMENT '电流71',
 `a72` DECIMAL(20,6) COMMENT '电流72',
 `a73` DECIMAL(20,6) COMMENT '电流73',
 `a74` DECIMAL(20,6) COMMENT '电流74',
 `a75` DECIMAL(20,6) COMMENT '电流75',
 `a76` DECIMAL(20,6) COMMENT '电流76',
 `a77` DECIMAL(20,6) COMMENT '电流77',
 `a78` DECIMAL(20,6) COMMENT '电流78',
 `a79` DECIMAL(20,6) COMMENT '电流79',
 `a80` DECIMAL(20,6) COMMENT '电流80',
 `a81` DECIMAL(20,6) COMMENT '电流81',
 `a82` DECIMAL(20,6) COMMENT '电流82',
 `a83` DECIMAL(20,6) COMMENT '电流83',
 `a84` DECIMAL(20,6) COMMENT '电流84',
 `a85` DECIMAL(20,6) COMMENT '电流85',
 `a86` DECIMAL(20,6) COMMENT '电流86',
 `a87` DECIMAL(20,6) COMMENT '电流87',
 `a88` DECIMAL(20,6) COMMENT '电流88',
 `a89` DECIMAL(20,6) COMMENT '电流89',
 `a90` DECIMAL(20,6) COMMENT '电流90',
 `a91` DECIMAL(20,6) COMMENT '电流91',
 `a92` DECIMAL(20,6) COMMENT '电流92',
 `a93` DECIMAL(20,6) COMMENT '电流93',
 `a94` DECIMAL(20,6) COMMENT '电流94',
 `a95` DECIMAL(20,6) COMMENT '电流95',
 `a96` DECIMAL(20,6) COMMENT '电流96',
 `mgt_org_code` varchar(50) COMMENT '管理单位编码',
 primary key (id, cust_no, phase_flag, mgt_org_code)
)
COMMENT '专变用户96点电流'
;

-- ============================================================
-- 外键约束
-- ============================================================

ALTER TABLE  dim_cst_elec_cons_cust ADD CONSTRAINT fk_elec_cons_cust_mgt_org_code FOREIGN KEY  (mgt_org_code)  REFERENCES  dim_cst_mgt_org(mgt_org_code);
ALTER TABLE  dim_cst_inst_elec_cons   ADD CONSTRAINT fk_inst_elec_cons_mgt_org_code FOREIGN KEY  (mgt_org_code)  REFERENCES  dim_cst_mgt_org(mgt_org_code);
ALTER TABLE  dwd_cst_meter_run   ADD CONSTRAINT fk_meter_run_mgt_org_code FOREIGN KEY  (mgt_org_code)  REFERENCES  dim_cst_mgt_org(mgt_org_code);
ALTER TABLE  dim_cst_elec_meter   ADD CONSTRAINT fk_elec_meter_mgt_org_code FOREIGN KEY  (mgt_org_code)  REFERENCES  dim_cst_mgt_org(mgt_org_code);
ALTER TABLE  dim_cst_adj_volt_dev   ADD CONSTRAINT fk_adj_volt_dev_mgt_org_code FOREIGN KEY  (mgt_org_code)  REFERENCES  dim_cst_mgt_org(mgt_org_code);
ALTER TABLE  dim_cst_dist_sta   ADD CONSTRAINT fk_dist_sta_mgt_org_code FOREIGN KEY  (mgt_org_code)  REFERENCES  dim_cst_mgt_org(mgt_org_code);
ALTER TABLE  dim_cst_pipeline   ADD CONSTRAINT fk_pipeline_mgt_org_code FOREIGN KEY  (mgt_org_code)  REFERENCES  dim_cst_mgt_org(mgt_org_code);
ALTER TABLE  dim_cst_cntrl_sta   ADD CONSTRAINT fk_cntrl_sta_mgt_org_code FOREIGN KEY  (mgt_org_code)  REFERENCES  dim_cst_mgt_org(mgt_org_code);
ALTER TABLE  dim_cst_dev   ADD CONSTRAINT fk_dev_mgt_org_code FOREIGN KEY  (mgt_org_code)  REFERENCES  dim_cst_mgt_org(mgt_org_code);
ALTER TABLE  dwd_cst_inst_bilg_card   ADD CONSTRAINT fk_bilg_card_mgt_org_code FOREIGN KEY  (mgt_org_code)  REFERENCES  dim_cst_mgt_org(mgt_org_code);
ALTER TABLE  dwd_cst_acct_bal   ADD CONSTRAINT fk_acct_bal_mgt_org_code FOREIGN KEY  (mgt_org_code)  REFERENCES  dim_cst_mgt_org(mgt_org_code);
ALTER TABLE  dwd_cst_es_meter_energy_day_l_xz   ADD CONSTRAINT fk_energy_day_l_mgt_org_code FOREIGN KEY  (mgt_org_code)  REFERENCES  dim_cst_mgt_org(mgt_org_code);
ALTER TABLE  dwd_cst_meter_energy_day_h_xz   ADD CONSTRAINT fk_energy_day_h_mgt_org_code FOREIGN KEY  (mgt_org_code)  REFERENCES  dim_cst_mgt_org(mgt_org_code);
ALTER TABLE  dwd_cst_rcvbl_acct   ADD CONSTRAINT fk_rcvbl_acct_mgt_org_code FOREIGN KEY  (mgt_org_code)  REFERENCES  dim_cst_mgt_org(mgt_org_code);
ALTER TABLE  dwd_cst_rcvd_acct   ADD CONSTRAINT fk_rcvd_acct_mgt_org_code FOREIGN KEY  (mgt_org_code)  REFERENCES  dim_cst_mgt_org(mgt_org_code);
ALTER TABLE  dwd_cst_charg_acct  ADD CONSTRAINT fk_charg_acct_mgt_org_code FOREIGN KEY  (mgt_org_code)  REFERENCES  dim_cst_mgt_org(mgt_org_code);

 ALTER TABLE dim_cst_elec_cons_cust ADD CONSTRAINT fk_cons_cust_cust_id FOREIGN KEY  (cust_id) REFERENCES dim_cst_cust(cust_id);
 ALTER TABLE dwd_cst_sgmt_qty_charg ADD CONSTRAINT fk_qty_charg_bilg_card_id FOREIGN KEY  (bilg_card_id) REFERENCES dwd_cst_inst_bilg_card(bilg_card_id);
 ALTER TABLE dwd_cst_addl_charg ADD CONSTRAINT fk_addl_charg_bilg_card_id FOREIGN KEY  (bilg_card_id) REFERENCES dwd_cst_inst_bilg_card(bilg_card_id);
 ALTER TABLE dwd_cst_special_expense ADD CONSTRAINT fk_special_expense_bilg_card_id FOREIGN KEY  (bilg_card_id) REFERENCES dwd_cst_inst_bilg_card(bilg_card_id);
 ALTER TABLE dwd_cst_rs_inst_bilg_card ADD CONSTRAINT fk_rs_bilg_rs_rcpt_id FOREIGN KEY  (bilg_rs_rcpt_id) REFERENCES dwd_cst_bilg_rs_rcpt(bilg_rs_rcpt_id);
 ALTER TABLE dwd_cst_mr_data ADD CONSTRAINT fk_mr_data_inst_id_asset_no FOREIGN KEY  (inst_id,meter_asset_no) REFERENCES dwd_cst_meter_run(inst_id,meter_asset_no);
 ALTER TABLE dwd_cst_rcvbl_addl_acct ADD CONSTRAINT fk_addl_acct_rcvbl_acct_id FOREIGN KEY  (rcvbl_acct_id) REFERENCES dwd_cst_rcvbl_acct(rcvbl_acct_id);
 ALTER TABLE dwd_cst_bilg_rs_rcpt ADD CONSTRAINT fk_rs_rcpt_cust_no FOREIGN KEY  (cust_no) REFERENCES dim_cst_elec_cons_cust(cust_no);  
 ALTER TABLE dwd_cst_es_meter_energy_day_p ADD CONSTRAINT fk_energy_day_p_asset_no FOREIGN KEY  (meter_asset_no) REFERENCES dwd_cst_meter_run(meter_asset_no); 
 ALTER TABLE dwd_cst_a_ll_dist_det_day ADD CONSTRAINT fk_dist_det_dist_sta_id FOREIGN KEY  (dist_sta_id) REFERENCES dim_cst_dist_sta(dist_sta_id); 
 ALTER TABLE dwd_cst_es_e_mp_comp_curve_h ADD CONSTRAINT fk_comp_curve_h_asset_no FOREIGN KEY  (meter_asset_no) REFERENCES dwd_cst_meter_run(meter_asset_no);  
 ALTER TABLE dwd_cst_es_e_mp_comp_curve_h_v ADD CONSTRAINT fk_comp_curve_h_v_asset_no FOREIGN KEY  (meter_asset_no) REFERENCES dwd_cst_meter_run(meter_asset_no);  
 ALTER TABLE dwd_cst_es_e_mp_comp_curve_h_a ADD CONSTRAINT fk_comp_curve_h_a_asset_no FOREIGN KEY  (meter_asset_no) REFERENCES dwd_cst_meter_run(meter_asset_no);  
 
ALTER TABLE dim_cst_cust_agrt ADD CONSTRAINT fk_settle_acct_id FOREIGN KEY  (settle_acct_id) REFERENCES dim_cst_settle_acct(settle_acct_id);
ALTER TABLE dim_cst_inst_elec_cons ADD CONSTRAINT fk_cust_agrt_id FOREIGN KEY  (cust_agrt_id) REFERENCES dim_cst_cust_agrt(cust_agrt_id);
ALTER TABLE dim_cst_settle_acct ADD CONSTRAINT fk_bp_id FOREIGN KEY  (bp_id) REFERENCES dim_cst_business_partner (bp_id);

ALTER TABLE dim_cst_inst_elec_cons ADD CONSTRAINT fk_inst_cust_id FOREIGN KEY  (cust_id) REFERENCES dim_cst_elec_cons_cust(cust_id);
ALTER TABLE dwd_cst_meter_run ADD CONSTRAINT fk_inst_id FOREIGN KEY  (inst_id) REFERENCES dim_cst_inst_elec_cons(inst_id);
ALTER TABLE dwd_cst_meter_run ADD CONSTRAINT fk_meter_id FOREIGN KEY  (meter_id) REFERENCES dim_cst_elec_meter(dev_id);
ALTER TABLE dim_cst_adj_volt_dev ADD CONSTRAINT fk_dev_dist_sta_id FOREIGN KEY  (dist_sta_id) REFERENCES dim_cst_dist_sta(dist_sta_id);
ALTER TABLE dim_cst_inst_elec_cons ADD CONSTRAINT fk_inst_dist_sta_id FOREIGN KEY  (dist_sta_id) REFERENCES dim_cst_dist_sta(dist_sta_id);
ALTER TABLE dim_cst_pline_dist_sta_rela ADD CONSTRAINT fk_pipeline_id FOREIGN KEY  (pipeline_id) REFERENCES dim_cst_pipeline(pipeline_id);
ALTER TABLE dim_cst_pline_dist_sta_rela  ADD CONSTRAINT fk_dist_sta_id FOREIGN KEY  (dist_sta_id) REFERENCES dim_cst_dist_sta(dist_sta_id);
ALTER TABLE dim_cst_cntrl_sta_pline_rela ADD CONSTRAINT fk_cntrl_sta_id FOREIGN KEY  (cntrl_sta_id) REFERENCES dim_cst_cntrl_sta(cntrl_sta_id);
ALTER TABLE dim_cst_cntrl_sta_pline_rela ADD CONSTRAINT fk_rela_pipeline_id FOREIGN KEY  (pipeline_id) REFERENCES dim_cst_pipeline(pipeline_id);

ALTER TABLE dwd_cst_inst_bilg_card ADD CONSTRAINT fk_bilg_card_inst_id FOREIGN KEY  (inst_id) REFERENCES dim_cst_inst_elec_cons(inst_id);
ALTER TABLE dwd_cst_acct_bal ADD CONSTRAINT fk_acct_bal_cust_id FOREIGN KEY  (cust_no) REFERENCES dim_cst_elec_cons_cust(cust_no);
ALTER TABLE dwd_cst_es_meter_energy_day_l_xz ADD CONSTRAINT fk_energy_day_l_asset_no FOREIGN KEY  (meter_asset_no) REFERENCES dwd_cst_meter_run(meter_asset_no);
ALTER TABLE dwd_cst_meter_energy_day_h_xz ADD CONSTRAINT fk_energy_day_l_asset_no FOREIGN KEY  (meter_asset_no) REFERENCES dwd_cst_meter_run(meter_asset_no);
ALTER TABLE dwd_cst_rcvd_acct ADD CONSTRAINT fk_rcvd_acct_charg_acct_id FOREIGN KEY  (charg_acct_id) REFERENCES dwd_cst_charg_acct(charg_acct_id);
ALTER TABLE dwd_cst_rcvd_acct ADD CONSTRAINT fk_rcvbl_acct_id FOREIGN KEY  (rcvbl_acct_id) REFERENCES dwd_cst_rcvbl_acct(rcvbl_acct_id);
ALTER TABLE dwd_cst_inst_bilg_card ADD CONSTRAINT fk_card_calc_id   FOREIGN KEY  (calc_id) REFERENCES dwd_cst_rcvbl_acct(qty_charg_calc_id)
ALTER TABLE dim_cst_elec_meter ADD CONSTRAINT fk_elec_meter_dev_id FOREIGN KEY  (dev_id) REFERENCES dim_cst_dev(dev_id);

