# -*- coding: utf-8 -*-
"""底座结构指纹：检测基础数据层表结构是否发生变化。

指纹只覆盖结构面（遵循"本体只随表结构变化而变化"）：
- 业务库 information_schema：表集合（name/comment）+ 每表列（name/type/pk/comment）+ 物理 FK 边
- 治理库 schema_relationship_docs：关系语义文档全文（人工治理的关系也属结构语义）

不含码值/问答对/错题等数据面。指纹不一致 → 生成变更提案走审批流。
"""
import hashlib
import json

from core.database import DatabaseManager


def _canon(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def compute_base_fingerprint(db: DatabaseManager = None) -> str:
    """计算底座结构指纹（sha256 hexdigest）。任何一路失败抛异常由调用方处理。"""
    db = db or DatabaseManager()
    payload = {'tables': [], 'columns': [], 'fks': [], 'rel_docs': []}

    with db.connect_business() as conn:
        payload['tables'] = [list(r) for r in conn.execute(
            "SELECT table_name, table_comment FROM information_schema.tables "
            "WHERE table_schema = DATABASE() AND table_type = 'BASE TABLE' "
            "ORDER BY table_name").fetchall()]
        payload['columns'] = [list(r) for r in conn.execute(
            "SELECT table_name, column_name, column_type, column_key, column_comment "
            "FROM information_schema.columns WHERE table_schema = DATABASE() "
            "ORDER BY table_name, ordinal_position").fetchall()]
        payload['fks'] = [list(r) for r in conn.execute(
            "SELECT table_name, column_name, referenced_table_name, referenced_column_name "
            "FROM information_schema.key_column_usage "
            "WHERE table_schema = DATABASE() AND referenced_table_name IS NOT NULL "
            "ORDER BY table_name, column_name").fetchall()]

    try:
        with db.connect_governance() as conn:
            payload['rel_docs'] = [list(r) for r in conn.execute(
                'SELECT title, path, join_conditions, business_scenarios '
                'FROM schema_relationship_docs ORDER BY id').fetchall()]
    except Exception as e:
        # 治理关系文档缺失不阻断指纹（退化为纯物理结构指纹）
        print(f'[WARN] 指纹计算：读取治理关系文档失败，仅按物理结构计算: {e}', flush=True)

    return hashlib.sha256(_canon(payload).encode('utf-8')).hexdigest()
