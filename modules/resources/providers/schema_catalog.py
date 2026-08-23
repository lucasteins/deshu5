# -*- coding: utf-8 -*-
"""表结构目录资源：35 张营销共享层表的表/列元数据。

包装 SchemaPreloader.get_instance()（内存单例，表/列/主键）与
schema_kb（governance 库注释文档检索）。P1 本期只读。
"""
from modules.resources.base import ResourceProvider


class SchemaCatalogProvider(ResourceProvider):
    name = 'schema_catalog'
    label = '表结构目录'
    description = '35 张营销共享层表的表/列元数据（表清单、字段、主键、中文注释）'
    entry_schema = [
        {'field': 'table_name', 'type': 'str', 'required': True},
        {'field': 'table_comment', 'type': 'str', 'required': False},
    ]

    def __init__(self):
        self._preloader = None   # SchemaPreloader 单例（首次使用时取）
        self._kb = None          # SchemaKnowledgeBase 惰性单例

    def _get_preloader(self):
        if self._preloader is None:
            from core.schema_preloader import SchemaPreloader
            self._preloader = SchemaPreloader.get_instance()
        return self._preloader

    def _get_kb(self):
        if self._kb is None:
            from core.schema_kb import SchemaKnowledgeBase
            self._kb = SchemaKnowledgeBase()
        return self._kb

    # ---------- 语义检索 ----------
    def retrieve(self, query: dict) -> dict:
        """两种 payload：
        {'question': str, 'tables': [..](可选), 'top_k_tables': n, 'top_k_columns': n}
            -> {'tables': 相关表文档, 'columns': 相关字段文档}（schema_kb 注释检索）
        {'table': str} -> {'item': 单表全量字段（同 get）}"""
        query = query or {}
        if query.get('table'):
            return {'item': self.get(query['table'])}
        question = query.get('question', '')
        if not question:
            return {'tables': [], 'columns': []}
        from core.rag_retriever import RAGRetriever
        keywords = RAGRetriever().extract_keywords(question)
        kb = self._get_kb()
        return {
            'tables': kb.retrieve_table_docs(keywords, limit=int(query.get('top_k_tables', 10))),
            'columns': kb.retrieve_column_docs(
                keywords, tables=query.get('tables'), limit=int(query.get('top_k_columns', 20))),
        }

    # ---------- 只读视图 ----------
    def list(self, filters=None, limit=200, offset=0) -> list:
        """表清单（名称/注释/字段数/主键/分层）。"""
        preloader = self._get_preloader()
        q = ((filters or {}).get('q') or '').lower()
        items = []
        for t in preloader.get_table_names():
            info = preloader.parser.tables.get(t, {})
            comment = info.get('comment') or ''
            if q and q not in t.lower() and q not in comment.lower():
                continue
            items.append({
                'table_name': t,
                'table_comment': comment,
                'column_count': len(info.get('columns', [])),
                'pk': info.get('pk') or [],
                'layer': 'dim' if t.startswith('dim_') else ('dwd' if t.startswith('dwd_') else 'other'),
            })
        return items[int(offset):int(offset) + int(limit)]

    def get(self, item_id):
        """单表详情：全量字段（名称/类型/PK/注释）+ 关联关系（同 /api/table-columns 口径）。"""
        table = str(item_id or '').strip()
        if not table:
            return None
        preloader = self._get_preloader()
        cols = preloader.get_columns(table)
        if not cols:
            return None
        rels = []
        for rel in preloader.get_relationships():
            a, b = rel['path'][0], rel['path'][1]
            if table in (a, b):
                other = b if a == table else a
                rels.append({'table': other,
                             'comment': preloader.get_table_comment(other),
                             'join_conditions': rel.get('join_conditions', [])})
        return {
            'table_name': table,
            'table_comment': preloader.get_table_comment(table),
            'pk': preloader.parser.tables.get(table, {}).get('pk') or [],
            'columns': cols,
            'relationships': rels,
        }

    def count(self) -> int:
        return len(self._get_preloader().get_table_names())

    # ---------- P1 本期只读 ----------
    def create(self, item: dict) -> dict:
        self._raise_readonly()

    def update(self, item_id, item: dict) -> dict:
        self._raise_readonly()

    def delete(self, item_id) -> bool:
        self._raise_readonly()

    def import_rows(self, rows: list) -> dict:
        self._raise_readonly()
