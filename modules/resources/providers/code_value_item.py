# -*- coding: utf-8 -*-
"""码值明细资源：code_value_items 表 CRUD（P2 放开）。

与 CodeValueProvider（码值域级）配套：域管 code_values，明细管 code_value_items。
注意：rag_retriever 的码值索引是进程内一次性加载（_cv_ready），
运行期增删改需重启应用才反映到生成链路（与 Excel 导入通道一致）。
"""
from core.database import DatabaseManager
from modules.resources.base import ResourceProvider, rows_to_dicts, row_to_dict
from core.rag_retriever import invalidate_code_value_index


class CodeValueItemProvider(ResourceProvider):
    name = 'code_value_item'
    label = '码值明细'
    description = '码值域的明细项（code_value_items，6198 行，分页管理）'
    entry_schema = [
        {'field': 'code_name', 'type': 'str', 'required': True},
        {'field': 'item_code', 'type': 'str', 'required': False},
        {'field': 'item_name', 'type': 'str', 'required': True},
        {'field': 'sort_order', 'type': 'int', 'required': False},
    ]

    _WRITABLE = ('code_name', 'item_code', 'item_name', 'sort_order')

    def __init__(self):
        self.db = DatabaseManager()

    # ---------- 语义检索 ----------
    def retrieve(self, query: dict) -> dict:
        """query: {'name': str} -> 按明细名称模糊命中的行（供条件值翻译排查）"""
        query = query or {}
        name = (query.get('name') or '').strip()
        if not name:
            return {'items': []}
        with self.db.connect_governance() as conn:
            rows = rows_to_dicts(conn.execute(
                'SELECT * FROM code_value_items WHERE item_name LIKE ? ORDER BY code_name, sort_order, id LIMIT 50',
                (f'%{name}%',)))
        return {'items': rows}

    # ---------- CRUD ----------
    def list(self, filters=None, limit=200, offset=0) -> list:
        filters = filters or {}
        conditions, params = [], []
        if filters.get('code_name'):
            conditions.append('code_name = ?')
            params.append(filters['code_name'])
        if filters.get('q'):
            conditions.append('(item_name LIKE ? OR item_code LIKE ? OR code_name LIKE ?)')
            params.extend([f"%{filters['q']}%"] * 3)
        where = ('WHERE ' + ' AND '.join(conditions)) if conditions else ''
        with self.db.connect_governance() as conn:
            cursor = conn.execute(
                f'SELECT * FROM code_value_items {where} ORDER BY code_name, sort_order, id LIMIT ? OFFSET ?',
                (*params, int(limit), int(offset)))
            return rows_to_dicts(cursor)

    def get(self, item_id):
        try:
            item_id = int(item_id)
        except (TypeError, ValueError):
            return None
        with self.db.connect_governance() as conn:
            cursor = conn.execute('SELECT * FROM code_value_items WHERE id = ?', (item_id,))
            return row_to_dict(cursor, cursor.fetchone())

    def create(self, item: dict) -> dict:
        errors = self.validate_item(item)
        if errors:
            raise ValueError('; '.join(errors))
        with self.db.connect_governance() as conn:
            # 域必须已存在（防孤儿明细）
            dom = conn.execute('SELECT COUNT(*) FROM code_values WHERE code_name = ?',
                               (item['code_name'].strip(),)).fetchone()[0]
            if not dom:
                raise ValueError(f"码值域不存在: {item['code_name']}（请先在码值资源中创建）")
            cursor = conn.execute(
                '''INSERT INTO code_value_items (code_name, item_code, item_name, sort_order)
                   VALUES (?, ?, ?, ?)''',
                (item['code_name'].strip(), item.get('item_code') or '',
                 item['item_name'].strip(), int(item.get('sort_order', 0))))
            conn.commit()
            new_id = cursor.lastrowid
        invalidate_code_value_index()  # R7：写后失效
        return self.get(new_id)

    def update(self, item_id, item: dict) -> dict:
        if self.get(item_id) is None:
            raise LookupError(f'码值明细不存在: {item_id}')
        errors = self.validate_item(item, partial=True)
        if errors:
            raise ValueError('; '.join(errors))
        sets, params = [], []
        for col in self._WRITABLE:
            if col in item:
                sets.append(f'{col} = ?')
                params.append(int(item[col]) if col == 'sort_order' else item[col])
        if sets:
            params.append(int(item_id))
            with self.db.connect_governance() as conn:
                conn.execute(f'UPDATE code_value_items SET {", ".join(sets)} WHERE id = ?', tuple(params))
                conn.commit()
            invalidate_code_value_index()  # R7：写后失效
        return self.get(item_id)

    def delete(self, item_id) -> bool:
        try:
            item_id = int(item_id)
        except (TypeError, ValueError):
            return False
        with self.db.connect_governance() as conn:
            cursor = conn.execute('DELETE FROM code_value_items WHERE id = ?', (item_id,))
            conn.commit()
            deleted = cursor.rowcount > 0
        if deleted:
            invalidate_code_value_index()  # R7：写后失效
        return deleted

    def count(self) -> int:
        with self.db.connect_governance() as conn:
            return conn.execute('SELECT COUNT(*) FROM code_value_items').fetchone()[0]

    def template_examples(self) -> list:
        return [
            {'code_name': 'cust_cls', 'item_code': '01', 'item_name': '高压', 'sort_order': 1},
            {'code_name': 'cust_cls', 'item_code': '02', 'item_name': '低压非居民', 'sort_order': 2},
        ]
