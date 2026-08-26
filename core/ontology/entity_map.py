# -*- coding: utf-8 -*-
"""实体精炼默认映射：101 张物理表 → 业务实体（三层两域）。

- DEFAULT_ENTITY_MAP：表名 → (实体英文名, 实体中文名, layer)
  layer: master（主数据）/ business（业务数据）/ report（统计报表）
- RELATION_TABLE_MAP：纯关联表 → (from_table, to_table)，降级为实体间关系，不再是实体
- ads_* 报表表不需逐项列出：未命中映射的 ads_ 前缀表自动一表一实体归入 report 层
- 统计语义触发词：报表层优先召回规则的问题检测词表

本文件是冷启动种子；首次引导落入 ontology_entity_defs 表后，以库中数据为准（Web 可编辑）。
"""
from typing import Dict, List, Tuple

# ==================== 主数据域（master） ====================
_MASTER = {
    # 客户域
    'dim_cst_cust': ('Customer', '客户'),
    'dim_cst_business_partner': ('Customer', '客户'),
    'dim_cst_cust_agrt': ('Customer', '客户'),
    'dim_cst_elec_cons_cust': ('ElecConsumer', '用电客户'),
    'dim_cst_inst_elec_cons': ('MeteringPoint', '计量点'),
    'dim_cst_elec_meter': ('Meter', '电能表'),
    'dim_cst_dev': ('Meter', '电能表'),
    'dim_cst_mgt_org': ('MgtOrg', '供电单位'),
    'dim_cst_pipeline': ('Pipeline', '线路'),
    'dim_cst_dist_sta': ('DistributionArea', '台区'),
    'dim_cst_adj_volt_dev': ('DistributionArea', '台区'),
    'dim_cst_cntrl_sta': ('Substation', '变电站'),
    'dim_cst_settle_acct': ('SettleAccount', '结算账户'),
    # 电网设备域
    'dim_grid_t_ts_sg_da_con_substation_b': ('GridPlant', '电网厂站'),
    'dim_grid_t_ts_sg_da_conversubstation_b': ('GridPlant', '电网厂站'),
    'dim_grid_t_ts_sg_da_con_commonsubstation_b': ('GridPlant', '电网厂站'),
    'dim_grid_t_ts_sg_da_con_plant_b': ('GridPlant', '电网厂站'),
    'dim_grid_t_ts_sg_da_con_plant_capacity': ('GridPlant', '电网厂站'),
    'dim_grid_t_ts_sg_da_con_pwrgrid_b': ('GridPlant', '电网厂站'),
    'dim_equ_t_p_pd_stationpsr': ('GridPlant', '电网厂站'),
    'dim_equ_t_p_pd_switchingstationpsr': ('GridPlant', '电网厂站'),
    'dim_ast_t_p_dy_stationzonepsr': ('GridPlant', '电网厂站'),
    'dim_grid_t_ts_sg_da_acline_b': ('GridLine', '电网线路'),
    'dim_grid_t_ts_sg_da_aclineend_b': ('GridLine', '电网线路'),
    'dim_grid_t_ts_sg_da_tline_b': ('GridLine', '电网线路'),
    'dim_equ_t_p_dy_linepsr': ('GridLine', '电网线路'),
    'dim_equ_t_p_pd_linepsr': ('GridLine', '电网线路'),
    'dim_equ_t_p_pd_cablepsr': ('GridLine', '电网线路'),
    'dim_ast_t_p_pd_feederlinepsr': ('GridLine', '电网线路'),
    'dwd_grid_t_ts_sg_da_feederline_b_new': ('GridLine', '电网线路'),
    'dim_grid_t_ts_sg_da_dev_pwrtransfm_b': ('GridTransformer', '电网变压器'),
    'dim_grid_t_ts_sg_da_transfmwd_b': ('GridTransformer', '电网变压器'),
    'dim_equ_t_p_pd_optransformerpsr': ('GridTransformer', '电网变压器'),
    'dim_equ_t_p_pdzn_transformerpsr': ('GridTransformer', '电网变压器'),
    'dim_grid_t_ts_sg_da_busbar_b': ('GridDevice', '电网一次设备'),
    'dim_grid_t_ts_sg_da_breaker_b': ('GridDevice', '电网一次设备'),
    'dim_grid_t_ts_sg_da_dev_generator_b': ('GridDevice', '电网一次设备'),
    'dim_grid_t_ts_sg_da_tddc': ('GridDevice', '电网一次设备'),
    'dim_ast_t_odps_sync_v_p_pd_polesitepsr': ('GridDevice', '电网一次设备'),
    'dwd_grid_t_ts_sg_da_con_jlxdjbxx': ('GridMeterProfile', '计量档案'),
}

