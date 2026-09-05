# -*- coding: utf-8 -*-
"""本体内存模型：基础数据层语义（表/列/关系/枚举/概念）的持久化无关表示。

Ontology 是不可变快照：
- builder 从底座（information_schema / 治理库文档·码值·概念映射）提炼
- store   持久化到 marketing_ontology 库（正式版 + 变更提案快照）
- service 加载已生效版本，向问数模块输出与 legacy 接口同形状的查询
- export  序列化为 OWL/RDF（RDF/XML、Turtle、N-Triples、JSON-LD）
"""
import json
import re
from dataclasses import dataclass, field
from typing import Dict, List


def mysql_to_xsd(mysql_type: str) -> str:
    """MySQL 列类型 → xsd 类型映射（导出 OWL DatatypeProperty range 用）。"""
    base = re.sub(r'\(.*\)', '', (mysql_type or '').upper()).strip()
    if base in ('BIGINT', 'INT', 'INTEGER', 'MEDIUMINT', 'SMALLINT', 'TINYINT', 'YEAR', 'BIT'):
        return 'xsd:integer'
    if base in ('DECIMAL', 'NUMERIC'):
        return 'xsd:decimal'
    if base in ('FLOAT', 'REAL'):
        return 'xsd:float'
    if base in ('DOUBLE',):
        return 'xsd:double'
    if base == 'DATE':
        return 'xsd:date'
    if base in ('DATETIME', 'TIMESTAMP'):
        return 'xsd:dateTime'
    if base == 'TIME':
        return 'xsd:time'
    if base in ('BLOB', 'BINARY', 'VARBINARY', 'LONGBLOB', 'MEDIUMBLOB', 'TINYBLOB'):
        return 'xsd:hexBinary'
    if base == 'BOOLEAN':
        return 'xsd:boolean'
    return 'xsd:string'


def table_kind(table_name: str) -> str:
    """dim_/dwd_ 前缀 → dimension/fact，其余 other。"""
    if table_name.startswith('dim_'):
        return 'dimension'
    if table_name.startswith('dwd_'):
        return 'fact'
    return 'other'


@dataclass
class OntologyClass:
    name: str            # 表名
    label: str           # 中文注释
    kind: str            # dimension / fact / other
    comment: str = ''

    def to_dict(self) -> dict:
        return {'name': self.name, 'label': self.label, 'kind': self.kind, 'comment': self.comment}

    @classmethod
    def from_dict(cls, d: dict) -> 'OntologyClass':
        return cls(name=d['name'], label=d.get('label', ''),
                   kind=d.get('kind', 'other'), comment=d.get('comment', ''))


@dataclass
class OntologyProperty:
    class_name: str      # 所属表
    name: str            # 列名
    label: str           # 中文注释
    data_type: str       # 原 MySQL 类型
    xsd_type: str        # 映射后的 xsd 类型
    is_pk: bool = False

    def to_dict(self) -> dict:
        return {'class_name': self.class_name, 'name': self.name, 'label': self.label,
                'data_type': self.data_type, 'xsd_type': self.xsd_type, 'is_pk': self.is_pk}

    @classmethod
    def from_dict(cls, d: dict) -> 'OntologyProperty':
        return cls(class_name=d['class_name'], name=d['name'], label=d.get('label', ''),
                   data_type=d.get('data_type', ''), xsd_type=d.get('xsd_type', 'xsd:string'),
                   is_pk=bool(d.get('is_pk')))


