# -*- coding: utf-8 -*-
"""本体导出：OWL/RDF 多格式序列化（rdflib）。

支持格式：
- owl    → RDF/XML（.owl 文件惯用）
- ttl    → Turtle
- nt     → N-Triples
- jsonld → JSON-LD

建模映射（与 plan §三一致）：
- 表 → owl:Class（dim_/dwd_ 前缀 → subClassOf ont:Dimension / ont:Fact）
- 列 → owl:DatatypeProperty（domain=表类，range=xsd 类型，ont:isPrimaryKey 注解）
- 关系 → owl:ObjectProperty（ont:joinCondition / skos:scopeNote / ont:relationSource 注解）
- 码值域 → owl:Class subClassOf ont:CodeValueSet；码值项 → owl:NamedIndividual + skos:Concept
- 列落域 → ont:hasCodeValueSet（列属性 → 码值域，ont:storageForm 注解形态）
- 业务概念 → skos:Concept + ont:mapsToEntity；同义词组 → skos:Concept + skos:altLabel
- 业务实体（精炼层）→ owl:Class subClassOf ont:MasterData/BusinessData/DataProduct；
  成员表类 subClassOf 实体类
"""
from typing import Tuple

from rdflib import Graph, Literal, Namespace, RDF, RDFS, URIRef
from rdflib.namespace import OWL, SKOS, XSD

import config
from core.ontology.model import Ontology

FORMATS = {
    'owl': ('xml', 'application/rdf+xml', '.owl'),
    'ttl': ('turtle', 'text/turtle', '.ttl'),
    'nt': ('nt', 'application/n-triples', '.nt'),
    'jsonld': ('json-ld', 'application/ld+json', '.jsonld'),
}


def _esc(name: str) -> str:
    """IRI 片段转义（表/列名均为 [a-z0-9_]，保守替换其他字符）。"""
    import re
    return re.sub(r'[^A-Za-z0-9_\-]', '_', name or '')


