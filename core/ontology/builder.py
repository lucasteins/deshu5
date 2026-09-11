# -*- coding: utf-8 -*-
"""本体提炼器：从基础数据层构建本体快照。

素材来源（全部为只读）：
- 表/列/关系：SchemaPreloader 单例（information_schema ∪ 治理关系文档，已完成双源合并）
- 码值：治理库 code_values / code_value_items / code_value_column_form
- 业务概念：modules.resources.providers.keyword_table_map（空表回退 intent_parser 常量）
- 同义词组：modules.resources.providers.business_rule.get_family_synonyms

注意依赖方向：core/ontology → modules.resources.providers 为函数级惰性 import
（沿用 core/knowledge_retriever.py 的既有惯例）。
"""
from datetime import datetime
from typing import Dict, List, Optional

from core.database import DatabaseManager
from core.ontology.model import (
    Ontology, OntologyClass, OntologyConcept, OntologyEntity, OntologyEnumeration,
    OntologyProperty, OntologyRelation, mysql_to_xsd, table_kind,
)


class OntologyBuilder:
    """从底座提炼本体快照。build() 每次全量构建（调用方负责与旧版 diff）。"""

    def __init__(self, db: DatabaseManager = None):
        self.db = db or DatabaseManager()

    def build(self, base_fingerprint: str = '', entity_defs: Optional[List[dict]] = None) -> Ontology:
        ont = Ontology(built_at=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                       base_fingerprint=base_fingerprint)
        self._build_schema(ont)
        self._build_enumerations(ont)
        self._build_concepts(ont)
        self._build_synonyms(ont)
        self._build_entities(ont, entity_defs)
        return ont

    # ==================== 表/列/关系 ====================

    def _build_schema(self, ont: Ontology):
        from core.schema_preloader import SchemaPreloader
        pre = SchemaPreloader.get_instance()

        for table in pre.get_table_names():
            comment = pre.get_table_comment(table) or ''
            ont.classes[table] = OntologyClass(
                name=table, label=comment, kind=table_kind(table), comment=comment)
            for col in pre.get_columns(table):
                ont.properties.append(OntologyProperty(
                    class_name=table,
                    name=col['name'],
                    label=col.get('comment') or '',
                    data_type=(col.get('type') or '').upper(),
                    xsd_type=mysql_to_xsd(col.get('type')),
                    is_pk=bool(col.get('pk')),
                ))

        for rel in pre.get_relationships():
            path = rel.get('path') or []
            if len(path) != 2:
                continue
            ont.relations.append(OntologyRelation(
                from_class=path[0], to_class=path[1],
                join_conditions=list(rel.get('join_conditions') or []),
                business_scenarios=list(rel.get('business_scenarios') or []),
                source=rel.get('source') or 'governance_doc',
            ))

    # ==================== 码值枚举 ====================

    def _build_enumerations(self, ont: Ontology):
        try:
            with self.db.connect_governance() as conn:
                for code_name, cn_name, l1, l2, l3 in conn.execute(
                        'SELECT code_name, code_cn_name, domain_l1, domain_l2, domain_l3 '
                        'FROM code_values ORDER BY code_name'):
                    ont.enumerations[code_name] = OntologyEnumeration(
                        code_name=code_name, cn_name=cn_name or '',
                        domain_l1=l1 or '', domain_l2=l2 or '', domain_l3=l3 or '')
                for code_name, item_code, item_name, sort_order in conn.execute(
                        'SELECT code_name, item_code, item_name, sort_order '
                        'FROM code_value_items ORDER BY code_name, sort_order'):
                    enum = ont.enumerations.get(code_name)
                    if enum is None or not item_name:
                        continue  # 孤儿明细（code_values 无定义）跳过，与 RAG 检索行为一致
                    enum.items.append({'code': str(item_code or ''),
                                       'name': str(item_name),
                                       'sort': int(sort_order or 0)})
                # 列落域 + 存储形态
                form_map = {}
                try:
                    for t, c, form in conn.execute(
                            'SELECT table_name, column_name, form FROM code_value_column_form'):
                        form_map[(t, c)] = form
                except Exception:
                    pass  # 形态表缺失不阻断
                # 列名 ∩ 码值域名 自动落列（与 RAGRetriever._load_code_value_index 同规则）
                for prop in ont.properties:
                    enum = ont.enumerations.get(prop.name)
                    if enum is not None:
                        enum.column_refs.append({
                            'table': prop.class_name, 'column': prop.name,
                            'form': form_map.get((prop.class_name, prop.name), '')})
        except Exception as e:
            print(f'[WARN] 本体提炼：码值枚举读取失败: {e}', flush=True)

    # ==================== 业务概念 ====================

    def _build_concepts(self, ont: Ontology):
        kw_map: Dict[str, List[str]] = {}
        try:
            from modules.resources.providers.keyword_table_map import get_keyword_table_map
            kw_map = get_keyword_table_map('intent') or {}
        except Exception as e:
            print(f'[WARN] 本体提炼：keyword_table_map 读取失败: {e}', flush=True)
        if not kw_map:
            try:
                from modules.training.engine.intent_parser import DEFAULT_CONCEPT_TO_TABLES
                kw_map = {k: list(v) for k, v in DEFAULT_CONCEPT_TO_TABLES.items()}
            except Exception:
                kw_map = {}
        for concept, tables in kw_map.items():
            if not concept:
                continue
            ont.concepts[concept] = OntologyConcept(
                concept=concept, maps_to=list(tables or []))

    # ==================== 同义词组 ====================

    def _build_synonyms(self, ont: Ontology):
        try:
            from modules.resources.providers.business_rule import get_family_synonyms
            groups = get_family_synonyms() or {}
            ont.synonym_groups = {k: list(v) for k, v in groups.items()}
        except Exception as e:
            print(f'[WARN] 本体提炼：同义词组读取失败: {e}', flush=True)

    # ==================== 业务实体层（精炼设计） ====================

    # 表名前缀 → 实体层（域标签聚合兜底用）
    _LAYER_BY_PREFIX = {'dim': 'master', 'dwd': 'business', 'dws': 'business', 'ads': 'report'}

    def _table_domain_info(self):
        """治理库业务域标签：{表名: (中文名, domain_l1, domain_l2)} 与 {二级域码: 中文名}。"""
        info, dom_names = {}, {}
        try:
            with self.db.connect_governance() as conn:
                for t, c, l1, l2 in conn.execute(
                        'SELECT table_name, table_comment, domain_l1, domain_l2 '
                        'FROM schema_table_docs'):
                    info[t] = (c or '', l1 or '', l2 or '')
                for code, name in conn.execute(
                        'SELECT domain_code, domain_name FROM business_domains '
                        'WHERE is_active = 1 AND level = 2'):
                    dom_names[code] = name
        except Exception as e:
            print(f'[WARN] 本体实体归类：治理域标签读取失败: {e}', flush=True)
        return info, dom_names

    def _reclassify_unmapped(self, ont: Ontology):
        """Unmapped 兜底实体的二次归类（只处理实体定义未覆盖的表，库中 defs 永远优先）：
        1. 代码种子映射 default_entity_map()（101 表口径，精确到表名）——并入既有实体，
           不存在则按种子 (英文名,中文名,layer) 新建；
        2. 治理库业务域标签（schema_table_docs.domain_l2 + business_domains 中文名）——
           按二级域聚合成 Domain_* 实体（layer 由表名前缀推导，parent=一级域码）；
        仍命不中的留在 Unmapped。素材提资新表由此免手工补 defs。
        """
        ent = ont.entities.get('Unmapped')
        if not ent or not ent.member_tables:
            return
        from core.ontology.entity_map import default_entity_map
        emap = default_entity_map()
        dom_info, dom_names = self._table_domain_info()
        seed_hit = domain_hit = 0
        still: List[str] = []
        for t in ent.member_tables:
            hit = emap.get(t)
            if hit:
                en, zh, layer = hit
                tgt = ont.entities.get(en)
                if tgt is None:
                    tgt = OntologyEntity(name=en, label=zh, layer=layer)
                    ont.entities[en] = tgt
                if t not in tgt.member_tables:
                    tgt.member_tables.append(t)
                seed_hit += 1
                continue
            _comment, l1, l2 = dom_info.get(t, ('', '', ''))
            if l2:
                en = f'Domain_{l2}'
                tgt = ont.entities.get(en)
                if tgt is None:
                    tgt = OntologyEntity(
                        name=en, label=dom_names.get(l2, l2),
                        layer=self._LAYER_BY_PREFIX.get(t.split('_', 1)[0], 'business'),
                        parent=l1, comment=f'按治理库二级业务分类 {l2} 自动聚合')
                    ont.entities[en] = tgt
                if t not in tgt.member_tables:
                    tgt.member_tables.append(t)
                domain_hit += 1
                continue
            still.append(t)
        if still:
            ent.member_tables = sorted(still)
        else:
            del ont.entities['Unmapped']
        for e in ont.entities.values():
            e.member_tables = sorted(set(e.member_tables))
        print(f'[Ontology] Unmapped 二次归类：种子映射 {seed_hit} 表，'
              f'域标签聚合 {domain_hit} 表，仍未映射 {len(still)} 表', flush=True)

    def _build_entities(self, ont: Ontology, entity_defs: Optional[List[dict]]):
        """按实体映射定义聚合物理表为业务实体。

        entity_defs=None → 用 entity_map 内置默认映射（调用方通常给库中 defs）。
        只聚合当前底座实际存在的表；映射中指向不存在表的成员自动丢弃。
        """
        try:
            from core.ontology.entity_map import build_entities
            if entity_defs:
                # 库中定义：表名 → 实体（逐表挂接，等价于 build_entities 的应用面）
                table_names = sorted(ont.classes.keys())
                table_set = set(table_names)
                for d in entity_defs:
                    members = [t for t in (d.get('member_tables') or []) if t in table_set]
                    if not members:
                        continue
                    # 标签兜底：defs 标签为空时取首个成员表的注释（治理文档兜底后通常非空）
                    label = d.get('label') or ''
                    if not label:
                        label = next((ont.classes[t].label for t in members
                                      if ont.classes[t].label), '') or d['name']
                    ont.entities[d['name']] = OntologyEntity(
                        name=d['name'], label=label,
                        layer=d.get('layer', 'business'), parent=d.get('parent', ''),
                        member_tables=sorted(members), comment=d.get('comment', ''))
                mapped = {t for e in ont.entities.values() for t in e.member_tables}
                missing = [t for t in table_names
                           if t not in mapped and t not in self._relation_tables()]
                if missing:
                    print(f'[WARN] 本体实体映射未覆盖 {len(missing)} 张表'
                          f'（先入 Unmapped，随后二次归类）: {missing[:8]}...', flush=True)
                    unmapped = [t for t in missing if not t.startswith('ads_')]
                    report_new = [t for t in missing if t.startswith('ads_')]
                    for t in report_new:
                        ont.entities[t] = OntologyEntity(
                            name=t, label=ont.classes[t].label or t,
                            layer='report', member_tables=[t])
                    if unmapped:
                        ent = ont.entities.setdefault('Unmapped', OntologyEntity(
                            name='Unmapped', label='未映射', layer='business'))
                        ent.member_tables = sorted(set(ent.member_tables) | set(unmapped))
            else:
                defs, unmapped = build_entities(
                    sorted(ont.classes.keys()),
                    table_comment=lambda t: ont.classes[t].label)
                if unmapped:
                    print(f'[WARN] 默认实体映射未覆盖（非 ads）表: {unmapped}', flush=True)
                for name, d in defs.items():
                    ont.entities[name] = OntologyEntity(
                        name=name, label=d['label'], layer=d['layer'],
                        parent=d.get('parent', ''), member_tables=d['member_tables'],
                        comment=d.get('comment', ''))
            # 实体定义未覆盖的新表：种子映射 → 治理域标签 二次归类，避免堆积 Unmapped
            self._reclassify_unmapped(ont)
        except Exception as e:
            print(f'[WARN] 本体提炼：实体层构建失败: {e}', flush=True)

    @staticmethod
    def _relation_tables() -> set:
        from core.ontology.entity_map import RELATION_TABLE_MAP
        return set(RELATION_TABLE_MAP)