@dataclass
class OntologyRelation:
    from_class: str
    to_class: str
    join_conditions: List[str] = field(default_factory=list)
    business_scenarios: List[str] = field(default_factory=list)
    source: str = 'physical_fk'   # physical_fk / governance_doc / both

    @property
    def key(self) -> str:
        """方向不敏感的关系键（diff 比对用）。"""
        a, b = sorted([self.from_class, self.to_class])
        return f'{a}--{b}'

    def to_dict(self) -> dict:
        return {'from_class': self.from_class, 'to_class': self.to_class,
                'join_conditions': self.join_conditions,
                'business_scenarios': self.business_scenarios, 'source': self.source}

    @classmethod
    def from_dict(cls, d: dict) -> 'OntologyRelation':
        return cls(from_class=d['from_class'], to_class=d['to_class'],
                   join_conditions=list(d.get('join_conditions') or []),
                   business_scenarios=list(d.get('business_scenarios') or []),
                   source=d.get('source', 'physical_fk'))


@dataclass
class OntologyEnumeration:
    code_name: str
    cn_name: str
    domain_l1: str = ''
    domain_l2: str = ''
    domain_l3: str = ''
    items: List[dict] = field(default_factory=list)        # [{code, name, sort}]
    column_refs: List[dict] = field(default_factory=list)  # [{table, column, form}]

    def to_dict(self) -> dict:
        return {'code_name': self.code_name, 'cn_name': self.cn_name,
                'domain_l1': self.domain_l1, 'domain_l2': self.domain_l2,
                'domain_l3': self.domain_l3, 'items': self.items, 'column_refs': self.column_refs}

    @classmethod
    def from_dict(cls, d: dict) -> 'OntologyEnumeration':
        return cls(code_name=d['code_name'], cn_name=d.get('cn_name', ''),
                   domain_l1=d.get('domain_l1', ''), domain_l2=d.get('domain_l2', ''),
                   domain_l3=d.get('domain_l3', ''),
                   items=list(d.get('items') or []), column_refs=list(d.get('column_refs') or []))


@dataclass
class OntologyEntity:
    """业务实体：一组同义物理表的语义聚合（精炼设计的核心）。

    layer: master（主数据）/ business（业务数据）/ report（统计报表）
    parent: 父实体名（预留层级，当前为空）
    member_tables: 成员物理表名列表（实体→表 1:N）
    """
    name: str            # 英文标识，如 Customer / DailyEnergy
    label: str           # 中文名，如 客户 / 日电量
    layer: str           # master / business / report
    member_tables: List[str] = field(default_factory=list)
    parent: str = ''
    comment: str = ''

    def to_dict(self) -> dict:
        return {'name': self.name, 'label': self.label, 'layer': self.layer,
                'member_tables': self.member_tables, 'parent': self.parent,
                'comment': self.comment}

    @classmethod
    def from_dict(cls, d: dict) -> 'OntologyEntity':
        return cls(name=d['name'], label=d.get('label', ''), layer=d.get('layer', 'business'),
                   member_tables=list(d.get('member_tables') or []),
                   parent=d.get('parent', ''), comment=d.get('comment', ''))


@dataclass
class OntologyConcept:
    concept: str                     # 业务概念词（如"客户""日电量"）
    maps_to: List[str] = field(default_factory=list)   # 映射的表类名
    alt_labels: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {'concept': self.concept, 'maps_to': self.maps_to, 'alt_labels': self.alt_labels}

    @classmethod
    def from_dict(cls, d: dict) -> 'OntologyConcept':
        return cls(concept=d['concept'], maps_to=list(d.get('maps_to') or []),
                   alt_labels=list(d.get('alt_labels') or []))


