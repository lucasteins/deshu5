# -*- coding: utf-8 -*-
"""码值资源：code_values / code_value_items / code_value_column_form 只读视图
+ 委托 rag_retriever.retrieve_code_values / get_code_value_translations 检索。

P1 本期只读：码值由 Excel 导入与校核脚本两条既有链路维护，Web 写入留待 P2。
"""
from core.database import DatabaseManager
from modules.resources.base import ResourceProvider
from core.rag_retriever import invalidate_code_value_index


class CodeValueProvider(ResourceProvider):
    name = 'code_value'
    label = '码值'
    description = '码值域 + 明细 + 列存储形态（生成 WHERE 条件值翻译依赖）'
    entry_schema = [
        {'field': 'code_name', 'type': 'str', 'required': True},
        {'field': 'code_cn_name', 'type': 'str', 'required': False},
        {'field': 'domain_l1', 'type': 'str', 'required': False},
        {'field': 'domain_l2', 'type': 'str', 'required': False},
        {'field': 'data_type', 'type': 'str', 'required': False},
        {'field': 'description', 'type': 'str', 'required': False},
    ]

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
        """两种 payload：
        {'question': str, 'tables': [..], 'per_domain': n, 'max_domains': m}
            -> {'items': 语义命中的码值域（含存储形态 form 与 名称=编码 对照）}
        {'mode': 'translations'}
            -> {'items': 存编码列的 名称->编码 翻译表 [{table, column, name_to_code}]}"""
        query = query or {}
        rag = self._get_rag()
        if query.get('mode') == 'translations':
            items = [{'table': t, 'column': c, 'name_to_code': mapping}
                     for t, c, mapping in rag.get_code_value_translations()]
            return {'items': items}
        question = query.get('question', '')
        tables = query.get('tables') or []
        items = rag.retrieve_code_values(
            question, tables,
            per_domain=int(query.get('per_domain', 8)),
            max_domains=int(query.get('max_domains', 8)))
        return {'items': items}

    # ---------- 只读视图 ----------
    def list(self, filters=None, limit=200, offset=0) -> list:
        """码值域清单（含明细数与落列映射，同 /api/code-value-domains 口径）。"""
        filters = filters or {}
        q = (filters.get('q') or '').strip()
        conditions, params = [], []
        if q:
            conditions.append('(v.code_name LIKE ? OR v.code_cn_name LIKE ?)')
            params.extend([f'%{q}%', f'%{q}%'])
        where = ('WHERE ' + ' AND '.join(conditions)) if conditions else ''
        with self.db.connect_governance() as conn:
            cursor = conn.execute(
                f'''SELECT v.code_name, v.code_cn_name, v.domain_l1, v.domain_l2, v.data_type, v.description,
                           COUNT(i.id) AS item_count
                    FROM code_values v
                    LEFT JOIN code_value_items i ON i.code_name = v.code_name
                    {where}
                    GROUP BY v.code_name, v.code_cn_name, v.domain_l1, v.domain_l2, v.data_type, v.description
                    ORDER BY item_count DESC, v.code_name
                    LIMIT ? OFFSET ?''',
                (*params, int(limit), int(offset)))
            rows = cursor.fetchall()
            # 列↔域映射（来自校核形态表）
            col_map = {}
            try:
                for t, c, cn in conn.execute(
                        'SELECT table_name, column_name, code_name FROM code_value_column_form'):
                    col_map.setdefault(cn, []).append(f'{t}.{c}')
            except Exception:
                pass
        return [{
            'code_name': r[0],
            'code_cn_name': r[1] or '',
            'domain_l1': r[2] or '',
            'domain_l2': r[3] or '',
            'data_type': r[4] or '',
            'description': r[5] or '',
            'item_count': r[6],
            'columns': col_map.get(r[0], []),
        } for r in rows]

    def get(self, item_id):
        """单域详情：item_id 即 code_name；含明细与列存储形态。"""
        code_name = str(item_id or '').strip()
        if not code_name:
            return None
        with self.db.connect_governance() as conn:
            row = conn.execute(
                'SELECT code_name, code_cn_name, domain_l1, domain_l2, data_type, description '
                'FROM code_values WHERE code_name = ?', (code_name,)).fetchone()
            if row is None:
                return None
            items = [{'item_code': r[0], 'item_name': r[1], 'sort_order': r[2]}
                     for r in conn.execute(
                         'SELECT item_code, item_name, sort_order FROM code_value_items '
                         'WHERE code_name = ? ORDER BY sort_order, id', (code_name,))]
            forms = [{'table_name': r[0], 'column_name': r[1], 'form': r[2]}
                     for r in conn.execute(
                         'SELECT table_name, column_name, form FROM code_value_column_form '
                         'WHERE code_name = ?', (code_name,))]
        return {
            'code_name': row[0],
            'code_cn_name': row[1] or '',
            'domain_l1': row[2] or '',
            'domain_l2': row[3] or '',
            'data_type': row[4] or '',
            'description': row[5] or '',
            'items': items,
            'column_forms': forms,
        }

    def count(self) -> int:
        with self.db.connect_governance() as conn:
            return conn.execute('SELECT COUNT(*) FROM code_values').fetchone()[0]

    # ---------- CRUD（P2 放开；明细走 CodeValueItemProvider） ----------
    def create(self, item: dict) -> dict:
        errors = self.validate_item(item)
        if errors:
            raise ValueError('; '.join(errors))
        try:
            with self.db.connect_governance() as conn:
                conn.execute(
                    '''INSERT INTO code_values (code_name, code_cn_name, domain_l1, domain_l2, data_type, description)
                       VALUES (?, ?, ?, ?, ?, ?)''',
                    (item['code_name'].strip(), item.get('code_cn_name') or '',
                     item.get('domain_l1') or '', item.get('domain_l2') or '',
                     item.get('data_type') or '', item.get('description') or ''))
                conn.commit()
        except Exception as e:
            if 'UNIQUE' in str(e).upper() or 'Duplicate' in str(e):
                raise ValueError(f"码值域已存在: {item['code_name']}")
            raise
        invalidate_code_value_index()  # R7：写后失效，下次读取重建索引
        return self.get(item['code_name'].strip())

    def update(self, item_id, item: dict) -> dict:
        code_name = str(item_id or '').strip()
        if self.get(code_name) is None:
            raise LookupError(f'码值域不存在: {item_id}')
        errors = self.validate_item(item, partial=True)
        if errors:
            raise ValueError('; '.join(errors))
        # code_name 被 code_value_items/code_value_column_form 引用，不允许改名（防孤儿数据）
        if item.get('code_name') and item['code_name'].strip() != code_name:
            raise ValueError('code_name 为主键引用，不允许改名；请新建域后迁移明细')
        sets, params = [], []
        for col in ('code_cn_name', 'domain_l1', 'domain_l2', 'data_type', 'description'):
            if col in item:
                sets.append(f'{col} = ?')
                params.append(item[col])
        if sets:
            params.append(code_name)
            with self.db.connect_governance() as conn:
                conn.execute(f'UPDATE code_values SET {", ".join(sets)} WHERE code_name = ?', tuple(params))
                conn.commit()
            invalidate_code_value_index()  # R7：写后失效
        return self.get(code_name)

    def delete(self, item_id) -> bool:
        code_name = str(item_id or '').strip()
        if not code_name:
            return False
        with self.db.connect_governance() as conn:
            n_items = conn.execute('SELECT COUNT(*) FROM code_value_items WHERE code_name = ?',
                                   (code_name,)).fetchone()[0]
            if n_items:
                raise ValueError(f'该域存在 {n_items} 条明细，请先在码值明细中清理后再删除域')
            cursor = conn.execute('DELETE FROM code_values WHERE code_name = ?', (code_name,))
            conn.commit()
            deleted = cursor.rowcount > 0
        if deleted:
            invalidate_code_value_index()  # R7：写后失效
        return deleted

    def template_examples(self) -> list:
        return [
            {'code_name': 'pay_mode', 'code_cn_name': '缴费方式', 'domain_l1': 'Cst', 'domain_l2': 'Cst04',
             'data_type': 'varchar', 'description': '客户缴费方式码值域'},
        ]
