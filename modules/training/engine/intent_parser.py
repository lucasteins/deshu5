# -*- coding: utf-8 -*-
"""语义意图解析器：将业务问题解析为结构化 SQL 意图"""
import json
import re
import os
import sys
from typing import Dict, List, Optional, Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from core.schema_kb import SchemaKnowledgeBase
from core.rag_retriever import RAGRetriever

# 优先使用项目本地 jieba
_VENDOR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.vendor')
if os.path.isdir(_VENDOR) and _VENDOR not in sys.path:
    sys.path.insert(0, _VENDOR)

try:
    import jieba
    _JIEBA_AVAILABLE = True
except ImportError:
    _JIEBA_AVAILABLE = False

# P2：关键词映射改从治理库读取（resources 层）；空表/异常时回退下方代码常量
from modules.resources.providers.keyword_table_map import get_keyword_table_map

# 业务概念 -> 表名（P2：已入库 keyword_table_map，scope=intent/shared；
# 本常量为空表/异常时的回退值，迁移脚本以本常量为入库来源）
DEFAULT_CONCEPT_TO_TABLES = {
    '客户': ['dim_cst_cust'],
    '用电客户': ['dim_cst_elec_cons_cust'],
    '能源客户': ['dim_cst_cust'],
    '安装点': ['dim_cst_inst_elec_cons'],
    '计量点': ['dim_cst_inst_elec_cons', 'dwd_cst_meter_run'],
    '表计': ['dwd_cst_meter_run', 'dim_cst_elec_meter'],
    '计量表': ['dwd_cst_meter_run'],
    '电量': ['dwd_cst_meter_energy_day_h_xz', 'dwd_cst_es_meter_energy_day_p'],
    '用电': ['dwd_cst_meter_energy_day_h_xz', 'dwd_cst_es_meter_energy_day_p'],
    '用电量': ['dwd_cst_meter_energy_day_h_xz', 'dwd_cst_es_meter_energy_day_p'],
    '日电量': ['dwd_cst_meter_energy_day_h_xz', 'dwd_cst_es_meter_energy_day_p'],
    '电费': ['dwd_cst_rcvbl_acct', 'dwd_cst_rcvd_acct'],
    '应收': ['dwd_cst_rcvbl_acct'],
    '实收': ['dwd_cst_rcvd_acct'],
    '收费': ['dwd_cst_charg_acct'],
    '缴费': ['dwd_cst_charg_acct'],
    '余额': ['dwd_cst_acct_bal'],
    '账户': ['dwd_cst_acct_bal', 'dim_cst_settle_acct'],
    '抄表': ['dwd_cst_mr_data'],
    '示数': ['dwd_cst_mr_data'],
    '欠费': ['dwd_cst_rcvbl_acct'],
    '管理单位': ['dim_cst_mgt_org'],
    '供电单位': ['dim_cst_mgt_org'],
    '供电所': ['dim_cst_mgt_org'],
    '线路': ['dim_cst_pipeline'],
    '变压器': ['dim_cst_adj_volt_dev', 'dim_cst_dist_sta'],
    '调压设备': ['dim_cst_adj_volt_dev'],
    '配送站': ['dim_cst_dist_sta'],
    '退费': ['dwd_cst_bilg_rs_rcpt'],
    '违约金': ['dwd_cst_addl_charg'],
    '电价': ['dwd_cst_sgmt_qty_charg'],
}


