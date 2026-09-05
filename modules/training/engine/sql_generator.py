# -*- coding: utf-8 -*-
"""LLM SQL 生成器：调用 KIMI API 生成 SQL（v2.4 安全版 / v3.0 意图版）"""
import os
import json
import re
import time
import threading
import requests
from typing import Dict, List, Optional
import config
from core.schema_loader import SchemaLoader
from core.rag_retriever import RAGRetriever
from core.database import DatabaseManager

# P2：硬编码业务知识改从治理库读取（resources 层）；空表/异常时回退本文件代码常量，
# 两条路径读出内容一致（见 resources/providers/keyword_table_map.py、business_rule.py 头部注释）
from modules.resources.providers.keyword_table_map import get_keyword_table_map
from modules.resources.providers.business_rule import (
    get_caliber_rules, get_family_synonyms, get_draft_filter_signals, get_analytical_keywords,
    render_caliber_injection)

# LLM HTTP 传输会话：trust_env=False 绕过本机系统代理（127.0.0.1:7890 故障时会把
# OpenSSL 握手重置为 SSL EOF；curl 不走注册表代理故正常）。仅影响传输层，不影响生成逻辑
_LLM_HTTP = requests.Session()
_LLM_HTTP.trust_env = False

# v3.0 新增：语义意图解析 + 结构化 SQL 构建
try:
    from modules.training.engine.intent_parser import IntentParser
    from modules.training.engine.sql_builder import SQLBuilder
    from modules.training.engine.sql_templates import SQLTemplateMatcher
    _INTENT_AVAILABLE = True
except Exception as e:
    _INTENT_AVAILABLE = False
    print(f"[WARN] 意图解析模块加载失败: {e}")


# ========== 关键词到表的硬编码映射（核心业务术语）==========
# P2：已入库 keyword_table_map（scope=v24_fallback/shared），消费点经
# get_keyword_table_map('v24_fallback') 读取；本常量为空表/异常时的回退值
KEYWORD_TO_TABLE_MAP = {
    '用电': ['dwd_cst_es_meter_energy_day_p', 'dwd_cst_meter_energy_day_h_xz'],
    '电量': ['dwd_cst_es_meter_energy_day_p', 'dwd_cst_meter_energy_day_h_xz'],
    'pap_e': ['dwd_cst_es_meter_energy_day_p', 'dwd_cst_meter_energy_day_h_xz'],
    '客户': ['dim_cst_elec_cons_cust', 'dim_cst_cust'],
    'cust_no': ['dim_cst_elec_cons_cust', 'dwd_cst_acct_bal'],
    'cust_id': ['dim_cst_cust', 'dim_cst_elec_cons_cust'],
    '余额': ['dwd_cst_acct_bal'],
    '账户': ['dwd_cst_acct_bal', 'dim_cst_settle_acct'],
    '应收': ['dwd_cst_rcvbl_acct'],
    '收费': ['dwd_cst_rcvd_acct'],
    '电费': ['dwd_cst_rcvbl_acct', 'dwd_cst_sgmt_qty_charg'],
    '管理单位': ['dim_cst_mgt_org'],
    '供电单位': ['dim_cst_mgt_org'],
    '计量': ['dwd_cst_meter_run', 'dim_cst_dev'],
    '设备': ['dim_cst_dev', 'dim_cst_inst_elec_cons'],
    '变压器': ['dim_cst_inst_elec_cons', 'dim_cst_adj_volt_dev'],
    '线路': ['dim_cst_pipeline'],
}

# 分析型问题关键词（"跳过 LLM 定位"与"草稿直出"两处判定共用；P2 已入库
# business_rules.analytical_kw，消费点经 get_analytical_keywords() 读取，本常量为空表回退值）
_DEFAULT_ANALYTICAL_KW = ('分析', '趋势', '分布', '关联', '对比', '占比', '异常', '波动', '画像', '核查', '差异', '情况')

# Schema-driven 语义映射速查（帮助大模型把业务术语映射到真实字段）
SEMANTIC_FIELD_HINTS = """
【字段语义速查】
- 客户/用电客户主表：dim_cst_elec_cons_cust；对外客户编号：cust_no；内部主键：cust_id（仅在连接时使用）
- 客户分类（高压、低压非居民、居民等）：dim_cst_elec_cons_cust.cust_cls_desc
- 客户名称：dim_cst_elec_cons_cust.cust_name；用电类别：dim_cst_elec_cons_cust.ec_categ_desc；电压等级：dim_cst_elec_cons_cust.cust_volt_desc
- 供电单位：dim_cst_mgt_org，单位代码 mgt_org_code，单位名称 mgt_org_name
- 计量点/安装点（按业务安装点统计）：dim_cst_inst_elec_cons，标识 inst_id，通过 cust_id 与用电客户关联
- 计量点运行（设备运行）：dwd_cst_meter_run，资产编号 meter_asset_no，通过 inst_id 与安装点关联
- 日电量：默认只用专变 dwd_cst_meter_energy_day_h_xz（用户级聚合/极值、计量点TOP-N 均如此），电量 pap_e，日期 data_date，经 meter_asset_no 与计量点关联；低压 dwd_cst_es_meter_energy_day_l_xz 仅明确"低压"口径或单计量点日电量明细时与专变 UNION ALL；公变 dwd_cst_es_meter_energy_day_p 仅全社会总量/趋势口径与专变 UNION ALL，不与用户级查询混合
- 设备/计量装置：dim_cst_dev，设备状态 dev_stat_desc，设备分类 dev_cls_desc，通过 dev_id 与 dwd_cst_meter_run.meter_id 关联
- 应收电费：dwd_cst_rcvbl_acct，统计时必须同时输出 SUM(rcvbl_amt) AS total_rcvbl、SUM(rcvd_amt) AS total_rcvd、SUM(arer_bal) AS total_arer；年月 rcvbl_ym
- 实收电费：dwd_cst_rcvd_acct，金额 rcvd_amt
"""

# ===== 业务专用规则（不常驻 Prompt，按问题关键词条件注入，见 _build_v3_prompt）=====
# 用电量口径（触发词见 _ELEC_TRIGGER_KW）：血泪规则——关联口径与 UNION ALL 全体用户
_ELEC_RULES = """【用电量查询口径——必须严格遵守】
- 默认只用专变表 dwd_cst_meter_energy_day_h_xz：客户/用户级用电量聚合与取极值（用电量TOP-N客户、各供电单位用电量总额、高压客户用电量、单客户最大单日用电量等）、计量点用电量TOP-N，一律只查该表，禁止 UNION ALL 低压表或公变表。
- 日电量表只能经 meter_asset_no 关联（通常通过 dwd_cst_meter_run 桥接），禁止将 cust_id、inst_id 直接 JOIN 到日电量表。
- 低压表 dwd_cst_es_meter_energy_day_l_xz 仅在问题明确要求"低压"口径、或查询单个计量点的日电量明细（计量点可能属低压）时，才与专变表 UNION ALL 使用。
- 仅全社会总量/趋势类问题（如"某日用电量总和""各天总用电量趋势"）：UNION ALL 合并专变表与公变表 dwd_cst_es_meter_energy_day_p 后再聚合；公变表仅限此口径，不与用户级查询混合。
- 日电量三表口径：低压=dwd_cst_es_meter_energy_day_l_xz、专变/高压=dwd_cst_meter_energy_day_h_xz、公变/台区=dwd_cst_es_meter_energy_day_p；电量字段均为 pap_e。
- 查询用电量 TOP-N 的计量点时，SELECT 必须同时包含 meter_asset_no、inst_name、SUM(pap_e) 和 AVG(pap_e)。"""
_ELEC_TRIGGER_KW = ('电量', '用电', 'pap_e')

# 供电单位/行政区划口径（触发词见 _ORG_TRIGGER_KW）
_ORG_RULES = """【供电单位口径——必须严格遵守】
- WHERE 使用具体 mgt_org_code 时，SELECT 同时输出 mgt_org_code 和 mgt_org_name 并按两者 GROUP BY。
- 按行政区划地名（如"杭州""拱墅"）筛选供电单位时，必须用 dim_cst_mgt_org.city_name（地市）/county_name（区县）/station_name（供电所）匹配，禁止用 mgt_org_name LIKE 上级地名——下属单位的 mgt_org_name 不含上级地名，会漏掉全部下属单位。"""
_ORG_TRIGGER_KW = ('供电', '管理单位', '地市', '区县', 'mgt_org',
                   '杭州', '宁波', '温州', '绍兴', '金华', '台州', '嘉兴', '湖州', '衢州', '丽水', '舟山')