# ==================== 业务数据域（business） ====================
_BUSINESS = {
    'dwd_cst_meter_energy_day_h_xz': ('DailyEnergy', '日电量'),
    'dwd_cst_es_meter_energy_day_p': ('DailyEnergy', '日电量'),
    'dwd_cst_es_meter_energy_day_l_xz': ('DailyEnergy', '日电量'),
    'dwd_cst_es_e_mp_comp_curve_h': ('Curve96', '计量点96点曲线'),
    'dwd_cst_es_e_mp_comp_curve_h_v': ('Curve96', '计量点96点曲线'),
    'dwd_cst_es_e_mp_comp_curve_h_a': ('Curve96', '计量点96点曲线'),
    'dwd_cst_a_ll_dist_det_day': ('AreaDailyEnergy', '台区日电量'),
    'dwd_cst_meter_run': ('MeterRun', '计量点运行'),
    'dwd_cst_mr_data': ('MeterReading', '抄表记录'),
    'dwd_cst_rcvbl_acct': ('Receivable', '应收电费'),
    'dwd_cst_rcvbl_addl_acct': ('Receivable', '应收电费'),
    'dwd_cst_rcvd_acct': ('Received', '实收电费'),
    'dwd_cst_charg_acct': ('Payment', '交费记录'),
    'dwd_cst_proc_acct': ('Payment', '交费记录'),
    'dwd_cst_acct_bal': ('AccountBalance', '账户余额'),
    'dwd_cst_addl_charg': ('IcpCharge', '安装点费用'),
    'dwd_cst_sgmt_qty_charg': ('IcpCharge', '安装点费用'),
    'dwd_cst_special_expense': ('IcpCharge', '安装点费用'),
    'dwd_cst_inst_bilg_card': ('BillingCard', '安装点计费卡'),
    'dwd_cst_bilg_rs_rcpt': ('Refund', '计费退补'),
    'dwd_cst_rs_inst_bilg_card': ('Refund', '计费退补'),
    'dwd_cst_wk_order': ('WorkOrder', '业务工单'),
    'dwd_cst_wkorder_affilmtrl_rec': ('WorkOrder', '业务工单'),
    'dwd_cst_invest_order': ('WorkOrder', '业务工单'),
    'dwd_cst_preapp_order': ('WorkOrder', '业务工单'),
    'dwd_cst_step_rec': ('WorkOrder', '业务工单'),
    'dwd_cst_dev_inst_rmv_wk_rec': ('WorkOrder', '业务工单'),
    'dwd_cst_bus_app_form': ('BizApplication', '业务申请'),
    'dwd_cst_cust_elec_app_rec': ('BizApplication', '业务申请'),
    'dwd_cst_connection_app_rec': ('BizApplication', '业务申请'),
    'dwd_cst_conn_gen_power_app': ('BizApplication', '业务申请'),
    'dwd_cst_dev_rcpt_app': ('BizApplication', '业务申请'),
    'dwd_cst_dev_rcpt_app_dtl_info': ('BizApplication', '业务申请'),
    'dwd_cst_gc_app_rec': ('BizApplication', '业务申请'),
    'dwd_cst_stop_rcvr_supl_app': ('BizApplication', '业务申请'),
    'dwd_cst_acc_sch': ('Schedule', '计划任务'),
    'dwd_cst_cust_agrt_sch': ('Schedule', '计划任务'),
    'dwd_cst_inst_elec_sch': ('Schedule', '计划任务'),
    'dwd_cst_meter_sch': ('Schedule', '计划任务'),
}

