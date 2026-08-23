# -*- coding: utf-8 -*-
"""表关系图资源：主外键关系与 JOIN 路径查询。

包装 SchemaPreloader.get_instance().get_relationships（启动预加载的权威关系）
与 schema_kb 的 schema_relationship_docs 检索。P1 本期只读。
"""
from modules.resources.base import ResourceProvider


class SchemaGraphProvider(ResourceProvider):
    name = 'schema_graph'
    label = '表关系图'
    description = '35 表主外键关系与 JOIN 路径（生成 JOIN 条件的权威来源）'
    entry_schema = [
        {'field': 'from_table', 'type': 'str', 'required': True},
        {'field': 'to_table', 'type': 'str', 'required': True},
        {'field': 'join_conditions', 'type': 'list', 'required': False},
        {'field': 'business_scenarios', 'type': 'list', 'required': False},
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

    @staticmethod
    def _rel_id(rel: dict) -> str:
        """关系的稳定标识：'a → b'（与 schema_relationship_docs.title 同式）。"""
        return ' → '.join(rel['path'])

    # ---------- 语义检索 / JOIN 路径查询 ----------
    def retrieve(self, query: dict) -> dict:
        """两种 payload：
        {'from_table': str, 'to_table': str(可选)} -> {'items': 覆盖这些表的关系路径}
        {'question': str, 'limit': n} -> {'items': schema_kb 关系文档语义检索}"""
        query = query or {}
        if query.get('question'):
            from core.rag_retriever import RAGRetriever
            keywords = RAGRetriever().extract_keywords(query['question'])
            items = self._get_kb().retrieve_relationship_docs(
                keywords, tables=query.get('tables'), limit=int(query.get('limit', 10)))
            return {'items': items}
        preloader = self._get_preloader()
        rels = preloader.get_relationships(
            from_table=query.get('from_table'), to_table=query.get('to_table'))
        return {'items': [self._to_item(rel) for rel in rels]}

    # ---------- 只读视图 ----------
    def _to_item(self, rel: dict) -> dict:
        return {
            'id': self._rel_id(rel),
            'title': self._rel_id(rel),
            'path': rel.get('path', []),
            'join_conditions': rel.get('join_conditions', []),
            'business_scenarios': rel.get('business_scenarios', []),
        }

    def list(self, filters=None, limit=200, offset=0) -> list:
        """关系清单（路径 + JOIN 条件）。"""
        preloader = self._get_preloader()
        q = ((filters or {}).get('q') or '').lower()
        items = []
        for rel in preloader.get_relationships():
            item = self._to_item(rel)
            if q and q not in item['title'].lower():
                continue
            items.append(item)
        return items[int(offset):int(offset) + int(limit)]

    def get(self, item_id):
        """单条关系：item_id 即 'a → b' 标题。"""
        title = str(item_id or '').strip()
        if not title:
            return None
        preloader = self._get_preloader()
        for rel in preloader.get_relationships():
            if self._rel_id(rel) == title:
                return self._to_item(rel)
        return None

    def count(self) -> int:
        return len(self._get_preloader().get_relationships())

    # ---------- P1 本期只读 ----------
    def create(self, item: dict) -> dict:
        self._raise_readonly()

    def update(self, item_id, item: dict) -> dict:
        self._raise_readonly()

    def delete(self, item_id) -> bool:
        self._raise_readonly()

    def import_rows(self, rows: list) -> dict:
        self._raise_readonly()
