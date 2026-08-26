# -*- coding: utf-8 -*-
"""SQL 模板注册表资源：sql_knowledge 表（模板行 = sql_rule 为完整查询骨架）。

2026-08-15 三表合并（_knowledge_merge.py）；2026-08-19 两次瘦身：先删 kind 等 16 列
（_knowledge_slim.py），再删 match_signature/slot_spec（_knowledge_sigslot_slim.py，
业务裁定：业务冗余，消费端运行时推导——签名由 engine/knowledge_retriever 加载时现算，
槽位由 TemplateMatcher._fill_skeleton 按占位符约定推导）。
本 provider 管模板行（TPL_WHERE 过滤）；REST 机器名 'sql_template' 不变。

SQLTemplateMatcher.match 每次匹配前经 get_enabled_template_names() 读启用集合：
表缺失/异常/空表返回 None（=全部启用，行为与入库前一致）。

缓存约定同其他知识表：模块级缓存，CRUD 写后 invalidate_template_cache() 失效。
"""
import json
import threading

from core.database import DatabaseManager
from modules.resources.base import ResourceProvider, rows_to_dicts, row_to_dict, now_str

# 模板行/规则行的内容自证判定（kind 列已删；2026-08-19 再删 match_signature 后，
# 模板 = sql_rule 为完整查询骨架（SELECT/WITH 开头）；规则 = 其余（含 trigger_words 的
# 口径规则 + 词表类备管行）。两类互补且互斥，合计=全表。
# 注意：不要用 LIKE 'SELECT%'——pymysql 带参执行会对 SQL 中字面 % 做格式化（ValueError），
# 故用 SUBSTR 等值比较。
TPL_WHERE = ("(UPPER(SUBSTR(LTRIM(COALESCE(sql_rule, '')), 1, 6)) = 'SELECT'"
             " OR UPPER(SUBSTR(LTRIM(COALESCE(sql_rule, '')), 1, 4)) = 'WITH')")
RULE_WHERE = ("(NOT (UPPER(SUBSTR(LTRIM(COALESCE(sql_rule, '')), 1, 6)) = 'SELECT'"
              " OR UPPER(SUBSTR(LTRIM(COALESCE(sql_rule, '')), 1, 4)) = 'WITH'))")