class RuleBasedIntentParser:
    """基于规则的意图解析器"""
    
    def __init__(self):
        self.kb = SchemaKnowledgeBase()
        self.column_comments = self.kb.get_column_comments()
        self._init_keyword_maps()
    
    def _init_keyword_maps(self):
        """初始化关键词映射（P2：优先读治理库 keyword_table_map，空表/异常回退代码常量）"""
        # 业务概念 -> 表名
        self.concept_to_tables = get_keyword_table_map('intent') or DEFAULT_CONCEPT_TO_TABLES
        
        # 聚合词
        self.agg_keywords = {
            '统计': 'SUM',
            '求和': 'SUM',
            '总计': 'SUM',
            '合计': 'SUM',
            '总和': 'SUM',
            '一共': 'SUM',
            '总共': 'SUM',
            '总额': 'SUM',
            '总数': 'COUNT',
            '数量': 'COUNT',
            '个数': 'COUNT',
            '多少': 'COUNT',
            '平均': 'AVG',
            '均值': 'AVG',
            '最大': 'MAX',
            '最高': 'MAX',
            '最小': 'MIN',
            '最低': 'MIN',
        }
        
        # 过滤词
        self.filter_keywords = {
            '高压': ('cust_cls_desc', '=', '高压'),
            '低压非居民': ('cust_cls_desc', '=', '低压非居民'),
            '低压居民': ('cust_cls_desc', '=', '低压居民'),
            '工业用电': ('ec_categ_desc', '=', '工业用电'),
            '非工业': ('ec_categ_desc', '=', '非工业'),
            '运行状态': ('dev_stat_desc', '=', '运行'),
        }
        
        # 时间模式
        self.time_patterns = [
            (r'(\d{4})年(\d{1,2})月(\d{1,2})日?', self._parse_full_date),
            (r'(\d{4})年(\d{1,2})月', self._parse_year_month),
            (r'(\d{4})年', self._parse_year),
            (r'(\d{4})-(\d{2})', self._parse_year_month_dash),
        ]
    
    def _parse_full_date(self, year, month, day, date_field: str = 'data_date') -> Dict:
        return {
            'op': '=',
            'value': f'{year}-{int(month):02d}-{int(day):02d}',
            'field': date_field
        }
    
    def _parse_full_date(self, year, month, day, date_field: str = 'data_date') -> Dict:
        return {
            'op': '=',
            'value': f'{year}-{int(month):02d}-{int(day):02d}',
            'field': date_field
        }
    
    def _parse_year_month(self, year, month, date_field: str = 'data_date') -> Dict:
        month = int(month)
        if date_field.split('.')[-1].endswith('_ym'):
            return {'op': '=', 'value': f'{year}{month:02d}', 'field': date_field}
        return {
            'op': 'BETWEEN',
            'value': [f'{year}-{month:02d}-01', f'{year}-{month:02d}-31'],
            'field': date_field
        }
    
    def _parse_year_month_ym(self, year, month, date_field: str = 'data_date') -> Dict:
        month = int(month)
        return {'op': '=', 'value': f'{year}{month:02d}', 'field': date_field}
    
    def _parse_year(self, year, date_field: str = 'data_date') -> Dict:
        if date_field.split('.')[-1].endswith('_ym'):
            return {'op': 'LIKE', 'value': f'{year}%', 'field': date_field}
        return {
            'op': 'BETWEEN',
            'value': [f'{year}-01-01', f'{year}-12-31'],
            'field': date_field
        }
    
    def _parse_year_month_dash(self, year, month, date_field: str = 'data_date') -> Dict:
        month = int(month)
        if date_field.split('.')[-1].endswith('_ym'):
            return {'op': '=', 'value': f'{year}{month:02d}', 'field': date_field}
        return {
            'op': 'BETWEEN',
            'value': [f'{year}-{month:02d}-01', f'{year}-{month:02d}-31'],
            'field': date_field
        }
    
    def _resolve_date_field(self, tables: List[str]) -> str:
        """根据相关表解析合适的日期字段"""
        if not tables:
            return 'data_date'
        
        # 表特定的优先级映射
        table_priority = {
            'dwd_cst_mr_data': ['this_read_act_date', 'this_read_frz_date', 'last_read_act_date'],
            'dwd_cst_rcvbl_acct': ['rcvbl_ym', 'issu_date'],
            'dwd_cst_meter_energy_day_h_xz': ['data_date'],
            'dwd_cst_es_meter_energy_day_p': ['data_date'],
        }
        
        for t in tables:
            for col in table_priority.get(t, []):
                if col in self.column_comments.get(t, {}):
                    return f'{t}.{col}'
        
        # 启发式：找注释含 日期/时间 且列名含 date/ym 的字段
        for t in tables:
            for col, comment in self.column_comments.get(t, {}).items():
                if any(k in comment for k in ['日期', '时间', '年月', '月份']) or 'date' in col.lower() or col.lower().endswith('_ym'):
                    return f'{t}.{col}'
        
        return 'data_date'
    
    def _extract_field_mentions(self, question: str) -> Dict[str, List[str]]:
        """从问题中提取提到的字段及对应表（按业务域过滤，避免无关表）"""
        # 提取可能的字段名（英文标识符）
        field_candidates = set(re.findall(r'\b([a-zA-Z_][a-zA-Z0-9_]*)\b', question))
        # 提取中文词（长度>=2）
        chinese_tokens = set(re.findall(r'[\u4e00-\u9fa5]{2,}', question))
        
        col_to_tables: Dict[str, List[str]] = {}
        
        for table, cols in self.column_comments.items():
            table_comment = (self.kb.ddl_parser.get_table_comment(table) or '').lower()
            table_text = f'{table} {table_comment}'
            domain_score = sum(1 for token in chinese_tokens if token in table_text)
            
            for col in cols:
                matched = False
                # 直接匹配字段名
                if col in field_candidates:
                    matched = True
                # 匹配字段注释中的中文词
                if not matched:
                    comment = (cols[col] or '').lower()
                    for token in chinese_tokens:
                        if token in comment:
                            matched = True
                            break
                
                if matched:
                    # 只保留与问题业务域相关、或已经明显相关的表
                    if domain_score > 0 or any(k in table_text for k in ['电量', '用电', '客户', '抄表', '应收', '计量', '供电']):
                        col_to_tables.setdefault(col, []).append(table)
        
        return col_to_tables
    
    def _infer_select_fields(self, question: str, tables: List[str]) -> List[Dict]:
        """根据问题推断需要 SELECT 的字段"""
        fields = []
        q = question.lower()
        
        # 表默认投影：当问题只要求“记录/列表”且没有明确字段时，返回最具代表性的字段
        self.table_default_fields = {
            'dim_cst_elec_cons_cust': ['cust_no', 'cust_cls_desc', 'ec_categ_desc'],
            'dim_cst_cust': ['cust_no', 'cust_name'],
            'dim_cst_mgt_org': ['mgt_org_code', 'mgt_org_name'],
            'dwd_cst_meter_run': ['meter_asset_no'],
            'dwd_cst_meter_energy_day_h_xz': ['meter_asset_no', 'data_date', 'pap_e'],
            'dwd_cst_rcvbl_acct': ['rcvbl_acct_id', 'acct_no', 'rcvbl_ym', 'rcvbl_amt'],
        }
        
        # 常见中文表达 -> 字段映射
        expression_to_fields = {
            '客户名称': [('dim_cst_cust', 'cust_name'), ('dim_cst_elec_cons_cust', 'cust_name')],
            '客户编号': [('dim_cst_cust', 'cust_no'), ('dim_cst_elec_cons_cust', 'cust_no')],
            '客户名称和编号': [('dim_cst_cust', 'cust_name'), ('dim_cst_cust', 'cust_no')],
            '客户名称及编号': [('dim_cst_cust', 'cust_name'), ('dim_cst_cust', 'cust_no')],
            '供电单位名称': [('dim_cst_mgt_org', 'mgt_org_name')],
            '供电单位编码': [('dim_cst_mgt_org', 'mgt_org_code')],
            '供电单位名称及编码': [('dim_cst_mgt_org', 'mgt_org_name'), ('dim_cst_mgt_org', 'mgt_org_code')],
            '供电单位名称和编码': [('dim_cst_mgt_org', 'mgt_org_name'), ('dim_cst_mgt_org', 'mgt_org_code')],
            '管理单位名称及编码': [('dim_cst_mgt_org', 'mgt_org_name'), ('dim_cst_mgt_org', 'mgt_org_code')],
            '计量点编号': [('dim_cst_inst_elec_cons', 'inst_id')],
            '资产编号': [('dwd_cst_meter_run', 'meter_asset_no')],
            '用电量': [('dwd_cst_meter_energy_day_h_xz', 'pap_e')],
            '日电量': [('dwd_cst_meter_energy_day_h_xz', 'pap_e')],
            '抄表数据': [('dwd_cst_mr_data', 'this_read_frz_date'), ('dwd_cst_mr_data', 'this_m_r'), ('dwd_cst_mr_data', 'this_mr_qty')],
        }
        
        for expr, candidates in expression_to_fields.items():
            if expr in question or expr in q:
                for table, col in candidates:
                    if table in tables and col in self.column_comments.get(table, {}):
                        fields.append({'name': f'{table}.{col}', 'role': 'select'})
        
        # 如果仍未识别字段，且问题是“列表/记录/有哪些/哪些”类，使用表默认投影
        if not fields and any(w in question for w in ['记录', '列表', '有哪些', '哪些', '所有']):
            for t in tables:
                if t in self.table_default_fields:
                    for col in self.table_default_fields[t]:
                        if col in self.column_comments.get(t, {}):
                            fields.append({'name': f'{t}.{col}', 'role': 'select'})
                    break
        
        # 去重
        seen = set()
        unique_fields = []
        for f in fields:
            key = f['name']
            if key not in seen:
                seen.add(key)
                unique_fields.append(f)
        
        return unique_fields
    
    def _infer_group_dimensions(self, question: str, tables: List[str]) -> List[str]:
        """根据问题中的‘各xxx’推断分组维度"""
        dims = []
        q = question.lower()
        
        # 供电单位/管理单位
        if any(w in q for w in ['各供电单位', '各管理单位', '按供电单位', '按管理单位', '每个供电单位', '各供电所']):
            if any('mgt_org' in t for t in tables):
                dims.append('mgt_org_code')
        
        # 客户（用电客户/能源客户）
        if any(w in q for w in ['各客户', '每个客户', '各用电客户', '按客户']):
            if 'dim_cst_elec_cons_cust' in tables:
                dims.append('cust_id')
            elif 'dim_cst_cust' in tables:
                dims.append('cust_id')
        
        # 计量点/资产编号
        if any(w in q for w in ['各计量点', '每个计量点', '按计量点', '各资产编号']):
            if any('meter_run' in t or 'inst_elec_cons' in t for t in tables):
                dims.append('meter_asset_no')
        
        return dims
    
    def _extract_explicit_filters(self, question: str) -> List[Dict]:
        """提取问题中的显式条件，如 field='value' 或 field=\"value\""""
        filters = []
        # 匹配 字段名 = '值' 或 字段名 = \"值\"，或 字段名=值（数字）
        pattern = r"([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*['\"]([^'\"]+)['\"]"
        for match in re.finditer(pattern, question):
            field = match.group(1)
            value = match.group(2)
            filters.append({'field': field, 'op': '=', 'value': value})
        return filters

    # ---------- 通用中文属性值过滤提取 ----------
    _FILTER_STOP_WORDS = {'请', '查询', '统计', '获取', '找出', '列出', '的', '信息', '清单', '记录', '详情', '数据'}
    _FILTER_GENERIC_ATTR_SUFFIX = {'信息', '清单', '记录', '详情', '数据', '条件'}

    def _tokenize(self, text: str) -> List[str]:
        """中文分词，未安装 jieba 时按字二元组回退"""
        text = text.strip()
        if not text:
            return []
        if _JIEBA_AVAILABLE:
            tokens = [t.strip() for t in jieba.lcut(text) if t.strip()]
            # 同时保留单字，增强短属性匹配
            tokens.extend(list(text))
            return tokens
        # 回退：字二元组 + 单字
        tokens = []
        for i in range(len(text)):
            tokens.append(text[i])
            if i + 1 < len(text):
                tokens.append(text[i:i + 2])
        return tokens

    def _clean_attr_phrase(self, attr: str) -> str:
        """清洗属性短语，去掉前后停用词和通用后缀"""
        attr = attr.strip().lower()
        # 去掉开头停用词
        changed = True
        while changed:
            changed = False
            for w in self._FILTER_STOP_WORDS:
                if attr.startswith(w):
                    attr = attr[len(w):].strip()
                    changed = True
                    break
        # 去掉结尾通用后缀
        for suffix in self._FILTER_GENERIC_ATTR_SUFFIX:
            if attr.endswith(suffix):
                attr = attr[:-len(suffix)].strip()
                break
        return attr

    def _match_attr_to_column(self, attr: str, value: str = ''):
        """
        把中文属性短语（如'设备状态'）映射到 (table, column, score)。
        score 为 0 表示未匹配。
        """
        attr = self._clean_attr_phrase(attr)
        if len(attr) < 2:
            return None, None, 0.0

        attr_tokens = set(self._tokenize(attr))
        if not attr_tokens:
            return None, None, 0.0

        value_has_chinese = bool(re.search(r'[\u4e00-\u9fa5]', value))
        best = {'table': None, 'col': None, 'score': 0.0}

        for table, cols in self.column_comments.items():
            table_comment = (self.kb.ddl_parser.get_table_comment(table) or '').lower()
            table_name = table.lower()

            # 尝试把属性短语中的表名/表注释去掉，得到更纯粹的字段描述
            attr_without_table = attr
            table_prefix_boost = 0.0
            for tname in [table_comment, table_name]:
                if tname and attr_without_table.startswith(tname):
                    attr_without_table = attr_without_table[len(tname):].strip()
                    table_prefix_boost = 0.15
            attr_without_table = self._clean_attr_phrase(attr_without_table)
            tokens_without_table = set(self._tokenize(attr_without_table))

            for col, comment in cols.items():
                comment_lower = (comment or '').lower()
                candidates = [
                    (attr, attr_tokens, 0.0),
                    (attr_without_table, tokens_without_table, table_prefix_boost),
                ]
                for candidate_attr, candidate_tokens, boost in candidates:
                    # 精确匹配字段注释
                    if candidate_attr == comment_lower or candidate_attr == col.lower():
                        score = 1.0 + boost
                    elif candidate_attr in comment_lower:
                        score = 0.9 + boost
                    else:
                        comment_tokens = set(self._tokenize(comment_lower))
                        if not comment_tokens:
                            continue
                        inter = candidate_tokens & comment_tokens
                        score = len(inter) / max(len(candidate_tokens), 1) + boost

                    # 值是中文描述时，优先匹配 *_desc 字段；非 _desc 字段降权
                    if value_has_chinese:
                        if col.lower().endswith('_desc'):
                            score += 0.12
                        else:
                            score -= 0.1

                    if score > best['score']:
                        best = {'table': table, 'col': col, 'score': score}

        # 阈值：至少有一个分词命中，且相对自信
        if best['score'] >= 0.3:
            return best['table'], best['col'], best['score']
        return None, None, 0.0

    def _extract_generic_filters(self, user_question: str) -> List[Dict]:
        """
        从自然语言中提取通用属性值过滤，例如：
        - 设备状态为“拆回待退”
        - 客户分类是“高压”
        - 设备分类等于“电能表”
        """
        filters = []
        # 属性在左、值在右，值带引号
        quoted_pattern = r"""(.+?)[是为等于][:：]?\s*["'“”‘’](.+?)["'“”‘’]"""
        for match in re.finditer(quoted_pattern, user_question):
            attr_raw = match.group(1).strip()
            value = match.group(2).strip()
            table, col, score = self._match_attr_to_column(attr_raw)
            if table and col:
                filters.append({
                    'field': f'{table}.{col}',
                    'op': '=',
                    'value': value,
                    '_match_score': score
                })

        # 无引号但明确的“X为Y”结构（Y 为短词/编号，且不是日期/纯数字）
        unquoted_pattern = r"""(.+?)[是为等于]([^，。；"'“”‘’\s]{1,12})"""
        for match in re.finditer(unquoted_pattern, user_question):
            attr_raw = match.group(1).strip()
            value = match.group(2).strip()
            if re.match(r'^\d{4}[-/年]', value) or re.match(r'^\d+$', value):
                continue
            if any(w in value for w in ['的', '为', '是', '和', '或']):
                continue
            table, col, score = self._match_attr_to_column(attr_raw)
            if table and col:
                field = f'{table}.{col}'
                # 去重：避免与引号模式重复
                if not any(f['field'] == field and f['value'] == value for f in filters):
                    filters.append({
                        'field': field,
                        'op': '=',
                        'value': value,
                        '_match_score': score
                    })
        return filters
    
    def parse(self, user_question: str, tables_hint: List[str] = None) -> Dict:
        """规则解析主入口"""
        intent = {
            'question_type': '查询',
            'tables': [],
            'fields': [],
            'filters': [],
            'joins': [],
            'group_by': [],
            'order_by': [],
            'limit': None,
            'confidence': 0.5
        }
        
        q = user_question.lower()
        
        # 1. 识别问题类型
        if any(w in q for w in ['统计', '求和', '平均', '最大', '最小', '总数']):
            intent['question_type'] = '统计'
        if any(w in q for w in ['top', '前', '排名']):
            intent['question_type'] = 'TOPN'
        if any(w in q for w in ['趋势', '变化', '增长', '下降']):
            intent['question_type'] = '趋势'
        if any(w in q for w in ['异常', '不匹配', '错误']):
            intent['question_type'] = '数据质量'
        
        # 2. 识别相关表（按优先级）
        related_tables = []
        
        # 先匹配具体概念
        for concept, tables in self.concept_to_tables.items():
            if concept in q:
                for t in tables:
                    if t not in related_tables:
                        related_tables.append(t)
        
        # 从问题中提到的字段推断表
        field_mentions = self._extract_field_mentions(user_question)
        for col, tables in field_mentions.items():
            for t in tables:
                if t not in related_tables:
                    related_tables.append(t)
        
        # 去重与优先级调整
        # 如果涉及用电/电量，补齐完整链路（但仅当尚未通过字段精确命中表时）
        if any(k in q for k in ['电量', '用电', '用电量', '日电量', 'pap_e']):
            chain = ['dim_cst_elec_cons_cust', 'dim_cst_inst_elec_cons', 'dwd_cst_meter_run', 'dwd_cst_meter_energy_day_h_xz']
            for t in chain:
                if t not in related_tables:
                    related_tables.append(t)
            # 移除 dim_cst_cust，使用 dim_cst_elec_cons_cust 替代
            if 'dim_cst_cust' in related_tables and 'dim_cst_elec_cons_cust' in related_tables:
                related_tables.remove('dim_cst_cust')
            # 移除公变表，优先使用专变表——但问题明确"公变/台区"口径时保留公变表
            # （电量新口径：公变表承载台区/全社会口径，此时专变表反而不是载体）
            if 'dwd_cst_es_meter_energy_day_p' in related_tables and 'dwd_cst_meter_energy_day_h_xz' in related_tables:
                if not any(k in q for k in ('公变', '台区')):
                    related_tables.remove('dwd_cst_es_meter_energy_day_p')
        
        # 如果提供了表提示，合并
        if tables_hint:
            for t in tables_hint:
                if t not in related_tables:
                    related_tables.append(t)
        
        intent['tables'] = related_tables[:6]  # 最多6张表
        
        # 3. 识别 SELECT 字段
        intent['fields'] = self._infer_select_fields(user_question, intent['tables'])
        
        # 4. 识别聚合
        for kw, agg in self.agg_keywords.items():
            if kw in q:
                intent['fields'].append({'name': '', 'role': 'aggregate', 'agg': agg})
                break
        
        # 4.5 TOP N 用电量/电费类问题，自动补全 SUM 聚合和分组
        if intent['question_type'] == 'TOPN' and any(k in q for k in ['用电量', '电费', '应收']):
            intent['fields'].append({'name': 'pap_e', 'role': 'aggregate', 'agg': 'SUM'})
            if 'meter_asset_no' in user_question or '计量点' in user_question:
                intent['group_by'].append('meter_asset_no')
            elif '客户' in user_question:
                intent['group_by'].append('cust_id')
        
        # 5. 识别过滤条件
        for kw, (field, op, value) in self.filter_keywords.items():
            if kw in q:
                intent['filters'].append({'field': field, 'op': op, 'value': value})
        
        # 显式条件，如 meter_asset_no='000001'
        explicit_filters = self._extract_explicit_filters(user_question)
        intent['filters'].extend(explicit_filters)

        # 通用中文属性值过滤，如“设备状态为‘拆回待退’”
        generic_filters = self._extract_generic_filters(user_question)
        for gf in generic_filters:
            table = gf['field'].split('.')[0]
            if table not in intent['tables']:
                intent['tables'].append(table)
            intent['filters'].append(gf)

        # 特殊业务规则：欠费
        if '欠费' in user_question and 'dwd_cst_rcvbl_acct' in intent['tables']:
            intent['filters'].append({'field': 'arer_bal', 'op': '>', 'value': 0})
            if '欠费天数' in user_question:
                intent['filters'].append({
                    'field': 'issu_date',
                    'op': '>',
                    'value': "julianday('now') - julianday(issu_date) > 30",
                    'raw': True
                })
        
        # 时间过滤：动态解析日期字段
        date_field = self._resolve_date_field(intent['tables'])
        is_ym_field = date_field.split('.')[-1].endswith('_ym')
        for pattern, parser in self.time_patterns:
            match = re.search(pattern, user_question)
            if match:
                if is_ym_field and parser in (self._parse_year_month, self._parse_year_month_dash):
                    time_filter = self._parse_year_month_ym(*match.groups(), date_field=date_field)
                else:
                    time_filter = parser(*match.groups(), date_field=date_field)
                intent['filters'].append(time_filter)
                break
        
        # 6. 识别 TOP N
        top_match = re.search(r'前\s*(\d+)', user_question)
        if not top_match:
            top_match = re.search(r'top\s*(\d+)', user_question, re.IGNORECASE)
        if top_match:
            intent['limit'] = int(top_match.group(1))
            intent['order_by'].append({'field': '', 'direction': 'DESC'})
        
        # 7. 根据表推断 JOIN
        if len(intent['tables']) >= 2:
            intent['joins'] = self._infer_joins(intent['tables'])
        
        # 8. 识别 GROUP BY（从问题中的“各xxx”推断，避免按表盲目追加）
        if any(f.get('role') == 'aggregate' for f in intent['fields']):
            group_dims = self._infer_group_dimensions(user_question, intent['tables'])
            for g in group_dims:
                if g not in intent['group_by']:
                    intent['group_by'].append(g)
        
        # 9. 过滤条件后处理：去掉“描述字段+代码字段”重复（保留描述字段）
        intent['filters'] = self._dedup_desc_code_filters(intent['filters'])

        # 10. 去重过滤条件（按字段短名+操作+值去重，优先保留带表前缀的字段）
        def _filter_short_key(f):
            field = f.get('field', '')
            short = field.split('.')[-1] if '.' in field else field
            v = f.get('value')
            if isinstance(v, list):
                v = tuple(v)
            return (short, f.get('op'), v)

        filter_map = {}
        for f in intent['filters']:
            key = _filter_short_key(f)
            existing = filter_map.get(key)
            # 保留带表前缀的；都没有前缀时保留先出现的
            if existing is None or ('.' in f.get('field', '') and '.' not in existing.get('field', '')):
                filter_map[key] = f
        intent['filters'] = list(filter_map.values())
        return intent

    def _dedup_desc_code_filters(self, filters: List[Dict]) -> List[Dict]:
        """如果同一值同时命中 *_desc 字段和其代码字段，只保留 *_desc 字段"""
        def _hashable(v):
            return tuple(v) if isinstance(v, list) else v

        desc_fields = {}
        for f in filters:
            field = f.get('field', '')
            short = field.split('.')[-1] if '.' in field else field
            if short.endswith('_desc'):
                code_field = short[:-5]
                desc_fields[(code_field, f.get('op'), _hashable(f.get('value')))] = short

        result = []
        for f in filters:
            field = f.get('field', '')
            short = field.split('.')[-1] if '.' in field else field
            if not short.endswith('_desc'):
                code_key = (short, f.get('op'), _hashable(f.get('value')))
                if code_key in desc_fields:
                    continue
            result.append(f)
        return result

    def _infer_joins(self, tables: List[str]) -> List[Dict]:
        """基于关系知识库推断表间 JOIN"""
        from core.schema_kb import SchemaKnowledgeBase
        kb = SchemaKnowledgeBase()
        
        # 检索包含这些表的关系路径
        results = kb.retrieve_relationship_docs(keywords=[], tables=tables, limit=20)
        
        joins = []
        seen = set()
        for r in results:
            path = r['path']
            # 检查路径是否覆盖了意图中的大部分表
            if len(set(path) & set(tables)) >= 2:
                for jc in r['join_conditions']:
                    if jc not in seen:
                        seen.add(jc)
                        # 解析 JOIN 条件
                        match = re.match(r'(\w+)\.(\w+)\s*=\s*(\w+)\.(\w+)', jc)
                        if match:
                            joins.append({
                                'left_table': match.group(1),
                                'left_col': match.group(2),
                                'right_table': match.group(3),
                                'right_col': match.group(4)
                            })
        
        return joins[:10]


