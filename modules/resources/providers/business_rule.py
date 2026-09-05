# -*- coding: utf-8 -*-
"""业务口径规则资源：sql_knowledge 表（规则行 = sql_rule 非完整查询骨架）。

2026-08-15 三表合并（_knowledge_merge.py）；2026-08-19 两次瘦身（_knowledge_slim.py /
_knowledge_sigslot_slim.py）。kind/rule_type/rule_key/content/match_signature/slot_spec
等列已删除：行类型由内容自证（sql_rule 以 SELECT/WITH 开头=模板骨架行，其余=规则行，
见 sql_template provider 的 TPL_WHERE/RULE_WHERE），本 provider 管规则行（RULE_WHERE 过滤）；
REST 机器名 'business_rules' 不变。

瘦身后的字段约定：
- name：规则类型标识原样保留（'elec_caliber'/'family_synonym'/'draft_guard' 等）；
- description：原 content 并入（词表类规则的 JSON 数组也在此）；
- sql_rule：口径规则的 SQL 片段（"表达式：..."行 + "过滤条件：..."行，迁移时已合并）；
- family_synonym 的差异词（原 rule_key）约定为 description JSON 数组首元素
  （数据已核对满足：负荷/功率/高压/专变/公变 均为首元素），get_family_synonyms 按此取值。

入库项（原 engine/sql_generator.py 硬编码，当前值以迁移时文件为准）：
- *_caliber     口径类规则 9 条（trigger_words 触发 + sql_rule 片段优先注入）
- semantic_hint 字段语义速查 SEMANTIC_FIELD_HINTS（备管）
- family_synonym 同族表消歧同义词（description=JSON 等价表达数组，首元素=差异词）
- draft_guard     草稿直出守门过滤信号词（description=JSON 数组）
- analytical_kw   分析型问题关键词（description=JSON 数组，生成链路两处消费）

消费方读取约定（见模块底部各 get_* 函数）：
- 模块级缓存，首次读取时查库；provider CRUD 写操作后 invalidate_rule_cache() 失效重建；
- 表不存在/查询异常/无有效行时返回 None/[]，消费方回退代码常量，保证任何环境可启动。
"""
import json
import threading

from core.database import DatabaseManager
from modules.resources.base import ResourceProvider, rows_to_dicts, row_to_dict, now_str
from modules.resources.providers.sql_template import (
    _ensure_table as _ensure_knowledge_table, RULE_WHERE)


def _is_caliber_type(rule_type: str) -> bool:
    """口径类规则判定：name（原 rule_type）以 _caliber 结尾即视为口径注入规则
    （trigger_words 命中才注入）。新口径类型（如 area_caliber 台区口径）无需改代码即可接入。"""
    return bool(rule_type) and rule_type.endswith('_caliber')


def _ensure_table(db=None):
    """建表（IF NOT EXISTS，双方言；与 sql_template provider 共用 sql_knowledge DDL）。"""
    _ensure_knowledge_table(db or DatabaseManager())


