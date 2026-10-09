# -*- coding: utf-8 -*-
"""本体提炼脚本共享模块：档位连接、Schema 上下文组装、实体草稿校验与读写。

仅供本目录脚本 import（不作为命令直接执行）。
项目模块（core.*）经 sys.path 引导后按包导入，与 tools/ 脚本同惯例。
"""
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent          # skills/ontology-distill/scripts
SKILL_DIR = HERE.parent                          # skills/ontology-distill
PROJECT_ROOT = HERE.parents[2]                   # 项目根
DRAFTS_DIR = SKILL_DIR / 'drafts'
REPORTS_DIR = SKILL_DIR / 'reports'

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core import db_profile                      # noqa: E402
from core.database import DatabaseManager        # noqa: E402

LAYERS = ('master', 'business', 'report')
ENTITY_NAME_RE = re.compile(r'^[A-Za-z][A-Za-z0-9_]*$')


def current_profile() -> dict:
    """当前档位 {name, label, business, governance, log, ontology}。"""
    return db_profile.current()


def db() -> DatabaseManager:
    return DatabaseManager()


# ==================== Schema 上下文组装 ====================

def list_base_tables(mgr: DatabaseManager) -> list:
    """当前档位业务库表清单 [{name, comment, rows}]，按表名排序。"""
    biz = current_profile()['business']
    with mgr.connect_business() as conn:
        rows = conn.execute(
            'SELECT TABLE_NAME, TABLE_COMMENT, TABLE_ROWS '
            'FROM information_schema.TABLES '
            'WHERE TABLE_SCHEMA = %s AND TABLE_TYPE = %s ORDER BY TABLE_NAME',
            (biz, 'BASE TABLE')).fetchall()
    return [{'name': r[0], 'comment': r[1] or '', 'rows': r[2] or 0} for r in rows]


def table_columns(mgr: DatabaseManager, table: str) -> list:
    """单列清单 [{name, comment, type, pk}]（按 ordinal）。"""
    biz = current_profile()['business']
    with mgr.connect_business() as conn:
        rows = conn.execute(
            'SELECT COLUMN_NAME, COLUMN_COMMENT, COLUMN_TYPE, COLUMN_KEY '
            'FROM information_schema.COLUMNS '
            'WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s ORDER BY ORDINAL_POSITION',
            (biz, table)).fetchall()
    return [{'name': r[0], 'comment': r[1] or '', 'type': r[2] or '',
             'pk': r[3] == 'PRI'} for r in rows]


def physical_fks(mgr: DatabaseManager) -> list:
    """物理外键 [{table, column, ref_table, ref_column}]。"""
    biz = current_profile()['business']
    with mgr.connect_business() as conn:
        rows = conn.execute(
            'SELECT TABLE_NAME, COLUMN_NAME, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME '
            'FROM information_schema.KEY_COLUMN_USAGE '
            'WHERE CONSTRAINT_SCHEMA = %s AND REFERENCED_TABLE_NAME IS NOT NULL '
            'ORDER BY TABLE_NAME, ORDINAL_POSITION', (biz,)).fetchall()
    return [{'table': r[0], 'column': r[1], 'ref_table': r[2], 'ref_column': r[3]}
            for r in rows]


def keyword_map(mgr: DatabaseManager) -> dict:
    """治理库概念映射 {keyword: [table, ...]}（kjgl 已有种子；空库返回 {}）。"""
    out = {}
    try:
        with mgr.connect_governance() as conn:
            for kw, t in conn.execute(
                    'SELECT keyword, table_name FROM keyword_table_map '
                    'WHERE enabled = 1 ORDER BY keyword'):
                out.setdefault(kw, []).append(t)
    except Exception:
        pass
    return out


