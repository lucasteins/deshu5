# -*- coding: utf-8 -*-
"""本体持久化：marketing_ontology 库 7 张表的读写。

- 正式表（ontology_meta + 5 张内容表）：当前生效版本，问数模块与导出只读这里
- ontology_proposals：变更提案（含完整快照与 diff），审批通过后快照整体换版

所有变更（首次引导除外）必须经 提案 → 前端审批 → approve() 落库，
本体绝不随请求临时抽取。
"""
import json
from datetime import datetime
from typing import List, Optional

from core.database import DatabaseManager, _ensure_database
from core import db_profile
from core.ontology.model import Ontology


class OntologyStore:
    """marketing_ontology 库读写。线程安全交给调用方（service 单例串行化）。"""

    def __init__(self, db: DatabaseManager = None):
        self.db = db or DatabaseManager()

    # ==================== 建库建表（幂等） ====================

    def init_tables(self):
        _ensure_database(db_profile.current()['ontology'])
        with self.db.connect_ontology() as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS ontology_meta (
                    id INT PRIMARY KEY AUTO_INCREMENT,
                    version INT NOT NULL,
                    base_fingerprint VARCHAR(64),
                    built_at VARCHAR(32),
                    summary TEXT,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS ontology_classes (
                    id INT PRIMARY KEY AUTO_INCREMENT,
                    name VARCHAR(128),
                    label VARCHAR(255),
                    kind VARCHAR(16),
                    comment VARCHAR(255),
                    version INT,
                    UNIQUE KEY uk_oc (name, version)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS ontology_properties (
                    id INT PRIMARY KEY AUTO_INCREMENT,
                    class_name VARCHAR(128),
                    name VARCHAR(128),
                    label VARCHAR(255),
                    data_type VARCHAR(64),
                    xsd_type VARCHAR(32),
                    is_pk TINYINT(1) DEFAULT 0,
                    version INT,
                    INDEX idx_op_class (class_name, version)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS ontology_relations (
                    id INT PRIMARY KEY AUTO_INCREMENT,
                    from_class VARCHAR(128),
                    to_class VARCHAR(128),
                    join_conditions TEXT,
                    business_scenarios TEXT,
                    source VARCHAR(32),
                    version INT,
                    INDEX idx_or_rel (from_class, to_class, version)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS ontology_enumerations (
                    id INT PRIMARY KEY AUTO_INCREMENT,
                    code_name VARCHAR(64),
                    cn_name VARCHAR(255),
                    domain_l1 VARCHAR(16),
                    domain_l2 VARCHAR(16),
                    domain_l3 VARCHAR(16),
                    items MEDIUMTEXT,
                    column_refs TEXT,
                    version INT,
                    UNIQUE KEY uk_oe (code_name, version)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS ontology_concepts (
                    id INT PRIMARY KEY AUTO_INCREMENT,
                    concept VARCHAR(128),
                    maps_to TEXT,
                    alt_labels TEXT,
                    version INT,
                    UNIQUE KEY uk_ocp (concept, version)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS ontology_entities (
                    id INT PRIMARY KEY AUTO_INCREMENT,
                    name VARCHAR(64),
                    label VARCHAR(128),
                    layer VARCHAR(16),
                    parent VARCHAR(64),
                    member_tables TEXT,
                    comment TEXT,
                    version INT,
                    UNIQUE KEY uk_oent (name, version)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS ontology_entity_defs (
                    id INT PRIMARY KEY AUTO_INCREMENT,
                    name VARCHAR(64),
                    label VARCHAR(128),
                    layer VARCHAR(16),
                    parent VARCHAR(64),
                    member_tables TEXT,
                    comment TEXT,
                    enabled TINYINT(1) DEFAULT 1,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE KEY uk_oedf (name)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS ontology_proposals (
                    id INT PRIMARY KEY AUTO_INCREMENT,
                    base_fingerprint VARCHAR(64),
                    diff MEDIUMTEXT,
                    snapshot MEDIUMTEXT,
                    status VARCHAR(16) DEFAULT 'pending',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    decided_at DATETIME NULL,
                    INDEX idx_op_status (status),
                    INDEX idx_op_fp (base_fingerprint)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            ''')
            # 实体描述扩容迁移：存量库 comment VARCHAR(255) → TEXT（幂等，已是 TEXT 则跳过）
            for tbl in ('ontology_entities', 'ontology_entity_defs'):
                try:
                    row = conn.execute(
                        "SELECT DATA_TYPE FROM information_schema.COLUMNS "
                        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = ? "
                        "AND COLUMN_NAME = 'comment'", (tbl,)).fetchone()
                    if row and row[0] in ('varchar', 'char'):
                        conn.execute(f'ALTER TABLE {tbl} MODIFY COLUMN comment TEXT')
                        print(f'[Ontology] {tbl}.comment 已扩容为 TEXT', flush=True)
                except Exception as e:
                    print(f'[WARN] {tbl}.comment 扩容失败（不阻断启动）: {e}', flush=True)
            conn.commit()

    # ==================== 生效版本 ====================

    def get_meta(self) -> Optional[dict]:
        """当前生效版本元信息；从未构建返回 None。"""
        with self.db.connect_ontology() as conn:
            row = conn.execute(
                'SELECT version, base_fingerprint, built_at, summary '
                'FROM ontology_meta ORDER BY version DESC LIMIT 1').fetchone()
        if not row:
            return None
        return {'version': row[0], 'base_fingerprint': row[1] or '',
                'built_at': row[2] or '', 'summary': json.loads(row[3] or '{}')}

    def load_active(self) -> Optional[Ontology]:
        """加载当前生效版本完整本体；未构建返回 None。"""
        meta = self.get_meta()
        if meta is None:
            return None
        v = meta['version']
        ont = Ontology(version=v, built_at=meta['built_at'],
                       base_fingerprint=meta['base_fingerprint'])
        with self.db.connect_ontology() as conn:
            from core.ontology.model import (OntologyClass, OntologyProperty,
                                             OntologyRelation, OntologyEnumeration,
                                             OntologyConcept)
            for name, label, kind, comment in conn.execute(
                    'SELECT name, label, kind, comment FROM ontology_classes WHERE version = ?',
                    (v,)):
                ont.classes[name] = OntologyClass(name=name, label=label or '',
                                                  kind=kind or 'other', comment=comment or '')
            for row in conn.execute(
                    'SELECT class_name, name, label, data_type, xsd_type, is_pk '
                    'FROM ontology_properties WHERE version = ?', (v,)):
                ont.properties.append(OntologyProperty(
                    class_name=row[0], name=row[1], label=row[2] or '',
                    data_type=row[3] or '', xsd_type=row[4] or 'xsd:string',
                    is_pk=bool(row[5])))
            for fc, tc, jc, bs, source in conn.execute(
                    'SELECT from_class, to_class, join_conditions, business_scenarios, source '
                    'FROM ontology_relations WHERE version = ?', (v,)):
                ont.relations.append(OntologyRelation(
                    from_class=fc, to_class=tc,
                    join_conditions=json.loads(jc or '[]'),
                    business_scenarios=json.loads(bs or '[]'),
                    source=source or 'physical_fk'))
            for code_name, cn_name, l1, l2, l3, items, refs in conn.execute(
                    'SELECT code_name, cn_name, domain_l1, domain_l2, domain_l3, items, column_refs '
                    'FROM ontology_enumerations WHERE version = ?', (v,)):
                ont.enumerations[code_name] = OntologyEnumeration(
                    code_name=code_name, cn_name=cn_name or '',
                    domain_l1=l1 or '', domain_l2=l2 or '', domain_l3=l3 or '',
                    items=json.loads(items or '[]'), column_refs=json.loads(refs or '[]'))
            for concept, maps_to, alt in conn.execute(
                    'SELECT concept, maps_to, alt_labels FROM ontology_concepts WHERE version = ?',
                    (v,)):
                ont.concepts[concept] = OntologyConcept(
                    concept=concept, maps_to=json.loads(maps_to or '[]'),
                    alt_labels=json.loads(alt or '[]'))
            from core.ontology.model import OntologyEntity
            for name, label, layer, parent, members, comment in conn.execute(
                    'SELECT name, label, layer, parent, member_tables, comment '
                    'FROM ontology_entities WHERE version = ?', (v,)):
                ont.entities[name] = OntologyEntity(
                    name=name, label=label or '', layer=layer or 'business',
                    parent=parent or '', member_tables=json.loads(members or '[]'),
                    comment=comment or '')
        ont.synonym_groups = dict(meta['summary'].get('synonym_groups') or {})
        return ont

    def save_active(self, ont: Ontology):
        """把本体快照落为新的生效版本（版本号 = 当前 + 1，事务）。"""
        meta = self.get_meta()
        ont.version = (meta['version'] if meta else 0) + 1
        summary = ont.summary()
        summary['synonym_groups'] = ont.synonym_groups  # 同义词组体量小，随 meta 存
        v = ont.version
        with self.db.connect_ontology() as conn:
            for c in ont.classes.values():
                conn.execute(
                    'INSERT INTO ontology_classes (name, label, kind, comment, version) '
                    'VALUES (?, ?, ?, ?, ?)', (c.name, c.label, c.kind, c.comment, v))
            for p in ont.properties:
                conn.execute(
                    'INSERT INTO ontology_properties '
                    '(class_name, name, label, data_type, xsd_type, is_pk, version) '
                    'VALUES (?, ?, ?, ?, ?, ?, ?)',
                    (p.class_name, p.name, p.label, p.data_type, p.xsd_type,
                     1 if p.is_pk else 0, v))
            for r in ont.relations:
                conn.execute(
                    'INSERT INTO ontology_relations '
                    '(from_class, to_class, join_conditions, business_scenarios, source, version) '
                    'VALUES (?, ?, ?, ?, ?, ?)',
                    (r.from_class, r.to_class, json.dumps(r.join_conditions, ensure_ascii=False),
                     json.dumps(r.business_scenarios, ensure_ascii=False), r.source, v))
            for e in ont.enumerations.values():
                conn.execute(
                    'INSERT INTO ontology_enumerations '
                    '(code_name, cn_name, domain_l1, domain_l2, domain_l3, items, column_refs, version) '
                    'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                    (e.code_name, e.cn_name, e.domain_l1, e.domain_l2, e.domain_l3,
                     json.dumps(e.items, ensure_ascii=False),
                     json.dumps(e.column_refs, ensure_ascii=False), v))
            for c in ont.concepts.values():
                conn.execute(
                    'INSERT INTO ontology_concepts (concept, maps_to, alt_labels, version) '
                    'VALUES (?, ?, ?, ?)',
                    (c.concept, json.dumps(c.maps_to, ensure_ascii=False),
                     json.dumps(c.alt_labels, ensure_ascii=False), v))
            for e in ont.entities.values():
                conn.execute(
                    'INSERT INTO ontology_entities '
                    '(name, label, layer, parent, member_tables, comment, version) '
                    'VALUES (?, ?, ?, ?, ?, ?, ?)',
                    (e.name, e.label, e.layer, e.parent,
                     json.dumps(e.member_tables, ensure_ascii=False), e.comment, v))
            conn.execute(
                'INSERT INTO ontology_meta (version, base_fingerprint, built_at, summary) '
                'VALUES (?, ?, ?, ?)',
                (v, ont.base_fingerprint, ont.built_at,
                 json.dumps(summary, ensure_ascii=False)))
            conn.commit()

    # ==================== 实体映射定义（可编辑规则，不随版本） ====================

    def get_entity_defs(self) -> List[dict]:
        """读取实体映射定义（enabled=1）。表不存在/异常返回 []。"""
        try:
            with self.db.connect_ontology() as conn:
                rows = conn.execute(
                    'SELECT name, label, layer, parent, member_tables, comment '
                    'FROM ontology_entity_defs WHERE enabled = 1 ORDER BY layer, name').fetchall()
            return [{'name': r[0], 'label': r[1] or '', 'layer': r[2] or 'business',
                     'parent': r[3] or '', 'member_tables': json.loads(r[4] or '[]'),
                     'comment': r[5] or ''} for r in rows]
        except Exception as e:
            print(f'[WARN] 读取实体映射定义失败: {e}', flush=True)
            return []

    def get_entity_def(self, name: str) -> Optional[dict]:
        """读取单个实体映射定义（enabled=1）。不存在/异常返回 None。"""
        try:
            with self.db.connect_ontology() as conn:
                row = conn.execute(
                    'SELECT name, label, layer, parent, member_tables, comment '
                    'FROM ontology_entity_defs WHERE name = ? AND enabled = 1',
                    (name,)).fetchone()
            if not row:
                return None
            return {'name': row[0], 'label': row[1] or '', 'layer': row[2] or 'business',
                    'parent': row[3] or '', 'member_tables': json.loads(row[4] or '[]'),
                    'comment': row[5] or ''}
        except Exception as e:
            print(f'[WARN] 读取实体映射定义失败: {e}', flush=True)
            return None

    def seed_entity_defs(self, defs: List[dict]):
        """实体定义表为空时播种（默认映射）。返回是否执行了播种。"""
        with self.db.connect_ontology() as conn:
            cnt = conn.execute('SELECT COUNT(*) FROM ontology_entity_defs').fetchone()[0]
            if cnt > 0:
                return False
            for d in defs:
                conn.execute(
                    'INSERT INTO ontology_entity_defs '
                    '(name, label, layer, parent, member_tables, comment) VALUES (?, ?, ?, ?, ?, ?)',
                    (d['name'], d.get('label', ''), d.get('layer', 'business'),
                     d.get('parent', ''), json.dumps(d.get('member_tables') or [],
                                                     ensure_ascii=False),
                     d.get('comment', '')))
            conn.commit()
        print(f'[Ontology] 实体映射定义播种完成: {len(defs)} 个实体', flush=True)
        return True

    def upsert_entity_def(self, d: dict):
        """新增/更新实体定义（按 name）。编辑后需重建提案审批才进生效版本。"""
        with self.db.connect_ontology() as conn:
            conn.execute(
                'INSERT INTO ontology_entity_defs '
                '(name, label, layer, parent, member_tables, comment, enabled) '
                'VALUES (?, ?, ?, ?, ?, ?, ?) '
                'ON DUPLICATE KEY UPDATE '
                'label=VALUES(label), layer=VALUES(layer), parent=VALUES(parent), '
                'member_tables=VALUES(member_tables), comment=VALUES(comment), '
                'enabled=VALUES(enabled), updated_at=CURRENT_TIMESTAMP',
                (d['name'], d.get('label', ''), d.get('layer', 'business'),
                 d.get('parent', ''), json.dumps(d.get('member_tables') or [], ensure_ascii=False),
                 d.get('comment', ''), int(d.get('enabled', 1))))
            conn.commit()

    def delete_entity_def(self, name: str) -> bool:
        """逻辑删除（enabled=0）。"""
        with self.db.connect_ontology() as conn:
            cursor = conn.execute(
                'UPDATE ontology_entity_defs SET enabled = 0, updated_at = CURRENT_TIMESTAMP '
                'WHERE name = ?', (name,))
            conn.commit()
            return cursor.rowcount > 0

    # ==================== 变更提案 ====================

    def create_proposal(self, ont: Ontology, diff: dict) -> int:
        with self.db.connect_ontology() as conn:
            cursor = conn.execute(
                'INSERT INTO ontology_proposals (base_fingerprint, diff, snapshot) '
                'VALUES (?, ?, ?)',
                (ont.base_fingerprint, json.dumps(diff, ensure_ascii=False), ont.to_json()))
            conn.commit()
            return cursor.lastrowid

    def has_pending(self, fingerprint: str = None) -> Optional[int]:
        """存在 pending 提案（可选按指纹限定）时返回其 id，否则 None。"""
        sql = "SELECT id FROM ontology_proposals WHERE status = 'pending'"
        params: tuple = ()
        if fingerprint:
            sql += ' AND base_fingerprint = ?'
            params = (fingerprint,)
        sql += ' ORDER BY id DESC LIMIT 1'
        with self.db.connect_ontology() as conn:
            row = conn.execute(sql, params).fetchone()
        return row[0] if row else None

    def list_proposals(self, limit: int = 20) -> List[dict]:
        with self.db.connect_ontology() as conn:
            rows = conn.execute(
                'SELECT id, base_fingerprint, diff, status, created_at, decided_at '
                'FROM ontology_proposals ORDER BY id DESC LIMIT ?', (int(limit),)).fetchall()
        out = []
        for pid, fp, diff, status, created, decided in rows:
            d = json.loads(diff or '{}')
            out.append({'id': pid, 'base_fingerprint': fp or '', 'status': status,
                        'counts': d.get('counts') or {},
                        'created_at': str(created or ''), 'decided_at': str(decided or '')})
        return out

    def get_proposal(self, pid: int) -> Optional[dict]:
        """提案详情（含 diff 明细，不含快照）。"""
        with self.db.connect_ontology() as conn:
            row = conn.execute(
                'SELECT id, base_fingerprint, diff, status, created_at, decided_at '
                'FROM ontology_proposals WHERE id = ?', (int(pid),)).fetchone()
        if not row:
            return None
        return {'id': row[0], 'base_fingerprint': row[1] or '',
                'diff': json.loads(row[2] or '{}'), 'status': row[3],
                'created_at': str(row[4] or ''), 'decided_at': str(row[5] or '')}

    def _load_proposal_snapshot(self, pid: int) -> Optional[Ontology]:
        with self.db.connect_ontology() as conn:
            row = conn.execute(
                "SELECT snapshot FROM ontology_proposals WHERE id = ? AND status = 'pending'",
                (int(pid),)).fetchone()
        return Ontology.from_json(row[0]) if row else None

    def approve(self, pid: int) -> Optional[Ontology]:
        """批准提案：快照落为新生效版本。非 pending 返回 None。"""
        ont = self._load_proposal_snapshot(pid)
        if ont is None:
            return None
        self.save_active(ont)
        with self.db.connect_ontology() as conn:
            conn.execute(
                "UPDATE ontology_proposals SET status = 'approved', decided_at = ? WHERE id = ?",
                (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), int(pid)))
            conn.commit()
        return ont

    def reject(self, pid: int) -> bool:
        with self.db.connect_ontology() as conn:
            cursor = conn.execute(
                "UPDATE ontology_proposals SET status = 'rejected', decided_at = ? "
                "WHERE id = ? AND status = 'pending'",
                (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), int(pid)))
            conn.commit()
            return cursor.rowcount > 0