# ==================== 关系表（降级为实体间关系，不再是实体） ====================
# 表名 → 语义说明（关系两端由该表自身的外键/列推导或治理关系文档给出）
RELATION_TABLE_MAP = {
    'dim_cst_cntrl_sta_pline_rela': '变电站—线路 关联',
    'dim_cst_pline_dist_sta_rela': '线路—台区 关联',
    'dim_equ_t_p_rel_transformer_priv': '专变—线路 关联',
    'dim_equ_t_p_rel_transformer_publ': '公变—线路 关联',
    'dim_equ_m_p_rel_pdtransformer_feederline': '配电变压器—馈线 关联',
}

# ==================== 统计语义触发词（报表层优先召回） ====================
# 问题含以下词之一 → 判定为"省/市/县三级统计"语义，优先检索 report 层
STATISTICAL_TRIGGERS = (
    '各市', '各地市', '各地区', '各区县', '各县', '各地市州',
    '全省', '全市', '全区', '分地区', '分区域', '按市', '按县', '按地区',
    '省市县', '三级', '地区分布', '区域分布', '行政区划',
)


def default_entity_map() -> Dict[str, Tuple[str, str, str]]:
    """表名 → (实体英文名, 实体中文名, layer)。"""
    out = {}
    for t, (en, zh) in _MASTER.items():
        out[t] = (en, zh, 'master')
    for t, (en, zh) in _BUSINESS.items():
        out[t] = (en, zh, 'business')
    return out


def build_entities(table_names: List[str],
                   table_comment=None) -> Tuple[Dict[str, dict], List[str]]:
    """按默认映射构建实体定义（dict 形态，供落库/构建本体）。

    table_names: 底座实际表清单（映射只覆盖在库表；未命中映射的表按规则兜底：
    ads_ 前缀 → report 层一表一实体；其余 → 归 master?business 兜底实体 Unmapped）。
    返回 (entities: {实体名: {name,label,layer,member_tables,parent,comment}}, unmapped: [表名])
    """
    emap = default_entity_map()
    entities: Dict[str, dict] = {}
    unmapped: List[str] = []

    def _ensure(name: str, label: str, layer: str) -> dict:
        if name not in entities:
            entities[name] = {'name': name, 'label': label, 'layer': layer,
                              'member_tables': [], 'parent': '', 'comment': ''}
        return entities[name]

    for t in table_names:
        if t in RELATION_TABLE_MAP:
            continue  # 关系表不占实体
        hit = emap.get(t)
        if hit:
            en, zh, layer = hit
            ent = _ensure(en, zh, layer)
        elif t.startswith('ads_'):
            # 报表层：一表一实体
            comment = (table_comment(t) if table_comment else '') or t
            ent = _ensure(t, comment, 'report')
        else:
            unmapped.append(t)
            ent = _ensure('Unmapped', '未映射', 'business')
        ent['member_tables'].append(t)

    for ent in entities.values():
        ent['member_tables'].sort()
    return entities, unmapped


def derive_entity_relations(relations: List[dict],
                            table_to_entity: Dict[str, str]) -> List[dict]:
    """实体间关系：由成员表间物理/治理关系聚合推导（去重，保留来源与场景）。

    relations: 表级关系 [{path:[a,b], join_conditions, business_scenarios, source}]
    返回实体级关系 [{from_entity, to_entity, member_relations:[表级 key], scenarios, sources}]
    """
    agg: Dict[Tuple[str, str], dict] = {}
    for r in relations:
        path = r.get('path') or []
        if len(path) != 2:
            continue
        ea, eb = table_to_entity.get(path[0]), table_to_entity.get(path[1])
        if not ea or not eb or ea == eb:
            continue  # 实体内部关系不入实体级
        key = tuple(sorted((ea, eb)))
        slot = agg.setdefault(key, {'from_entity': key[0], 'to_entity': key[1],
                                    'member_relations': [], 'scenarios': [], 'sources': set()})
        slot['member_relations'].append(f'{path[0]}--{path[1]}')
        for s in (r.get('business_scenarios') or []):
            if s and s not in slot['scenarios']:
                slot['scenarios'].append(s)
        if r.get('source'):
            slot['sources'].add(r['source'])
    out = []
    for v in agg.values():
        v['sources'] = sorted(v['sources'])
        v['member_relations'].sort()
        out.append(v)
    return sorted(out, key=lambda x: (x['from_entity'], x['to_entity']))