def _ensure_table(db=None):
    """建表（IF NOT EXISTS，MySQL 方言，瘦身版 12 列）。读取路径首次访问时也会调用，保证任何环境可启动。"""
    db = db or DatabaseManager()
    with db.connect_governance() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS sql_knowledge (
                id INT PRIMARY KEY AUTO_INCREMENT,
                name VARCHAR(128) NOT NULL COMMENT '中文名称',
                description TEXT COMMENT '中文描述',
                sql_rule TEXT COMMENT 'SQL规则：模板=完整骨架(含槽位)；规则=SQL片段(表达式/过滤)',
                trigger_words TEXT COMMENT '规则触发关键词 JSON',
                sql_tables TEXT COMMENT '依赖表 JSON',
                example_qa_ids TEXT COMMENT '溯源题号',
                domain_l1 VARCHAR(16),
                domain_l2 VARCHAR(16),
                domain_l3 VARCHAR(16),
                enabled TINYINT(1) DEFAULT 1,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='统一SQL知识库（瘦身版）'
        ''')
        conn.commit()


class SQLTemplateProvider(ResourceProvider):
    name = 'sql_template'
    label = 'SQL 模板'
    description = '高频问题模板（sql_rule 为完整查询骨架的行；enabled 控制 SQLTemplateMatcher 是否启用）'
    entry_schema = [
        {'field': 'name', 'type': 'str', 'required': True},
        {'field': 'description', 'type': 'str', 'required': False},
        {'field': 'sql_rule', 'type': 'str', 'required': False},
        {'field': 'sql_tables', 'type': 'list', 'required': False},
        {'field': 'example_qa_ids', 'type': 'str', 'required': False},
        {'field': 'domain_l1', 'type': 'str', 'required': False},
        {'field': 'domain_l2', 'type': 'str', 'required': False},
        {'field': 'domain_l3', 'type': 'str', 'required': False},
        {'field': 'enabled', 'type': 'int', 'required': False},
    ]

    _WRITABLE = ('name', 'description', 'sql_rule', 'sql_tables', 'example_qa_ids',
                 'domain_l1', 'domain_l2', 'domain_l3', 'enabled')

    def __init__(self):
        self.db = DatabaseManager()
        _ensure_table(self.db)

    # ---------- 语义检索 ----------
    def retrieve(self, query: dict) -> dict:
        """query: {'question': str} -> 模板匹配结果（SQLTemplateMatcher，仅启用模板）"""
        query = query or {}
        question = query.get('question', '')
        if not question:
            return {'items': []}
        from modules.training.engine.sql_templates import SQLTemplateMatcher
        hit = SQLTemplateMatcher().match(question)
        return {'items': [hit] if hit else []}

    # ---------- CRUD ----------
    @staticmethod
    def _decode(row):
        """JSON 文本列转对象，便于前端编辑。"""
        for col in ('sql_tables',):
            if row and isinstance(row.get(col), str):
                try:
                    row[col] = json.loads(row[col])
                except Exception:
                    pass
        return row

    @staticmethod
    def _encode(col, value):
        if col == 'enabled':
            return int(value)
        if col == 'sql_tables' and isinstance(value, (list, dict)):
            return json.dumps(value, ensure_ascii=False)
        return value

    def list(self, filters=None, limit=200, offset=0) -> list:
        filters = filters or {}
        conditions, params = [TPL_WHERE], []
        if filters.get('enabled') is not None:
            conditions.append('enabled = ?')
            params.append(int(filters['enabled']))
        if filters.get('q'):
            conditions.append('(name LIKE ? OR description LIKE ?)')
            params.extend([f"%{filters['q']}%", f"%{filters['q']}%"])
        where = 'WHERE ' + ' AND '.join(conditions)
        with self.db.connect_governance() as conn:
            cursor = conn.execute(
                f'SELECT * FROM sql_knowledge {where} ORDER BY id LIMIT ? OFFSET ?',
                (*params, int(limit), int(offset)))
            return [self._decode(r) for r in rows_to_dicts(cursor)]

    def get(self, item_id):
        try:
            item_id = int(item_id)
        except (TypeError, ValueError):
            return None
        with self.db.connect_governance() as conn:
            cursor = conn.execute(
                f'SELECT * FROM sql_knowledge WHERE id = ? AND {TPL_WHERE}', (item_id,))
            return self._decode(row_to_dict(cursor, cursor.fetchone()))

    def create(self, item: dict) -> dict:
        errors = self.validate_item(item)
        if errors:
            raise ValueError('; '.join(errors))
        cols = [c for c in self._WRITABLE if c in item]
        with self.db.connect_governance() as conn:
            cursor = conn.execute(
                f'INSERT INTO sql_knowledge ({", ".join(cols)}, updated_at)'
                f' VALUES ({", ".join(["?"] * len(cols))}, ?)',
                tuple(self._encode(c, item[c]) for c in cols) + (now_str(),))
            conn.commit()
            new_id = cursor.lastrowid
        invalidate_template_cache()
        return self.get(new_id)

    def update(self, item_id, item: dict) -> dict:
        if self.get(item_id) is None:
            raise LookupError(f'SQL 模板不存在: {item_id}')
        errors = self.validate_item(item, partial=True)
        if errors:
            raise ValueError('; '.join(errors))
        sets, params = [], []
        for col in self._WRITABLE:
            if col in item:
                sets.append(f'{col} = ?')
                params.append(self._encode(col, item[col]))
        if sets:
            sets.append('updated_at = ?')
            params.append(now_str())
            params.append(int(item_id))
            with self.db.connect_governance() as conn:
                conn.execute(f'UPDATE sql_knowledge SET {", ".join(sets)} '
                             f'WHERE id = ? AND {TPL_WHERE}', tuple(params))
                conn.commit()
            invalidate_template_cache()
        return self.get(item_id)

    def delete(self, item_id) -> bool:
        try:
            item_id = int(item_id)
        except (TypeError, ValueError):
            return False
        with self.db.connect_governance() as conn:
            cursor = conn.execute(
                f'DELETE FROM sql_knowledge WHERE id = ? AND {TPL_WHERE}', (item_id,))
            conn.commit()
            deleted = cursor.rowcount > 0
        if deleted:
            invalidate_template_cache()
        return deleted

    def count(self) -> int:
        with self.db.connect_governance() as conn:
            return conn.execute(
                f'SELECT COUNT(*) FROM sql_knowledge WHERE {TPL_WHERE}').fetchone()[0]

    def template_examples(self) -> list:
        return [
            {'name': '按供电单位统计客户数量',
             'description': '各供电单位客户数统计',
             'sql_rule': "SELECT m.mgt_org_code, m.mgt_org_name, COUNT(DISTINCT c.cust_id) AS cust_count\n"
                         "FROM dim_cst_cust c LEFT JOIN dim_cst_mgt_org m ON c.mgt_org_code = m.mgt_org_code\n"
                         "GROUP BY m.mgt_org_code, m.mgt_org_name ORDER BY cust_count DESC",
             'sql_tables': ['dim_cst_cust', 'dim_cst_mgt_org'],
             'enabled': 1},
        ]


# ==================== 消费方读取口（模块级缓存 + 空表回退） ====================

_tpl_cache = None          # list[dict]，模板行
_cache_lock = threading.Lock()


def invalidate_template_cache():
    """CRUD 写操作后调用：下次读取重新查库。联动失效统一知识检索层缓存。"""
    global _tpl_cache
    with _cache_lock:
        _tpl_cache = None
    try:
        from core.knowledge_retriever import invalidate_knowledge_cache
        invalidate_knowledge_cache()
    except Exception:
        pass


def _get_rows() -> list:
    """模板行（TPL_WHERE：sql_rule 为完整查询骨架；共享进程级缓存，CRUD 后失效重建）。"""
    global _tpl_cache
    with _cache_lock:
        if _tpl_cache is None:
            rows = []
            try:
                _ensure_table()
                db = DatabaseManager()
                with db.connect_governance() as conn:
                    rows = rows_to_dicts(conn.execute(
                        f'SELECT * FROM sql_knowledge WHERE {TPL_WHERE}'))
            except Exception as e:
                print(f'[WARN] 读取 sql_knowledge(模板行) 失败（按全部启用处理）: {e}')
            _tpl_cache = rows
        return _tpl_cache


def get_enabled_template_names():
    """启用模板名集合。表缺失/异常/空表返回 None（=全部启用，行为与入库前一致）。"""
    rows = _get_rows()
    if not rows:
        return None
    return {r['name'] for r in rows if r.get('enabled')}


def get_signature_templates() -> list:
    """已废弃（2026-08-19：match_signature/slot_spec 列删除，签名/槽位改由
    engine/knowledge_retriever 与 TemplateMatcher 运行时推导，生成链路不再经此读取）。
    保留空实现仅为兼容历史调用方；表缺列/异常/无命中返回 []。"""
    return [{'id': r['id'], 'name': r['name'], 'skeleton': r.get('sql_rule') or '',
             'slot_spec': {}, 'signature': {}}
            for r in _get_rows() if r.get('enabled') and r.get('sql_rule')]