class BusinessRuleProvider(ResourceProvider):
    name = 'business_rules'
    label = '业务口径规则'
    description = '生成链路按关键词注入的业务口径/同义词/守门词（非完整查询骨架的行）'
    entry_schema = [
        {'field': 'name', 'type': 'str', 'required': True},
        {'field': 'description', 'type': 'str', 'required': True},
        {'field': 'sql_rule', 'type': 'str', 'required': False},
        {'field': 'trigger_words', 'type': 'list', 'required': False},
        {'field': 'sql_tables', 'type': 'list', 'required': False},
        {'field': 'domain_l1', 'type': 'str', 'required': False},
        {'field': 'domain_l2', 'type': 'str', 'required': False},
        {'field': 'domain_l3', 'type': 'str', 'required': False},
        {'field': 'enabled', 'type': 'int', 'required': False},
    ]

    _WRITABLE = ('name', 'description', 'sql_rule', 'trigger_words', 'sql_tables',
                 'domain_l1', 'domain_l2', 'domain_l3', 'enabled')

    def __init__(self):
        self.db = DatabaseManager()
        _ensure_table(self.db)

    # ---------- 语义检索 ----------
    def retrieve(self, query: dict) -> dict:
        """query: {'rule_type': str} -> 该 name 全部启用行（rule_type 为旧字段名=name）；
        {'question': str} -> 口径类规则中触发词命中该问题的行（模拟生成链路）"""
        query = query or {}
        rows = [r for r in _get_rows() if r.get('enabled')]
        if query.get('rule_type'):
            rows = [r for r in rows if r['name'] == query['rule_type']]
        elif query.get('question'):
            q = query['question']
            hit = []
            for r in rows:
                if _is_caliber_type(r['name']):
                    triggers = _json_loads(r.get('trigger_words')) or []
                    if any(k in q for k in triggers):
                        hit.append(r)
            rows = hit
        return {'items': rows}

    # ---------- CRUD ----------
    @staticmethod
    def _decode(row):
        """JSON 文本列转对象，便于前端编辑。"""
        for col in ('trigger_words', 'sql_tables'):
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
        if col in ('trigger_words', 'sql_tables') and isinstance(value, list):
            return json.dumps(value, ensure_ascii=False)
        return value

    def list(self, filters=None, limit=200, offset=0) -> list:
        filters = filters or {}
        conditions, params = [RULE_WHERE], []
        if filters.get('rule_type'):
            conditions.append('name = ?')
            params.append(filters['rule_type'])
        if filters.get('enabled') is not None:
            conditions.append('enabled = ?')
            params.append(int(filters['enabled']))
        if filters.get('q'):
            conditions.append('(name LIKE ? OR description LIKE ?)')
            params.extend([f"%{filters['q']}%", f"%{filters['q']}%"])
        where = 'WHERE ' + ' AND '.join(conditions)
        with self.db.connect_governance() as conn:
            cursor = conn.execute(
                f'SELECT * FROM sql_knowledge {where} ORDER BY name, id LIMIT ? OFFSET ?',
                (*params, int(limit), int(offset)))
            return [self._decode(r) for r in rows_to_dicts(cursor)]

    def get(self, item_id):
        try:
            item_id = int(item_id)
        except (TypeError, ValueError):
            return None
        with self.db.connect_governance() as conn:
            cursor = conn.execute(
                f'SELECT * FROM sql_knowledge WHERE id = ? AND {RULE_WHERE}', (item_id,))
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
        invalidate_rule_cache()
        return self.get(new_id)

    def update(self, item_id, item: dict) -> dict:
        if self.get(item_id) is None:
            raise LookupError(f'业务规则不存在: {item_id}')
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
                             f'WHERE id = ? AND {RULE_WHERE}', tuple(params))
                conn.commit()
            invalidate_rule_cache()
        return self.get(item_id)

    def delete(self, item_id) -> bool:
        try:
            item_id = int(item_id)
        except (TypeError, ValueError):
            return False
        with self.db.connect_governance() as conn:
            cursor = conn.execute(
                f'DELETE FROM sql_knowledge WHERE id = ? AND {RULE_WHERE}', (item_id,))
            conn.commit()
            deleted = cursor.rowcount > 0
        if deleted:
            invalidate_rule_cache()
        return deleted

    def count(self) -> int:
        with self.db.connect_governance() as conn:
            return conn.execute(
                f'SELECT COUNT(*) FROM sql_knowledge WHERE {RULE_WHERE}').fetchone()[0]

    def template_examples(self) -> list:
        return [
            {'name': 'elec_caliber',
             'description': '【用电量查询口径——必须严格遵守】\n- ……（规则说明正文）',
             'sql_rule': '表达式：SUM(e.pap_e)',
             'trigger_words': ['电量', '用电', 'pap_e'], 'enabled': 1},
            {'name': 'family_synonym',
             'description': '["高压", "专变"]', 'enabled': 1},
        ]


# ==================== 消费方读取口（模块级缓存 + 空表回退） ====================

