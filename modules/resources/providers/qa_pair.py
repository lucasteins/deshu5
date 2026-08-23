# -*- coding: utf-8 -*-
"""问答对资源：qa_pairs 表 CRUD + 委托 RAGRetriever 的 QA 检索（复用其打分，不复制逻辑）。"""
from core.database import DatabaseManager
from modules.resources.base import ResourceProvider, rows_to_dicts, row_to_dict, now_str


class QAPairProvider(ResourceProvider):
    name = 'qa_pair'
    label = '问答对'
    description = '治理库 qa_pairs 表：NL2SQL 训练问答对（RAG 检索池）'
    entry_schema = [
        {'field': 'question', 'type': 'str', 'required': True},
        {'field': 'standard_sql', 'type': 'str', 'required': True},
        {'field': 'difficulty', 'type': 'str', 'required': False},
        {'field': 'source', 'type': 'str', 'required': False},
        {'field': 'tags', 'type': 'str', 'required': False},
        {'field': 'is_usable', 'type': 'int', 'required': False},
    ]

    # 允许 CRUD 直写的列（与 app.py 现有路由的列用法一致）
    _WRITABLE = ('question', 'standard_sql', 'difficulty', 'source', 'tags', 'is_usable')

    def __init__(self):
        self.db = DatabaseManager()
        self._rag = None   # RAGRetriever 惰性单例（首次 retrieve 时构造）

    def _get_rag(self):
        if self._rag is None:
            import config
            from core.rag_retriever import RAGRetriever
            self._rag = RAGRetriever(top_k=getattr(config, 'RAG_TOP_K', 5))
        return self._rag

    # ---------- 语义检索 ----------
    def retrieve(self, query: dict) -> dict:
        """query: {'question': str, 'top_k': int(可选)} -> {'items': RAG 三层检索结果}"""
        query = query or {}
        question = query.get('question', '')
        items = self._get_rag().retrieve(question, top_k=query.get('top_k')) if question else []
        return {'items': items}

    # ---------- CRUD ----------
    def list(self, filters=None, limit=200, offset=0) -> list:
        filters = filters or {}
        conditions, params = [], []
        if filters.get('difficulty'):
            conditions.append('difficulty = ?')
            params.append(filters['difficulty'])
        if filters.get('source'):
            conditions.append('source = ?')
            params.append(filters['source'])
        if filters.get('is_usable') is not None:
            conditions.append('is_usable = ?')
            params.append(int(filters['is_usable']))
        if filters.get('q'):
            conditions.append('question LIKE ?')
            params.append(f"%{filters['q']}%")
        where = ('WHERE ' + ' AND '.join(conditions)) if conditions else ''
        with self.db.connect_governance() as conn:
            cursor = conn.execute(
                f'SELECT * FROM qa_pairs {where} ORDER BY id DESC LIMIT ? OFFSET ?',
                (*params, int(limit), int(offset)))
            return rows_to_dicts(cursor)

    def get(self, item_id):
        try:
            item_id = int(item_id)
        except (TypeError, ValueError):
            return None
        with self.db.connect_governance() as conn:
            cursor = conn.execute('SELECT * FROM qa_pairs WHERE id = ?', (item_id,))
            return row_to_dict(cursor, cursor.fetchone())

    def create(self, item: dict) -> dict:
        errors = self.validate_item(item)
        if errors:
            raise ValueError('; '.join(errors))
        with self.db.connect_governance() as conn:
            cursor = conn.execute(
                '''INSERT INTO qa_pairs (question, standard_sql, difficulty, source, tags, is_usable, ingest_time)
                   VALUES (?, ?, ?, ?, ?, ?, ?)''',
                (item['question'].strip(), item['standard_sql'].strip(),
                 item.get('difficulty') or '进阶题', item.get('source') or 'user_saved',
                 item.get('tags') or '', int(item.get('is_usable', 1)), now_str()))
            conn.commit()
            new_id = cursor.lastrowid
        return self.get(new_id)

    def update(self, item_id, item: dict) -> dict:
        if self.get(item_id) is None:
            raise LookupError(f'问答对不存在: {item_id}')
        errors = self.validate_item(item, partial=True)
        if errors:
            raise ValueError('; '.join(errors))
        sets, params = [], []
        for col in self._WRITABLE:
            if col in item:
                sets.append(f'{col} = ?')
                params.append(int(item[col]) if col == 'is_usable' else item[col])
        if sets:
            params.append(int(item_id))
            with self.db.connect_governance() as conn:
                conn.execute(f'UPDATE qa_pairs SET {", ".join(sets)} WHERE id = ?', tuple(params))
                conn.commit()
        return self.get(item_id)

    def delete(self, item_id) -> bool:
        try:
            item_id = int(item_id)
        except (TypeError, ValueError):
            return False
        with self.db.connect_governance() as conn:
            cursor = conn.execute('DELETE FROM qa_pairs WHERE id = ?', (item_id,))
            conn.commit()
            return cursor.rowcount > 0

    def count(self) -> int:
        with self.db.connect_governance() as conn:
            return conn.execute('SELECT COUNT(*) FROM qa_pairs').fetchone()[0]

    def template_examples(self) -> list:
        return [
            {'question': '查询2026年4月用电量TOP10的计量点',
             'standard_sql': "SELECT meter_asset_no, SUM(pap_e) AS total_pap_e "
                             "FROM dwd_cst_meter_energy_day_h_xz "
                             "WHERE strftime('%Y-%m', data_date) = '2026-04' "
                             "GROUP BY meter_asset_no ORDER BY total_pap_e DESC LIMIT 10;",
             'difficulty': '进阶题', 'source': 'import', 'tags': '用电量,TOP-N', 'is_usable': 1},
        ]