@dataclass
class Ontology:
    """本体快照：一个生效版本的全部语义。"""
    version: int = 0
    built_at: str = ''
    base_fingerprint: str = ''
    classes: Dict[str, OntologyClass] = field(default_factory=dict)
    properties: List[OntologyProperty] = field(default_factory=list)
    relations: List[OntologyRelation] = field(default_factory=list)
    enumerations: Dict[str, OntologyEnumeration] = field(default_factory=dict)
    concepts: Dict[str, OntologyConcept] = field(default_factory=dict)
    entities: Dict[str, OntologyEntity] = field(default_factory=dict)  # 业务实体层（精炼设计）
    synonym_groups: Dict[str, List[str]] = field(default_factory=dict)  # 同族表消歧同义词组

    def summary(self) -> dict:
        by_layer = {}
        for e in self.entities.values():
            by_layer[e.layer] = by_layer.get(e.layer, 0) + 1
        return {
            'version': self.version,
            'built_at': self.built_at,
            'base_fingerprint': self.base_fingerprint,
            'classes': len(self.classes),
            'properties': len(self.properties),
            'relations': len(self.relations),
            'enumerations': len(self.enumerations),
            'concepts': len(self.concepts),
            'entities': len(self.entities),
            'entities_by_layer': by_layer,
            'synonym_groups': len(self.synonym_groups),
        }

    def to_dict(self) -> dict:
        return {
            'version': self.version,
            'built_at': self.built_at,
            'base_fingerprint': self.base_fingerprint,
            'classes': {k: v.to_dict() for k, v in self.classes.items()},
            'properties': [p.to_dict() for p in self.properties],
            'relations': [r.to_dict() for r in self.relations],
            'enumerations': {k: v.to_dict() for k, v in self.enumerations.items()},
            'concepts': {k: v.to_dict() for k, v in self.concepts.items()},
            'entities': {k: v.to_dict() for k, v in self.entities.items()},
            'synonym_groups': self.synonym_groups,
        }

    @classmethod
    def from_dict(cls, d: dict) -> 'Ontology':
        return cls(
            version=int(d.get('version') or 0),
            built_at=d.get('built_at', ''),
            base_fingerprint=d.get('base_fingerprint', ''),
            classes={k: OntologyClass.from_dict(v) for k, v in (d.get('classes') or {}).items()},
            properties=[OntologyProperty.from_dict(p) for p in (d.get('properties') or [])],
            relations=[OntologyRelation.from_dict(r) for r in (d.get('relations') or [])],
            enumerations={k: OntologyEnumeration.from_dict(v)
                          for k, v in (d.get('enumerations') or {}).items()},
            concepts={k: OntologyConcept.from_dict(v) for k, v in (d.get('concepts') or {}).items()},
            entities={k: OntologyEntity.from_dict(v) for k, v in (d.get('entities') or {}).items()},
            synonym_groups={k: list(v) for k, v in (d.get('synonym_groups') or {}).items()},
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_json(cls, s: str) -> 'Ontology':
        return cls.from_dict(json.loads(s))


def diff_ontologies(old: 'Ontology', new: 'Ontology') -> dict:
    """计算两版本体的结构 diff（提案审批视图的数据源）。

    只覆盖结构面：类/属性/关系的增删改 + 枚举/概念刷新计数。
    """
    # ---- 类 ----
    old_c, new_c = old.classes, new.classes
    c_added = sorted(set(new_c) - set(old_c))
    c_removed = sorted(set(old_c) - set(new_c))
    c_changed = sorted(
        name for name in set(old_c) & set(new_c)
        if (old_c[name].label, old_c[name].kind) != (new_c[name].label, new_c[name].kind))

    # ---- 属性（按 (表, 列) 键）----
    def _pkey(p):
        return (p.class_name, p.name)
    old_p = {_pkey(p): p for p in old.properties}
    new_p = {_pkey(p): p for p in new.properties}
    p_added = sorted(f'{t}.{c}' for t, c in set(new_p) - set(old_p))
    p_removed = sorted(f'{t}.{c}' for t, c in set(old_p) - set(new_p))
    p_changed = sorted(
        ({'name': f'{k[0]}.{k[1]}',
          'old': {'label': old_p[k].label, 'data_type': old_p[k].data_type, 'is_pk': old_p[k].is_pk},
          'new': {'label': new_p[k].label, 'data_type': new_p[k].data_type, 'is_pk': new_p[k].is_pk}}
         for k in set(old_p) & set(new_p)
         if (old_p[k].label, old_p[k].data_type, old_p[k].is_pk)
            != (new_p[k].label, new_p[k].data_type, new_p[k].is_pk)),
        key=lambda x: x['name'])

    # ---- 关系（方向不敏感键 + join 条件集合比对）----
    old_r = {r.key: r for r in old.relations}
    new_r = {r.key: r for r in new.relations}
    r_added = sorted(set(new_r) - set(old_r))
    r_removed = sorted(set(old_r) - set(new_r))
    r_changed = sorted(
        ({'key': k,
          'old': {'join_conditions': old_r[k].join_conditions, 'source': old_r[k].source},
          'new': {'join_conditions': new_r[k].join_conditions, 'source': new_r[k].source}}
         for k in set(old_r) & set(new_r)
         if sorted(old_r[k].join_conditions) != sorted(new_r[k].join_conditions)
         or old_r[k].source != new_r[k].source),
        key=lambda x: x['key'])

    # ---- 枚举/概念（数据面，只报计数）----
    e_changed = sum(
        1 for k in set(old.enumerations) & set(new.enumerations)
        if old.enumerations[k].items != new.enumerations[k].items)
    enum_diff = {
        'added': sorted(set(new.enumerations) - set(old.enumerations)),
        'removed': sorted(set(old.enumerations) - set(new.enumerations)),
        'items_changed': e_changed,
    }
    concept_diff = {
        'added': sorted(set(new.concepts) - set(old.concepts)),
        'removed': sorted(set(old.concepts) - set(new.concepts)),
        'changed': sorted(
            k for k in set(old.concepts) & set(new.concepts)
            if old.concepts[k].maps_to != new.concepts[k].maps_to),
    }

    # ---- 实体（精炼层）：按 name 键比对 label/layer/成员表 ----
    old_e = old.entities
    new_e = new.entities
    e_added = sorted(set(new_e) - set(old_e))
    e_removed = sorted(set(old_e) - set(new_e))
    e_changed = sorted(
        ({'name': k,
          'old': {'label': old_e[k].label, 'layer': old_e[k].layer,
                  'member_tables': old_e[k].member_tables,
                  'comment': (old_e[k].comment or '')[:120]},
          'new': {'label': new_e[k].label, 'layer': new_e[k].layer,
                  'member_tables': new_e[k].member_tables,
                  'comment': (new_e[k].comment or '')[:120]}}
         for k in set(old_e) & set(new_e)
         if (old_e[k].label, old_e[k].layer, old_e[k].member_tables, old_e[k].comment)
            != (new_e[k].label, new_e[k].layer, new_e[k].member_tables, new_e[k].comment)),
        key=lambda x: x['name'])
    entity_diff = {'added': e_added, 'removed': e_removed, 'changed': e_changed}

    diff = {
        'classes': {'added': c_added, 'removed': c_removed, 'changed': c_changed},
        'properties': {'added': p_added, 'removed': p_removed, 'changed': p_changed},
        'relations': {'added': r_added, 'removed': r_removed, 'changed': r_changed},
        'enumerations': enum_diff,
        'concepts': concept_diff,
        'entities': entity_diff,
    }
    diff['counts'] = {
        'classes_added': len(c_added), 'classes_removed': len(c_removed),
        'classes_changed': len(c_changed),
        'properties_added': len(p_added), 'properties_removed': len(p_removed),
        'properties_changed': len(p_changed),
        'relations_added': len(r_added), 'relations_removed': len(r_removed),
        'relations_changed': len(r_changed),
        'enumerations_added': len(enum_diff['added']),
        'enumerations_removed': len(enum_diff['removed']),
        'enumerations_items_changed': e_changed,
        'concepts_added': len(concept_diff['added']),
        'concepts_removed': len(concept_diff['removed']),
        'concepts_changed': len(concept_diff['changed']),
        'entities_added': len(e_added), 'entities_removed': len(e_removed),
        'entities_changed': len(e_changed),
    }
    return diff