_rule_cache = None         # list[dict]，规则行
_cache_lock = threading.Lock()


def _json_loads(text):
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def invalidate_rule_cache():
    """CRUD 写操作后调用：下次读取重新查库。联动失效统一知识检索层缓存。"""
    global _rule_cache
    with _cache_lock:
        _rule_cache = None
    try:
        from core.knowledge_retriever import invalidate_knowledge_cache
        invalidate_knowledge_cache()
    except Exception:
        pass


def _get_rows() -> list:
    """规则行（RULE_WHERE：sql_rule 非完整查询骨架，按 id 序）。表缺失/异常返回 []。"""
    global _rule_cache
    with _cache_lock:
        if _rule_cache is None:
            rows = []
            try:
                _ensure_table()
                db = DatabaseManager()
                with db.connect_governance() as conn:
                    rows = rows_to_dicts(conn.execute(
                        f'SELECT * FROM sql_knowledge WHERE {RULE_WHERE} ORDER BY id'))
            except Exception as e:
                print(f'[WARN] 读取 sql_knowledge(规则行) 失败（消费方将回退代码常量）: {e}')
            _rule_cache = rows
        return _rule_cache


def get_caliber_rules() -> list:
    """口径注入规则 [{rule_type, content, triggers, sql_rule, sql_tables}]，按 id 序
    （电量口径在前，与原代码两处 if 的顺序一致）。无有效行返回 []（消费方回退常量）。
    rule_type/content 为兼容键名，分别取自瘦身表 name/description 列。"""
    rules = []
    for r in _get_rows():
        if _is_caliber_type(r['name']) and r.get('enabled'):
            rules.append({
                'rule_type': r['name'],
                'content': r.get('description') or '',
                'triggers': tuple(_json_loads(r.get('trigger_words')) or []),
                'sql_rule': r.get('sql_rule') or '',
                'sql_tables': _json_loads(r.get('sql_tables')) or [],
            })
    return rules


def render_caliber_injection(rule: dict) -> str:
    """口径规则注入文本渲染：sql_rule 非空时输出"口径片段头+片段正文+涉及表"，
    否则回退整段 description 文本（词表类规则/未回填行的原行为）。
    rule 可为 get_caliber_rules 行（content/triggers 键）或检索层 item（description 键）。"""
    sql_rule = rule.get('sql_rule') or ''
    desc = rule.get('description') or rule.get('content') or ''
    if sql_rule.strip():
        first_line = desc.split('\n')[0].strip('【】') or rule.get('rule_type') or rule.get('name', '')
        first_line = first_line.removesuffix('——必须严格遵守')  # 避免与片段头尾缀重复
        lines = [f'【{first_line}——口径片段，必须严格遵守】', sql_rule]
        tables = rule.get('sql_tables')
        if tables:
            lines.append('涉及表：' + ', '.join(tables) if isinstance(tables, list)
                         else f"涉及表：{tables}")
        return '\n'.join(lines)
    return desc


def get_family_synonyms() -> dict:
    """同族表消歧同义词 {差异词: tuple(等价表达)}，按 id 序。无有效行返回 {}。
    瘦身后 rule_key 列已删：差异词 = description JSON 数组首元素（迁移时核对过的约定）。"""
    out = {}
    for r in _get_rows():
        if r['name'] == 'family_synonym' and r.get('enabled'):
            group = _json_loads(r.get('description'))
            if group:
                out[group[0]] = tuple(group)
    return out


def get_draft_filter_signals():
    """草稿直出守门过滤信号词 tuple。无有效行返回 None。"""
    for r in _get_rows():
        if r['name'] == 'draft_guard' and r.get('enabled'):
            words = _json_loads(r.get('description'))
            if words:
                return tuple(words)
    return None


def get_analytical_keywords():
    """分析型问题关键词 tuple。无有效行返回 None。"""
    for r in _get_rows():
        if r['name'] == 'analytical_kw' and r.get('enabled'):
            words = _json_loads(r.get('description'))
            if words:
                return tuple(words)
    return None
