# -*- coding: utf-8 -*-
"""错题资源：error_records 表 CRUD + 委托 rag_retriever.retrieve_error_records 检索。"""
from core.database import DatabaseManager
from modules.resources.base import ResourceProvider, rows_to_dicts, row_to_dict, now_str


class ErrorRecordProvider(ResourceProvider):
    name = 'error_record'
    label = '错题记录'
    description = '治理库 error_records 表：生成错误样本（未解决错题进入 RAG 提示）'
    entry_schema = [
        {'field': 'business_question', 'type': 'str', 'required': True},
        {'field': 'generated_sql', 'type': 'str', 'required': False},
        {'field': 'correct_sql', 'type': 'str', 'required': False},
        {'field': 'error_type', 'type': 'str', 'required': False},
        {'field': 'error_detail', 'type': 'str', 'required': False},
        {'field': 'frequency', 'type': 'int', 'required': False},
        {'field': 'is_resolved', 'type': 'int', 'required': False},
    ]

    # 允许 CRUD 直写的列（与 app.py /api/error-update 的列用法一致）
    _WRITABLE = ('business_question', 'generated_sql', 'correct_sql', 'error_type',
                 'error_detail', 'user_feedback', 'frequency', 'is_resolved', 'resolution_note')

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
        """query: {'question': str, 'limit': int(可选)} -> {'items': 相似未解决错题}"""
        query = query or {}
        question = query.get('question', '')
        items = self._get_rag().retrieve_error_records(
            question, limit=int(query.get('limit', 3))) if question else []
        return {'items': items}

    # ---------- CRUD ----------
    def list(self, filters=None, limit=200, offset=0) -> list:
        filters = filters or {}
        conditions, params = [], []
        if filters.get('error_type'):
            conditions.append('error_type = ?')
            params.append(filters['error_type'])
        if filters.get('is_resolved') is not None:
            conditions.append('is_resolved = ?')
            params.append(int(filters['is_resolved']))
        if filters.get('q'):
            conditions.append('business_question LIKE ?')
            params.append(f"%{filters['q']}%")
        where = ('WHERE ' + ' AND '.join(conditions)) if conditions else ''
        with self.db.connect_governance() as conn:
            cursor = conn.execute(
                f'SELECT * FROM error_records {where} ORDER BY id DESC LIMIT ? OFFSET ?',
                (*params, int(limit), int(offset)))
            return rows_to_dicts(cursor)

    def get(self, item_id):
        try:
            item_id = int(item_id)
        except (TypeError, ValueError):
            return None
        with self.db.connect_governance() as conn:
            cursor = conn.execute('SELECT * FROM error_records WHERE id = ?', (item_id,))
            return row_to_dict(cursor, cursor.fetchone())

    def create(self, item: dict) -> dict:
        errors = self.validate_item(item)
        if errors:
            raise ValueError('; '.join(errors))
        now = now_str()
        generated_sql = item.get('generated_sql') or ''
        with self.db.connect_governance() as conn:
            cursor = conn.execute(
                '''INSERT INTO error_records
                   (business_question, generated_sql, correct_sql, error_type, error_detail,
                    sql_pattern, frequency, is_resolved, created_at, last_occurred)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (item['business_question'].strip(), generated_sql, item.get('correct_sql') or '',
                 item.get('error_type') or '待分类', item.get('error_detail') or '',
                 generated_sql[:200], int(item.get('frequency', 1)),
                 int(item.get('is_resolved', 0)), now, now))
            conn.commit()
            new_id = cursor.lastrowid
        return self.get(new_id)

    def update(self, item_id, item: dict) -> dict:
        if self.get(item_id) is None:
            raise LookupError(f'错题记录不存在: {item_id}')
        errors = self.validate_item(item, partial=True)
        if errors:
            raise ValueError('; '.join(errors))
        sets, params = [], []
        for col in self._WRITABLE:
            if col in item:
                sets.append(f'{col} = ?')
                params.append(int(item[col]) if col in ('frequency', 'is_resolved') else item[col])
        if sets:
            # 与 app.py 一致：任意编辑都刷新 last_occurred
            sets.append('last_occurred = ?')
            params.append(now_str())
            params.append(int(item_id))
            with self.db.connect_governance() as conn:
                conn.execute(f'UPDATE error_records SET {", ".join(sets)} WHERE id = ?', tuple(params))
                conn.commit()
        return self.get(item_id)

    def delete(self, item_id) -> bool:
        try:
            item_id = int(item_id)
        except (TypeError, ValueError):
            return False
        with self.db.connect_governance() as conn:
            cursor = conn.execute('DELETE FROM error_records WHERE id = ?', (item_id,))
            conn.commit()
            return cursor.rowcount > 0

    def count(self) -> int:
        with self.db.connect_governance() as conn:
            return conn.execute('SELECT COUNT(*) FROM error_records').fetchone()[0]

    def template_examples(self) -> list:
        return [
            {'business_question': '统计各供电单位2026年3月的应收电费',
             'generated_sql': "SELECT mgt_org_name, SUM(rcvbl_amt) FROM dwd_cst_rcvbl_acct GROUP BY mgt_org_name",
             'correct_sql': '应同时输出 SUM(rcvbl_amt)/SUM(rcvd_amt)/SUM(arer_bal) 三口径',
             'error_type': '口径错误', 'error_detail': '应收口径缺 rcvd/arer 两列',
             'frequency': 1, 'is_resolved': 0},
        ]
