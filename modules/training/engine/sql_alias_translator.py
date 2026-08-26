"""SQL 别名翻译模块：纯代码 SQL → 带 AS 中文别名 SQL（只改 SELECT 子句）"""
import re
import time
import requests
from typing import Dict, List, Optional, Tuple
import config
from core.database import DatabaseManager


class SQLAliasTranslator:
    """
    SQL 别名翻译器（v2.5 两段式分离）
    
    职责：接收已验证的纯代码 SQL，返回只修改 SELECT 子句的展示 SQL。
    策略：优先用规则兜底，复杂情况用 LLM 增强。
    """
    
    def __init__(self):
        self.db = DatabaseManager()
        self.api_url = config.KIMI_API_URL
        self.api_key = config.KIMI_API_KEY
        self.model = config.KIMI_MODEL
    
    def translate(self, sql: str, tables: List[str], user_question: str) -> Optional[str]:
        """
        翻译纯代码 SQL 为带中文别名的展示 SQL
        
        返回：带别名 SQL（成功）或 None（失败/回退）
        """
        if not sql:
            return None
        
        # 如果 tables 为空，尝试从 SQL 中提取
        if not tables:
            tables = self._extract_tables_from_sql(sql)
        
        # 1. 加载字段注释（即使为空也继续，会尝试基于字段名猜测）
        field_comments = self._load_field_comments(tables)
        
        # 2. 尝试规则翻译（不需要 LLM，给无别名字段加 AS）
        rule_sql = self._rule_translate(sql, field_comments)
        if rule_sql and rule_sql != sql:
            # 验证规则翻译是否安全（只改了 SELECT 部分）
            if self._validate_translation(sql, rule_sql):
                return rule_sql
        
        # 3. 规则不够完整，调用 LLM 翻译
        try:
            llm_sql = self._llm_translate(sql, field_comments, user_question)
            if llm_sql and self._validate_translation(sql, llm_sql):
                return llm_sql
        except Exception:
            pass
        
        return None
    
    def _load_field_comments(self, tables: List[str]) -> Dict[str, str]:
        """加载字段注释（权威源 = 业务库 information_schema，经 SchemaPreloader 单例缓存）。"""
        comments = {}
        if not tables:
            return comments
        try:
            from core.schema_preloader import SchemaPreloader
            preloader = SchemaPreloader.get_instance()
            for table in tables:
                for col in preloader.get_columns(table):
                    if col.get('comment'):
                        comments[col['name']] = col['comment']
        except Exception:
            pass
        return comments
    
    def _rule_translate(self, sql: str, field_comments: Dict[str, str]) -> Optional[str]:
        """
        规则翻译：给每个 SELECT 字段加 AS 中文别名
        
        支持简单查询和 WITH 子句（CTE），处理所有 SELECT 子句。
        同时同步替换 ORDER BY / HAVING 中对旧别名的引用。
        
        处理策略：
        - 字段在注释表中 → 用注释
        - 字段不在注释表中 → 基于字段名生成别名
        - COUNT(*) → 记录数
        - COUNT(DISTINCT x) → x的不重复计数
        - 聚合函数（如 SUM(pap_e)）→ SUM(pap_e) AS 正向有功总电能量总和
        - 已有别名的 → 替换为中文别名，并同步更新 ORDER BY / HAVING 中的引用
        - 带表前缀的 → 提取裸字段名查找
        """
        # 查找所有 SELECT ... FROM 匹配（支持 WITH 子句中的多个 SELECT）
        matches = list(re.finditer(r'(SELECT\s+)(.*?)(FROM\b)', sql, re.IGNORECASE | re.DOTALL))
        if not matches:
            return None
        
        translated_sql = sql
        modified = False
        alias_map = {}  # 旧英文别名 → 新中文别名（用于同步更新 ORDER BY / HAVING）
        
        # 从后往前处理，避免位置偏移
        for match in reversed(matches):
            prefix = match.group(1)
            select_body = match.group(2)
            suffix = match.group(3)
            
            # 分割字段表达式（处理嵌套括号）
            fields = self._split_fields(select_body)
            
            new_fields = []
            for field_expr in fields:
                field_stripped = field_expr.strip()
                if not field_stripped or field_stripped == '*':
                    new_fields.append(field_stripped)
                    continue
                
                # 提取已有别名（如果有）
                existing_alias_match = re.search(r'\s+AS\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*$', field_stripped, flags=re.IGNORECASE)
                existing_alias = existing_alias_match.group(1) if existing_alias_match else None
                
                # 去掉已有别名（如果有）
                original_expr = re.sub(r'\s+AS\s+[a-zA-Z_][a-zA-Z0-9_]*\s*$', '', field_stripped, flags=re.IGNORECASE).strip()
                
                # 含 CASE 的复杂表达式：正则无法安全解析内部结构，保持模型原样不加/改别名
                # （防 2026-08-14 发现的别名错位：'case总和'、'AS 应收amt else 0 end)' 等）
                if re.search(r'\bCASE\b', original_expr, flags=re.IGNORECASE):
                    new_fields.append(field_stripped)
                    continue
                
                # 提取裸字段名和判断表达式类型
                bare_name, expr_type = self._extract_bare_field(original_expr)
                
                # SQL 关键字误识别为字段名时跳过（如 CASE 拆分残留）
                if bare_name and bare_name.upper() in ('CASE', 'WHEN', 'THEN', 'ELSE', 'END', 'AS',
                                                       'SELECT', 'FROM', 'WHERE', 'AND', 'OR', 'ON'):
                    new_fields.append(field_stripped)
                    continue
                
                if not bare_name and expr_type != 'count_star':
                    new_fields.append(field_stripped)
                    continue
                
                # 获取字段注释
                comment = field_comments.get(bare_name) if bare_name else None
                
                # 生成显示别名
                if expr_type == 'count_star':
                    # COUNT(*) → 记录数
                    display_name = '记录数'
                elif expr_type == 'count_distinct':
                    # COUNT(DISTINCT x) → x的不重复计数
                    if comment:
                        display_name = f"{comment}不重复计数"
                    else:
                        display_name = f"{self._guess_field_alias(bare_name)}不重复计数"
                elif expr_type == 'function':
                    # COALESCE, IFNULL 等 → 基于第一个参数的别名
                    if comment:
                        display_name = comment
                    else:
                        display_name = self._guess_field_alias(bare_name)
                elif expr_type == 'aggregate':
                    func_name = original_expr.split('(')[0].strip().upper()
                    agg_suffix = {'SUM': '总和', 'COUNT': '计数', 'AVG': '平均',
                                  'MAX': '最大值', 'MIN': '最小值'}.get(func_name, func_name)
                    if comment:
                        display_name = f"{comment}{agg_suffix}"
                    else:
                        display_name = f"{self._guess_field_alias(bare_name)}{agg_suffix}"
                else:
                    # 纯字段
                    if comment:
                        display_name = comment
                    else:
                        display_name = self._guess_field_alias(bare_name)
                
                new_fields.append(f"{original_expr} AS {display_name}")
                
                # 记录旧别名 → 新别名映射（用于同步更新 ORDER BY / HAVING）
                if existing_alias:
                    alias_map[existing_alias] = display_name
                
                modified = True
            
            if modified:
                new_select = prefix + ', '.join(new_fields) + ' ' + suffix
                translated_sql = translated_sql[:match.start()] + new_select + translated_sql[match.end():]
        
        # 同步替换 ORDER BY / HAVING 中的旧别名引用
        if alias_map:
            translated_sql = self._replace_order_by_aliases(translated_sql, alias_map)
        
        return translated_sql if translated_sql != sql else None
    
    def _replace_order_by_aliases(self, sql: str, alias_map: Dict[str, str]) -> str:
        """
        替换 ORDER BY / HAVING 子句中对旧别名的引用。
        
        只替换不带表前缀的纯标识符（如 ORDER BY total_pap_e DESC），
        不替换带表前缀的字段引用（如 ORDER BY c.mgt_org_code）。
        """
        # 匹配 ORDER BY 子句: ORDER BY ... [ASC|DESC] [, ...]
        # 匹配到下一个关键字（LIMIT, GROUP BY, HAVING, UNION, SELECT, WITH, 或字符串结束）
        order_pattern = re.compile(
            r'(ORDER\s+BY\s+)(.*?)(?=\b(?:LIMIT|GROUP\s+BY|HAVING|UNION|SELECT|WITH|INTERSECT|EXCEPT)\b|$)',
            re.IGNORECASE | re.DOTALL
        )
        
        def replace_order_by(match):
            prefix = match.group(1)
            body = match.group(2)
            # 对 body 中的每个旧别名，替换为中文别名
            # 使用 \b 确保只替换独立的标识符，不替换字段名的一部分
            for old_alias, new_alias in alias_map.items():
                body = re.sub(r'\b' + re.escape(old_alias) + r'\b', new_alias, body)
            return prefix + body
        
        sql = order_pattern.sub(replace_order_by, sql)
        
        # 匹配 HAVING 子句: HAVING ... [AND|OR] ...
        # 匹配到下一个关键字（ORDER BY, GROUP BY, LIMIT, UNION, SELECT, WITH, 或字符串结束）
        having_pattern = re.compile(
            r'(HAVING\s+)(.*?)(?=\b(?:ORDER\s+BY|GROUP\s+BY|LIMIT|UNION|SELECT|WITH|INTERSECT|EXCEPT)\b|$)',
            re.IGNORECASE | re.DOTALL
        )
        
        def replace_having(match):
            prefix = match.group(1)
            body = match.group(2)
            for old_alias, new_alias in alias_map.items():
                body = re.sub(r'\b' + re.escape(old_alias) + r'\b', new_alias, body)
            return prefix + body
        
        sql = having_pattern.sub(replace_having, sql)
        
        return sql
    
    def _extract_tables_from_sql(self, sql: str) -> List[str]:
        """从 SQL 中提取表名（FROM 和 JOIN 后的表名）"""
        tables = set()
        for match in re.finditer(r'FROM\s+([a-zA-Z_][a-zA-Z0-9_]*)', sql, re.IGNORECASE):
            tables.add(match.group(1))
        for match in re.finditer(r'JOIN\s+([a-zA-Z_][a-zA-Z0-9_]*)', sql, re.IGNORECASE):
            tables.add(match.group(1))
        return sorted(tables)
    
    def _guess_field_alias(self, field_name: str) -> str:
        """
        基于字段名猜测中文别名（当字段不在注释表中时使用）
        
        策略：将英文下划线命名拆分为中文
        """
        if not field_name:
            return '字段'
        
        # 常见前缀映射（按优先级排序，长的优先）
        prefix_map = {
            'mgt_org': '管理单位', 'elec_cons': '用电', 'elec_meter': '电能表',
            'inst_elec': '安装点用电', 'inst_bilg': '安装点计费', 'settle_acct': '结算账户',
            'cust_agrt': '客户协议', 'business_partner': '业务伙伴', 'cntrl_sta': '枢纽站',
            'dist_sta': '配送站', 'pline_dist': '管线配送', 'adj_volt': '调压',
            'rcvbl_acct': '应收台账', 'rcvd_acct': '实收台账', 'charg_acct': '交费台账',
            'addl_charg': '加收费', 'spcl_exp': '专属费用', 'sgmt_qty': '分段量',
            'meter_energy': '日电量', 'es_meter': '电能表', 'es_e': '电能',
            'mp_comp': '负荷', 'a_ll': '线损', 'bilg_rs': '计费退补',
            'rs_inst': '退补安装点', 'mr_data': '抄表数据', 'rcvbl_addl': '应收加收',
            'credit_code': '统一社会信用代码', 'id_card': '身份证', 'vat_tax': '增值税',
            'urbanrural': '城乡', 'urban': '城市', 'rural': '农村',
            'cust_ind': '客户行业', 'high_ec': '高耗能', 'dereg': '市场化',
            'graded': '分次', 'cost_ctrl': '费控', 'tmp_ec': '临时用能',
            'main_hshd': '主户', 'stop_supl': '停供', 'rcvr_supl': '复供',
            'transfer': '转供', 'prod_shift': '生产班次', 'load_char': '负荷性质',
            'load_charts': '负荷特性', 'cstm_query': '自定义查询', 'ec_stf': '用能人数',
            'e_carea': '用能面积', 'recent_chg': '最近变更', 'usage_dur': '使用期限',
            'lock_stat': '锁定状态', 'charg_remind': '催费', 'scy_cap': '保安负荷',
            'hndl_time': '办理时间', 'last_insp': '上次检查', 'chkr_no': '检查人员',
            'e_sdate': '送能日期', 'pwr_off': '停电', 'bus_prov': '省',
            'bus_city': '市', 'bus_county': '区县', 'bus_st': '街道',
            'bus_neighbor': '社区', 'bus_rd': '道路', 'bus_cmny': '小区',
            'contact_name': '联系人名称', 'contact_mobile': '联系人电话',
            'is_cdzh': '是否充电桩户', 'is_yqh': '是否园区户', 'is_zgh': '是否转供户',
            'is_bzgh': '是否被转供户', 'is_dbh': '是否低保户', 'is_wbh': '是否五保户',
            'is_gs': '是否规上企业', 'is_xw': '是否小微企业', 'is_credit': '是否通过验证',
            'cust_cls': '客户分类', 'ec_categ': '用能类别', 'cust_volt': '客户承压',
            'volt_cls': '电压分类', 'prc_code': '价格码', 'ctlg_prc': '目录定价',
            'inst_cls': '安装点分类', 'inst_char': '安装点性质', 'inst_cap': '安装点容量',
            'inst_affil': '安装点所属', 'inst_usage': '安装点用途', 'bilg_card': '计费卡',
            'qty_charg': '量费', 't_settle': '总结算', 't_ctlg': '总目录',
            't_addl': '总加收', 'settle_qty': '结算量', 'settle_exp': '结算费',
            'settle_prc': '结算价格', 'ext_settle': '扩展结算', 'actl_out': '实际出账',
            'time_rng': '时间范围', 'err_occur': '错误发生', 'hndl_stat': '处理状态',
            'rs_reason': '退补原因', 'rs_cls': '退补类别', 'rs_ym': '退补年月',
            'app_date': '申请日期', 'applnt': '申请人', 'site_chk': '现场核查',
            'chk_rslt': '核查结果', 'attach_id': '附件标识', 'bilg_unit': '计费单元',
            'rs_app': '退补申请', 'rcvd_ym': '实收年月', 'rcvbl_ym': '应收年月',
            'acct_no': '记账编号', 'acct_ym': '记账年月', 'acct_date': '记账日期',
            'cap_no': '资金编号', 'trans_run': '交易流水', 'pay_order': '支付单',
            'cash_chk': '解款', 'mgt_chan': '渠道单位', 'prc_ind': '行业分类',
            'prc_volt': '承压等级', 'prc_type': '电价类别', 'prc_ec': '用能类别',
            'meter_mode': '计量方式', 'inst_usage': '安装点用途', 'gen_cons': '发用电户',
            'gen_mode': '发电方式', 'cust_pscateg': '客户电源', 'gc_type': '发电客户',
            'e_consp': '能源消纳', 'high_ec': '高耗能', 'as_ym': '追补年月',
            'per_mon': '每月', 'unit_eval': '单位评估', 'power_ratio': '功率比例',
            'exp_ymd': '费用日期', 'trnsm_dist': '输配', 'calc_bus': '计算业务',
            'addl_num': '加收量', 'addl_prc': '加收单价', 'addl_amt': '加收费用',
            'addl_charg_ctlg': '加收项', 'spcl_fee': '专属费用', 'scnd_lv': '二级',
            'settle_exp_qty': '结算量值', 'calc_actl': '计算实际', 'actl_pf': '实际功率因数',
            'pf_std': '功率因数标准', 'comp_rto': '综合倍率', 'ref_meter': '参考表计',
            'share_flag': '共用标志', 'share_met': '共用计量', 'meter_logic': '计量逻辑',
            'dev_cls': '设备分类', 'categ': '类别', 'dev_type': '设备类型',
            'dev_stat': '设备状态', 'instal_date': '安装日期', 'creator': '创建人',
            'self_rto': '自身倍率', 'bidi_meter': '双向计量', 'wh_id': '库房',
            'wh_area': '库区', 'stor_area': '存放区', 'stor_loc': '储位',
            'rv_desc': '额定电压', 'cali_cur': '标定电流', 'wire_mode': '接线方式',
            'resrc_supl': '资源供应', 'srv_kind': '服务种类', 'det_addr': '详细地址',
            'publ_clg': '公专', 'branch_flag': '分支标志', 'pip_line': '管线',
            'path_org': '途径单位', 'conn_line': '入线', 'pms_cntrl': '电网枢纽',
            'pms_pipeline': '电网线路', 'elec_cons_cust': '用电客户',
            'cust': '客户', 'user': '用户', 'org': '单位',
            'elec': '用电', 'inst': '安装点', 'meter': '表计', 'dev': '设备',
            'rcpt': '单据', 'bilg': '计费', 'charg': '收费', 'acct': '账户',
            'bal': '余额', 'amt': '金额', 'exp': '费用', 'qty': '数量',
            'stat': '状态', 'desc': '描述', 'name': '名称', 'no': '编号',
            'code': '代码', 'id': '标识', 'date': '日期', 'time': '时间',
            'addr': '地址', 'type': '类型', 'cls': '分类', 'categ': '类别',
            'flag': '标志', 'mode': '方式', 'cap': '容量', 'num': '数量',
            'count': '计数', 'total': '总计', 'sum': '总和', 'avg': '平均',
            'max': '最大', 'min': '最小', 'year': '年', 'month': '月',
            'day': '日', 'prc': '价格', 'ec': '用能', 'energy': '电量',
            'pwr': '功率', 'volt': '电压', 'cur': '电流', 'rto': '倍率',
            'settle': '结算', 'rcvbl': '应收', 'rcvd': '实收', 'addl': '加收',
            'spcl': '专属', 'ind': '行业', 'industry': '产业',
            'attr': '属性', 'province': '省', 'city': '市', 'county': '县',
            'station': '站', 'team': '班组', 'bus': '业务', 'area': '面积',
            'hndl': '办理', 'chk': '检查', 'insp': '检查', 'chkr': '检查人员',
            'expr': '到期', 'tmp': '临时', 'main': '主', 'attach': '附件',
            'contact': '联系人', 'mobile': '电话', 'credit': '信用', 'vat': '增值税',
            'tax': '税务', 'pp': '预付费', 'auth': '认证', 'within': '范围内',
            'plan': '计划', 'range': '范围', 'his': '历史', 'snapshot': '快照',
            'snap': '快照', 'calc': '计算', 'run': '运行', 'cons': '消费',
            'prod': '生产', 'shift': '班次', 'supl': '供应', 'stop': '停',
            'rcvr': '恢复', 'off': '断', 'reason': '原因', 'method': '方法',
            'source': '来源', 'target': '目标', 'result': '结果', 'item': '项目',
            'detail': '明细', 'data': '数据', 'read': '抄见', 'frz': '冻结',
            'this': '本次', 'last': '上次', 'comp': '综合', 'digit': '位数',
            'abnor': '异常', 'srch': '搜索', 'word': '词', 'greeting': '称谓',
            'risk': '风险', 'lv': '等级', 'not': '票据', 'note': '备注',
            'fix': '修正', 'quality': '质量', 'whole': '完整', 'point': '点数',
            'phase': '相位', 'ta': '电流变比', 'tv': '电压变比',
            'cust_no': '客户编号', 'cust_name': '客户名称', 'mgt_org_code': '管理单位编码',
            'mgt_org_name': '管理单位名称', 'cust_ind_cls_desc': '客户行业分类描述',
            'cust_ind_ustry_cls': '客户产业分类', 'ppy_auth_flag': '预付费认证标志',
            'within_city_plan_range_desc': '城市规划范围描述', 'creat_date': '创建日期',
            'urbanrural_categ': '城乡类别', 'urbanrural_categ_desc': '城乡类别描述',
            'cust_ind_cls': '客户行业分类', 't_bal': '总余额', 'frz_amt': '冻结金额',
            'tmp_frz_amt': '临时冻结金额', 'cur_avail_bal': '当前可用余额',
            'rcvd_adv_bal': '预收余额', 'rchg_card_bal': '充值卡余额',
            'spcl_bal': '专用余额', 'thrd_prty_bal': '第三方余额',
            'pap_e': '正向有功总电能量', 'rap_e': '反向有功总电能量',
            'prp_e': '正向无功总电能量', 'data_date': '数据日期',
            'meter_asset_no': '表计资产编号', 't_factor': '综合倍率',
            'ec_qty': '能源量', 'rcvbl_amt': '应收金额', 'rcvd_amt': '实收金额',
            'in_trnst_amt': '在途金额', 'arer_bal': '欠费余额',
            'write_off_amt': '核销金额', 'rcvbl_lqd_damg': '应收违约金',
            'rcvd_lqd_damg': '实收违约金', 'issu_date': '发行日期',
            'charg_ym': '收费年月', 'charg_date': '收费日期', 'charg_amt': '收费金额',
            'chan_no': '渠道编号', 'rela_id': '关联标识', 'acct_no': '记账编号',
            'acct_ym': '记账年月', 'acct_date': '记账日期', 'cap_no': '资金编号',
            'trans_run_acct_no': '交易流水号', 'pay_order_id': '支付单标识',
            'cash_chk_rec_id': '解款记录标识', 'mgt_chan_org': '渠道单位',
        }
        
        # 尝试拆分为单词，优先匹配最长的前缀
        alias = field_name
        remaining = field_name.lower()
        translated_parts = []
        
        # 简单策略：先尝试完整匹配，然后逐词拆分
        if remaining in prefix_map:
            return prefix_map[remaining]
        
        # 按 '_' 拆分，逐词翻译
        parts = remaining.split('_')
        for part in parts:
            if part in prefix_map:
                translated_parts.append(prefix_map[part])
            else:
                # 保留原单词（如果无法翻译）
                translated_parts.append(part)
        
        alias = ''.join(translated_parts)
        return alias if alias else field_name
    
    def _extract_bare_field(self, expr: str) -> Tuple[Optional[str], Optional[str]]:
        """
        从字段表达式提取裸字段名和表达式类型
        
        返回: (bare_name, expr_type)
        expr_type: 'count_star' | 'count_distinct' | 'aggregate' | 'field' | None
        """
        expr = expr.strip()
        
        # COUNT(*)
        if re.match(r'^COUNT\s*\(\s*\*\s*\)$', expr, re.IGNORECASE):
            return (None, 'count_star')
        
        # COUNT(DISTINCT x) 或 COUNT(DISTINCT a.x)
        distinct_match = re.match(r'^COUNT\s*\(\s*DISTINCT\s+([a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)?)\s*\)$', expr, re.IGNORECASE)
        if distinct_match:
            param = distinct_match.group(1)
            if '.' in param:
                param = param.split('.')[-1]
            return (param.strip('`'), 'count_distinct')
        
        # 聚合函数和常用函数：SUM(a.t_bal), COALESCE(b.t_settle_exp, 0)
        # 匹配 FUNC_NAME( first_param, ... ) 格式
        func_match = re.match(r'^(\w+)\s*\(\s*([a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)?)', expr, re.IGNORECASE)
        if func_match:
            func_name = func_match.group(1).upper()
            param = func_match.group(2)
            # 提取裸字段名（去掉表前缀）
            if '.' in param:
                param = param.split('.')[-1]
            param = param.strip('`')
            
            if func_name in ('SUM', 'COUNT', 'AVG', 'MAX', 'MIN'):
                return (param or '', 'aggregate')
            # COALESCE, IFNULL 等函数 → 基于第一个参数生成别名
            if func_name in ('COALESCE', 'IFNULL', 'NULLIF', 'GREATEST', 'LEAST'):
                return (param or '', 'function')
        
        # 表前缀：a.pap_e → pap_e
        if '.' in expr:
            parts = expr.split('.')
            return (parts[-1].strip('`'), 'field')
        
        # 纯字段
        if re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', expr):
            return (expr, 'field')
        
        return (None, None)
    
    def _llm_translate(self, sql: str, field_comments: Dict[str, str], user_question: str) -> Optional[str]:
        """LLM 翻译：处理复杂情况（默认 10s 超时，避免展示阶段拖慢整体响应）"""
        if not self.api_key:
            return None

        comments_str = '\n'.join([f'{k} → {v}' for k, v in field_comments.items()])

        prompt = f"""你是 MySQL SQL 专家。请给以下 SQL 的 SELECT 字段添加中文别名。

**严格遵守**：
- 只修改 SELECT 子句中的字段别名
- 不修改 WHERE、FROM、JOIN、GROUP BY、ORDER BY、HAVING 等任何其他部分
- 如果字段已有别名，替换为中文别名
- 如果字段没有别名，在末尾添加 AS 中文别名
- 聚合函数 SUM(x) → AS 字段注释 + 总和
- 聚合函数 COUNT(x) → AS 字段注释 + 计数
- 聚合函数 AVG(x) → AS 字段注释 + 平均
- 纯字段 → AS 字段注释
- ORDER BY 中的字段名保持原样，不要替换
- 不要添加注释行，不要修改 SQL 结构，只输出一行 SQL

原始 SQL：
{sql}

字段注释：
{comments_str}

只输出一行 SQL：
"""

        # 展示阶段的轻量调用：thinking=False 走快速模式（kimi 下为 0.6+disabled）；
        # Provider 参数差异统一由 llm_config.build_request 封装
        from core.llm_config import build_request as _llm_build
        url, headers, payload = _llm_build(
            [{'role': 'user', 'content': prompt}], max_tokens=800, thinking=False)

        t0 = time.perf_counter()
        response = requests.post(url, headers=headers, json=payload,
                                 timeout=getattr(config, 'LLM_TIMEOUT_ALIAS', 10))
        response.raise_for_status()
        data = response.json()
        content = data['choices'][0]['message']['content']
        elapsed = (time.perf_counter() - t0) * 1000
        print(f"[DEBUG] alias translate elapsed={elapsed:.1f}ms prompt_len={len(prompt)}", flush=True)
        
        # 提取 SQL
        content = re.sub(r'```sql\n?', '', content, flags=re.IGNORECASE)
        content = re.sub(r'```\n?', '', content)
        for line in content.split('\n'):
            stripped = line.strip()
            if stripped and not stripped.startswith('--'):
                return stripped
        return None
    
    def _validate_translation(self, original_sql: str, translated_sql: str) -> bool:
        """验证翻译只改了 SELECT 部分（每个 FROM 之后的内容完全一致）"""
        if not translated_sql:
            return False
        
        # 提取所有 FROM 关键字的位置
        orig_matches = list(re.finditer(r'FROM\b', original_sql, re.IGNORECASE))
        trans_matches = list(re.finditer(r'FROM\b', translated_sql, re.IGNORECASE))
        
        if len(orig_matches) != len(trans_matches):
            return False
        
        # 比较每个 FROM 之后的内容（直到下一个 SELECT/UNION/ORDER/GROUP/HAVING/LIMIT/) ）
        for orig_m, trans_m in zip(orig_matches, trans_matches):
            orig_tail = original_sql[orig_m.end():]
            trans_tail = translated_sql[trans_m.end():]
            
            # 查找下一个关键字的起始位置
            orig_next = re.search(r'\b(?:SELECT|UNION|ORDER\s+BY|GROUP\s+BY|HAVING|LIMIT|\))', orig_tail, re.IGNORECASE)
            trans_next = re.search(r'\b(?:SELECT|UNION|ORDER\s+BY|GROUP\s+BY|HAVING|LIMIT|\))', trans_tail, re.IGNORECASE)
            
            orig_len = orig_next.start() if orig_next else len(orig_tail)
            trans_len = trans_next.start() if trans_next else len(trans_tail)
            
            orig_from_body = orig_tail[:orig_len]
            trans_from_body = trans_tail[:trans_len]
            
            # 标准化后比较
            if ' '.join(orig_from_body.split()) != ' '.join(trans_from_body.split()):
                return False
        
        return True
    
    def _split_fields(self, select_body: str) -> List[str]:
        """分割 SELECT 字段列表（处理嵌套括号）"""
        fields = []
        current = []
        depth = 0
        
        for char in select_body:
            if char == '(':
                depth += 1
                current.append(char)
            elif char == ')':
                depth -= 1
                current.append(char)
            elif char == ',' and depth == 0:
                fields.append(''.join(current).strip())
                current = []
            else:
                current.append(char)
        
        if current:
            fields.append(''.join(current).strip())
        
        return fields
