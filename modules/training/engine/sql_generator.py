# -*- coding: utf-8 -*-
"""LLM SQL 生成器：调用 KIMI API 生成 SQL（v2.4 安全版 / v3.0 意图版）"""
import re
import time
import threading
from typing import Dict, List, Optional
import config
from core.schema_loader import SchemaLoader
from core.rag_retriever import RAGRetriever
from core.database import DatabaseManager
from core.llm_transport import call_llm as _transport_call_llm
from modules.training.engine.sql_sanitize import SqlSanitizer
from modules.training.engine.audit_repair import AuditRepair
from modules.training.engine.table_locator import TableLocator
from modules.training.engine.prompt_builder import PromptBuilder
from modules.training.engine.constants import PLACE_KEYWORDS as _PLACE_KEYWORDS

# P2：硬编码业务知识改从治理库读取（resources 层）；空表/异常时回退本文件代码常量，
# 两条路径读出内容一致（见 resources/providers/keyword_table_map.py、business_rule.py 头部注释）
from modules.resources.providers.keyword_table_map import get_keyword_table_map
from modules.resources.providers.business_rule import (
    get_family_synonyms, get_draft_filter_signals, get_analytical_keywords)

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
        self._san = SqlSanitizer(db=self.db, rag=self.rag_retriever,
                                 code_value_translations_fn=self._code_value_translations)
        self._ar = AuditRepair(call_llm=self._call_llm, san=self._san,
                               table_names_fn=self.schema_loader.get_table_names)
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
        
        self._loc = TableLocator(schema_src_fn=self._schema_src, family_synonyms_fn=self._family_synonyms,
                                 onto=self._onto, call_llm=self._call_llm, wf=self._wf,
                                 table_names_fn=self.schema_loader.get_table_names)
        self._pb = PromptBuilder(wf=self._wf, db=self.db, san=self._san,
                                 schema_src_fn=self._schema_src, rag=self.rag_retriever,
                                 retrieve_code_values_fn=self._retrieve_code_values,
                                 concept_map_fn=self._concept_map,
                                 collapse_series_fn=TableLocator.collapse_series)

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
        return SqlSanitizer.clean_aliases(sql)

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
        ctx = {}  # 本次生成的 per-call 上下文（定位结果/列摘要/码值命中），替代原 self._last_* 实例状态，并发安全
        if self.use_intent:
            try:
                intent_result = self._generate_with_intent(
                    user_question=user_question,
                    review_feedback=review_feedback,
                    progress_cb=progress_cb,
                    thinking_cb=thinking_cb,
                    ctx=ctx
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
        last_located = ctx.get('located')
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
                'generation_mode': 'llm_v24',
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
            exec_err = self._exec_probe(sql)
            error_msg = (f'SQL 执行校验失败: {exec_err}' if exec_err
                         else 'LLM 生成的 SQL 前缀或语法校验失败')
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
        thinking_cb=None,
        ctx: Optional[Dict] = None
    ) -> Dict:
        """v3.0 意图驱动生成：意图解析 + 多路 RAG + LLM 生成"""
        ctx = ctx if ctx is not None else {}
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
        located_tables = self._merge_located_tables(user_question, intent, llm_tables, rule_tables, draft_tables, ctx)
        _think({'kind': 'tables', 'tables': located_tables,
                'sources': {'rule': rule_tables or [], 'llm': llm_tables or [], 'draft': draft_tables or []}})
        columns_context = self._build_columns_context(located_tables, user_question, ctx)
        _think({'kind': 'columns', 'summary': ctx.get('columns_summary', [])})
        # 最短业务 JOIN 路径（图谱 BFS 能力复用，禁 mgt_org 中转）；独立成段，便于超预算时单独裁撤
        join_path_hint = self._build_join_path_hint(located_tables)
        code_value_context = self._build_code_value_context(user_question, located_tables, ctx)
        _think({'kind': 'code_values', 'items': ctx.get('code_value_hits', [])})

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
        else:
            t0 = time.perf_counter()
            sql = self._clean_sql_pipeline(raw_content)
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
            audit_failed = False
            if structural_ok and (llm_sql_valid or audit_on):
                # ===== 两段式工作流（2026-08-15）：none 草稿 → 执行 → low 档合理性审计 =====
                gen_mode = 'schema_llm_v3'
                if audit_on:
                    sql, audit_err, audit_note = self._audit_stage(
                        user_question, sql, columns_context, join_path_hint, _think, timers)
                    if audit_err is not None:
                        # 草稿及审计修正均不可执行：转入修复通道
                        last_error = f'SQL 执行失败: {audit_err}'
                        audit_failed = True
                    elif audit_note:
                        gen_mode = 'schema_llm_v3_audited'
                        tables_in_sql = self._extract_tables_from_sql(sql)
                if not audit_failed:
                    timers['total'] = (time.perf_counter() - t_start) * 1000
                    print(f"[SQLGen timing] {timers}, prompt_len={len(prompt)}", flush=True)
                    return {
                        'sql': sql,
                        'explanation': explanation or f"基于 Schema 语义 + LLM 生成：{intent.get('question_type', '查询')}",
                        'tables_involved': tables_in_sql,
                        'located_tables': located_tables,
                        'code_value_hits': ctx.get('code_value_hits', []),
                        'success': True,
                        'raw_response': raw_content,
                        'prompt': call_prompt,
                        'intent': intent,
                        'error_records_matched': len(error_records) if error_records else 0,
                        'generation_mode': gen_mode,
                        'timers': timers
                    }
            if not audit_failed:
                # 记录失败原因，供修复通道与最终错误信息使用
                if has_invalid_table:
                    invalid = [t for t in tables_in_sql if t not in valid_tables]
                    last_error = f"使用了不存在的表名: {invalid}"
                elif is_sentinel:
                    last_error = "模型声明无法回答（返回了哨兵 SQL）。请基于给定 Schema 直接生成真实查询，不要声明无法回答"
                elif not sql:
                    last_error = "没有生成有效的 SELECT 语句"
                else:
                    exec_err = self._exec_probe(sql)
                    if exec_err:
                        last_error = f"SQL 执行失败: {exec_err}"
        
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
                    'code_value_hits': ctx.get('code_value_hits', []),
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
        return self._pb.build_pattern_context(user_question, located_tables)

    def _locate_tables_with_llm(self, user_question: str, fallback_tables: List[str]) -> List[str]:
        return self._loc.locate_tables_with_llm(user_question, fallback_tables)

    # 草稿直出守门的过滤信号词：问题含这些词通常需要 WHERE 过滤；规则解析未抓到时不许直出
    # （P2：已入库 business_rules.draft_guard，消费点经 get_draft_filter_signals() 读取，本常量为空表回退值）
    _DRAFT_FILTER_SIGNALS = ('状态', '处于', '标志', '类别', '分类', '等级', '类型', '渠道', '原因', '方式') + _PLACE_KEYWORDS

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

    def _merge_located_tables(self, user_question: str, intent: Dict, llm_tables: List[str],
                              rule_tables: List[str], draft_tables: List[str], ctx: Dict,
                              max_tables: int = 8) -> List[str]:
        return self._loc.merge_located_tables(user_question, intent, llm_tables, rule_tables, draft_tables, ctx, max_tables)

    def _disambiguate_sibling_tables(self, tables: List[str], user_question: str,
                                     protect: set = None) -> List[str]:
        return self._loc.disambiguate_sibling_tables(tables, user_question, protect)

    def _build_join_path_hint(self, tables: List[str], max_paths: int = 4) -> str:
        return self._loc.build_join_path_hint(tables, max_paths)

    @staticmethod
    def _collapse_series(cols: List[Dict]) -> List[tuple]:
        return TableLocator.collapse_series(cols)

    def _build_columns_context(self, tables: List[str], user_question: str = '',
                               ctx: Optional[Dict] = None) -> str:
        return self._pb.build_columns_context(tables, user_question, ctx)

    def _translate_code_value_literals(self, sql: str) -> str:
        return self._san.translate_code_value_literals(sql)

    def _complete_name_literals(self, sql: str) -> str:
        return self._san.complete_name_literals(sql)

    def _build_code_value_context(self, user_question: str, tables: List[str],
                                  ctx: Optional[Dict] = None) -> str:
        return self._pb.build_code_value_context(user_question, tables, ctx)

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
        return self._pb.build_v3_prompt(user_question, intent, pattern_context, error_context, draft_sql, review_feedback, previous_error, org_hints, qa_context, columns_context, code_value_context, join_hint)

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
        place_keywords = list(_PLACE_KEYWORDS) + ['公司', '供电', '管理单位', '地市', '区县', '城市', '地区']
        for pk in place_keywords:
            if pk in user_question:
                if 'dim_cst_mgt_org' not in all_tables:
                    all_tables.append('dim_cst_mgt_org')
                break
        
        return all_tables[:4]  # 增加到最多4个表
    
    def _build_qa_context(self, retrieved_pairs: List[Dict]) -> str:
        return self._pb.build_qa_context(retrieved_pairs)

    def _extract_condition_fields(self, sql: str) -> str:
        return self._san.extract_condition_fields(sql)

    def _build_prompt(
        self,
        user_question: str,
        schema_context: str,
        qa_context: str,
        related_tables: List[str],
        error_context: Optional[str] = None,
        review_feedback: Optional[str] = None
    ) -> str:
        return self._pb.build_prompt(user_question, schema_context, qa_context, related_tables, error_context, review_feedback)

    def _call_llm(self, prompt: str, user_question: str = None, max_tokens: int = 8000, thinking: Optional[bool] = None, timeout: Optional[int] = None, stream_cb=None, model_override: Optional[str] = None, effort: Optional[str] = None) -> Dict:
        """LLM 调用薄封装：传输细节（会话/流式/降级/重试）已下沉 core/llm_transport，签名与行为不变"""
        return _transport_call_llm(
            prompt, user_question=user_question, max_tokens=max_tokens, thinking=thinking,
            default_thinking=self._thinking, timeout=timeout, stream_cb=stream_cb,
            model_override=model_override, effort=effort,
            usage_cb=self._accumulate_usage, table_names_fn=self.schema_loader.get_table_names)

    @staticmethod
    def _is_sentinel_sql(sql: str) -> bool:
        return SqlSanitizer.is_sentinel_sql(sql)

    def _extract_sql(self, content: str) -> str:
        return self._san.extract_sql(content)

    def _extract_explanation(self, content: str) -> str:
        return self._san.extract_explanation(content)

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
                'generation_mode': 'qa_fallback',
                'source_qa_id': best_pair['id']
            }
        return None
    
    def _build_org_hints(self, user_question: str) -> str:
        return self._pb.build_org_hints(user_question)

    def _build_error_context(self, error_records: List[Dict]) -> str:
        return self._pb.build_error_context(error_records)

    def _extract_tables_from_sql(self, sql: str) -> List[str]:
        return self._san.extract_tables_from_sql(sql)

    def _normalize_sql_dialect(self, sql: str) -> str:
        return self._san.normalize_sql_dialect(sql)

    def _clean_sql_pipeline(self, raw_content: str) -> str:
        return self._san.clean_sql_pipeline(raw_content)

    def _exec_probe(self, sql: str) -> str:
        return self._san.exec_probe(sql)

    def _probe_sql_error(self, sql: str) -> str:
        return self._san.probe_sql_error(sql)

    def _probe_sql_result(self, sql: str, limit: int = 5):
        return self._san.probe_sql_result(sql, limit)

    def _audit_sql_with_llm(self, user_question: str, sql: str, probe, columns_context: str,
                            join_hint: str = '') -> Optional[Dict]:
        return self._ar.audit_sql_with_llm(user_question, sql, probe, columns_context, join_hint)

    def _audit_stage(self, user_question: str, sql: str, columns_context: str, join_hint: str,
                     think, timers: Dict):
        return self._ar.audit_stage(user_question, sql, columns_context, join_hint, think, timers)

    def _repair_sql_with_llm(self, user_question: str, bad_sql: str, error: str,
                             columns_context: str, join_hint: str = '', pattern_hint: str = '') -> Optional[str]:
        return self._ar.repair_sql_with_llm(user_question, bad_sql, error, columns_context, join_hint, pattern_hint)

    def _validate_sql(self, sql: str) -> bool:
        return self._san.validate_sql(sql)