def build_graph(ont: Ontology) -> Graph:
    ONT = Namespace(config.ONTOLOGY_BASE_IRI)
    g = Graph()
    g.bind('ont', ONT)
    g.bind('owl', OWL)
    g.bind('skos', SKOS)
    g.bind('xsd', XSD)

    # ---- 本体头 ----
    root = URIRef(config.ONTOLOGY_BASE_IRI.rstrip('#/'))
    g.add((root, RDF.type, OWL.Ontology))
    g.add((root, OWL.versionInfo,
           Literal(f'v{ont.version} built_at={ont.built_at} '
                   f'fingerprint={ont.base_fingerprint[:12]}')))
    g.add((root, RDFS.label, Literal('营销共享层数据本体（deshu5）', lang='zh')))

    # ---- 上层分类 ----
    g.add((ONT.Dimension, RDF.type, OWL.Class))
    g.add((ONT.Dimension, RDFS.label, Literal('维度表', lang='zh')))
    g.add((ONT.Fact, RDF.type, OWL.Class))
    g.add((ONT.Fact, RDFS.label, Literal('事实表', lang='zh')))
    g.add((ONT.CodeValueSet, RDF.type, OWL.Class))
    g.add((ONT.CodeValueSet, RDFS.label, Literal('码值域', lang='zh')))

    # ---- 表类 ----
    for c in ont.classes.values():
        uri = ONT[f'table/{_esc(c.name)}']
        g.add((uri, RDF.type, OWL.Class))
        if c.label:
            g.add((uri, RDFS.label, Literal(c.label, lang='zh')))
        if c.kind == 'dimension':
            g.add((uri, RDFS.subClassOf, ONT.Dimension))
        elif c.kind == 'fact':
            g.add((uri, RDFS.subClassOf, ONT.Fact))

    # ---- 列属性 ----
    for p in ont.properties:
        uri = ONT[f'prop/{_esc(p.class_name)}__{_esc(p.name)}']
        g.add((uri, RDF.type, OWL.DatatypeProperty))
        g.add((uri, RDFS.domain, ONT[f'table/{_esc(p.class_name)}']))
        g.add((uri, RDFS.range, XSD[p.xsd_type.split(':', 1)[-1]]))
        if p.label:
            g.add((uri, RDFS.label, Literal(p.label, lang='zh')))
        g.add((uri, ONT.mysqlType, Literal(p.data_type)))
        if p.is_pk:
            g.add((uri, ONT.isPrimaryKey, Literal(True)))

    # ---- 关系 ----
    for r in ont.relations:
        uri = ONT[f'rel/{_esc(r.from_class)}__{_esc(r.to_class)}']
        g.add((uri, RDF.type, OWL.ObjectProperty))
        g.add((uri, RDFS.domain, ONT[f'table/{_esc(r.from_class)}']))
        g.add((uri, RDFS.range, ONT[f'table/{_esc(r.to_class)}']))
        for jc in r.join_conditions:
            g.add((uri, ONT.joinCondition, Literal(jc)))
        for sc in r.business_scenarios:
            g.add((uri, SKOS.scopeNote, Literal(sc, lang='zh')))
        g.add((uri, ONT.relationSource, Literal(r.source)))

    # ---- 码值域与码值项 ----
    for e in ont.enumerations.values():
        uri = ONT[f'code/{_esc(e.code_name)}']
        g.add((uri, RDF.type, OWL.Class))
        g.add((uri, RDFS.subClassOf, ONT.CodeValueSet))
        if e.cn_name:
            g.add((uri, RDFS.label, Literal(e.cn_name, lang='zh')))
        for lvl, val in (('domainL1', e.domain_l1), ('domainL2', e.domain_l2),
                         ('domainL3', e.domain_l3)):
            if val:
                g.add((uri, ONT[lvl], Literal(val)))
        for it in e.items:
            iuri = ONT[f'code/{_esc(e.code_name)}/{_esc(it.get("code") or it.get("name"))}']
            g.add((iuri, RDF.type, OWL.NamedIndividual))
            g.add((iuri, RDF.type, SKOS.Concept))
            g.add((iuri, RDF.type, uri))
            if it.get('name'):
                g.add((iuri, SKOS.prefLabel, Literal(it['name'], lang='zh')))
            if it.get('code'):
                g.add((iuri, ONT.itemCode, Literal(it['code'])))
        # 列落域：列属性 → 码值域（含存储形态）
        for ref in e.column_refs:
            puri = ONT[f'prop/{_esc(ref.get("table"))}__{_esc(ref.get("column"))}']
            g.add((puri, ONT.hasCodeValueSet, uri))
            if ref.get('form'):
                g.add((puri, ONT.storageForm, Literal(ref['form'])))

    # ---- 业务概念与同义词 ----
    for c in ont.concepts.values():
        uri = ONT[f'concept/{_esc(c.concept)}']
        g.add((uri, RDF.type, SKOS.Concept))
        g.add((uri, SKOS.prefLabel, Literal(c.concept, lang='zh')))
        for t in c.maps_to:
            g.add((uri, ONT.mapsToEntity, ONT[f'table/{_esc(t)}']))
        for alt in c.alt_labels:
            g.add((uri, SKOS.altLabel, Literal(alt, lang='zh')))
    for word, group in ont.synonym_groups.items():
        uri = ONT[f'synonym/{_esc(word)}']
        g.add((uri, RDF.type, SKOS.Concept))
        g.add((uri, SKOS.prefLabel, Literal(word, lang='zh')))
        for w in group:
            if w != word:
                g.add((uri, SKOS.altLabel, Literal(w, lang='zh')))

    # ---- 业务实体层（精炼设计：主数据/业务数据/统计报表） ----
    layer_cls = {'master': (ONT.MasterData, '主数据'),
                 'business': (ONT.BusinessData, '业务数据'),
                 'report': (ONT.DataProduct, '统计报表')}
    for cls_uri, zh in layer_cls.values():
        g.add((cls_uri, RDF.type, OWL.Class))
        g.add((cls_uri, RDFS.label, Literal(zh, lang='zh')))
    for e in ont.entities.values():
        euri = ONT[f'entity/{_esc(e.name)}']
        g.add((euri, RDF.type, OWL.Class))
        if e.label:
            g.add((euri, RDFS.label, Literal(e.label, lang='zh')))
        if e.comment:
            g.add((euri, RDFS.comment, Literal(e.comment, lang='zh')))
        layer = layer_cls.get(e.layer)
        if layer:
            g.add((euri, RDFS.subClassOf, layer[0]))
        # 成员表类 subClassOf 实体类（物理表是实体的承载）
        for t in e.member_tables:
            g.add((ONT[f'table/{_esc(t)}'], RDFS.subClassOf, euri))

    return g


def export_ontology(ont: Ontology, fmt: str = 'ttl') -> Tuple[str, str, str]:
    """导出本体。返回 (内容文本, mimetype, 文件扩展名)。fmt ∈ FORMATS。"""
    if fmt not in FORMATS:
        raise ValueError(f'不支持的导出格式: {fmt}（可选: {", ".join(FORMATS)}）')
    rdf_format, mimetype, ext = FORMATS[fmt]
    g = build_graph(ont)
    content = g.serialize(format=rdf_format)
    if isinstance(content, bytes):
        content = content.decode('utf-8')
    return content, mimetype, ext
