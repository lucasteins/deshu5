# -*- coding: utf-8 -*-
"""关键词-表关联资源：keyword_table_map 表。

统一两处硬编码：
- engine/sql_generator.py 的 KEYWORD_TO_TABLE_MAP（v2.4 回退定位）
- engine/intent_parser.py 的 concept_to_tables（v3 意图解析）

2026-08-07 起取消 scope 字段：全部映射全域共用（两个消费方读同一全集）。
2026-08-20 起删除 ord_v24/ord_intent：368/356 行为 NULL 的历史迁移残留排序键，
行序由 id 天然保持（迁移时按原常量顺序插入），两个 scope 排序统一为 id 序。

消费方读取约定（见模块底部 get_keyword_table_map）：
- 模块级缓存，首次读取时查库；provider CRUD 写操作后 invalidate_keyword_cache() 失效重建；
- 表不存在/查询异常/无有效行时返回 {}，消费方回退代码常量，保证任何环境可启动。
"""
import threading

from core.database import DatabaseManager
from modules.resources.base import ResourceProvider, rows_to_dicts, row_to_dict, now_str


def _ensure_table(db=None):
    """建表（IF NOT EXISTS，双方言）。读取路径首次访问时也会调用，保证任何环境可启动。"""
    db = db or DatabaseManager()
    with db.connect_governance() as conn:
        if db.get_dialect() == 'mysql':
            conn.execute('''
                CREATE TABLE IF NOT EXISTS keyword_table_map (
                    id INT PRIMARY KEY AUTO_INCREMENT,
                    keyword VARCHAR(64),
                    table_name VARCHAR(128),
                    enabled TINYINT(1) DEFAULT 1,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE KEY uk_ktm (keyword, table_name)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            ''')
        else:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS keyword_table_map (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    keyword VARCHAR(64),
                    table_name VARCHAR(128),
                    enabled BOOLEAN DEFAULT 1,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (keyword, table_name)
                )
            ''')
        conn.commit()


class KeywordTableProvider(ResourceProvider):
    name = 'keyword_table_map'
    label = '关键词-表关联'
    description = '业务关键词 → 数据表映射（全域共用：v2.4 回退定位与 v3 意图解析读同一全集）'
    entry_schema = [
        {'field': 'keyword', 'type': 'str', 'required': True},
        {'field': 'table_name', 'type': 'str', 'required': True},
        {'field': 'enabled', 'type': 'int', 'required': False},
    ]

    _WRITABLE = ('keyword', 'table_name', 'enabled')

    def __init__(self):
        self.db = DatabaseManager()
        _ensure_table(self.db)

    # ---------- 语义检索 ----------
    def retrieve(self, query: dict) -> dict:
        """query: {'keyword': str} -> {'items': 该关键词的映射行（含表清单 tables）}"""
        query = query or {}
        keyword = (query.get('keyword') or '').strip()
        if not keyword:
            return {'items': []}
        with self.db.connect_governance() as conn:
            rows = rows_to_dicts(conn.execute(
                'SELECT * FROM keyword_table_map WHERE keyword = ? AND enabled = 1 ORDER BY id',
                (keyword,)))
        tables = []
        for r in rows:
            if r['table_name'] not in tables:
                tables.append(r['table_name'])
        return {'items': [{'keyword': keyword, 'tables': tables, 'rows': rows}] if rows else []}

    # ---------- CRUD ----------
    def list(self, filters=None, limit=200, offset=0) -> list:
        filters = filters or {}
        conditions, params = [], []
        if filters.get('enabled') is not None:
            conditions.append('enabled = ?')
            params.append(int(filters['enabled']))
        if filters.get('q'):
            conditions.append('(keyword LIKE ? OR table_name LIKE ?)')
            params.extend([f"%{filters['q']}%", f"%{filters['q']}%"])
        where = ('WHERE ' + ' AND '.join(conditions)) if conditions else ''
        with self.db.connect_governance() as conn:
            cursor = conn.execute(
                f'SELECT * FROM keyword_table_map {where} ORDER BY keyword, id LIMIT ? OFFSET ?',
                (*params, int(limit), int(offset)))
            return rows_to_dicts(cursor)

    def get(self, item_id):
        try:
            item_id = int(item_id)
        except (TypeError, ValueError):
            return None
        with self.db.connect_governance() as conn:
            cursor = conn.execute('SELECT * FROM keyword_table_map WHERE id = ?', (item_id,))
            return row_to_dict(cursor, cursor.fetchone())

    def create(self, item: dict) -> dict:
        errors = self.validate_item(item)
        if errors:
            raise ValueError('; '.join(errors))
        with self.db.connect_governance() as conn:
            cursor = conn.execute(
                '''INSERT INTO keyword_table_map (keyword, table_name, enabled, updated_at)
                   VALUES (?, ?, ?, ?)''',
                (item['keyword'].strip(), item['table_name'].strip(),
                 int(item.get('enabled', 1)), now_str()))
            conn.commit()
            new_id = cursor.lastrowid
        invalidate_keyword_cache()
        return self.get(new_id)

    def update(self, item_id, item: dict) -> dict:
        if self.get(item_id) is None:
            raise LookupError(f'关键词-表关联不存在: {item_id}')
        errors = self.validate_item(item, partial=True)
        if errors:
            raise ValueError('; '.join(errors))
        sets, params = [], []
        for col in self._WRITABLE:
            if col in item:
                sets.append(f'{col} = ?')
                params.append(int(item[col]) if col == 'enabled'
                              and item[col] is not None else item[col])
        if sets:
            sets.append('updated_at = ?')
            params.append(now_str())
            params.append(int(item_id))
            with self.db.connect_governance() as conn:
                conn.execute(f'UPDATE keyword_table_map SET {", ".join(sets)} WHERE id = ?', tuple(params))
                conn.commit()
            invalidate_keyword_cache()
        return self.get(item_id)

    def delete(self, item_id) -> bool:
        try:
            item_id = int(item_id)
        except (TypeError, ValueError):
            return False
        with self.db.connect_governance() as conn:
            cursor = conn.execute('DELETE FROM keyword_table_map WHERE id = ?', (item_id,))
            conn.commit()
            deleted = cursor.rowcount > 0
        if deleted:
            invalidate_keyword_cache()
        return deleted

    def count(self) -> int:
        with self.db.connect_governance() as conn:
            return conn.execute('SELECT COUNT(*) FROM keyword_table_map').fetchone()[0]

    def template_examples(self) -> list:
        return [
            {'keyword': '电量', 'table_name': 'dwd_cst_meter_energy_day_h_xz', 'enabled': 1},
            {'keyword': '台区', 'table_name': 'dim_cst_dist_sta', 'enabled': 1},
        ]


# ==================== 消费方读取口（模块级缓存 + 空表回退） ====================

_map_cache = None          # {'v24_fallback': {kw: [tables]}, 'intent': {...}}
_cache_lock = threading.Lock()


def invalidate_keyword_cache():
    """CRUD 写操作后调用：下次读取重新查库。"""
    global _map_cache
    with _cache_lock:
        _map_cache = None


def _load_maps() -> dict:
    """从治理库重建两个消费方的映射（全域共用同一全集，按 id 序——2026-08-20 起 ord_* 已删）。"""
    out = {'v24_fallback': {}, 'intent': {}}
    try:
        _ensure_table()
        db = DatabaseManager()
        with db.connect_governance() as conn:
            cursor = conn.execute(
                'SELECT keyword, table_name FROM keyword_table_map WHERE enabled = 1 ORDER BY id')
            rows = cursor.fetchall()
            for scope in out:
                for kw, tbl in rows:
                    out[scope].setdefault(kw, []).append(tbl)
    except Exception as e:
        print(f'[WARN] 读取 keyword_table_map 失败（消费方将回退代码常量）: {e}')
    return out


def get_keyword_table_map(scope: str) -> dict:
    """消费方读取口：scope='v24_fallback' | 'intent'，返回 {keyword: [table, ...]}。
    表缺失/异常/该 scope 无有效行时返回 {}，消费方据此回退代码常量。"""
    global _map_cache
    with _cache_lock:
        if _map_cache is None:
            _map_cache = _load_maps()
        return _map_cache.get(scope) or {}