class LLMIntentParser:
    """基于 LLM 的意图解析器"""
    
    def __init__(self):
        self.api_url = config.KIMI_API_URL
        self.api_key = config.KIMI_API_KEY
        self.model = config.KIMI_MODEL
    
    def parse(self, user_question: str, schema_context: str = '', rag_context: str = '') -> Dict:
        """调用 LLM 解析意图"""
        import requests
        
        prompt = self._build_prompt(user_question, schema_context, rag_context)

        # Provider 参数差异统一由 llm_config.build_request 封装
        from core.llm_config import build_request as _llm_build
        use_thinking = getattr(config, 'KIMI_THINKING', False)
        url, headers, payload = _llm_build(
            [{'role': 'user', 'content': prompt}], max_tokens=8000, thinking=use_thinking)

        try:
            response = requests.post(url, headers=headers, json=payload, timeout=60)
            response.raise_for_status()
            data = response.json()
            content = data['choices'][0]['message']['content']
            return self._extract_json(content)
        except Exception as e:
            return {
                'error': str(e),
                'confidence': 0.0,
                'tables': [],
                'fields': [],
                'filters': [],
                'joins': [],
                'group_by': [],
                'order_by': [],
                'limit': None
            }
    
    def _build_prompt(self, user_question: str, schema_context: str, rag_context: str) -> str:
        return f"""你是电力营销领域的 NL2SQL 意图解析专家。请将用户的业务问题解析为结构化的 SQL 意图 JSON。

【数据库 Schema 信息】
{schema_context}

【参考模式】
{rag_context}

【用户问题】
{user_question}

【输出格式】
请只输出一个 JSON 对象，不要 Markdown 代码块，不要解释：
{{
  "question_type": "查询/统计/TOPN/趋势/数据质量",
  "tables": ["表名1", "表名2"],
  "fields": [
    {{"name": "字段名", "role": "select/aggregate/condition", "agg": "SUM/COUNT/AVG/MAX/MIN"}}
  ],
  "filters": [
    {{"field": "字段名", "op": "=/>/</LIKE/BETWEEN", "value": "值"}}
  ],
  "joins": [
    {{"left_table": "表A", "left_col": "字段", "right_table": "表B", "right_col": "字段"}}
  ],
  "group_by": ["字段名"],
  "order_by": [{{"field": "字段名", "direction": "ASC/DESC"}}],
  "limit": null,
  "confidence": 0.9
}}

注意：
1. 表名和字段名必须使用数据库中的实际英文名。
2. 如果问题中没有明确时间，不要编造时间条件。
3. 如果无法确定某个字段，留空字符串。
4. confidence 表示你对解析结果的信心（0-1）。
"""
    
    def _extract_json(self, content: str) -> Dict:
        """从 LLM 输出中提取 JSON"""
        # 尝试直接解析
        try:
            return json.loads(content)
        except Exception:
            pass
        
        # 去掉 markdown 代码块
        content = re.sub(r'```json\n?', '', content)
        content = re.sub(r'```\n?', '', content)
        
        # 查找第一个 JSON 对象
        match = re.search(r'\{.*\}', content, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except Exception:
                pass
        
        return {
            'error': '无法解析 LLM 输出',
            'raw_content': content,
            'confidence': 0.0
        }


class IntentParser:
    """意图解析器：规则 + LLM 双层解析"""
    
    def __init__(self):
        self.rule_parser = RuleBasedIntentParser()
        self.llm_parser = LLMIntentParser()
        self.kb = SchemaKnowledgeBase()
        self.rag = RAGRetriever(top_k=3)
    
    def parse(self, user_question: str, use_llm: bool = True) -> Dict:
        """
        解析用户问题为 SQL 意图
        
        策略：
        1. 先用规则解析器快速得到意图和候选表
        2. 检索 Schema 知识库构建上下文
        3. 可选调用 LLM 进行精化
        4. 融合规则结果和 LLM 结果
        """
        # 规则解析
        rule_intent = self.rule_parser.parse(user_question)
        
        # 检索 RAG 上下文
        schema_docs = self.kb.retrieve(
            user_question,
            tables=rule_intent['tables'],
            top_k_tables=5,
            top_k_columns=15,
            top_k_relationships=5,
            top_k_patterns=3
        )
        
        schema_context = self._build_schema_context(schema_docs)
        rag_pattern_context = self._build_pattern_context(schema_docs['patterns'])
        
        if use_llm:
            llm_intent = self.llm_parser.parse(user_question, schema_context, rag_pattern_context)
            final_intent = self._merge_intents(rule_intent, llm_intent)
        else:
            final_intent = rule_intent
        
        # 补全 JOIN 路径
        if final_intent.get('tables') and len(final_intent['tables']) >= 2 and not final_intent.get('joins'):
            final_intent['joins'] = self.rule_parser._infer_joins(final_intent['tables'])
        
        final_intent['_schema_docs'] = schema_docs
        return final_intent
    
    def _build_schema_context(self, schema_docs: Dict) -> str:
        """构建 Schema 上下文文本"""
        lines = []
        
        lines.append("【相关表】")
        for t in schema_docs.get('tables', []):
            lines.append(f"- {t['table_name']} ({t['table_comment']})")
        
        lines.append("\n【相关字段】")
        for c in schema_docs.get('columns', [])[:15]:
            lines.append(f"- {c['table_name']}.{c['column_name']} ({c['column_comment']})")
        
        lines.append("\n【表关系】")
        for r in schema_docs.get('relationships', [])[:5]:
            lines.append(f"- {r['title']}")
            for jc in r['join_conditions']:
                lines.append(f"  JOIN: {jc}")
        
        return '\n'.join(lines)
    
    def _build_pattern_context(self, patterns: List[Dict]) -> str:
        """构建模式上下文文本"""
        if not patterns:
            return ''
        lines = ["【参考 SQL 模式】"]
        for p in patterns[:3]:
            lines.append(p['doc_text'])
        return '\n'.join(lines)
    
    def _merge_intents(self, rule_intent: Dict, llm_intent: Dict) -> Dict:
        """融合规则意图和 LLM 意图"""
        merged = dict(rule_intent)
        
        # 如果 LLM 置信度高，优先使用 LLM 的表、字段、条件
        llm_conf = llm_intent.get('confidence', 0)
        
        if llm_conf >= 0.7:
            if llm_intent.get('tables'):
                merged['tables'] = llm_intent['tables']
            if llm_intent.get('fields'):
                merged['fields'] = llm_intent['fields']
            if llm_intent.get('filters'):
                merged['filters'] = llm_intent['filters']
            if llm_intent.get('joins'):
                merged['joins'] = llm_intent['joins']
            if llm_intent.get('group_by'):
                merged['group_by'] = llm_intent['group_by']
            if llm_intent.get('order_by'):
                merged['order_by'] = llm_intent['order_by']
            if llm_intent.get('limit'):
                merged['limit'] = llm_intent['limit']
        
        # 融合置信度
        merged['confidence'] = max(rule_intent.get('confidence', 0.5), llm_conf)
        merged['llm_confidence'] = llm_conf
        merged['rule_confidence'] = rule_intent.get('confidence', 0.5)
        
        return merged


if __name__ == '__main__':
    parser = IntentParser()
    questions = [
        "统计各供电单位下高压客户的总用电量",
        "查询2026年4月用电量排名前10的计量点",
        "找出存在欠费的客户"
    ]
    for q in questions:
        print(f"\n问题: {q}")
        intent = parser.parse(q, use_llm=False)
        print(json.dumps(intent, ensure_ascii=False, indent=2))
