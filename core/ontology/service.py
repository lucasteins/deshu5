# -*- coding: utf-8 -*-
"""本体服务：问数模块的统一本体访问门面（进程级单例）。

职责：
1. 从 marketing_ontology 库加载已生效版本（内存缓存，不临时抽取）
2. 向问数模块输出与 legacy 接口同形状的查询：
   - Schema 面对齐 SchemaPreloader：get_table_names/get_table_comment/get_columns/get_relationships
   - 概念面对齐 keyword_table_map provider：get_concept_table_map(scope)
   - 码值面对齐 RAGRetriever：retrieve_code_values/get_code_value_translations
   - 同义词对齐 business_rule：get_family_synonyms
3. 生命周期：首次引导（空库直接构建 v1 生效）、漂移检测、提案生成、审批执行
"""
import threading
from typing import Dict, List, Optional, Tuple

import config
from core.database import DatabaseManager
from core.ontology.builder import OntologyBuilder
from core.ontology.fingerprint import compute_base_fingerprint
from core.ontology.model import Ontology, diff_ontologies
from core.ontology.store import OntologyStore


class OntologyService:
    _instance: Optional['OntologyService'] = None
    _instance_lock = threading.Lock()

    def __init__(self):
        self.db = DatabaseManager()
        self.store = OntologyStore(self.db)
        self.builder = OntologyBuilder(self.db)
        self._ont: Optional[Ontology] = None
        self._load_lock = threading.Lock()
        # 码值索引（由枚举快照派生，结构与 RAGRetriever._cv_* 一致）
        self._cv_domains: Dict[str, dict] = {}
        self._cv_items: Dict[str, list] = {}
        self._cv_col_map: Dict[str, list] = {}
        self._cv_form_map: Dict[Tuple[str, str], str] = {}

    @classmethod
    def get_instance(cls) -> 'OntologyService':
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    # ==================== 生命周期 ====================

    def invalidate(self):
        """缓存失效：下次访问从本体库重载（切档/审批通过后调用）。"""
        with self._load_lock:
            self._ont = None

    def available(self) -> bool:
        """本体是否可用（已加载生效版本）。供 knowledge.source 兜底判断。"""
        if not getattr(config, 'ONTOLOGY_ENABLED', True):
            return False
        try:
            self._ensure_loaded()
            return self._ont is not None
        except Exception:
            return False

    def _ensure_loaded(self) -> Optional[Ontology]:
        if self._ont is not None:
            return self._ont
        with self._load_lock:
            if self._ont is not None:
                return self._ont
            self.store.init_tables()
            ont = self.store.load_active()
            if ont is None:
                ont = self._bootstrap()
            if ont is not None:
                self._build_cv_index(ont)
            self._ont = ont
            return self._ont

    def _bootstrap(self) -> Optional[Ontology]:
        """首次引导：本体库为空时直接构建 v1 生效（无旧版本可比，不构成"变更"）。"""
        try:
            fp = compute_base_fingerprint(self.db)
            ont = self.builder.build(base_fingerprint=fp,
                                     entity_defs=self._entity_defs())
            self.store.save_active(ont)
            print(f'[Ontology] 首次引导完成：v{ont.version} '
                  f'（类 {len(ont.classes)} / 属性 {len(ont.properties)} / '
                  f'关系 {len(ont.relations)} / 枚举 {len(ont.enumerations)} / '
                  f'实体 {len(ont.entities)}）', flush=True)
            return ont
        except Exception as e:
            print(f'[WARN] 本体首次引导失败（knowledge.source=ontology 将回退 legacy）: {e}',
                  flush=True)
            return None

    def _entity_defs(self) -> List[dict]:
        """实体映射定义：库中优先；空表时用默认映射播种。"""
        self.store.init_tables()
        defs = self.store.get_entity_defs()
        if defs:
            return defs
        try:
            from core.ontology.entity_map import build_entities
            from core.schema_preloader import SchemaPreloader
            pre = SchemaPreloader.get_instance()
            entity_dicts, _ = build_entities(
                pre.get_table_names(), table_comment=pre.get_table_comment)
            seeded = list(entity_dicts.values())
            self.store.seed_entity_defs(seeded)
            return seeded
        except Exception as e:
            print(f'[WARN] 实体映射定义播种失败（回退默认映射）: {e}', flush=True)
            return []

    # ==================== 漂移检测与提案 ====================

    def drift_check(self, auto_propose: bool = True) -> dict:
        """跑一次漂移检测。返回 {drift, fingerprint, active_version, proposal_id?, diff?}。"""
        self._ensure_loaded()
        fp = compute_base_fingerprint(self.db)
        meta = self.store.get_meta()
        result = {'drift': False, 'fingerprint': fp,
                  'active_version': meta['version'] if meta else None}
        if meta and meta['base_fingerprint'] == fp:
            return result
        result['drift'] = True
        if not auto_propose or meta is None:
            return result
        pending = self.store.has_pending(fp)
        if pending:
            result['proposal_id'] = pending
            return result
        # 同指纹曾被驳回 → 不重复自动生成（只能手动 rebuild）
        if self._rejected(fp):
            result['note'] = '同指纹提案已被驳回，需手动重建'
            return result
        new_ont = self.builder.build(base_fingerprint=fp,
                                     entity_defs=self._entity_defs())
        diff = diff_ontologies(self._ont, new_ont) if self._ont else {}
        pid = self.store.create_proposal(new_ont, diff)
        result.update({'proposal_id': pid, 'diff': diff})
        print(f'[Ontology] 检测到底座结构变化，已生成提案 #{pid}', flush=True)
        return result

    def _rejected(self, fingerprint: str) -> bool:
        with self.db.connect_ontology() as conn:
            row = conn.execute(
                "SELECT 1 FROM ontology_proposals "
                "WHERE base_fingerprint = ? AND status = 'rejected' LIMIT 1",
                (fingerprint,)).fetchone()
        return row is not None

    def rebuild_proposal(self) -> dict:
        """手动全量重建：无论指纹是否变化都生成提案（仍走审批）。"""
        self._ensure_loaded()
        fp = compute_base_fingerprint(self.db)
        new_ont = self.builder.build(base_fingerprint=fp,
                                     entity_defs=self._entity_defs())
        diff = diff_ontologies(self._ont, new_ont) if self._ont else {}
        pid = self.store.create_proposal(new_ont, diff)
        return {'proposal_id': pid, 'diff': diff, 'fingerprint': fp}

    def approve(self, pid: int) -> Optional[Ontology]:
        ont = self.store.approve(pid)
        if ont is not None:
            self.invalidate()
            self._ensure_loaded()
            print(f'[Ontology] 提案 #{pid} 已批准，生效版本 v{ont.version}', flush=True)
        return ont

    def reject(self, pid: int) -> bool:
        return self.store.reject(pid)

    # ==================== Schema 面（对齐 SchemaPreloader） ====================

    def get_table_names(self) -> List[str]:
        ont = self._ensure_loaded()
        return sorted(ont.classes.keys()) if ont else []

    def get_table_comment(self, table: str) -> str:
        ont = self._ensure_loaded()
        c = ont.classes.get(table) if ont else None
        return c.label if c else ''

    def get_columns(self, table: str) -> List[Dict]:
        ont = self._ensure_loaded()
        if not ont:
            return []
        return [{'name': p.name, 'type': p.data_type, 'comment': p.label,
                 'pk': 1 if p.is_pk else 0}
                for p in ont.properties if p.class_name == table]

    def get_relationships(self, from_table: str = None, to_table: str = None) -> List[Dict]:
        ont = self._ensure_loaded()
        if not ont:
            return []
        out = []
        for r in ont.relations:
            if from_table and from_table not in (r.from_class, r.to_class):
                continue
            if to_table and to_table not in (r.from_class, r.to_class):
                continue
            out.append({'path': [r.from_class, r.to_class],
                        'join_conditions': list(r.join_conditions),
                        'business_scenarios': list(r.business_scenarios),
                        'source': r.source})
        return out

    def get_family_synonyms(self) -> dict:
        """同族表消歧同义词 {差异词: tuple(等价表达)}（对齐 business_rule.get_family_synonyms）。"""
        ont = self._ensure_loaded()
        if not ont:
            return {}
        return {k: tuple(v) for k, v in ont.synonym_groups.items()}

    # ==================== 概念面（对齐 keyword_table_map provider） ====================

    def get_concept_table_map(self, scope: str = 'intent') -> Dict[str, List[str]]:
        """{概念词: [表, ...]}。scope 参数仅为接口兼容（全域共用同一全集）。"""
        ont = self._ensure_loaded()
        if not ont:
            return {}
        return {c: list(oc.maps_to) for c, oc in ont.concepts.items() if oc.maps_to}

    # ==================== 码值面（对齐 RAGRetriever） ====================

    def _build_cv_index(self, ont: Ontology):
        self._cv_domains = {}
        self._cv_items = {}
        self._cv_col_map = {}
        self._cv_form_map = {}
        for code_name, enum in ont.enumerations.items():
            self._cv_domains[code_name] = {
                'cn_name': enum.cn_name, 'domain': enum.domain_l2 or enum.domain_l1}
            items = [(it.get('code', ''), it.get('name', ''))
                     for it in enum.items if it.get('name')]
            if items:
                self._cv_items[code_name] = items
            for ref in enum.column_refs:
                t, c = ref.get('table', ''), ref.get('column', '')
                if not t or not c:
                    continue
                self._cv_col_map.setdefault(code_name, []).append((t, c))
                if ref.get('form'):
                    self._cv_form_map[(t, c)] = ref['form']

    def retrieve_code_values(self, question: str, tables: List[str],
                             per_domain: int = 8, max_domains: int = 8) -> List[Dict]:
        """与 RAGRetriever.retrieve_code_values 同形状，索引来自本体枚举快照。"""
        self._ensure_loaded()
        if not self._cv_domains:
            return []
        from core.rag_retriever import match_code_value_index, extract_keywords
        return match_code_value_index(
            self._cv_domains, self._cv_items, self._cv_col_map, self._cv_form_map,
            question, tables, per_domain=per_domain, max_domains=max_domains,
            tokenize=extract_keywords)

    def get_code_value_translations(self) -> List[Tuple[str, str, Dict[str, str]]]:
        """存编码列的 名称→编码 翻译表（与 RAGRetriever 同形状）。"""
        self._ensure_loaded()
        col_to_domain = {}
        for code_name, cols in self._cv_col_map.items():
            for tc in cols:
                col_to_domain.setdefault(tc, code_name)
        result = []
        for (t, c), form in self._cv_form_map.items():
            if form != '编码':
                continue
            dn = col_to_domain.get((t, c))
            if not dn:
                continue
            n2c = {name: code for code, name in self._cv_items.get(dn, []) if name and code}
            if n2c:
                result.append((t, c, n2c))
        return result

    # ==================== 实体面（精炼层：主数据/业务数据/统计报表） ====================

    def get_entities(self, layer: str = None) -> List[dict]:
        """实体列表（可选 layer 过滤），附成员表计数。"""
        ont = self._ensure_loaded()
        if not ont:
            return []
        out = []
        for e in sorted(ont.entities.values(), key=lambda x: (x.layer, x.name)):
            if layer and e.layer != layer:
                continue
            d = e.to_dict()
            d['member_count'] = len(e.member_tables)
            out.append(d)
        return out

    def get_entity(self, name: str) -> Optional[dict]:
        ont = self._ensure_loaded()
        e = ont.entities.get(name) if ont else None
        return e.to_dict() if e else None

    def table_to_entity(self) -> Dict[str, str]:
        """物理表 → 实体名 反查索引。"""
        ont = self._ensure_loaded()
        out = {}
        if ont:
            for e in ont.entities.values():
                for t in e.member_tables:
                    out[t] = e.name
        return out

    def get_entity_relations(self) -> List[dict]:
        """实体间关系（由成员表级关系聚合推导）。"""
        ont = self._ensure_loaded()
        if not ont:
            return []
        from core.ontology.entity_map import derive_entity_relations
        rel_docs = [{'path': [r.from_class, r.to_class],
                     'join_conditions': r.join_conditions,
                     'business_scenarios': r.business_scenarios,
                     'source': r.source} for r in ont.relations]
        return derive_entity_relations(rel_docs, self.table_to_entity())

    # ==================== 报表层优先召回（省/市/县三级统计问题） ====================

    def is_statistical_question(self, question: str) -> bool:
        """问题是否含 省/市/县三级统计语义（报表层优先的触发条件）。"""
        if not question:
            return False
        from core.ontology.entity_map import STATISTICAL_TRIGGERS
        return any(w in question for w in STATISTICAL_TRIGGERS)

    def locate_report_tables(self, question: str) -> List[str]:
        """报表层定位：统计语义问题在 report 层实体中检索命中表。

        命中通道：① 概念映射中指向 report 表的概念词命中问题；
        ② report 实体的中文名/表名与问题子串互命中。
        返回物理表名列表（可为空，空则由明细层汇总兜底）。
        """
        ont = self._ensure_loaded()
        if not ont or not self.is_statistical_question(question):
            return []
        report_tables = set()
        for e in ont.entities.values():
            if e.layer == 'report':
                report_tables.update(e.member_tables)
        if not report_tables:
            return []

        hits = set()
        # ① 概念映射：概念词命中问题且其映射表含 report 表
        for c in ont.concepts.values():
            if c.concept and len(c.concept) >= 2 and c.concept in question:
                for t in c.maps_to:
                    if t in report_tables:
                        hits.add(t)
        # ② 实体中文名/表注释命中（生产库注释齐；staging 注释弱，主要靠通道①）
        for e in ont.entities.values():
            if e.layer != 'report':
                continue
            if e.label and len(e.label) >= 2 and e.label in question:
                hits.update(e.member_tables)
                continue
            for t in e.member_tables:
                label = ont.classes.get(t).label if t in ont.classes else ''
                if label and len(label) >= 2 and label in question:
                    hits.add(t)
        return sorted(hits)