def assemble_schema_context(mgr: DatabaseManager, max_col: int = 25) -> dict:
    """LLM 提案用的完整 Schema 上下文。"""
    tables = list_base_tables(mgr)
    for t in tables:
        cols = table_columns(mgr, t['name'])
        # 主键/有注释的列优先，超长截断（保留前 max_col 个并标注总数）
        key_cols = [c for c in cols if c['pk'] or c['comment']]
        rest = [c for c in cols if not (c['pk'] or c['comment'])]
        picked = (key_cols + rest)[:max_col]
        t['columns'] = picked
        t['column_count'] = len(cols)
        t['columns_truncated'] = len(cols) > len(picked)
    return {'profile': current_profile(), 'tables': tables,
            'fks': physical_fks(mgr), 'keyword_map': keyword_map(mgr)}


def render_context_text(ctx: dict) -> str:
    """Schema 上下文 → prompt 文本段。"""
    lines = []
    for t in ctx['tables']:
        head = f"表 {t['name']}"
        if t['comment']:
            head += f"（{t['comment']}）"
        head += f"，约 {t['rows']} 行，{t['column_count']} 列"
        if t['columns_truncated']:
            head += '（仅列关键列）'
        lines.append(head)
        for c in t['columns']:
            seg = f"  - {c['name']} {c['type']}"
            if c['pk']:
                seg += ' [主键]'
            if c['comment']:
                seg += f"：{c['comment']}"
            lines.append(seg)
    if ctx['fks']:
        lines.append('')
        lines.append('物理外键（JOIN 边）：')
        for fk in ctx['fks']:
            lines.append(f"  - {fk['table']}.{fk['column']} -> "
                         f"{fk['ref_table']}.{fk['ref_column']}")
    if ctx['keyword_map']:
        lines.append('')
        lines.append('已登记的业务概念→表映射（供参考，勿推翻重来）：')
        for kw, ts in sorted(ctx['keyword_map'].items()):
            lines.append(f"  - {kw}: {', '.join(ts)}")
    return '\n'.join(lines)


# ==================== 实体草稿校验与读写 ====================

def load_draft(path: str) -> dict:
    """读草稿 JSON（必须含 entities 数组）。"""
    with open(path, encoding='utf-8') as f:
        draft = json.load(f)
    if not isinstance(draft, dict) or not isinstance(draft.get('entities'), list):
        raise ValueError('草稿格式错误：顶层必须是含 entities 数组的对象')
    return draft


def save_draft(path: Path, draft: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp, path)


def validate_draft(draft: dict, base_tables: list) -> tuple:
    """校验实体草稿。返回 (errors, warnings)；errors 非空即不可用。

    V1 name 合法（英文标识符）；V2 layer ∈ master/business/report；
    V3 member_tables ⊆ 底座实有表（未知表 = error）；
    V4 覆盖检查：未覆盖表 = warning（关系表就近挂主导实体后即可覆盖）；
    V5 一表多挂 = warning（允许，但提示确认）；
    V6 name 重复 = error。
    """
    errors, warnings = [], []
    table_set = {t['name'] for t in base_tables}
    entities = draft['entities']
    seen, covered = set(), {}

    for i, e in enumerate(entities):
        name = (e.get('name') or '').strip()
        where = f'entities[{i}]' + (f'({name})' if name else '')
        if not name or not ENTITY_NAME_RE.match(name):
            errors.append(f'{where}: name 缺失或非法（需英文标识符）')
        elif name in seen:
            errors.append(f'{where}: name 重复')
        seen.add(name)
        layer = (e.get('layer') or '').strip()
        if layer not in LAYERS:
            errors.append(f'{where}: layer 必须是 {"/".join(LAYERS)} 之一')
        if not (e.get('label') or '').strip():
            warnings.append(f'{where}: label（中文名）为空')
        members = e.get('member_tables')
        if not isinstance(members, list) or not members:
            errors.append(f'{where}: member_tables 为空（实体至少挂一张表）')
            continue
        for t in members:
            if t not in table_set:
                errors.append(f'{where}: member_tables 含底座不存在的表 {t}')
            covered.setdefault(t, []).append(name)

    uncovered = sorted(table_set - set(covered))
    if uncovered:
        warnings.append(f'未覆盖 {len(uncovered)} 张表（将落入 Unmapped）: {uncovered}')
    for t, names in sorted(covered.items()):
        if len(names) > 1:
            warnings.append(f'表 {t} 被多个实体共享 {names}（允许，确认是否有意）')
    return errors, warnings