class SQLGenerator:
    """基于 RAG + Few-shot + LLM 的 SQL 生成器（v2.4 安全版 / v3.0 意图版）"""
    
    def __init__(self, workflow=None):
        # P3：声明式工作流配置。None -> 环境变量 SQL_WORKFLOW -> 'default' 预设（= 当前生产行为）；
        # 各阶段开关/配额在消费点读 self._wf，代码常量保留为 DEFAULTS 来源/兜底（见 workflow/engine.py）
        from modules.training.workflow.engine import resolve_workflow
        self._wf = resolve_workflow(workflow)
        self.schema_loader = SchemaLoader()
        self.rag_retriever = RAGRetriever(top_k=self._wf['rag']['top_k'])
        self.db = DatabaseManager()
        self.api_url = config.KIMI_API_URL
        self.api_key = config.KIMI_API_KEY
        self.model = config.KIMI_MODEL
        self._thinking = self._wf['generate']['thinking']  # thinking 模式开关（温度约束见 config 注释）
        self._static_prompt_head = None  # 静态提示词头部缓存：初始化后构建一次，不随每个问题重复组装

        # 知识源（knowledge.source）：ontology → 本体服务（marketing_ontology 已生效版本）；
        # 本体未就绪/异常时自动回退 legacy（SchemaPreloader/治理库直读），问数永不因本体缺失中断
        self._onto = None
        if self._wf.get('knowledge', {}).get('source', 'legacy') == 'ontology':
            try:
                from core.ontology.service import OntologyService
                svc = OntologyService.get_instance()
                if svc.available():
                    self._onto = svc
                else:
                    print('[SQLGen] knowledge.source=ontology 但本体未就绪，回退 legacy', flush=True)
            except Exception as e:
                print(f'[WARN] 本体服务不可用，回退 legacy: {e}', flush=True)
        
        # v3.0 新增（concept_map 经知识源解析注入：ontology 档为本体概念面，否则 None=治理库/常量）
        self.use_intent = getattr(config, 'USE_INTENT_GENERATION', True) and _INTENT_AVAILABLE
        if self.use_intent:
            self.intent_parser = IntentParser(concept_map=self._concept_map('intent'))
            self.sql_builder = SQLBuilder()
            self.template_matcher = SQLTemplateMatcher(concept_map=self._concept_map('intent'))

        # LLM token 用量累加器（benchmark 效率统计用，纯附加；每次 generate 重置）
        self._usage_lock = threading.Lock()
        self._usage_acc = self._empty_usage()

    @staticmethod
    def _empty_usage() -> Dict:
        return {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0, 'llm_calls': 0}

    def _accumulate_usage(self, usage: Optional[Dict]):
        """累加一次 LLM HTTP 响应的 token 用量（无 usage 字段时仅计调用数）"""
        with self._usage_lock:
            self._usage_acc['llm_calls'] += 1
            usage = usage or {}
            self._usage_acc['prompt_tokens'] += int(usage.get('prompt_tokens') or 0)
            self._usage_acc['completion_tokens'] += int(usage.get('completion_tokens') or 0)
            self._usage_acc['total_tokens'] += int(usage.get('total_tokens') or 0)

    def _snapshot_usage(self) -> Dict:
        with self._usage_lock:
            return dict(self._usage_acc)

    # ==================== 知识源解析（knowledge.source：ontology / legacy） ====================

    def _schema_src(self):
        """Schema 知识源（表名/注释/列/关系）：ontology 档走本体服务，否则 SchemaPreloader。"""
        if self._onto is not None:
            return self._onto
        from core.schema_preloader import SchemaPreloader
        return SchemaPreloader.get_instance()

    def _concept_map(self, scope: str) -> dict:
        """概念→表映射：ontology 档走本体概念面，否则 keyword_table_map provider。"""
        if self._onto is not None:
            try:
                m = self._onto.get_concept_table_map(scope)
                if m:
                    return m
            except Exception as e:
                print(f'[WARN] 本体概念映射读取失败，回退 keyword_table_map: {e}', flush=True)
        return get_keyword_table_map(scope)

    def _family_synonyms(self) -> dict:
        """同族表消歧同义词：ontology 档走本体同义词组，否则 business_rule provider。"""
        if self._onto is not None:
            try:
                s = self._onto.get_family_synonyms()
                if s:
                    return s
            except Exception as e:
                print(f'[WARN] 本体同义词读取失败，回退 business_rule: {e}', flush=True)
        return get_family_synonyms()

    def _retrieve_code_values(self, question: str, tables: List[str],
                              per_domain: int = 8, max_domains: int = 8) -> List[Dict]:
        """码值检索：ontology 档走本体枚举快照索引，否则 RAG 治理库索引。"""
        if self._onto is not None:
            try:
                return self._onto.retrieve_code_values(question, tables, per_domain, max_domains)
            except Exception as e:
                print(f'[WARN] 本体码值检索失败，回退 RAG: {e}', flush=True)
        return self.rag_retriever.retrieve_code_values(question, tables, per_domain, max_domains)

    def _code_value_translations(self):
        """码值 名称→编码 翻译表：ontology 档走本体枚举快照，否则 RAG。"""
        if self._onto is not None:
            try:
                return self._onto.get_code_value_translations()
            except Exception as e:
                print(f'[WARN] 本体码值翻译表失败，回退 RAG: {e}', flush=True)
        return self.rag_retriever.get_code_value_translations()

    @staticmethod
    def _clean_aliases(sql: str) -> str:
        """别名纪律（确定性后处理）：核心词组、≤8 个汉字、禁标点。
        LLM 常把列注释整段搬进别名（'AS 地区名称（全国/省/城市）'、'AS 行政区划代码（GB/T 2260）'，
        全角括号/空格/斜杠在 MySQL 裸标识符中非法或截断残留 → 1064）。
        两步：① AS 核心词（全角注释段） → 只留核心词；② 含中文且 >8 字的别名截到 8 字。"""
        if not sql:
            return sql
        sql = re.sub(r'(?i)\bAS\s+([^\s,（）()]+)\s*（[^）]*）', r'AS \1', sql)
        sql = re.sub(r'(?i)\bAS\s+([^\s,（）()]+)',
                     lambda m: 'AS ' + m.group(1)[:8]
                     if (len(m.group(1)) > 8 and re.search(r'[一-鿿]', m.group(1))) else m.group(0),
                     sql)
        return sql

    def generate(
        self,
        user_question: str,
        retrieved_pairs: Optional[List[Dict]] = None,
        review_feedback: Optional[str] = None,
        error_records: Optional[List[Dict]] = None,
        progress_cb=None,
        thinking_cb=None
    ) -> Dict:
        """公有入口：包装 _generate_impl，在返回 dict 中附加本次 LLM token 用量（usage 字段）"""
        with self._usage_lock:
            self._usage_acc = self._empty_usage()
        result = self._generate_impl(
            user_question=user_question,
            retrieved_pairs=retrieved_pairs,
            review_feedback=review_feedback,
            error_records=error_records,
            progress_cb=progress_cb,
            thinking_cb=thinking_cb
        )
        if isinstance(result, dict):
            if result.get('sql'):
                result['sql'] = self._clean_aliases(result['sql'])  # 别名纪律最终收口
            result['usage'] = self._snapshot_usage()
            result.setdefault('workflow', self._wf.get('_name', 'custom'))  # P3：报告追溯用
        return result

    def _generate_impl(
        self,
        user_question: str,
        retrieved_pairs: Optional[List[Dict]] = None,
        review_feedback: Optional[str] = None,
        error_records: Optional[List[Dict]] = None,
        progress_cb=None,
        thinking_cb=None
    ) -> Dict:
        """生成 SQL（v3.0：优先使用意图解析 + 结构化构建，失败则回退到 LLM）

        progress_cb(stage, elapsed_ms)：阶段进度回调（SSE 实时进度用），
        stage 取值：rag / schema / llm / validate。
        thinking_cb(evt_dict)：思考过程回调（SSE 思考窗口用），
        evt_dict 至少含 kind 字段（intent/tables/columns/code_values/llm/repair/template/draft）。
        """
        
        # ===== v3.0 意图驱动生成 =====
        if self.use_intent:
            try:
                intent_result = self._generate_with_intent(
                    user_question=user_question,
                    review_feedback=review_feedback,
                    progress_cb=progress_cb,
                    thinking_cb=thinking_cb
                )
                if intent_result.get('success'):
                    return intent_result
            except Exception as e:
                print(f"[WARN] 意图生成失败，回退到 LLM: {e}")
        
        # ===== v2.4 LLM 生成（回退） =====
        # P3：v24 回退通道可由工作流关闭（fallback.v24=false 时意图失败即返回失败）
        if not self._wf['fallback']['v24']:
            return {
                'sql': '',
                'explanation': '',
                'tables_involved': [],
                'success': False,
                'error': '意图生成未成功，且 v2.4 回退通道已在工作流配置中禁用'
            }
        if retrieved_pairs is None:
            retrieved_pairs = self.rag_retriever.retrieve(user_question)
        if error_records is None:
            error_records = self.rag_retriever.retrieve_error_records(user_question)
        
        # 优先复用 v3.0 已定位的表（比关键词猜的 4 张表准得多）；定位结果与问题绑定，防串题
        last_located = getattr(self, '_last_located', None)
        if last_located and last_located[0] == user_question and last_located[1]:
            related_tables = last_located[1]
        else:
            related_tables = self._identify_related_tables(user_question, retrieved_pairs)
        schema_context = self.schema_loader.build_schema_context(related_tables)
        qa_context = self._build_qa_context(retrieved_pairs)
        error_context = self._build_error_context(error_records)
        
        prompt = self._build_prompt(
            user_question=user_question,
            schema_context=schema_context,
            qa_context=qa_context,
            related_tables=related_tables,
            error_context=error_context,
            review_feedback=review_feedback
        )
        
        # 尝试调用 LLM
        raw_content = ""
        llm_success = False
        try:
            result = self._call_llm(prompt, user_question=user_question,
                                    max_tokens=self._wf['generate']['max_tokens'])
            raw_content = result['content']
            llm_success = True
        except Exception as e:
            # API 调用失败时，fallback 兜底
            fallback_result = self._fallback_to_qa_pair(user_question, retrieved_pairs)
            if fallback_result:
                return fallback_result
            return {
                'sql': '',
                'explanation': '',
                'tables_involved': related_tables,
                'success': False,
                'error': f'API 调用失败: {str(e)}'
            }
        
        sql = self._extract_sql(raw_content)
        explanation = self._extract_explanation(raw_content)
        
        tables_in_sql = self._extract_tables_from_sql(sql)
        valid_tables = set(self.schema_loader.get_table_names())
        has_invalid_table = any(t not in valid_tables for t in tables_in_sql)
        
        # 如果 LLM 编造了表名，自动重试一次，明确告诉可用表名
        if has_invalid_table:
            invalid = [t for t in tables_in_sql if t not in valid_tables]
            retry_prompt = prompt + f"""

【错误修正】你上次生成的 SQL 使用了不存在的表名: {invalid}。
以下是你唯一可以使用的表名（禁止编造任何其他表名）：
{', '.join(related_tables)}

请重新生成，严格使用上面列出的表名。"""
            try:
                result = self._call_llm(retry_prompt, user_question=user_question,
                                        max_tokens=self._wf['generate']['max_tokens'])
                raw_content = result['content']
                sql = self._extract_sql(raw_content)
                explanation = self._extract_explanation(raw_content)
                tables_in_sql = self._extract_tables_from_sql(sql)
                has_invalid_table = any(t not in valid_tables for t in tables_in_sql)
            except Exception as e:
                pass  # 重试失败，保留第一次的结果
        
        allowed_prefixes = ['SELECT', 'WITH']
        # 兜底路径同样要过执行校验：编造列/错误的 JOIN/哨兵 SQL 一律不得作为成功结果返回
        if sql and any(sql.strip().upper().startswith(p) for p in allowed_prefixes) \
                and not has_invalid_table and not self._is_sentinel_sql(sql) and self._validate_sql(sql):
            return {
                'sql': sql,
                'explanation': explanation,
                'tables_involved': related_tables,
                'success': True,
                'raw_response': raw_content,
                'prompt': prompt,
                'error_records_matched': len(error_records) if error_records else 0
            }
        
        # LLM 生成了内容但格式不对、包含无效表名、哨兵 SQL 或执行校验失败
        if not sql:
            error_msg = 'LLM 返回内容为空或只包含注释，未生成有效 SQL'
        elif has_invalid_table:
            invalid = [t for t in tables_in_sql if t not in valid_tables]
            error_msg = f'LLM 编造了不存在的表名: {invalid}。必须使用 schema 中提供的实际表名'
        elif self._is_sentinel_sql(sql):
            error_msg = 'LLM 声明无法回答（返回了哨兵 SQL），未生成真实查询'
        else:
            # 默认错误信息不能误导为"不是 SELECT 开头"——抓取真实执行错误
            error_msg = 'LLM 生成的 SQL 未通过校验'
            try:
                with self.db.connect_business() as conn:
                    test_sql = re.sub(r'\s+LIMIT\s+\d+\s*$', '', sql.rstrip(';').strip(), flags=re.IGNORECASE) + ' LIMIT 1'
                    conn.execute(test_sql)
                error_msg = 'LLM 生成的 SQL 前缀或语法校验失败'
            except Exception as exec_err:
                error_msg = f'SQL 执行校验失败: {exec_err}'
        return {
            'sql': sql or '',
            'explanation': explanation or 'LLM 输出格式异常',
            'tables_involved': related_tables,
            'success': False,
            'error': error_msg,
            'raw_response': raw_content,
            'prompt': prompt
        }
    
    def _generate_with_intent(
        self,
        user_question: str,
        review_feedback: Optional[str] = None,
        progress_cb=None,
        thinking_cb=None
    ) -> Dict:
        """v3.0 意图驱动生成：意图解析 + 多路 RAG + LLM 生成"""
        t_start = time.perf_counter()
        timers = {}

        def _emit(stage, ms):
            if progress_cb:
                try:
                    progress_cb(stage, ms)
                except Exception:
                    pass

        def _think(evt: Dict):
            """思考事件回调（前端思考窗口）：回调异常不影响生成主流程"""
            if thinking_cb:
                try:
                    thinking_cb(evt)
                except Exception:
                    pass

        # 1. RAG 检索 / 规则意图解析 / LLM 定位表 三路并行（RAG 与定位均为 I/O 等待，可重叠）
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

        def _timed(label, fn):
            def _run():
                t_s = time.perf_counter()
                r = fn()
                timers[label] = (time.perf_counter() - t_s) * 1000
                return r
            return _run

        ex = ThreadPoolExecutor(max_workers=3)
        fut_rag = ex.submit(_timed('rag_retrieve', lambda: self.rag_retriever.retrieve_all(user_question)))
        fut_intent = ex.submit(_timed('intent_parse', lambda: self.intent_parser.parse(user_question, use_llm=False)))

        def _locate():
            # 等意图解析出回退表，并叠加 RAG 语义检索的表（定位 LLM 失败时的第二语义通道）
            rule_tables = []
            try:
                rule_tables = fut_intent.result().get('tables', []) or []
            except Exception:
                pass
            rag_tables = []
            try:
                rag_tables = [t['table_name'] for t in fut_rag.result().get('schema', {}).get('tables', [])[:4]]
            except Exception:
                pass
            fallback = list(dict.fromkeys(rule_tables + rag_tables))
            # LLM 定位通道总开关（wf.locate.enabled）：关闭时完全不发定位调用，
            # 表来源=规则+RAG+草稿（合并打分天然兼容空 LLM 通道，Track B 验证准确率无损）
            if not self._wf['locate']['enabled']:
                print(f"[SQLGen] LLM 定位通道已关闭，使用规则+RAG 通道: {fallback}", flush=True)
                return [], fallback
            # trackB-r3d: 规则与 RAG 双通道在头部表已一致、问题简短且非分析型时，跳过 LLM 定位
            # （省 1 次调用及其推理 token/延迟）；合并打分对空 LLM 结果天然兼容，语义回退不变
            _analyt_kw = get_analytical_keywords() or _DEFAULT_ANALYTICAL_KW
            if (rule_tables and rag_tables and set(rule_tables[:2]) & set(rag_tables)
                    and len(user_question) <= 30 and not any(k in user_question for k in _analyt_kw)):
                print(f"[SQLGen] 定位表: 规则×RAG 头部一致，跳过 LLM 定位: {fallback}", flush=True)
                return [], fallback
            llm_tables = self._locate_tables_with_llm(user_question, fallback)
            return llm_tables, fallback
        fut_locate = ex.submit(_timed('table_locate', _locate))

        rag_result = fut_rag.result()
        intent = fut_intent.result()
        retrieved_pairs = rag_result['qa_pairs']
        error_records = rag_result['error_records']
        schema_docs = rag_result['schema']
        patterns = rag_result['patterns']
        _emit('rag', timers.get('rag_retrieve', 0))

        # 如果规则解析没识别到表，从 RAG 结果补充
        if not intent.get('tables'):
            intent['tables'] = [t['table_name'] for t in schema_docs.get('tables', [])[:6]]

        # 规则通道易把同族表全部带入（如 功率/电压/电流 三张曲线表），先按问题关键词消歧再构建草稿，
        # 否则草稿会把误带表 JOIN 进去，污染后续打分合并
        intent['tables'] = self._disambiguate_sibling_tables(intent.get('tables', []), user_question)

        _think({
            'kind': 'intent',
            'question_type': intent.get('question_type', '查询'),
            'tables': intent.get('tables', []),
            'fields': [{'name': f.get('name', ''), 'role': f.get('role', ''),
                        'agg': f.get('agg', '')} for f in intent.get('fields', [])],
            'filters': [{'field': f.get('field', ''), 'op': f.get('op', ''),
                         'value': f.get('value', '')} for f in intent.get('filters', [])]
        })

        # 2. 生成草稿 SQL（用于给 LLM 参考）
        t0 = time.perf_counter()
        draft_result = self.sql_builder.build(intent, schema_docs)
        draft_sql = draft_result.get('sql', '') if draft_result.get('success') else ''
        draft_valid = self._validate_sql(draft_sql) if draft_sql else False
        timers['draft_build'] = (time.perf_counter() - t0) * 1000

        # 如果草稿可执行且意图相对简单，直接返回草稿，避免 LLM 引入错误
        has_explicit_fields = bool(intent.get('fields'))
        has_aggregate = any(f.get('role') == 'aggregate' for f in intent.get('fields', []))
        is_simple_single_table = len(draft_result.get('tables_involved', [])) == 1 and not has_aggregate
        # 复杂/分析型问题不走草稿直出：规则解析只能抓住表面条件，直出会丢弃真实分析意图
        analytical_kw = get_analytical_keywords() or _DEFAULT_ANALYTICAL_KW
        is_complex_question = len(user_question) > 30 or any(k in user_question for k in analytical_kw)
        # P3：草稿直出总开关 wf.draft_direct.enabled；两个守门可在 wf.draft_direct.guards 分别开关
        _dd = self._wf['draft_direct']
        use_draft_direct = _dd['enabled'] and draft_valid and not review_feedback and not has_aggregate \
            and (has_explicit_fields or is_simple_single_table) and not is_complex_question

        # 草稿直出守门：问题含过滤信号词（状态/类别/地名等）但规则解析未抓到任何过滤条件时，
        # 草稿大概率漏 WHERE 或选错表，放弃直出走 LLM 全流程（直出绕过 LLM 无人纠错）
        if use_draft_direct and 'filter_signal' in _dd['guards'] and not intent.get('filters') \
                and any(s in user_question for s in (get_draft_filter_signals() or self._DRAFT_FILTER_SIGNALS)):
            print(f"[SQLGen] 草稿直出被守门拦截（过滤信号未捕获）: {user_question[:20]}", flush=True)
            use_draft_direct = False

        # 守门2：裸 SELECT * 草稿禁止直出——规则通道只猜到表、没有任何字段/条件证据时，
        # SELECT * 与 gold 的显式列投影必然不匹配（全量定型中此类草稿均分 0.015）
        if use_draft_direct and 'bare_select_star' in _dd['guards'] \
                and re.search(r'(?i)\bSELECT\s+(?:\w+\.)?\*\s+FROM', draft_sql or ''):
            print(f"[SQLGen] 草稿直出被守门拦截（裸 SELECT *）: {user_question[:20]}", flush=True)
            use_draft_direct = False

        if use_draft_direct:
            # 草稿直出：定位调用结果不再需要，丢弃线程、立即返回
            ex.shutdown(wait=False, cancel_futures=True)
            _think({'kind': 'draft', 'mode': 'direct', 'sql_len': len(draft_sql or ''),
                    'ms': int(timers.get('draft_build', 0))})
            _emit('schema', timers.get('intent_parse', 0) + timers.get('draft_build', 0))
            _emit('llm', 0)
            _emit('validate', 0)
            timers['total'] = (time.perf_counter() - t_start) * 1000
            print(f"[SQLGen timing] {timers}, prompt_len=0", flush=True)
            return {
                'sql': draft_sql,
                'explanation': f"基于意图解析直接生成的 {intent.get('question_type')} SQL",
                'tables_involved': draft_result.get('tables_involved', []),
                'success': True,
                'generation_mode': 'intent_draft_direct',
                'intent': intent,
                'timers': timers
            }

        # 模板前置（workflow 可选阶段，默认关）：回放校验过的签名模板优先于 LLM 主生成——
        # 命中即免 30-80s 主调用且结果与金标语料同构。匹配器自带分析型/长问题守卫、
        # 槽位填充与执行校验（_probe_ok），这里再做一次方言级校验双保险
        if self._wf.get('template_first', {}).get('enabled') and not review_feedback:
            tpl_result = self.template_matcher.match(user_question)
            tpl_sql = (tpl_result or {}).get('sql', '')
            if tpl_sql and self._validate_sql(tpl_sql):
                ex.shutdown(wait=False, cancel_futures=True)
                _think({'kind': 'template', 'name': tpl_result.get('template_name', ''),
                        'via': 'template_first'})
                _emit('schema', timers.get('intent_parse', 0) + timers.get('draft_build', 0))
                _emit('llm', 0)
                _emit('validate', 0)
                timers['total'] = (time.perf_counter() - t_start) * 1000
                print(f"[SQLGen timing] {timers}, template_first hit: {tpl_result.get('template_name')}", flush=True)
                return {
                    'sql': tpl_sql,
                    'explanation': f"签名模板前置命中：{tpl_result.get('template_name')}",
                    'tables_involved': self._extract_tables_from_sql(tpl_sql),
                    'success': True,
                    'generation_mode': 'template_first',
                    'intent': intent,
                    'timers': timers
                }

        # 3. 取定位结果，打分合并（草稿 > LLM > 规则）+ 同族消歧；检索其全量字段与相关码值；再构建 Prompt（静态头部一次构建，每题只组装动态部分）
        # 定位等待总时长硬顶：超时/异常则放弃 LLM 定位通道，由规则+RAG+草稿通道合并继续，
        # 不让一个慢推理调用阻塞整题（定位线程后台自行收尾，HTTP 层另有 LLM_TIMEOUT_LOCATE 兜底）
        try:
            llm_tables, rule_tables = fut_locate.result(timeout=self._wf['locate']['wait_max'])
            ex.shutdown(wait=True)
        except FuturesTimeoutError:
            llm_tables = []
            rule_tables = list(dict.fromkeys(
                (intent.get('tables') or [])
                + [t['table_name'] for t in schema_docs.get('tables', [])[:4]]))
            print(f"[SQLGen] 定位等待超 {self._wf['locate']['wait_max']}s，降级为规则+RAG 通道: {rule_tables}", flush=True)
            ex.shutdown(wait=False, cancel_futures=True)
        except Exception as e:
            llm_tables = []
            rule_tables = list(dict.fromkeys(
                (intent.get('tables') or [])
                + [t['table_name'] for t in schema_docs.get('tables', [])[:4]]))
            print(f"[SQLGen] 定位线程异常（{e}），降级为规则+RAG 通道: {rule_tables}", flush=True)
            ex.shutdown(wait=False, cancel_futures=True)
        draft_tables = draft_result.get('tables_involved', []) if draft_result else []
        located_tables = self._merge_located_tables(user_question, intent, llm_tables, rule_tables, draft_tables)
        _think({'kind': 'tables', 'tables': located_tables,
                'sources': {'rule': rule_tables or [], 'llm': llm_tables or [], 'draft': draft_tables or []}})
        columns_context = self._build_columns_context(located_tables, user_question)
        _think({'kind': 'columns', 'summary': getattr(self, '_last_columns_summary', [])})
        # 最短业务 JOIN 路径（图谱 BFS 能力复用，禁 mgt_org 中转）；独立成段，便于超预算时单独裁撤
        join_path_hint = self._build_join_path_hint(located_tables)
        code_value_context = self._build_code_value_context(user_question, located_tables)
        _think({'kind': 'code_values', 'items': getattr(self, '_last_code_value_hits', [])})

        t0 = time.perf_counter()
        pattern_context = self._build_pattern_context(user_question, located_tables)
        qa_context = self._build_qa_context(retrieved_pairs)
        error_context = self._build_error_context(error_records)
        org_hints = self._build_org_hints(user_question)

        prompt = self._build_v3_prompt(
            user_question=user_question,
            intent=intent,
            pattern_context=pattern_context,
            qa_context=qa_context,
            columns_context=columns_context,
            code_value_context=code_value_context,
            error_context=error_context,
            draft_sql=draft_sql,
            review_feedback=review_feedback,
            org_hints=org_hints,
            join_hint=join_path_hint
        )
        timers['prompt_build'] = (time.perf_counter() - t0) * 1000
        _emit('schema', timers.get('intent_parse', 0) + timers.get('draft_build', 0)
              + timers.get('table_locate', 0) + timers.get('prompt_build', 0))

        # 5. 调用 LLM 生成 SQL（单次完整生成；未过校验走下方快速修复通道，不再整轮重试）
        valid_tables = set(self.schema_loader.get_table_names())
        allowed_prefixes = ['SELECT', 'WITH']

        last_error = None
        sql = ''          # 初始化，避免首次调用即异常时下方兜底引用未绑定变量
        raw_content = ''
        try:
            from core.llm_config import current as _llm_current
            _llm_model = _llm_current().get('model', '')
        except Exception:
            _llm_model = ''
        _think({'kind': 'llm', 'phase': 'assemble', 'prompt_chars': len(prompt), 'model': _llm_model})
        # LLM 思考流式输出（仅 SSE 思考窗口场景；config.LLM_STREAM_THINKING 可关）
        _stream_cb = None
        if thinking_cb is not None and getattr(config, 'LLM_STREAM_THINKING', True):
            def _stream_cb(phase, text):
                _think({'kind': 'llm_stream', 'phase': phase, 'text': text})
        for attempt in range(1):
            try:
                call_prompt = prompt
                t0 = time.perf_counter()
                result = self._call_llm(call_prompt, user_question=user_question,
                                        max_tokens=self._wf['generate']['max_tokens'],
                                        stream_cb=_stream_cb,
                                        effort=getattr(config, 'GEN_DRAFT_EFFORT', 'none'))
                timers['llm_call'] = (time.perf_counter() - t0) * 1000
                _emit('llm', timers['llm_call'])
                raw_content = result['content']
            except Exception as e:
                last_error = f'LLM 调用异常: {e}'  # 记录真实原因（超时等），供最终错误信息使用
                _think({'kind': 'llm', 'phase': 'error', 'error': str(e)[:300]})
                break

            t0 = time.perf_counter()
            sql = self._extract_sql(raw_content)
            sql = self._normalize_sql_dialect(sql)
            sql = self._translate_code_value_literals(sql)
            explanation = self._extract_explanation(raw_content)
            tables_in_sql = self._extract_tables_from_sql(sql)
            has_invalid_table = any(t not in valid_tables for t in tables_in_sql)
            is_sentinel = self._is_sentinel_sql(sql)
            llm_sql_valid = (self._validate_sql(sql) if sql else False) and not is_sentinel
            timers['validation'] = (time.perf_counter() - t0) * 1000
            _emit('validate', timers['validation'])
            _think({'kind': 'llm', 'phase': 'done', 'ms': int(timers['llm_call']),
                    'sql_len': len(sql or '')})

            # 结构门槛（前缀/非法表/哨兵）；可执行性校验在审计开启时交给审计阶段（报错上下文是其输入）
            structural_ok = (sql and any(sql.strip().upper().startswith(p) for p in allowed_prefixes)
                             and not has_invalid_table and not is_sentinel)
            audit_on = getattr(config, 'GEN_SQL_AUDIT', True)
            if structural_ok and (llm_sql_valid or audit_on):
                # ===== 两段式工作流（2026-08-15）：none 草稿 → 执行 → low 档合理性审计 =====
                gen_mode = 'schema_llm_v3'
                if audit_on:
                    sql, audit_err, audit_note = self._audit_stage(
                        user_question, sql, columns_context, join_path_hint, _think, timers)
                    if audit_err is not None:
                        # 草稿及审计修正均不可执行：转入修复通道
                        candidate = sql
                        last_error = f'SQL 执行失败: {audit_err}'
                        break
                    if audit_note:
                        gen_mode = 'schema_llm_v3_audited'
                        tables_in_sql = self._extract_tables_from_sql(sql)
                timers['total'] = (time.perf_counter() - t_start) * 1000
                print(f"[SQLGen timing] {timers}, prompt_len={len(prompt)}", flush=True)
                return {
                    'sql': sql,
                    'explanation': explanation or f"基于 Schema 语义 + LLM 生成：{intent.get('question_type', '查询')}",
                    'tables_involved': tables_in_sql,
                    'located_tables': located_tables,
                    'code_value_hits': getattr(self, '_last_code_value_hits', []),
                    'success': True,
                    'raw_response': raw_content,
                    'prompt': call_prompt,
                    'intent': intent,
                    'error_records_matched': len(error_records) if error_records else 0,
                    'generation_mode': gen_mode,
                    'timers': timers
                }
            
            # 记录错误用于下一轮重试
            if has_invalid_table:
                invalid = [t for t in tables_in_sql if t not in valid_tables]
                last_error = f"使用了不存在的表名: {invalid}"
            elif is_sentinel:
                last_error = "模型声明无法回答（返回了哨兵 SQL）。请基于给定 Schema 直接生成真实查询，不要声明无法回答"
            elif not sql:
                last_error = "没有生成有效的 SELECT 语句"
            else:
                # 尝试执行看具体错误
                try:
                    with self.db.connect_business() as conn:
                        test_sql = re.sub(r'\s+LIMIT\s+\d+\s*$', '', sql.rstrip(';').strip(), flags=re.IGNORECASE) + ' LIMIT 1'
                        conn.execute(test_sql)
                except Exception as e:
                    last_error = f"SQL 执行失败: {str(e)}"
        
        # ===== 快速修复通道：完整生成未过校验、但已有接近正确的候选 SQL 时，
        # 只把失败 SQL + 数据库报错 + 问题回喂 LLM 定点修复（小 prompt 快速模式），
        # 避免带着 schema 整轮重新生成 =====
        candidate = sql
        # R3：修复通道附带"定位表匹配的模式骨架"作为结构参照（统一知识检索层取骨架；检索失败/无匹配时为空串，行为同前）
        repair_pattern_hint = self._build_pattern_context(user_question, located_tables)
        # P3：修复通道开关/轮数由 wf.repair 控制（enabled=false 或 max_rounds=0 时整段跳过）
        for repair_round in range(self._wf['repair']['max_rounds'] if self._wf['repair']['enabled'] else 0):
            if not candidate or self._is_sentinel_sql(candidate):
                break
            exec_err = self._probe_sql_error(candidate)
            if not exec_err:
                break  # 防御：理论上循环结束仍不可执行；已可执行则无需修
            _think({'kind': 'repair', 'attempt': repair_round + 1, 'reason': str(exec_err)[:300]})
            t0 = time.perf_counter()
            fixed = self._repair_sql_with_llm(user_question, candidate, exec_err,
                                              columns_context, join_path_hint, repair_pattern_hint)
            timers[f'repair_{repair_round + 1}'] = (time.perf_counter() - t0) * 1000
            if not fixed:
                break
            if self._validate_sql(fixed):
                gen_mode = 'schema_llm_v3_repair'
                if getattr(config, 'GEN_SQL_AUDIT', True):
                    # 修复产物同样过 low 档审计（两段式覆盖修复通道）
                    fixed2, audit_err, audit_note = self._audit_stage(
                        user_question, fixed, columns_context, join_path_hint, _think, timers)
                    if audit_err is not None:
                        candidate = fixed2
                        continue  # 审计修正仍不可执行：以最新版为基继续修复循环
                    fixed = fixed2
                    if audit_note:
                        gen_mode = 'schema_llm_v3_repair_audited'
                timers['total'] = (time.perf_counter() - t_start) * 1000
                print(f"[SQLGen timing] {timers}, prompt_len={len(prompt)}", flush=True)
                return {
                    'sql': fixed,
                    'explanation': '基于执行报错定点修复的 SQL',
                    'tables_involved': self._extract_tables_from_sql(fixed),
                    'located_tables': located_tables,
                    'code_value_hits': getattr(self, '_last_code_value_hits', []),
                    'success': True,
                    'raw_response': raw_content,
                    'prompt': prompt,
                    'intent': intent,
                    'error_records_matched': len(error_records) if error_records else 0,
                    'generation_mode': gen_mode,
                    'timers': timers
                }
            candidate = fixed  # 以最新版为基继续修（报错已更新）

        # LLM 失败或未通过校验：尝试模板兜底（复杂/分析型问题不用模板——正则易误配到无关模板）
        # P3：模板兜底可由工作流关闭（fallback.template=false）
        if self._wf['fallback']['template'] and not is_complex_question:
            template_result = self.template_matcher.match(user_question)
        else:
            template_result = None
        if template_result and template_result.get('sql'):
            template_sql = self._normalize_sql_dialect(template_result['sql'])
            if self._validate_sql(template_sql):
                _think({'kind': 'template', 'name': template_result['template_name'],
                        'via': 'template_fallback'})
                timers['total'] = (time.perf_counter() - t_start) * 1000
                print(f"[SQLGen timing] {timers}, prompt_len={len(prompt)}", flush=True)
                return {
                    'sql': template_sql,
                    'explanation': f"LLM 未通过校验，使用业务模板兜底：{template_result['template_name']}",
                    'tables_involved': self._extract_tables_from_sql(template_sql),
                    'success': True,
                    'generation_mode': 'template_fallback',
                    'template_name': template_result['template_name'],
                    'timers': timers
                }

        # LLM 结果未通过校验但草稿可用，返回草稿兜底。
        # 复杂/分析型问题不兜底：草稿只有表面条件，返回它等于返回错误答案，宁可报错。
        # P3：草稿兜底可由工作流关闭（fallback.draft=false）
        if self._wf['fallback']['draft'] and draft_valid and not is_complex_question:
            _think({'kind': 'draft', 'mode': 'fallback', 'sql_len': len(draft_sql or '')})
            timers['total'] = (time.perf_counter() - t_start) * 1000
            print(f"[SQLGen timing] {timers}, prompt_len={len(prompt)}", flush=True)
            return {
                'sql': draft_sql,
                'explanation': 'LLM 生成结果未通过校验，使用意图构建的草稿 SQL',
                'tables_involved': intent.get('tables', []),
                'success': True,
                'generation_mode': 'intent_draft_fallback',
                'timers': timers
            }

        timers['total'] = (time.perf_counter() - t_start) * 1000
        print(f"[SQLGen timing] {timers}, prompt_len={len(prompt)}", flush=True)
        return {
            'sql': sql or '',
            'success': False,
            # 携带最后一次校验失败的具体原因（无效表名/执行错误），避免笼统的"未通过校验"
            'error': f'LLM 生成结果未通过校验: {last_error}' if last_error else 'LLM 生成结果未通过校验',
            'tables_involved': intent.get('tables', []),
            'timers': timers
        }

    def _build_pattern_context(self, user_question: str, located_tables: List[str] = None) -> str:
        """从统一知识检索层构建 SQL 模式上下文。

        取 knowledge_retriever.retrieve 统一打分后 sql_rule 为完整骨架（SELECT/WITH 开头）
        的模板行 Top-2（按 定位表∩sql_tables 重叠度），注入参数化骨架 + 一句场景描述
        （description 首行——迁移时 doc_text 场景行保持在前）；
        定位表与任何模式都无重叠时不注入（避免无关模式误导，防误导口径同前）。
        2026-08-19 瘦身：骨架/表列改为 sql_rule/sql_tables，replay_pass 列已删。
        """
        try:
            from core.knowledge_retriever import retrieve
            items = retrieve(user_question, tables=located_tables, top_k=10,
                             concept_map=self._concept_map('intent'))['items']
        except Exception as e:
            print(f"[WARN] 知识检索失败，模式段为空: {e}", flush=True)
            return ''
        # 骨架行 = sql_rule 为完整查询（SELECT/WITH 开头）；规则片段（SUM(...) 等）不算
        skeleton_rows = [it for it in items
                         if re.match(r'(?i)^\s*(SELECT|WITH)\b', it.get('sql_rule') or '')]
        if not skeleton_rows:
            return ''

        located = set(located_tables or [])

        def _path(it):
            return set(it.get('table_list') or [])

        scored = [(len(_path(p) & located), p) for p in skeleton_rows]
        scored = [s for s in scored if s[0] > 0]
        if not scored:
            return ''
        scored.sort(key=lambda x: (-x[0], -x[1].get('total_score', 0)))
        lines = ["【参考 SQL 模式】（按定位表匹配）"]
        for _, p in scored[:2]:
            scene = (p.get('description') or '').split('\n')[0]
            tables_txt = ' + '.join(sorted(_path(p)))
            lines.append(f"模式「{p.get('name', '')}」（适用：{tables_txt}）：{scene}")
            lines.append(p['sql_rule'])
        return '\n'.join(lines)

    # roster 关键列筛选：注释含这些业务词的列对定位最有判别力（曲线序列列单独经折叠纳入）
    _ROSTER_KEY_KW = ('日期', '年月', '编号', '名称', '金额', '余额', '电量', '电能量', '功率', '负荷',
                      '电压', '电流', '状态', '分类', '类别', '原因', '类型', '标志', '标识', '倍率',
                      '示数', '渠道', '单位')

    def _get_table_roster(self) -> str:
        """全部数据表名册（表名+注释+关键列摘要），静态文本，进程内缓存一次。
        关键列让定位 LLM 能判断“96点功率”这类诉求该选哪张表，而不是只靠表名猜。
        瘦身策略（trackC 验证准确率无损）：仅同族表（名+注释无法互相区分）保留关键列供消歧，
        其余表名+注释已自解释——定位 prompt 从 9.4K 字符降到 ~2.2K，恒推理模型下显著提速。"""
        roster = getattr(self, '_table_roster', None)
        if roster is None:
            roster = ''
            try:
                preloader = self._schema_src()
                table_names = preloader.get_table_names()
                # 同族表判定：公共前缀 ≥15 且各自后缀 ≤4（如 curve_h/h_v/h_a、energy_day_l_xz/p）
                family = set()
                for i, a in enumerate(table_names):
                    for b in table_names[i + 1:]:
                        k = 0
                        while k < min(len(a), len(b)) and a[k] == b[k]:
                            k += 1
                        if k >= 15 and len(a) - k <= 4 and len(b) - k <= 4:
                            family.add(a)
                            family.add(b)
                lines = []
                for t in table_names:
                    comment = preloader.get_table_comment(t)
                    head = f"- {t}：{comment}" if comment else f"- {t}"
                    keys = []
                    if t in family:
                        for entry in self._collapse_series(preloader.get_columns(t)):
                            if entry[0] == 'range':
                                _, first, last, count = entry
                                keys.append(f"{first['name']}~{last['name']}({first.get('comment', '')}~{last.get('comment', '')})")
                            else:
                                c = entry[1]
                                cmt = c.get('comment') or ''
                                if c.get('pk') or any(kw in cmt for kw in self._ROSTER_KEY_KW):
                                    keys.append(f"{c['name']}({cmt})" if cmt else c['name'])
                    if len(keys) > 6:  # trackB-r1d: 12→6
                        keys = keys[:6] + ['…']
                    if keys:
                        head += ' | 关键列: ' + ', '.join(keys)
                    lines.append(head)
                roster = '\n'.join(lines)
            except Exception as e:
                print(f"[WARN] 构建表名册失败: {e}", flush=True)
            self._table_roster = roster
        return roster

    def _locate_tables_with_llm(self, user_question: str, fallback_tables: List[str]) -> List[str]:
        """LLM 定位与问题相关的数据表；失败时静默回退到规则识别结果，不阻断生成"""
        known_tables = set(self.schema_loader.get_table_names())
        fallback = [t for t in (fallback_tables or []) if t in known_tables]
        located = []
        roster = self._get_table_roster()
        if roster:
            prompt = f"""以下是电力营销数据仓库的全部数据表：
{roster}

业务问题：{user_question}

请从上述表中选出回答该问题所必需的数据表（只选必需的，包括必须 JOIN 的中间关联表；不要选可选或仅可能相关的表）。
注意：问题中的每个筛选条件（时间、状态、缴费/结算渠道、分类等）和每个统计维度，都必须有能承载它的表。
只输出 JSON 数组（如 ["table_a", "table_b"]），不要输出任何其他内容。最多 8 张。"""
            for attempt in range(1):  # 单次尝试：定位只是三路提示通道之一，超时/失败由规则+RAG+草稿通道兜底
                try:
                    # 恒推理模型（deepseek）忽略 thinking=False 且推理长尾可达 100s+，
                    # 用独立短超时把 schema 阶段长尾封死在 wf.locate.timeout（默认 25s）
                    result = self._call_llm(prompt, user_question=user_question, max_tokens=512, thinking=False,
                                            timeout=self._wf['locate']['timeout'],
                                            model_override=getattr(config, 'AUX_MODEL', None))
                    m = re.search(r'\[[^\]]*\]', result.get('content', '') or '', re.S)
                    if m:
                        names = json.loads(m.group(0))
                        if isinstance(names, list):
                            located = [t for t in names if isinstance(t, str) and t in known_tables]
                    if not located:
                        # 宽松回退：模型未按 JSON 输出时，直接扫描内容中出现的真实表名
                        content = result.get('content', '') or ''
                        located = [t for t in sorted(known_tables, key=len, reverse=True) if t in content]
                        # 去除被更长表名包含的短名（如 dim_cst_cust 被 dim_cst_cust_agrt 覆盖）
                        located = [t for t in located
                                   if not any(t != o and t in o for o in located)]
                    if located:
                        break
                except Exception as e:
                    print(f"[WARN] LLM 定位表失败(第{attempt + 1}次): {e}", flush=True)
        print(f"[SQLGen] 定位表: LLM={located} 规则={fallback}", flush=True)
        # 只返回 LLM 原始选表；与规则表的打分合并、同族消歧、桥接补全统一由 _merge_located_tables 完成
        return located

    # 草稿直出守门的过滤信号词：问题含这些词通常需要 WHERE 过滤；规则解析未抓到时不许直出
    # （P2：已入库 business_rules.draft_guard，消费点经 get_draft_filter_signals() 读取，本常量为空表回退值）
    _DRAFT_FILTER_SIGNALS = ('状态', '处于', '标志', '类别', '分类', '等级', '类型', '渠道', '原因', '方式',
                             '杭州', '宁波', '温州', '绍兴', '金华', '台州', '嘉兴', '湖州', '衢州', '丽水', '舟山')

    # 同族表消歧的同义词表：键为注释差异词，值为问题中可能出现的等价表达
    # （P2：已入库 business_rules.family_synonym，消费点经 get_family_synonyms() 读取，本常量为空表回退值）
    FAMILY_SYNONYMS = {
        '负荷': ('负荷', '功率', '有功'),
        '功率': ('功率', '负荷', '有功'),
        '电压': ('电压',),
        '电流': ('电流',),
        '低压': ('低压',),
        '高压': ('高压', '专变'),
        '专变': ('专变', '高压'),
        '公变': ('公变', '台区'),
    }

    def _locate_report_tables(self, user_question: str) -> List[str]:
        """报表层优先定位（knowledge.report_first）：省/市/县三级统计语义问题
        在本体 report 层实体中检索命中表；无命中返回空（明细层汇总兜底）。"""
        if not self._onto or not self._wf.get('knowledge', {}).get('report_first', True):
            return []
        try:
            return self._onto.locate_report_tables(user_question)
        except Exception as e:
            print(f'[WARN] 报表层定位失败: {e}', flush=True)
            return []

    def _merge_located_tables(self, user_question: str, intent: Dict, llm_tables: List[str],
                              rule_tables: List[str], draft_tables: List[str], max_tables: int = 8) -> List[str]:
        """打分制合并三路定位结果 → 同族消歧 → 封顶 → 桥接补全。
        证据权重：报表层优先(5，仅统计语义问题) > 草稿 SQL 实际用到(4) > LLM 语义定位(3) > 规则/RAG 召回(1)；
        仅规则命中的表若承载筛选条件字段则 +2 保命（条件关键词驱动是规则通道的职责）。"""
        scores = {}
        report_tables = self._locate_report_tables(user_question)
        for t in report_tables:
            scores[t] = scores.get(t, 0) + 5
        if report_tables:
            print(f"[SQLGen] 报表层优先命中: {report_tables}", flush=True)
        for t in draft_tables or []:
            scores[t] = scores.get(t, 0) + 4
        for t in llm_tables or []:
            scores[t] = scores.get(t, 0) + 3
        for t in rule_tables or []:
            scores[t] = scores.get(t, 0) + 1
        if not scores:
            return []
        for t in self._filter_carrier_tables(intent, list(scores)):
            if scores[t] <= 1:
                scores[t] += 2
        order_hint = {t: i for i, t in enumerate(rule_tables or [])}
        ranked = sorted(scores, key=lambda t: (-scores[t], order_hint.get(t, 99)))
        ranked = self._disambiguate_sibling_tables(ranked, user_question, protect=set(llm_tables or []))
        merged = ranked[:max_tables]
        print(f"[SQLGen] 定位表打分合并: {[(t, scores[t]) for t in merged]}", flush=True)
        expanded = self._expand_with_bridge_tables(merged, max_tables=12)
        # 记录表分级：分数前 N 的表给全量字段；桥接表与低分表只给关键列（防宽表撑爆 prompt）
        self._last_full_tables = set(merged[:self._wf['prompt']['full_column_top_n']])
        self._last_bridge_tables = set(expanded) - set(merged)
        self._last_located = (user_question, expanded)  # 供 v2.4 回退路径复用（与问题绑定，防串题）
        return expanded

    def _filter_carrier_tables(self, intent: Dict, candidates: List[str]) -> set:
        """intent.filters 中的条件字段实际落在哪些候选表上（用于规则表的承载反证）"""
        fields = {str(f.get('field', '')).split('.')[-1] for f in (intent.get('filters') or []) if f.get('field')}
        if not fields:
            return set()
        carriers = set()
        try:
            preloader = self._schema_src()
            for t in candidates:
                cols = {c['name'] for c in preloader.get_columns(t)}
                if fields & cols:
                    carriers.add(t)
        except Exception:
            pass
        return carriers

    def _disambiguate_sibling_tables(self, tables: List[str], user_question: str,
                                     protect: set = None) -> List[str]:
        """同族表（公共前缀≥15 且各自剩余后缀≤4 字符，如 curve_h/h_v/h_a）按问题关键词消歧：
        关键词能命中族内部分成员时，裁掉未命中且不在 protect 中的成员；
        无法区分时保守全留。protect 用于保护有独立证据的表（如 LLM 定位结果）。"""
        if len(tables) < 2:
            return tables
        protect = protect or set()
        n = len(tables)
        parent = list(range(n))

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for i in range(n):
            for j in range(i + 1, n):
                a, b = tables[i], tables[j]
                k = 0
                while k < min(len(a), len(b)) and a[k] == b[k]:
                    k += 1
                if k >= 15 and len(a) - k <= 4 and len(b) - k <= 4:
                    ri, rj = find(i), find(j)
                    if ri != rj:
                        parent[ri] = rj
        # 实体同族合并（精炼层）：同实体成员表视为一族；限 ≤4 成员的小实体，
        # 防"业务申请"这类大主题实体把语义不同的成员过度剪枝
        if self._onto is not None:
            try:
                t2e = self._onto.table_to_entity()
                ent_members = {}
                for i, t in enumerate(tables):
                    e = t2e.get(t)
                    if e:
                        ent_members.setdefault(e, []).append(i)
                for idxs in ent_members.values():
                    if 2 <= len(idxs) <= 4:
                        for j in idxs[1:]:
                            ri, rj = find(idxs[0]), find(j)
                            if ri != rj:
                                parent[ri] = rj
            except Exception:
                pass
        fams = {}
        for i in range(n):
            fams.setdefault(find(i), []).append(tables[i])
        pruned = []
        result = list(tables)
        for members in fams.values():
            if len(members) < 2:
                continue
            hits = {m: self._family_keyword_hit(m, members, user_question) for m in members}
            if not any(hits.values()):
                continue  # 关键词无法区分，保守全留
            for m in members:
                if not hits[m] and m not in protect and m in result:
                    result.remove(m)
                    pruned.append(m)
        if pruned:
            print(f"[SQLGen] 同族表消歧剔除: {pruned}", flush=True)
        return result or tables

    def _family_keyword_hit(self, table: str, members: List[str], user_question: str) -> bool:
        """本表注释相对族内其他表的差异词（经同义词扩展）是否出现在问题中"""
        try:
            preloader = self._schema_src()
            comment = preloader.get_table_comment(table) or ''
            others = ''.join((preloader.get_table_comment(o) or '') for o in members if o != table)
            diff = ''.join(ch for ch in comment if ch not in others).strip()
            keywords = set()
            for key, group in (self._family_synonyms() or self.FAMILY_SYNONYMS).items():
                if key in diff or (key in comment and key not in others):
                    keywords.update(group)
            if len(diff) >= 2:
                keywords.add(diff)
            return any(kw in user_question for kw in keywords if len(kw) >= 2)
        except Exception:
            return False

    # 与图谱模式一致的规则：mgt_org 是万能枢纽，禁止作为中转（仅允许作为端点）
    FK_PATH_BLOCKED_HUBS = ('dim_cst_mgt_org',)

    def _build_fk_graph(self):
        """主外键邻接图 + 边 JOIN 条件索引（来自本体/启动预加载，按 knowledge.source 选源）"""
        rels = self._schema_src().get_relationships()
        graph, conds = {}, {}
        for r in rels:
            a, b = r['path'][0], r['path'][1]
            graph.setdefault(a, set()).add(b)
            graph.setdefault(b, set()).add(a)
            conds[frozenset((a, b))] = r.get('join_conditions', [])
        return graph, conds

    def _bfs_fk_path(self, graph, start, goal, blocked=()):
        """最短 FK 路径（BFS，blocked 节点不可作为中转）"""
        from collections import deque
        queue = deque([(start, [start])])
        seen = {start}
        while queue:
            node, path = queue.popleft()
            if node == goal:
                return path
            for nb in graph.get(node, ()):
                if nb in blocked and nb != goal:
                    continue
                if nb not in seen:
                    seen.add(nb)
                    queue.append((nb, path + [nb]))
        return None

    def _expand_with_bridge_tables(self, tables: List[str], max_tables: int = 10) -> List[str]:
        """按主外键关系图补全桥接中间表：定位表两两求最短关联路径，路径上的中间表自动补入。
        解决 LLM 只挑"端点表"、漏掉看似无关但必须 JOIN 的中间表的问题。"""
        if len(tables) < 2:
            return tables
        try:
            graph, _ = self._build_fk_graph()
        except Exception:
            return tables

        result = list(tables)
        for i in range(len(tables)):
            for j in range(i + 1, len(tables)):
                path = self._bfs_fk_path(graph, tables[i], tables[j], blocked=self.FK_PATH_BLOCKED_HUBS)
                if path and len(path) > 2:
                    for mid in path[1:-1]:
                        if mid not in result:
                            print(f"[SQLGen] 补全桥接表: {mid} ({tables[i]} <-> {tables[j]})", flush=True)
                            result.append(mid)
        return result[:max_tables]

    def _build_join_path_hint(self, tables: List[str], max_paths: int = 4) -> str:
        """基于定位表计算最短业务 JOIN 路径（含每跳条件），作为提示词脚手架。"""
        if len(tables) < 2:
            return ''
        try:
            graph, conds = self._build_fk_graph()
        except Exception:
            return ''
        lines = []
        seen_paths = set()
        for i in range(len(tables)):
            for j in range(i + 1, len(tables)):
                a, b = tables[i], tables[j]
                path = self._bfs_fk_path(graph, a, b, blocked=self.FK_PATH_BLOCKED_HUBS)
                if not path or len(path) < 2:
                    continue
                key = tuple(path)
                if key in seen_paths:
                    continue
                seen_paths.add(key)
                hop_conds = []
                for k in range(len(path) - 1):
                    hop_conds.extend(conds.get(frozenset((path[k], path[k + 1])), []))
                lines.append('- ' + ' → '.join(path))
                if hop_conds:
                    lines.append('  ' + '；'.join(hop_conds))
                if len(lines) >= max_paths * 2:
                    break
            if len(lines) >= max_paths * 2:
                break
        if not lines:
            return ''
        return '【推荐 JOIN 路径（按主外键图最短业务路径，已排除 mgt_org 中转）】\n' + '\n'.join(lines)

    @staticmethod
    def _collapse_series(cols: List[Dict]) -> List[tuple]:
        """把 p1~p96 这类连续编号列折叠。返回有序条目：('col', c) 或 ('range', first, last, count)"""
        groups = {}
        for i, c in enumerate(cols):
            m = re.match(r'^(.*?)(\d+)$', c['name'])
            if m:
                groups.setdefault(m.group(1), []).append((int(m.group(2)), i))
        collapsed_at = {}  # 序列首个成员下标 -> (first, last, count)
        skip = set()       # 序列其余成员下标
        for items in groups.values():
            if len(items) < 4:
                continue
            nums = sorted(n for n, _ in items)
            if nums[-1] - nums[0] != len(nums) - 1:
                continue  # 编号不连续，不折叠
            positions = sorted(i for _, i in items)
            collapsed_at[positions[0]] = (cols[positions[0]], cols[positions[-1]], len(items))
            skip.update(positions[1:])
        entries = []
        for i, c in enumerate(cols):
            if i in collapsed_at:
                first, last, count = collapsed_at[i]
                entries.append(('range', first, last, count))
            elif i not in skip:
                entries.append(('col', c))
        return entries

    def _format_column_lines(self, cols: List[Dict]) -> List[str]:
        """格式化字段行；连续编号列（曲线表 96 点）折叠为一行范围描述，避免撑爆 prompt"""
        def fmt(c):
            pk_flag = ' PK' if c.get('pk') else ''
            line = f"  - {c['name']} {c.get('type') or ''}{pk_flag}".rstrip()
            if c.get('comment'):
                line += f"：{c['comment']}"
            return line

        lines = []
        for entry in self._collapse_series(cols):
            if entry[0] == 'range':
                _, first, last, count = entry
                head = f"  - {first['name']} ~ {last['name']}（共 {count} 个连续编号列）"
                if first.get('comment') or last.get('comment'):
                    head += f"：{first.get('comment', '')} ~ {last.get('comment', '')}"
                lines.append(head)
            else:
                lines.append(fmt(entry[1]))
        return lines

    @staticmethod
    def _prioritize_columns(cols: List[Dict], q_tokens: List[str] = None) -> List[Dict]:
        """字段按“生成 SQL 价值”重排（同层保持 DDL 原序）：
        PK > 主外键/日期 > 问题命中列 > 维度描述与标志列 > 其余。
        用于列数封顶时优先保留条件与维度承载列。"""
        q_tokens = q_tokens or []

        def rank(c):
            name = c['name']
            cmt = c.get('comment') or ''
            if c.get('pk'):
                return 0
            if name.endswith(('_id', '_no', '_code', '_ym')) or 'date' in name:
                return 1
            if q_tokens and any(tok in cmt for tok in q_tokens):
                return 2
            if name.endswith(('_desc', '_flag')):
                return 3
            return 4

        return [cols[i] for i in sorted(range(len(cols)), key=lambda i: (rank(cols[i]), i))]

    def _build_columns_context(self, tables: List[str], user_question: str = '') -> str:
        """检索定位表的全量字段信息（来自启动时预加载的元数据，每题零 DB 开销）。
        紧凑表除关键列外，按问题关键词补入条件承载列（如“已拆除”对应的拆除状态列）。"""
        self._last_columns_summary = []  # 每表注入行数/总列数摘要（思考事件透出用）
        if not tables:
            return ''
        try:
            preloader = self._schema_src()
        except Exception:
            return ''
        # 问题关键词（用于紧凑表的条件列补入）；通用词不参与，近义词先做小映射
        q_tokens = []
        if user_question:
            try:
                _STOP = {'用电', '客户', '用户', '供电', '电力', '电费', '电量', '单位', '公司',
                         '数据', '信息', '情况', '查询', '统计', '哪些', '什么', '是否', '存在'}
                q_tokens = [t for t in self.rag_retriever.extract_keywords(user_question)
                            if len(t) >= 2 and t not in _STOP]
                for alias, target in (('城市', '城乡'), ('农村', '城乡')):
                    if alias in user_question and target not in q_tokens:
                        q_tokens.append(target)
            except Exception:
                q_tokens = []
        lines = ["【相关表字段全量（以下为问题定位到的表的全部字段，SELECT/WHERE/JOIN 只能使用这些字段）】"]
        bridge_tables = getattr(self, '_last_bridge_tables', set()) or set()
        full_tables = getattr(self, '_last_full_tables', None)
        max_cols = self._wf['prompt']['column_max_per_table']
        for t in tables:
            cols = preloader.get_columns(t)
            if not cols:
                continue
            comment = preloader.get_table_comment(t)
            # 桥接表与低分表只承担 JOIN/弱相关，给关键列 + 问题条件列即可
            compact = (t in bridge_tables) or (full_tables is not None and t not in full_tables)
            if compact:
                keys = [c for c in cols if c.get('pk') or c['name'].endswith(('_id', '_no', '_code'))
                        or 'date' in c['name'] or c['name'].endswith('_ym')][:10]
                key_names = {c['name'] for c in keys}
                extras = [c for c in cols
                          if c['name'] not in key_names
                          and (c['name'].endswith(('_desc', '_flag'))
                               or any(tok in (c.get('comment') or '') for tok in q_tokens))][:8]
                tag = '桥接表' if t in bridge_tables else '低相关表'
                head = f"表 {t}（{comment}）[仅关键列]：" if comment else f"表 {t}[仅关键列]："
                lines.append(head)
                for c in keys + extras:
                    pk_flag = ' PK' if c.get('pk') else ''
                    line = f"  - {c['name']} {c.get('type') or ''}{pk_flag}".rstrip()
                    if c.get('comment'):
                        line += f"：{c['comment']}"
                    lines.append(line)
                self._last_columns_summary.append({'table': t, 'shown': len(keys + extras), 'total': len(cols)})
                continue
            lines.append(f"表 {t}（{comment}）：" if comment else f"表 {t}：")
            col_lines = self._format_column_lines(self._prioritize_columns(cols, q_tokens))
            shown = min(len(col_lines), max_cols)
            if len(col_lines) > max_cols:  # 单表列数硬封顶（防超宽维表失控）
                col_lines = col_lines[:max_cols] + [f"  …（其余 {len(col_lines) - max_cols} 列略）"]
            lines.extend(col_lines)
            self._last_columns_summary.append({'table': t, 'shown': shown, 'total': len(cols)})
        return '\n'.join(lines) if len(lines) > 1 else ''

    def _translate_code_value_literals(self, sql: str) -> str:
        """把存编码列上的中文描述条件值机械翻译成编码（LLM 不守形态规则时的确定性兜底）。
        只替换该列条件表达式中的引号字面量，避免误伤其他位置。开头先过别名纪律清理。
        附带：名称列的唯一前缀补全（LLM 写 report_name='全社会用电量'，
        实际值为'全社会用电量（亿千瓦时）'——唯一前缀时确定性补全，否则不动）。"""
        if not sql:
            return sql
        sql = self._clean_aliases(sql)  # 别名纪律（核心词组、≤8 字、禁标点）
        sql = self._complete_name_literals(sql)
        try:
            mappings = self._code_value_translations()
        except Exception as e:
            print(f"[WARN] 码值翻译表获取失败: {e}", flush=True)
            return sql
        for table, column, name_to_code in mappings:
            # 1) 等值条件：col = '名称' → col = '编码'
            for name, code in name_to_code.items():
                if not name or not code or name == code:
                    continue
                pattern = re.compile(
                    r"((?:\w+\.)?" + re.escape(column) + r"\s*=\s*)'" + re.escape(name) + r"'",
                    re.IGNORECASE)
                sql = pattern.sub(lambda m: m.group(1) + f"'{code}'", sql)

            # 2) IN 列表：col IN ('a','b') → 括号内逐项翻译
            def _fix_in(m, _n2c=name_to_code):
                body = m.group(2)
                for name, code in _n2c.items():
                    if not name or not code or name == code:
                        continue
                    body = body.replace(f"'{name}'", f"'{code}'")
                return m.group(1) + body + m.group(3)
            sql = re.sub(
                r"((?:\w+\.)?" + re.escape(column) + r"\s+IN\s*\()([^)]*)(\))",
                _fix_in, sql, flags=re.IGNORECASE)
        return sql

    def _complete_name_literals(self, sql: str) -> str:
        """名称形态码值列的唯一前缀补全（确定性）：
        col = 'X' 且 X 不是值域成员、但值域中恰有唯一成员以 X 开头 → 补全为该成员。
        依据 code_value_column_form 的 (table, column, code_name, form='名称') 登记。"""
        try:
            self.rag_retriever._load_code_value_index()
            items_map = self.rag_retriever._cv_items
            with self.db.connect_governance() as conn:
                rows = conn.execute("SELECT table_name, column_name, code_name "
                                    "FROM code_value_column_form WHERE form='名称'").fetchall()
        except Exception:
            return sql
        for table, column, code_name in rows:
            if table not in sql:
                continue
            names = [name for _c, name in items_map.get(code_name, []) if name]
            if not names:
                continue
            for m in re.finditer(
                    r"((?:\w+\.)?" + re.escape(column) + r"\s*=\s*)'([^']+)'", sql, re.IGNORECASE):
                val = m.group(2)
                if val in names:
                    continue
                cands = [n for n in names if n.startswith(val)]
                if len(cands) == 1:
                    sql = sql.replace(m.group(0), m.group(1) + f"'{cands[0]}'")
        return sql

    def _build_code_value_context(self, user_question: str, tables: List[str]) -> str:
        """码值维度上下文：按语义注入问题相关的码值域（可选值、维度↔表字段关系）"""
        self._last_code_value_hits = []
        try:
            domains = self._retrieve_code_values(user_question, tables)
        except Exception as e:
            print(f"[WARN] 码值检索失败: {e}", flush=True)
            return ''
        if not domains:
            return ''
        # 记录命中域供接口透传（前端上下文面板展示）
        self._last_code_value_hits = [
            {'code_name': d['code_name'], 'cn_name': d['cn_name'],
             'form': d.get('form'), 'matched': d['matched'],
             'columns': [f"{t}.{c}" for t, c in (d.get('columns') or [])]}
            for d in domains
        ]
        lines = ["【维度码值（WHERE 条件值必须从这里取，禁止臆造；已标注码值所在表与字段）】"]
        cap = self._wf['prompt']['code_value_max']
        hard_cap = int(cap * 1.2)  # 命中域/落列域也不超过的硬顶（模糊命中过多时防失控）
        total = len(lines[0])
        # 排序：定位表落列的域优先（生成真正要用的，如曲线表 data_type），其次问题命中域；
        # 软顶只裁“既没落列也没命中”的域，硬顶一律停
        located = set(tables or [])

        def _dom_located(d):
            return any(t in located for t, _ in (d.get('columns') or []))

        domains = sorted(domains, key=lambda d: (not _dom_located(d), not bool(d.get('matched'))))
        for d in domains:
            matched = d.get('matched') or []
            located_dom = _dom_located(d)
            # 明细瘦身（2026-08-14）：既未命中也未落列的域不进 prompt
            if not matched and not located_dom:
                continue
            seg = []
            cols = d['columns']
            if cols:
                col_desc = '、'.join(f"{t}.{c}" for t, c in cols[:2])
                head = f"- {d['code_name']}（{d['cn_name']}，字段 {col_desc}）"
            else:
                head = f"- {d['code_name']}（{d['cn_name']}，码值域，未直接落列）"
            form = d.get('form')
            if form == '编码':
                head += '【该字段存编码，WHERE 条件请使用编码，不要用中文描述】'
            if matched:
                head += f" ★问题命中: {'、'.join(matched)}"
            seg.append(head)
            if form == '编码' and d.get('pairs'):
                # 对照表：命中域只传命中项，落列未命中域留 5 个样例，并标注域内总项数
                pairs = [(n, c) for n, c in d['pairs'] if c]
                full = d.get('total') or len(pairs)
                if matched:
                    pairs = [nc for nc in pairs if nc[0] in matched]
                else:
                    pairs = pairs[:5]
                pair_txt = '、'.join(f"{n}={c}" for n, c in pairs)
                if pair_txt:
                    suffix = (f'（共{full}项，仅示命中）' if matched and full > len(pairs)
                              else (f'（共{full}项）' if full > len(pairs) else ''))
                    seg.append(f"  名称=编码 对照：{pair_txt}{suffix}")
            elif d['values']:
                full = d.get('total') or len(d['values'])
                vals = matched if matched else d['values'][:5]
                suffix = (f'（共{full}项，仅示命中）' if matched and full > len(vals)
                          else (f'（共{full}项）' if full > len(vals) else ''))
                seg.append(f"  可选值：{'、'.join(vals)}{suffix}")
            seg_len = sum(len(s) + 1 for s in seg)
            if len(lines) > 1:
                if total + seg_len > hard_cap:
                    break
                if total + seg_len > cap and not (d.get('matched') or _dom_located(d)):
                    break  # 软顶：只裁既未命中也未落列的域
            lines.extend(seg)
            total += seg_len
        return '\n'.join(lines)

    def _get_static_prompt_head(self) -> str:
        """极简静态头部（~1K）：通用 SQL 生成纪律。全库 Schema、few-shot 示例已移除
        （由定位表字段上下文 + JOIN 路径提示 + 问答对 RAG 定向覆盖）；
        用电量/供电单位等业务专用规则由 _build_v3_prompt 按问题关键词条件注入。"""
        if self._static_prompt_head is None:
            date_hint = (
                "日期过滤请使用 DATE_FORMAT(date_col, '%Y-%m') = 'YYYY-MM'、DATE_FORMAT(date_col, '%Y-%m-%d') = 'YYYY-MM-DD' 或 BETWEEN 语法。"
            )
            self._static_prompt_head = f"""你是电力营销数据仓库的 SQL 专家。根据下方定位到的表字段与问题，生成一条可执行的 MySQL SELECT 语句。

【生成规则】
1. 只输出一行纯 SELECT（或 WITH...SELECT），不要 Markdown 代码块，不要解释性文字。
2. 只能使用下方 Schema 中出现的表名和字段名，禁止编造；JOIN 必须基于 Schema 列出的关联字段或推荐 JOIN 路径。
3. {date_hint}
4. 聚合函数（SUM/COUNT/AVG/MAX/MIN）必须有 GROUP BY，且分组字段必须出现在 SELECT 中。
5. WHERE 中作为等值筛选条件的维度属性字段（如状态、分类、单位代码）必须同时出现在 SELECT 列表中（多表查询带表别名）。
6. 问题中出现"X 为/是/等于'Y'"的属性值条件时，必须关联 X 所在的维度表，在 WHERE 中写入该条件，并在 SELECT 中输出该维度字段。
7. 输出完整性（重要）：
   a) 任何题目，SELECT 必须保留主业务对象的对外编号和名称（主语为客户/用户时取 cust_no、cust_name；主语为安装点时取 inst_no、inst_name；其余对象同理），以及所属单位（mgt_org_code、mgt_org_name）；
   b) WHERE 中出现的筛选条件字段必须在 SELECT 中保留对应输出列；
   c) 涉及对比/环比/同比/超阈值的题目，对比双方的度量和计算结果列必须全部输出。如"哪些低压用户的电费超过上月30%"：SELECT 客户编号、客户名称、所属单位、本月电费、上月电费、增长比例，缺一不可。
8. TOP N 用 ORDER BY ... DESC LIMIT N；无排序要求时不加 ORDER BY。
9. SELECT 字段使用英文代码名，不要加中文别名；除第 5/6/7 条要求保留的字段外，不要堆砌与问题无关的维度字段；表内部主键标识列（cust_id、inst_id 等 _id 列）仅用于 JOIN/关联，禁止输出到 SELECT（对外编号用 cust_no、inst_no 等）。
10. WHERE 条件值的存储形态严格服从【维度码值】标注：标注"存编码"的字段一律写编码（按"名称=编码 对照"翻译），禁止写中文描述。
11. JOIN 策略（重要）：以问题主语所在的表为主表；维表、弱关联表、仅取属性的关联一律用 LEFT JOIN，防止关联表无匹配记录时主表数据丢失；仅当业务语义确实要求"两侧都必须匹配"时才用 INNER JOIN。多级关联（客户→安装点→计量点→电量）逐级 LEFT JOIN 同理。
12. 生成前自查：问题中每个筛选条件、分组维度、统计指标都必须在 SQL 中有对应体现，禁止省略。
13. 推理从简：内部推理控制在 3 步以内（定位字段→确定关联→写出 SQL），不要反复权衡验证，直接输出最终 SQL。
"""
        return self._static_prompt_head

    def _build_v3_prompt(
        self,
        user_question: str,
        intent: Dict,
        pattern_context: str,
        error_context: str,
        draft_sql: str,
        review_feedback: Optional[str] = None,
        previous_error: Optional[str] = None,
        org_hints: str = "",
        qa_context: str = "",
        columns_context: str = "",
        code_value_context: str = "",
        join_hint: str = ""
    ) -> str:
        """构建单次生成 Prompt：静态头部（初始化时构建一次）+ 每题动态部分（业务问题 + 3条参考问答对等）"""

        # 意图摘要
        intent_summary = f"""问题类型：{intent.get('question_type', '查询')}
涉及表：{', '.join(intent.get('tables', []))}
聚合：{', '.join(f.get('agg', '') for f in intent.get('fields', []) if f.get('role') == 'aggregate')}
过滤条件：{json.dumps(intent.get('filters', []), ensure_ascii=False)}
分组：{', '.join(intent.get('group_by', []))}
排序：{json.dumps(intent.get('order_by', []), ensure_ascii=False)}
LIMIT：{intent.get('limit', '无')}"""

        feedback_section = f"\n【审查反馈】{review_feedback}\n请修正后重新生成。" if review_feedback else ''
        draft_section = f"\n【草稿 SQL 参考】\n{draft_sql}\n你可以参考上述草稿的结构，但请根据 Schema 生成最准确的 SQL。" if draft_sql else ''
        error_section = f"\n【上次生成错误】\n{previous_error}\n请根据错误信息修正 SQL，确保字段和表名真实存在、JOIN 条件正确。" if previous_error else ''
        org_section = f"\n{org_hints}" if org_hints else ''

        # 当前日期上下文：供“今年X月/去年/本月”等相对时间换算
        now = time.localtime()
        date_context = (
            f"【当前日期】{time.strftime('%Y-%m-%d', now)}"
            f"（今年={now.tm_year}年、去年={now.tm_year - 1}年、本月={time.strftime('%Y-%m', now)}；"
            f"问题中的“今年X月”“去年X月”“本月”等相对时间必须按此换算）"
        )

        # 业务专用规则按问题关键词条件注入（不命中不付费）
        # 统一走 knowledge_retriever.retrieve（查询无类型过滤，2026-08-19 瘦身后表内也无类型列），
        # 取 items 中触发词命中且带 sql_rule 片段的规则行（item_type='rule'）渲染注入；
        # 检索层异常回退 get_caliber_rules 直读；空表再回退 _ELEC_RULES/_ORG_RULES 代码常量。
        rule_modules = []
        _kr_items = None
        try:
            from core.knowledge_retriever import retrieve as _kr_retrieve
            _kr_items = _kr_retrieve(user_question, top_k=20,
                                     concept_map=self._concept_map('intent'))['items']
        except Exception as e:
            print(f"[WARN] 统一知识检索失败，口径注入回退规则直读: {e}", flush=True)
        if _kr_items is not None:
            _seen_nm = set()
            for _it in _kr_items:
                if _it.get('item_type') != 'rule' or not (_it.get('sql_rule') or '').strip():
                    continue
                if _it.get('scores', {}).get('trigger', 0) <= 0:
                    continue  # 口径注入仍以触发词命中为前提（与原 trigger_words 口径一致）
                if _it['id'] in _seen_nm:
                    continue
                _seen_nm.add(_it['id'])
                rule_modules.append(render_caliber_injection({
                    'name': _it.get('name') or '', 'description': _it.get('description') or '',
                    'sql_rule': _it.get('sql_rule') or '',
                    'sql_tables': _it.get('table_list') or []}))
        else:
            caliber_rules = get_caliber_rules()
            if caliber_rules:
                for _rule in caliber_rules:
                    if any(k in user_question for k in _rule['triggers']):
                        rule_modules.append(render_caliber_injection(_rule))
            else:
                if any(k in user_question for k in _ELEC_TRIGGER_KW):
                    rule_modules.append(_ELEC_RULES)
                if any(k in user_question for k in _ORG_TRIGGER_KW):
                    rule_modules.append(_ORG_RULES)
        modules_section = '\n' + '\n\n'.join(rule_modules) + '\n' if rule_modules else ''
        join_section = f"\n{join_hint}\n" if join_hint else ''

        prompt = f"""{self._get_static_prompt_head()}
{modules_section}{columns_context}
{join_section}{code_value_context}
{org_section}
{qa_context}
{pattern_context}

{error_context}

{date_context}
【用户问题】
{user_question}

【问题意图解析】
{intent_summary}
{draft_section}
{feedback_section}
{error_section}

【输出】一行纯代码 SQL：
"""
        # 总预算硬顶：超配时从低优先级段开始整段裁撤（错题 → 问答对），
        # JOIN 提示与码值段保护不裁（JOIN 路径是多表正确性的关键证据，且仅数百字符；
        # 条件值正确性依赖码值段，其自身有 2500/3000 软硬顶）；
        # 核心段（极简规则、字段上下文、问题本身）不动
        budget = self._wf['prompt']['budget']
        if len(prompt) > budget:
            for section in (error_context, qa_context):
                if len(prompt) <= budget:
                    break
                if section and section.strip() and section in prompt:
                    prompt = prompt.replace(section, '', 1)
                    print(f"[WARN] prompt 超预算({budget})，裁撤一段: {section.strip().split(chr(10))[0][:20]}", flush=True)
            if len(prompt) > budget:
                print(f"[WARN] prompt 裁撤后仍超预算: {len(prompt)} > {budget}", flush=True)
        return prompt
    
    def _identify_related_tables(self, user_question: str, retrieved_pairs: List[Dict]) -> List[str]:
        keywords = self.rag_retriever.extract_keywords(user_question)
        mapped_tables = []
        # P2：关键词映射已入库 keyword_table_map，空表/异常回退 KEYWORD_TO_TABLE_MAP 常量
        for kw in keywords:
            for pattern, tables in (self._concept_map('v24_fallback') or KEYWORD_TO_TABLE_MAP).items():
                if pattern in kw.lower() or kw.lower() in pattern:
                    for t in tables:
                        if t not in mapped_tables:
                            mapped_tables.append(t)
        tables_from_qa = []
        for pair in retrieved_pairs:
            sql = pair.get('standard_sql', '')
            for match in re.finditer(r'FROM\s+(\w+)', sql, re.IGNORECASE):
                t = match.group(1)
                if t not in tables_from_qa:
                    tables_from_qa.append(t)
            for match in re.finditer(r'JOIN\s+(\w+)', sql, re.IGNORECASE):
                t = match.group(1)
                if t not in tables_from_qa:
                    tables_from_qa.append(t)
        tables_from_keywords = self.schema_loader.find_related_tables(keywords)
        all_tables = []
        seen = set()
        for t in mapped_tables + tables_from_qa + tables_from_keywords:
            if t not in seen:
                seen.add(t)
                all_tables.append(t)
        # 如果用户问题涉及地名或管理单位，自动加入管理单位表
        place_keywords = ['杭州', '宁波', '温州', '绍兴', '金华', '台州', '嘉兴', '湖州', '衢州', '丽水', '舟山',
                          '公司', '供电', '管理单位', '地市', '区县', '城市', '地区']
        for pk in place_keywords:
            if pk in user_question:
                if 'dim_cst_mgt_org' not in all_tables:
                    all_tables.append('dim_cst_mgt_org')
                break
        
        return all_tables[:4]  # 增加到最多4个表
    
    def _build_qa_context(self, retrieved_pairs: List[Dict]) -> str:
        """
        构建RAG参考上下文（v3.2 平衡版）
        
        关键原则：展示完整SQL结构供LLM学习，但标注条件值需要替换。
        既保留few-shot学习能力，又防止无脑复制。
        """
        if not retrieved_pairs:
            return ""
        
        lines = []
        total = 0
        cap = self._wf['prompt']['qa_max']
        for i, pair in enumerate(retrieved_pairs[:3], 1):
            question = pair.get('question', '')
            sql = pair.get('standard_sql', '')

            # 提取表关联路径
            tables = self._extract_tables_from_sql(sql)
            table_chain = ' → '.join(tables) if len(tables) > 1 else tables[0] if tables else '未知'

            # 提取关键条件字段（用于标注哪些条件需要替换）
            condition_fields = self._extract_condition_fields(sql)

            seg = [f"参考{i}（问题：{question}）："]
            seg.append(f"  · 涉及的表：{table_chain}")
            seg.append(f"  · SQL：{sql}")
            if condition_fields:
                seg.append(f"  · 【注意】以下条件值仅适用于'{question}'，当前问题不同时必须替换：{condition_fields}")
            seg.append("")
            seg_len = sum(len(s) + 1 for s in seg)
            if total + seg_len > cap and lines:
                break  # 超配额则少给参考，保住核心段
            lines.extend(seg)
            total += seg_len

        return "\n".join(lines)
    
    def _extract_condition_fields(self, sql: str) -> str:
        """提取SQL中具体的条件值字段，用于标注'需要替换'"""
        patterns = []
        
        # 提取等值条件中的具体值
        eq_matches = re.findall(r"(\w+\.?\w*)\s*=\s*['\"]([^'\"]+)['\"]", sql, re.IGNORECASE)
        for field, value in eq_matches:
            short_field = field.split('.')[-1] if '.' in field else field
            patterns.append(f"{short_field}='{value}'")
        
        # 提取日期条件
        date_matches = re.findall(r"DATE_FORMAT\([^)]+\)\s*=\s*['\"]([^'\"]+)['\"]", sql, re.IGNORECASE)
        for value in date_matches:
            patterns.append(f"日期='{value}'")
        
        # 提取 LIKE 条件
        like_matches = re.findall(r"(\w+\.?\w*)\s+LIKE\s+['\"]([^'\"]+)['\"]", sql, re.IGNORECASE)
        for field, value in like_matches:
            short_field = field.split('.')[-1] if '.' in field else field
            patterns.append(f"{short_field} LIKE '{value}'")
        
        return '，'.join(patterns[:5]) if patterns else ''

    def _build_prompt(
        self,
        user_question: str,
        schema_context: str,
        qa_context: str,
        related_tables: List[str],
        error_context: Optional[str] = None,
        review_feedback: Optional[str] = None
    ) -> str:
        """构建 LLM Prompt（v2.5 极简版：只保留核心信息，避免模型混淆）"""
        feedback_section = ""
        if review_feedback:
            feedback_section = f"""
【审查反馈】{review_feedback}
请修正后重新生成。"""
        
        # 简化：去掉 few-shot、历史参考、错题修正，避免模型被无关信息干扰
        prompt = f"""你是 MySQL SQL 专家。根据问题生成一行可执行的 SELECT 语句。

【表名白名单——只能用这些表，禁止编造任何其他表名】
{chr(10).join(f'- {t}' for t in related_tables)}

【高频关联路径——客户查用电量，照此 JOIN】
- 只按客户汇总：dim_cst_elec_cons_cust c JOIN dwd_cst_meter_run mr ON c.cust_id = mr.cust_id JOIN 日电量表 e ON mr.meter_asset_no = e.meter_asset_no
- 需要安装点属性时改为经安装点中转：c.cust_id = ie.cust_id（dim_cst_inst_elec_cons ie）、ie.inst_id = mr.inst_id
- 日电量表：低压 dwd_cst_es_meter_energy_day_l_xz / 专变 dwd_cst_meter_energy_day_h_xz / 公变 dwd_cst_es_meter_energy_day_p，电量字段 pap_e；用户级聚合/极值、计量点TOP-N 只用专变表，禁止 UNION ALL；低压表仅明确低压口径或单计量点明细时用；全社会总量/趋势才 UNION ALL 专变+公变；cust_id、inst_id 禁止直接连日电量表

【规则——严格遵守】
- 必须且只能使用上面白名单中列出的表名，禁止编造不存在的表名（如 electricity_usage、customer_id 等）
- 必须以 SELECT 或 WITH 开头
- SELECT 字段用英文代码名，不要加中文别名。字段后的 /* 中文注释 */ 仅作参考，不要写入 SQL
- 不要 Markdown 代码块
- 不要只返回说明性文字或注释，必须输出可执行的 SQL 代码
- 日期用 DATE_FORMAT(date_col, '%Y-%m') 格式（MySQL 语法）
- 字符串匹配用 LIKE 'keyword%' 或 LIKE '%keyword'
- TOP N 用 ORDER BY ... DESC LIMIT N（MySQL 语法）
- WHERE 中作为等值筛选条件的维度属性字段（如状态、分类、单位代码等）必须同时出现在 SELECT 列表中

【可用表结构详细】
{schema_context}
{feedback_section}
【当前问题】
{user_question}

【输出】一行纯代码 SQL，字段名用英文代码名：
"""
        return prompt
    
    def _call_llm(self, prompt: str, user_question: str = None, max_tokens: int = 8000, thinking: Optional[bool] = None, timeout: Optional[int] = None, stream_cb=None, model_override: Optional[str] = None, effort: Optional[str] = None) -> Dict:
        from core.llm_config import current as _llm_current, build_request as _llm_build, preview_config as _llm_preview
        cfg = _llm_current()
        # 辅助任务模型覆盖（仅 deepseek provider 下生效）：修复/定位等快速调用走非推理模型
        if model_override and cfg.get('provider') == 'deepseek':
            cfg = _llm_preview({'provider': 'deepseek', 'model': model_override})
        if not cfg.get('api_key'):
            raise ValueError(f"{cfg['provider']} API Key 未配置（请在前端设置页配置）")
        print(f"[DEBUG] prompt_length={len(prompt)}", flush=True)
        last_error = None
        t_start = time.perf_counter()
        http_timeout = timeout if timeout is not None else config.LLM_TIMEOUT_FAST

        # Phase 1: 主调用（thinking 默认跟随配置；辅助任务如表定位传 thinking=False 走快速模式）
        # Provider 间参数差异（temperature/thinking 参数形态）统一由 llm_config.build_request 封装
        use_thinking = getattr(self, '_thinking', getattr(config, 'KIMI_THINKING', False)) if thinking is None else thinking
        url, headers, payload = _llm_build(
            [{'role': 'user', 'content': prompt}], max_tokens=max_tokens, thinking=use_thinking,
            cfg=cfg, effort=effort)

        # 推理截断降级（2026-08-14）：恒推理模型推理超过 max_tokens 上限时返回空内容，
        # 原样重试必重蹈覆辙——降级到非推理辅助模型（AUX_MODEL）重试一次
        def _degrade_with_aux():
            aux = getattr(config, 'AUX_MODEL', None)
            if not aux or cfg.get('provider') != 'deepseek' or payload.get('model') == aux:
                return None
            try:
                cfg2 = _llm_preview({'provider': 'deepseek', 'model': aux})
                u2, h2, p2 = _llm_build([{'role': 'user', 'content': prompt}],
                                        max_tokens=max_tokens, thinking=False, cfg=cfg2)
                t0 = time.perf_counter()
                r2 = _LLM_HTTP.post(u2, headers=h2, json=p2, timeout=http_timeout)
                r2.raise_for_status()
                d2 = r2.json()
                c2 = d2['choices'][0]['message']['content']
                print(f"[DEBUG] 推理截断降级 {aux} content_len={len(c2) if c2 else 0} elapsed={(time.perf_counter() - t0) * 1000:.1f}ms", flush=True)
                if c2 and c2.strip():
                    return {'content': c2, 'usage': d2.get('usage', {})}
            except Exception as e:
                print(f"[DEBUG] 降级重试失败: {e}", flush=True)
            return None

        # 流式通道（仅主生成调用传入 stream_cb）：SSE 增量转发 reasoning_content/content 到思考窗口
        if stream_cb is not None:
            spayload = dict(payload, stream=True)
            resp = None
            try:
                t0 = time.perf_counter()
                resp = _LLM_HTTP.post(url, headers=headers, json=spayload, timeout=http_timeout, stream=True)
                resp.raise_for_status()
                resp.encoding = 'utf-8'
                parts = {'reasoning': [], 'content': []}
                full_content = []
                stream_fr = None
                last_flush = time.perf_counter()

                def _flush(force=False):
                    nonlocal last_flush
                    now = time.perf_counter()
                    if not force and now - last_flush < 0.25:
                        return
                    last_flush = now
                    for ph in ('reasoning', 'content'):
                        if parts[ph]:
                            chunk = ''.join(parts[ph])
                            parts[ph] = []
                            try:
                                stream_cb(ph, chunk)
                            except Exception:
                                pass

                for raw in resp.iter_lines(decode_unicode=True):
                    if not raw:
                        continue
                    body = raw.strip()
                    if not body.startswith('data:'):
                        continue
                    body = body[5:].strip()
                    if body == '[DONE]':
                        break
                    try:
                        sevt = json.loads(body)
                        choice0 = (sevt.get('choices') or [{}])[0]
                        delta = choice0.get('delta') or {}
                        if choice0.get('finish_reason'):
                            stream_fr = choice0['finish_reason']
                    except Exception:
                        continue
                    rc = delta.get('reasoning_content')
                    dc = delta.get('content')
                    if rc:
                        parts['reasoning'].append(rc)
                    if dc:
                        parts['content'].append(dc)
                        full_content.append(dc)
                    _flush()
                _flush(force=True)
                content = ''.join(full_content)
                elapsed = (time.perf_counter() - t0) * 1000
                print(f"[DEBUG] provider={cfg['provider']} model={payload['model']} stream thinking={use_thinking} content_len={len(content)} elapsed={elapsed:.1f}ms", flush=True)
                if content.strip():
                    return {'content': content, 'usage': {}}
                # 流式空响应：推理截断直接降级辅助模型（不再走非流式原样重试，省 2 分钟）
                if stream_fr == 'length':
                    res = _degrade_with_aux()
                    if res:
                        return res
                raise ValueError('流式返回内容为空')
            except Exception as e:
                print(f"[DEBUG] stream error={e}，回退非流式主调用", flush=True)
            finally:
                try:
                    if resp is not None:
                        resp.close()
                except Exception:
                    pass

        for empty_retry in range(2):  # content 为空属边缘情况（推理截断/端点抖动），原样重试一次
            t0 = time.perf_counter()
            try:
                response = _LLM_HTTP.post(url, headers=headers, json=payload, timeout=http_timeout)
                response.raise_for_status()
                data = response.json()
                self._accumulate_usage(data.get('usage'))
                content = data['choices'][0]['message']['content']
                finish_reason = (data['choices'][0].get('finish_reason') or '')
                elapsed = (time.perf_counter() - t0) * 1000
                print(f"[DEBUG] provider={cfg['provider']} model={payload['model']} thinking={use_thinking} content_len={len(content) if content else 0} elapsed={elapsed:.1f}ms", flush=True)
                if content and content.strip():
                    return {'content': content, 'usage': data.get('usage', {})}
                last_error = '返回内容为空'
                # 长耗时空响应（恒推理模型推理截断，finish_reason=length）重试必重蹈覆辙，直接放弃；
                # 仅快速返回的空响应（端点抖动）原样重试一次
                if empty_retry == 0 and finish_reason != 'length' and elapsed <= config.LLM_EMPTY_RETRY_MAX_MS:
                    print(f"[DEBUG] content 为空（{elapsed:.0f}ms, finish={finish_reason}），原样重试一次", flush=True)
                    continue
                print(f"[DEBUG] content 为空（{elapsed:.0f}ms, finish={finish_reason}），判定推理截断，放弃重试", flush=True)
                break
            except requests.exceptions.HTTPError as e:
                elapsed = (time.perf_counter() - t0) * 1000
                print(f"[DEBUG] thinking={use_thinking} http_error={response.status_code} elapsed={elapsed:.1f}ms {response.text[:120]}", flush=True)
                last_error = e
                break
            except Exception as e:
                elapsed = (time.perf_counter() - t0) * 1000
                print(f"[DEBUG] thinking={use_thinking} error={e} elapsed={elapsed:.1f}ms", flush=True)
                last_error = e
                break

        # 主调用失败：先尝试降级辅助模型（推理截断/空响应场景），再视配置走重试兜底
        res = _degrade_with_aux()
        if res:
            return res

        # Phase 2: 重试兜底（默认关闭，避免极端慢请求）
        if not getattr(config, 'USE_REASONING_FALLBACK', False):
            total_elapsed = (time.perf_counter() - t_start) * 1000
            print(f"[DEBUG] 主调用失败（{total_elapsed:.1f}ms），重试兜底已关闭", flush=True)
            raise ValueError(f"LLM 调用失败: {last_error or '返回为空或被拒绝'}")

        for attempt in range(2):
            url, headers, payload = _llm_build(
                [{'role': 'user', 'content': prompt}], max_tokens=16000, thinking=True)
            t0 = time.perf_counter()
            try:
                response = _LLM_HTTP.post(url, headers=headers, json=payload, timeout=config.LLM_TIMEOUT_REASONING)
                response.raise_for_status()
                data = response.json()
                self._accumulate_usage(data.get('usage'))
                content = data['choices'][0]['message']['content']
                elapsed = (time.perf_counter() - t0) * 1000
                print(f"[DEBUG] reasoning attempt={attempt} content_len={len(content) if content else 0} elapsed={elapsed:.1f}ms", flush=True)
                if content and content.strip():
                    return {'content': content, 'usage': data.get('usage', {})}
                short_question = user_question or ""
                short_prompt = (
                    f"你是电力营销 SQL 专家。根据问题生成一行可执行 SELECT。\n"
                    f"问题：{short_question}\n"
                    f"可用表：{', '.join(self.schema_loader.get_table_names()[:10])}\n"
                    f"只输出一行 SELECT。"
                )
                prompt = short_prompt
                print(f"[DEBUG] retrying with short_prompt, len={len(short_prompt)}", flush=True)
            except Exception as e:
                last_error = e
                elapsed = (time.perf_counter() - t0) * 1000
                print(f"[DEBUG] reasoning attempt={attempt} error={e} elapsed={elapsed:.1f}ms", flush=True)
                if attempt < 1:
                    time.sleep(min(2 ** attempt, 8))
        raise ValueError(f"LLM 调用失败或返回内容为空: {last_error}")

    @staticmethod
    def _is_sentinel_sql(sql: str) -> bool:
        """检测“哨兵 SQL”：模型自认无法回答时编的占位语句（非代码约定，模型自发行为）。
        特征：含 cannot_answer/missing_required_tables 字样，或 SELECT 列表只有一个字符串字面量。"""
        if not sql:
            return False
        low = sql.lower()
        if 'cannot_answer' in low or 'missing_required_tables' in low:
            return True
        body = re.sub(r'/\*.*?\*/', '', sql)  # 去块注释
        m = re.match(r"""(?is)^\s*select\s+('[^']*'|"[^"]*")(\s+(?:as\s+)?\w+)?\s*(from\b|$)""", body)
        return bool(m)

    def _extract_sql(self, content: str) -> str:
        if not content:
            return ''
        # Strip thinking tags if present
        content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL)
        content = re.sub(r'```sql\n?', '', content, flags=re.IGNORECASE)
        content = re.sub(r'```\n?', '', content)
        lines = content.split('\n')
        sql_lines = []
        in_sql = False
        allowed_prefixes = ['SELECT', 'WITH']
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith('--') or stripped.startswith('//'):
                continue
            if any(stripped.upper().startswith(p) for p in allowed_prefixes):
                in_sql = True
            if in_sql:
                sql_lines.append(stripped)
        if sql_lines:
            sql = ' '.join(sql_lines)
            sql = re.sub(r'\s+', ' ', sql)
            return sql.strip()
        return ''
    
    def _extract_explanation(self, content: str) -> str:
        if not content:
            return ''
        lines = content.split('\n')
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('--'):
                return stripped.lstrip('--').strip()
        return ''
    
    def _fallback_to_qa_pair(self, user_question: str, retrieved_pairs: List[Dict]) -> Optional[Dict]:
        """
        Fallback降级策略（v3.1）：API调用失败时的兜底策略。
        
        仅在网络/API异常时启用，不用于LLM输出格式异常的情况。
        高相似度(>0.85)时才返回，并明确标记为fallback，便于前端区分。
        """
        if not retrieved_pairs:
            return None
        
        user_keywords = set(self.rag_retriever.extract_keywords(user_question))
        best_pair = None
        best_score = 0
        
        for pair in retrieved_pairs:
            base_score = pair.get('combined_score', 0)
            sql = pair.get('standard_sql', '')
            keyword_bonus = 0
            for kw in user_keywords:
                if len(kw) >= 2 and kw in sql.lower():
                    keyword_bonus += 0.2
            total_score = base_score + keyword_bonus
            if total_score > best_score:
                best_score = total_score
                best_pair = pair
        
        # 只有相似度足够高时才fallback，避免答非所问
        if best_pair and best_score > 0.85:
            return {
                'sql': best_pair['standard_sql'],
                'explanation': f'[系统兜底] 基于最相似历史问答生成（匹配度: {best_score:.2f}）',
                'tables_involved': self._extract_tables_from_sql(best_pair['standard_sql']),
                'success': True,
                'fallback': True,
                'source_qa_id': best_pair['id']
            }
        return None
    
    def _build_org_hints(self, user_question: str) -> str:
        """如果问题中包含已知供电单位名称，注入其代码提示，避免 LIKE 泛化导致错误。"""
        try:
            with self.db.connect_business() as conn:
                rows = conn.execute(
                    "SELECT mgt_org_code, mgt_org_name FROM dim_cst_mgt_org WHERE mgt_org_name IS NOT NULL AND mgt_org_name != ''"
                ).fetchall()
        except Exception:
            return ''
        hints = []
        for code, name in rows:
            if name and name in user_question:
                hints.append(f"- 问题中出现的供电单位 '{name}' 对应 mgt_org_code = '{code}'，请优先使用该代码做等值匹配。")
        if not hints:
            return ''
        return "【供电单位精确匹配提示】\n" + "\n".join(hints) + "\n"

    def _build_error_context(self, error_records: List[Dict]) -> str:
        if not error_records:
            return ""
        lines = ["【历史错题修正】（以下问题之前曾生成错误SQL，已修正，请勿再犯同样错误）\n"]
        total = len(lines[0])
        cap = self._wf['prompt']['error_max']
        for i, rec in enumerate(error_records[:2], 1):  # 只取最多2个错题
            wrong_sql = rec.get('wrong_sql', '')
            correct_sql = rec.get('correct_sql', '')
            # 截断SQL以避免prompt过长
            if len(wrong_sql) > 200:
                wrong_sql = wrong_sql[:200] + '...'
            if len(correct_sql) > 200:
                correct_sql = correct_sql[:200] + '...'
            seg = [f"--- 错题 #{i} ---"]
            seg.append(f"问题：{rec.get('question', 'N/A')}")
            seg.append(f"❌ 错误SQL（{rec.get('error_type', '错误')}）：{wrong_sql}")
            seg.append(f"✅ 正确SQL：{correct_sql}")
            if rec.get('reason'):
                seg.append(f"原因：{rec['reason']}")
            seg.append("")
            seg_len = sum(len(s) + 1 for s in seg)
            if total + seg_len > cap and len(lines) > 1:
                break  # 超配额则少给错题
            lines.extend(seg)
            total += seg_len
        return '\n'.join(lines)
    
    def _extract_tables_from_sql(self, sql: str) -> List[str]:
        tables = set()
        for match in re.finditer(r'FROM\s+(\w+)', sql, re.IGNORECASE):
            tables.add(match.group(1))
        for match in re.finditer(r'JOIN\s+(\w+)', sql, re.IGNORECASE):
            tables.add(match.group(1))
        # 排除 CTE 别名（WITH xxx AS (...) / , xxx AS (...)）——它们不是真实表
        cte_names = set()
        for match in re.finditer(r'(?:\bWITH|,)\s*(\w+)\s+AS\s*\(', sql, re.IGNORECASE):
            cte_names.add(match.group(1))
        return sorted(tables - cte_names)
    
    def _normalize_sql_dialect(self, sql: str) -> str:
        """规范化日期函数为 MySQL 方言（LLM/模板侧的 strftime 一律转 DATE_FORMAT）"""
        if not sql:
            return sql

        def replace_strftime(match):
            fmt = match.group(1)
            col = match.group(2).strip()
            return f"DATE_FORMAT({col}, '{fmt}')"
        sql = re.sub(r"strftime\s*\(\s*['\"]([^'\"]+)['\"]\s*,\s*([^)]+)\s*\)", replace_strftime, sql, flags=re.IGNORECASE)

        return sql
    
    def _probe_sql_error(self, sql: str) -> str:
        """试执行 SQL（LIMIT 1），返回数据库报错文本；可执行则返回空串"""
        if not sql:
            return 'SQL 为空'
        test_sql = sql.rstrip(';').strip()
        test_sql = re.sub(r'\s+LIMIT\s+\d+\s*$', '', test_sql, flags=re.IGNORECASE)
        test_sql += ' LIMIT 1'
        try:
            with self.db.connect_business() as conn:
                conn.execute(test_sql)
            return ''
        except Exception as e:
            return str(e)

    # ---------- 两段式工作流：none 草稿 → 执行 → low 档审计（2026-08-15）----------
    def _probe_sql_result(self, sql: str, limit: int = 5):
        """试执行 SQL 并取结果证据：返回 (row_count, sample_rows, headers, error)。"""
        try:
            inner = sql.strip().rstrip(';')
            with self.db.connect_business() as conn:
                cur = conn.execute(f'SELECT * FROM ({inner}) AS _t LIMIT {int(limit)}')
                rows = cur.fetchall()
                headers = [d[0] for d in cur.description] if cur.description else []
                cur2 = conn.execute(f'SELECT COUNT(*) FROM ({inner}) AS _t')
                total = cur2.fetchone()[0]
            return total, [list(r) for r in rows], headers, None
        except Exception as e:
            return None, None, None, str(e)

    def _audit_sql_with_llm(self, user_question: str, sql: str, probe, columns_context: str,
                            join_hint: str = '') -> Optional[Dict]:
        """low 档合理性审计：业务原问题 + SQL + 执行结果/报错 → {"pass":bool, "reason", "sql"?}。

        审计模型走 GEN_AUDIT_EFFORT（默认 low）思考档；失败返回 None（调用方按草稿放行）。"""
        total, sample, headers, err = probe
        if err is None:
            exec_text = f"执行成功，返回 {total} 行。列：{headers}；样例（至多5行）：{json.dumps(sample, ensure_ascii=False, default=str)[:600]}"
        else:
            exec_text = f"执行报错：{err}"
        prompt = f"""你是电力营销数据仓库的 SQL 审计专家。审计下面这条由快速模型生成的 SQL 是否正确回答了业务问题。

【业务问题】
{user_question}

【待审计 SQL】
{sql}

【执行情况】
{exec_text}

【已定位表字段参考】
{columns_context[:3000]}
{join_hint[:600] if join_hint else ''}

【审计要点】
1. 筛选条件与问题一致（时间、分类、状态等无遗漏、无捏造）；条件值形态正确（存编码字段必须用编码值）
2. 主业务对象的编号、名称、所属单位已保留在 SELECT；对比/环比题的双侧度量与比例列齐全
3. JOIN 策略正确：维表/弱关联/仅取属性的关联用 LEFT JOIN（防止主表数据丢失）；JOIN 条件字段合法
4. 聚合必有 GROUP BY 且分组字段在 SELECT 中；日期函数为 MySQL 方言（DATE_FORMAT）
5. 若执行为空结果，判断是条件过严/SQL 逻辑问题还是数据本身无匹配

【输出】只输出 JSON，不要任何其他内容：
- 通过：{{"pass": true}}
- 不通过：{{"pass": false, "reason": "问题简述", "sql": "修正后的完整可执行 SQL"}}
推理从简，直接给结论。"""
        try:
            result = self._call_llm(prompt, user_question=user_question, max_tokens=6000,
                                    thinking=getattr(config, 'GEN_AUDIT_THINKING', False),
                                    effort=getattr(config, 'GEN_AUDIT_EFFORT', 'low'))
            content = result.get('content', '')
            m = re.search(r'\{.*\}', content, re.S)
            if not m:
                return None
            data = json.loads(m.group(0))
            if 'pass' not in data:
                return None
            return data
        except Exception as e:
            print(f"[WARN] SQL 审计调用失败: {e}", flush=True)
            return None

    def _audit_stage(self, user_question: str, sql: str, columns_context: str, join_hint: str,
                     think, timers: Dict):
        """两段式审计阶段：执行草稿 → low 档审计 → 必要时采纳修正版（修正版须可执行）。

        返回 (final_sql, exec_error, note)：exec_error 为 None 表示最终 SQL 可执行；
        note 取值 ''（草稿通过/审计不可用）/ 'audited'（采纳审计修正）。"""
        t0 = time.perf_counter()
        total, sample, headers, err = self._probe_sql_result(sql)
        think({'kind': 'audit_llm', 'phase': 'start',
               'exec_ok': err is None, 'row_count': total, 'error': (err or '')[:200]})
        verdict = self._audit_sql_with_llm(user_question, sql, (total, sample, headers, err),
                                           columns_context, join_hint)
        timers['audit_llm'] = (time.perf_counter() - t0) * 1000
        if not verdict:
            # 审计不可用：草稿可执行则放行，不可执行交后续修复通道
            think({'kind': 'audit_llm', 'phase': 'done', 'verdict': 'unavailable',
                   'ms': int(timers['audit_llm'])})
            return sql, err, ''
        if verdict.get('pass'):
            think({'kind': 'audit_llm', 'phase': 'done', 'verdict': 'pass',
                   'ms': int(timers['audit_llm'])})
            return sql, err, ''
        fixed_raw = verdict.get('sql') or ''
        reason = (verdict.get('reason') or '')[:200]
        if not fixed_raw.strip():
            think({'kind': 'audit_llm', 'phase': 'done', 'verdict': 'fail_nosql', 'reason': reason,
                   'ms': int(timers['audit_llm'])})
            return sql, err, ''
        # 审计修正版：清洗 + 校验 + 试执行，全部通过才采纳
        fixed = self._translate_code_value_literals(
            self._normalize_sql_dialect(self._extract_sql(fixed_raw)))
        valid = (fixed and self._validate_sql(fixed) and not self._is_sentinel_sql(fixed)
                 and all(t in set(self.schema_loader.get_table_names())
                         for t in self._extract_tables_from_sql(fixed)))
        if valid:
            _, _, _, err2 = self._probe_sql_result(fixed)
            if err2 is None:
                think({'kind': 'audit_llm', 'phase': 'done', 'verdict': 'fixed', 'reason': reason,
                       'ms': int(timers['audit_llm'])})
                return fixed, None, 'audited'
            think({'kind': 'audit_llm', 'phase': 'done', 'verdict': 'fix_still_broken',
                   'reason': (err2 or '')[:200], 'ms': int(timers['audit_llm'])})
        else:
            think({'kind': 'audit_llm', 'phase': 'done', 'verdict': 'fix_invalid',
                   'reason': reason, 'ms': int(timers['audit_llm'])})
        return sql, err, ''

    def _repair_sql_with_llm(self, user_question: str, bad_sql: str, error: str,
                             columns_context: str, join_hint: str = '', pattern_hint: str = '') -> Optional[str]:
        """快速修复：只把失败 SQL + 数据库报错 + 业务问题（+已定位字段）回喂 LLM 定点修正，
        小 prompt、快速模式，避免整轮重新生成。返回修复后的 SQL（未执行校验，调用方负责），
        无法修复返回 None。pattern_hint（R3）：定位表匹配的模式骨架，作结构参照。"""
        if not bad_sql:
            return None
        pattern_section = ''
        if pattern_hint:
            # 截断保护：修复 prompt 保持小体量（个别 CTE 骨架可超 1KB）
            pattern_section = ('\n【参考模式骨架】（结构参照，{{}} 槽位须替换为本题实际条件值）\n'
                               + pattern_hint[:800] + '\n')
        prompt = f"""你是电力营销数据仓库的 SQL 修复专家。下面这条 SQL 执行报错，请修复后只输出一行可执行的 SELECT（或 WITH...SELECT），不要 Markdown 代码块，不要解释。

【业务问题】
{user_question}

【执行报错的 SQL】
{bad_sql}

【数据库报错】
{error}

【修复要求】
1. 优先按报错修正字段名/表名/JOIN 条件，保持原查询意图不变，不要重写整句结构。
2. 只能使用下方 Schema 中出现的表名和字段名，禁止编造。
3. WHERE 中等值筛选的维度属性字段须同时出现在 SELECT 中；条件值形态服从码值标注（存编码写编码）。
4. JOIN 策略：维表/弱关联/仅取属性的关联用 LEFT JOIN（防止关联表无匹配时主表数据丢失）；仅业务语义确实要求两侧必须匹配时才用 INNER JOIN。
5. 推理从简，直接输出修复后的 SQL。

{columns_context}
{join_hint}
{pattern_section}
【输出】一行修复后的 SQL："""
        try:
            result = self._call_llm(prompt, user_question=user_question, max_tokens=4000, thinking=False,
                                    model_override=getattr(config, 'AUX_MODEL', None))
            sql = self._extract_sql(result['content'])
            sql = self._normalize_sql_dialect(sql)
            sql = self._translate_code_value_literals(sql)
            if not sql or self._is_sentinel_sql(sql):
                return None
            allowed = ['SELECT', 'WITH']
            if not any(sql.strip().upper().startswith(p) for p in allowed):
                return None
            tables_in_sql = self._extract_tables_from_sql(sql)
            valid_tables = set(self.schema_loader.get_table_names())
            if any(t not in valid_tables for t in tables_in_sql):
                return None
            return sql
        except Exception as e:
            print(f"[WARN] SQL 快速修复调用失败: {e}", flush=True)
            return None

    def _validate_sql(self, sql: str) -> bool:
        """尝试执行 SQL（LIMIT 1）验证语法和字段正确性"""
        if not sql:
            return False
        sql_upper = sql.strip().upper()
        if not any(sql_upper.startswith(p) for p in ['SELECT', 'WITH']):
            return False
        
        # 安全检查
        forbidden = ['DROP', 'DELETE', 'UPDATE', 'INSERT', 'ALTER', 'CREATE', 'TRUNCATE']
        for kw in forbidden:
            if kw in sql_upper:
                return False
        
        test_sql = sql.rstrip(';').strip()
        # 移除已有 LIMIT，统一加 LIMIT 1
        test_sql = re.sub(r'\s+LIMIT\s+\d+\s*$', '', test_sql, flags=re.IGNORECASE)
        test_sql += ' LIMIT 1'
        
        try:
            with self.db.connect_business() as conn:
                conn.execute(test_sql)
            return True
        except Exception:
            return False

    def _extract_aggregation_pattern(self, sql: str) -> str:
        """提取SQL中的聚合模式（如 SUM/COUNT/AVG + GROUP BY）"""
        patterns = []
        agg_funcs = re.findall(r'(SUM|COUNT|AVG|MAX|MIN|COUNT\s*\(\s*DISTINCT)\s*\(', sql, re.IGNORECASE)
        if agg_funcs:
            patterns.append(f"聚合函数: {', '.join(set(f.upper() for f in agg_funcs))}")
        group_by = re.search(r'GROUP\s+BY\s+([^,\s]+)', sql, re.IGNORECASE)
        if group_by:
            patterns.append(f"分组字段: {group_by.group(1).strip()}")
        return '；'.join(patterns) if patterns else ''
    
    def _extract_filter_pattern(self, sql: str) -> str:
        """提取SQL中的过滤条件模式"""
        patterns = []
        # WHERE条件
        where_match = re.search(r'WHERE\s+(.+?)(?:GROUP|ORDER|LIMIT|$)', sql, re.IGNORECASE | re.DOTALL)
        if where_match:
            where_clause = where_match.group(1).strip()
            # 简化显示
            if 'DATE_FORMAT' in where_clause.upper() or 'YEAR' in where_clause.upper() or 'MONTH' in where_clause.upper():
                patterns.append('日期范围过滤')
            if 'LIKE' in where_clause.upper():
                patterns.append('字符串模糊匹配')
            if '=' in where_clause and not patterns:
                patterns.append('等值匹配')
        # HAVING条件
        having_match = re.search(r'HAVING\s+(.+?)(?:ORDER|LIMIT|$)', sql, re.IGNORECASE | re.DOTALL)
        if having_match:
            patterns.append('聚合后过滤(HAVING)')
        return '；'.join(patterns) if patterns else ''
    
    def _extract_order_limit_pattern(self, sql: str) -> str:
        """提取排序和限制模式"""
        patterns = []
        order_match = re.search(r'ORDER\s+BY\s+(.+?)(?:LIMIT|$)', sql, re.IGNORECASE)
        if order_match:
            order_cols = order_match.group(1).strip()
            desc = 'DESC' in order_cols.upper()
            patterns.append(f"按 {order_cols.split()[0]} {'降序' if desc else '升序'}排序")
        limit_match = re.search(r'LIMIT\s+(\d+)', sql, re.IGNORECASE)
        if limit_match:
            patterns.append(f"取前 {limit_match.group(1)} 条")
        return '；'.join(patterns) if patterns else ''
