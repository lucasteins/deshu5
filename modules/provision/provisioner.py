# -*- coding: utf-8 -*-
"""素材提资转换器：xlsx 模板 → 解析 → 校验 → 字段级溯源转换入库 → LLM 批量标注 → 人工复核

《素材提资页面设计方案》（已批准 2026-08-20）实现。四步工作流：上传解析 → 校验与映射预览
（字段级溯源图例）→ 执行转换（直接转换入库 + LLM 批量标注）→ 人工复核队列。

溯源（ingest_provenance）逐字段记录：
- direct  直接转换（模板单元格原值），source_ref=Sheet!单元格（如 S5!C3），review_status='na'
- system  系统推导（映射/解析/拼接/probe），source_ref=推导方式，review_status='na'
- llm     LLM 标注（批量≤10条 JSON 数组输出），source_ref='llm:模型名'，review_status='pending'
- manual  人工标注（维护人空缺/LLM 降级/复核改值），复核确认后 review_status='confirmed'
页面展示时 direct+system 合并为"直接转换"。

模板 9 Sheet：S2-管理元数据 / S3-码值维度 / S3-码值明细 / S4-表关联关系 / S5-问答对素材 /
S6-业务规则（00b-枚举字典、00c-二级业务分类为校验依据，00-填写说明忽略）。
表头兼容"（必填）/（选填）/✅"标记（匹配时剥离）。
"""
import json
import os
import re
import time
import uuid
from datetime import datetime

import openpyxl

from core.database import DatabaseManager

UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'uploads')

LAYERS = ('DIM', 'DWD', 'DWS', 'ADS')

# Sheet 识别：sheet 名关键词 → (sheet_key, 单元格引用前缀)
# 注意：长关键词必须先匹配——'S3-码值明细（运营组，如S3-码值维度有新增则需梳理明细）'
# 的括号说明里含"S3-码值维度"，先匹配短词会把明细 Sheet 误判成维度 Sheet
SHEET_KEYS = (
    ('S2-管理元数据', 'S2'),
    ('S2b-字段明细', 'S2B'),
    ('S3-码值明细', 'S3I'),
    ('S3-码值维度', 'S3D'),
    ('S4-表关联关系', 'S4'),
    ('S5-问答对素材', 'S5'),
    ('S6-业务规则', 'S6'),
)

# 目标表字段白名单（confirm 回填防注入）
TARGET_FIELDS = {
    'schema_table_docs': {'table_name', 'table_comment', 'row_count', 'column_count',
                          'doc_text', 'domain_l1', 'domain_l2', 'domain_l3'},
    'schema_column_docs': {'table_name', 'column_name', 'column_comment',
                           'data_type', 'is_pk', 'doc_text'},
    'code_values': {'code_name', 'code_cn_name', 'data_type', 'description',
                    'domain_l1', 'domain_l2', 'domain_l3'},
    'code_value_items': {'code_name', 'item_code', 'item_name', 'sort_order'},
    'code_value_column_form': {'table_name', 'column_name', 'code_name', 'form'},
    'schema_relationship_docs': {'title', 'path', 'join_conditions', 'business_scenarios', 'doc_text'},
    'qa_pairs': {'question', 'standard_sql', 'difficulty', 'tags', 'source', 'answer',
                 'explanation', 'objects_involved', 'generation_method', 'is_usable',
                 'domain_l1', 'domain_l2', 'domain_l3', 'ingest_time', 'maintainer'},
    'sql_knowledge': {'name', 'description', 'sql_rule', 'trigger_words', 'sql_tables',
                      'example_qa_ids', 'domain_l1', 'domain_l2', 'domain_l3', 'enabled'},
}


# ==================== 建表 ====================

def ensure_tables(db=None):
    """ingest_runs / ingest_provenance 建表（IF NOT EXISTS，MySQL 方言）。"""
    db = db or DatabaseManager()
    with db.connect_governance() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS ingest_runs (
                run_id VARCHAR(32) PRIMARY KEY,
                file_name VARCHAR(255),
                status VARCHAR(16),
                total_rows INT,
                ok_rows INT,
                review_rows INT,
                fail_rows INT,
                created_at DATETIME,
                finished_at DATETIME
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS ingest_provenance (
                id INT PRIMARY KEY AUTO_INCREMENT,
                run_id VARCHAR(32),
                sheet_name VARCHAR(64),
                src_row INT,
                target_table VARCHAR(64),
                target_key VARCHAR(128),
                field_name VARCHAR(64),
                source_kind VARCHAR(8),
                source_ref VARCHAR(64),
                field_value TEXT,
                review_status VARCHAR(12) DEFAULT 'na',
                created_at DATETIME,
                reviewed_at DATETIME,
                KEY idx_prov_run (run_id),
                KEY idx_prov_target (target_table, target_key),
                KEY idx_prov_review (review_status)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        ''')
        conn.commit()


# ==================== 解析 ====================

def _norm_header(h):
    """表头归一：去空白与（必填）/（选填）/✅ 标记。"""
    if h is None:
        return ''
    h = re.sub(r'\s+', '', str(h))
    h = re.sub(r'（(必填|选填)）', '', h)
    return h.replace('✅', '').strip()


def _sheet_key(name):
    for kw, key in SHEET_KEYS:
        if kw in name:
            return key
    return None


def parse_workbook(path: str) -> dict:
    """openpyxl 只读解析 → {sheet_key: {'title', 'headers': [归一表头], 'rows': [{字段: {'v','cell'}}]}}。
    rows 的键为归一表头；cell 为 Sheet!列字母行号（如 S5!C3），供 direct 溯源。"""
    from openpyxl.utils import get_column_letter
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    parsed = {}
    for ws in wb.worksheets:
        key = _sheet_key(ws.title)
        if not key:
            continue
        rows_iter = ws.iter_rows()
        header_row = None
        headers = []
        data = []
        # enumerate 行号对齐 Excel 物理行号；空单元格在只读模式下是 EmptyCell
        # （无 column/row 属性），用枚举序号而非 cell.column/cell.row 取坐标
        for excel_row_no, row in enumerate(rows_iter, start=1):
            if header_row is None:
                headers = [_norm_header(c.value) for c in row]
                if any(headers):
                    header_row = excel_row_no
                continue
            rec = {}
            empty = True
            for i, cell in enumerate(row):
                if i >= len(headers) or not headers[i]:
                    continue
                v = cell.value
                if isinstance(v, str):
                    v = v.strip()
                if v not in (None, ''):
                    empty = False
                rec[headers[i]] = {
                    'v': v,
                    'cell': f'{key}!{get_column_letter(i + 1)}{excel_row_no}',
                }
            if not empty:
                data.append({'src_row': excel_row_no, 'fields': rec})
        parsed[key] = {'title': ws.title, 'headers': [h for h in headers if h], 'rows': data}
    wb.close()
    return parsed


def _f(rec, *names):
    """取记录字段（按候选名归一表头匹配，长名优先防 描述/描述列名 碰撞）。"""
    for n in sorted(names, key=len, reverse=True):
        if n in rec['fields']:
            item = rec['fields'][n]
            return item['v'], item['cell']
    return None, None


# ==================== 校验 ====================

def _domain_maps(db):
    """business_domains：一级 中文名→code；二级 code 集合。"""
    l1, l2 = {}, set()
    with db.connect_governance() as conn:
        for code, name, level in conn.execute(
                'SELECT domain_code, domain_name, level FROM business_domains WHERE is_active = 1'):
            if level == 1:
                l1[name] = code
            elif level == 2:
                l2.add(code)
    return l1, l2


def _biz_table_cols(db):
    """marketing_40 information_schema：{table: set(columns)}（S4/S5 校验依据）。"""
    out = {}
    with db.connect_business() as conn:
        cursor = conn.execute(
            'SELECT TABLE_NAME, COLUMN_NAME FROM information_schema.COLUMNS '
            'WHERE TABLE_SCHEMA = DATABASE()')
        for t, c in cursor.fetchall():
            out.setdefault(t, set()).add(c)
    return out


def _probe_sql(db, sql: str):
    """S5 SQL 试执行（剥尾 LIMIT 后 LIMIT 1）。返回 None=通过，否则错误文本。"""
    if not sql or not re.match(r'(?i)^\s*(SELECT|WITH)\b', sql.strip()):
        return 'SQL 为空或非 SELECT'
    try:
        test_sql = re.sub(r'\s+LIMIT\s+\d+\s*$', '', sql.rstrip(';').strip(), flags=re.IGNORECASE)
        with db.connect_business() as conn:
            conn.execute(test_sql + ' LIMIT 1')
        return None
    except Exception as e:
        return str(e)[:200]


def _code_value_maps(db):
    """库内码值现状：{code_name: (中文名, 类型, 描述)} 与 {(code_name,item_code): item_name}。
    供 S3 交叉校核（改进2，2026-08-20）：防"模板错误值静默覆盖库内正确值"事故复发。"""
    dims, items = {}, {}
    with db.connect_governance() as conn:
        for cn, ccn, dt, desc in conn.execute(
                'SELECT code_name, code_cn_name, data_type, description FROM code_values'):
            dims[cn] = (ccn or '', dt or '', desc or '')
        for cn, ic, inm in conn.execute(
                'SELECT code_name, item_code, item_name FROM code_value_items'):
            items[(cn, str(ic))] = inm or ''
    return dims, items


def validate(parsed: dict, db=None) -> dict:
    """校验（00b/00c 为依据）：层级∈{DIM,DWD,DWS,ADS}、所属域∈10域、二级分类∈business_domains、
    必填非空、码值明细 code_name⊆码值维度、S4 表/列在 marketing_40 存在、S5 SQL probe 试执行。
    返回 {sheet_key: {'ok': n, 'warn': n, 'fail': n, 'issues': [{'row','level','msg'}]}}。"""
    db = db or DatabaseManager()
    l1_map, l2_codes = _domain_maps(db)
    biz_cols = _biz_table_cols(db)
    cv_dims, cv_items = _code_value_maps(db)  # 改进2：库内码值现状（交叉校核依据）
    result = {}

    def _issue(bucket, row, level, msg):
        bucket['issues'].append({'row': row, 'level': level, 'msg': msg})
        bucket[level] += 1

    # ---- S2 管理元数据 ----
    if 'S2' in parsed:
        b = {'ok': 0, 'warn': 0, 'fail': 0, 'issues': []}
        for rec in parsed['S2']['rows']:
            r = rec['src_row']
            tname, _ = _f(rec, '表名')
            cname, _ = _f(rec, '中文名')
            dom, _ = _f(rec, '所属域')
            sub, _ = _f(rec, '二级业务分类')
            layer, _ = _f(rec, '层级')
            rows_cnt, _ = _f(rec, '数据行数')
            span, _ = _f(rec, '数据时间范围')
            bad = False
            for label, v in (('表名', tname), ('中文名', cname), ('所属域', dom),
                             ('层级', layer), ('数据行数', rows_cnt), ('数据时间范围', span)):
                if v in (None, ''):
                    _issue(b, r, 'fail', f'必填字段[{label}]为空')
                    bad = True
            if layer and str(layer).upper() not in LAYERS:
                _issue(b, r, 'fail', f'层级[{layer}]不在 {LAYERS}')
                bad = True
            if dom and dom not in l1_map:
                _issue(b, r, 'fail', f'所属域[{dom}]不在 10 个一级业务域')
                bad = True
            if sub:
                # 域码前缀长度不固定（Grid 为 4 字母，其余 3 字母），用 + 而非 {3}
                m = re.match(r'^([A-Za-z]+\d{2})', str(sub))
                if not m or m.group(1) not in l2_codes:
                    _issue(b, r, 'warn', f'二级业务分类[{sub}]无法解析到 business_domains')
            if tname and tname not in biz_cols:
                _issue(b, r, 'warn', f'表[{tname}]在 marketing_40 不存在（仅建档，不阻断）')
            if not bad:
                b['ok'] += 1
        result['S2'] = b

    # ---- S2b 字段明细（字段级元数据 → schema_column_docs）----
    if 'S2B' in parsed:
        b = {'ok': 0, 'warn': 0, 'fail': 0, 'issues': []}
        for rec in parsed['S2B']['rows']:
            r = rec['src_row']
            tname, _ = _f(rec, '表名')
            colname, _ = _f(rec, '字段名')
            cmt, _ = _f(rec, '字段中文注释')
            dt, _ = _f(rec, '数据类型')
            ispk, _ = _f(rec, '是否主键')
            bad = False
            for label, v in (('表名', tname), ('字段名', colname),
                             ('字段中文注释', cmt), ('数据类型', dt), ('是否主键', ispk)):
                if v in (None, ''):
                    _issue(b, r, 'fail', f'必填字段[{label}]为空')
                    bad = True
            # 是否主键取值应为 0/1
            if ispk not in (None, '') and str(ispk).strip() not in ('0', '1'):
                _issue(b, r, 'warn', f'是否主键[{ispk}]非 0/1，按 0 处理')
            # 表/列存在性交叉校核（仅 warn，允许建档未来表）
            if tname and tname not in biz_cols:
                _issue(b, r, 'warn', f'表[{tname}]在 marketing_40 不存在（仅建档，不阻断）')
            elif tname and colname and colname not in biz_cols[tname]:
                _issue(b, r, 'warn', f'字段[{tname}.{colname}]在 marketing_40 不存在（仅建档，不阻断）')
            if not bad:
                b['ok'] += 1
        result['S2B'] = b

    # ---- S3 码值维度 ----
    s3d_names = set()
    if 'S3D' in parsed:
        b = {'ok': 0, 'warn': 0, 'fail': 0, 'issues': []}
        for rec in parsed['S3D']['rows']:
            r = rec['src_row']
            cn, _ = _f(rec, 'code_name')
            ccn, _ = _f(rec, '中文名')
            dom, _ = _f(rec, '所属域')
            dt, _ = _f(rec, '数据类型')
            desc, _ = _f(rec, '描述')  # 注意：'描述'≠'描述列名'（归一表头是精确键，勿混用候选）
            bad = False
            for label, v in (('code_name', cn), ('中文名', ccn), ('数据类型', dt), ('描述', desc)):
                if v in (None, ''):
                    _issue(b, r, 'fail', f'必填字段[{label}]为空')
                    bad = True
            # 长度须落在 code_values 列宽内，超限提前 fail（否则执行期 DataError 中断整个 run）
            for label, v, lim in (('code_name', cn, 64), ('中文名', ccn, 255),
                                  ('数据类型', dt, 32), ('描述', desc, 255)):
                if v not in (None, '') and len(str(v)) > lim:
                    _issue(b, r, 'fail', f'字段[{label}]长度 {len(str(v))} 超过列宽 {lim}：{str(v)[:40]}')
                    bad = True
            if dom and dom not in l1_map:
                _issue(b, r, 'warn', f'所属域[{dom}]不在 10 个一级业务域')
            if cn:
                s3d_names.add(str(cn))
                # 改进2 交叉校核①：code_name 库内已存在 → data_type/中文名/描述不一致列 warn
                # （域级差异，不阻断；静默覆盖事故防复发点之一）
                if str(cn) in cv_dims:
                    db_ccn, db_dt, db_desc = cv_dims[str(cn)]
                    for label, tpl_v, db_v in (('中文名', ccn, db_ccn),
                                               ('数据类型', dt, db_dt),
                                               ('描述', desc, db_desc)):
                        if str(tpl_v or '') != str(db_v or ''):
                            _issue(b, r, 'warn',
                                   f'域级差异[{cn}.{label}]：库内[{db_v or "空"}] vs 模板[{tpl_v or "空"}]')
            if not bad:
                b['ok'] += 1
        result['S3D'] = b

    # ---- S3 码值明细 ----
    if 'S3I' in parsed:
        b = {'ok': 0, 'warn': 0, 'fail': 0, 'issues': []}
        for rec in parsed['S3I']['rows']:
            r = rec['src_row']
            cn, _ = _f(rec, 'code_name')
            ic, _ = _f(rec, 'item_code')
            inm, _ = _f(rec, 'item_name')
            bad = False
            for label, v in (('code_name', cn), ('item_code', ic), ('item_name', inm)):
                if v in (None, ''):
                    _issue(b, r, 'fail', f'必填字段[{label}]为空')
                    bad = True
            # 长度须落在 code_value_items 列宽内，超限提前 fail（否则执行期 DataError 中断整个 run）
            for label, v, lim in (('code_name', cn, 64), ('item_code', ic, 64), ('item_name', inm, 255)):
                if v not in (None, '') and len(str(v)) > lim:
                    _issue(b, r, 'fail', f'字段[{label}]长度 {len(str(v))} 超过列宽 {lim}：{str(v)[:40]}')
                    bad = True
            if cn and s3d_names and str(cn) not in s3d_names:
                _issue(b, r, 'fail', f'code_name[{cn}]不在 S3-码值维度（明细须⊆维度）')
                bad = True
            # 改进2 交叉校核②：明细 code_name+item_code 库内已存在但 item_name 不同 →
            # fail 级映射冲突，阻断直接转换、强制人工复核（cust_cls:03 事故防复发点）
            if cn and ic and (str(cn), str(ic)) in cv_items:
                db_name = cv_items[(str(cn), str(ic))]
                if str(inm or '') != str(db_name or ''):
                    _issue(b, r, 'fail',
                           f'映射冲突[{cn}:{ic}] item_name：库内[{db_name}] vs 模板[{inm or "空"}]')
                    bad = True
            if not bad:
                b['ok'] += 1
        result['S3I'] = b

    # ---- S4 表关联关系 ----
    if 'S4' in parsed:
        b = {'ok': 0, 'warn': 0, 'fail': 0, 'issues': []}
        for rec in parsed['S4']['rows']:
            r = rec['src_row']
            st, _ = _f(rec, '源表')
            sc, _ = _f(rec, '源列')
            tt, _ = _f(rec, '目标表')
            tc, _ = _f(rec, '目标列')
            bad = False
            for label, v in (('源表', st), ('源列', sc), ('目标表', tt), ('目标列', tc)):
                if v in (None, ''):
                    _issue(b, r, 'fail', f'必填字段[{label}]为空')
                    bad = True
            if not bad:
                for t, c, side in ((st, sc, '源'), (tt, tc, '目标')):
                    if t not in biz_cols:
                        _issue(b, r, 'fail', f'{side}表[{t}]在 marketing_40 不存在')
                        bad = True
                    elif c not in biz_cols[t]:
                        _issue(b, r, 'fail', f'{side}列[{t}.{c}]在 marketing_40 不存在')
                        bad = True
            if not bad:
                b['ok'] += 1
        result['S4'] = b

    # ---- S5 问答对素材 ----
    if 'S5' in parsed:
        b = {'ok': 0, 'warn': 0, 'fail': 0, 'issues': []}
        for rec in parsed['S5']['rows']:
            r = rec['src_row']
            q, _ = _f(rec, '自然语言问题', '问题')
            sql, _ = _f(rec, '标准SQL')
            keeper, _ = _f(rec, '维护人')
            usable, _ = _f(rec, '是否可用')
            bad = False
            if q in (None, ''):
                _issue(b, r, 'fail', '必填字段[问题]为空')
                bad = True
            err = _probe_sql(db, sql)
            if err:
                _issue(b, r, 'fail', f'标准SQL probe 失败: {err}')
                bad = True
            if usable in (None, ''):
                _issue(b, r, 'warn', '是否可用为空，按 1 处理')
            if keeper in (None, ''):
                _issue(b, r, 'warn', '维护人空缺，该行转入人工复核（manual/pending）')
            if not bad:
                b['ok'] += 1
        result['S5'] = b

    # ---- S6 业务规则 ----
    if 'S6' in parsed:
        b = {'ok': 0, 'warn': 0, 'fail': 0, 'issues': []}
        for rec in parsed['S6']['rows']:
            r = rec['src_row']
            desc, _ = _f(rec, '业务规则描述', '规则描述')
            pos, _ = _f(rec, '正例SQL')
            if desc in (None, ''):
                _issue(b, r, 'fail', '规则描述为空')
            elif pos in (None, ''):
                _issue(b, r, 'warn', '正例SQL为空，仅建描述行')
            else:
                b['ok'] += 1
        result['S6'] = b

    return result


# ==================== 转换（记录构建 + 入库 + 溯源） ====================

def _extract_tables_from_sql(sql: str) -> list:
    """SQL → 涉及表（FROM/JOIN 提取，保序去重）。"""
    out = []
    for m in re.finditer(r'(?i)\b(?:FROM|JOIN)\s+([a-zA-Z_][\w]*)', sql or ''):
        t = m.group(1)
        if t.upper() not in ('SELECT',) and t not in out:
            out.append(t)
    return out


def _upsert(conn, table, key_cols, row: dict):
    """应用级 upsert（双方言兼容）：存在则 UPDATE 非键列，否则 INSERT。返回 (existed, id)。"""
    where = ' AND '.join(f'{k} = ?' for k in key_cols)
    params = [row[k] for k in key_cols]
    cur = conn.execute(f'SELECT id FROM {table} WHERE {where} LIMIT 1', params)
    ex = cur.fetchone()
    if ex:
        sets = [(k, v) for k, v in row.items() if k not in key_cols]
        if sets:
            conn.execute(
                f'UPDATE {table} SET {", ".join(f"{k} = ?" for k, _ in sets)} WHERE id = ?',
                [v for _, v in sets] + [ex[0]])
        return True, ex[0]
    cols = list(row)
    cursor = conn.execute(
        f'INSERT INTO {table} ({", ".join(cols)}) VALUES ({", ".join(["?"] * len(cols))})',
        [row[c] for c in cols])
    return False, getattr(cursor, 'lastrowid', None)


def _upsert_guard(conn, table, key_cols, row: dict, compare_cols):
    """改进1：upsert 冲突保护（2026-08-20，cust_cls 静默覆盖事故防复发）。
    - 目标行不存在 → 正常插入，返回 ('insert', id, [])
    - 已存在且 compare_cols 值一致 → 静默跳过，返回 ('skip', id, [])
    - 已存在且值有差异 → 默认不覆盖，返回 ('conflict', id, [(field, 库内值, 模板值)])
      冲突项由调用方写 provenance（manual/pending，模板值入 field_value），
      人工复核 confirm 后才允许用模板值覆盖（confirm_provenance 回填路径）。
    比较口径：字符串化后比较（None 与 '' 视为一致）；updated_at 不入 compare_cols。"""
    where = ' AND '.join(f'{k} = ?' for k in key_cols)
    params = [row[k] for k in key_cols]
    cur = conn.execute(f'SELECT * FROM {table} WHERE {where} LIMIT 1', params)
    ex = cur.fetchone()
    if not ex:
        cols = list(row)
        cursor = conn.execute(
            f'INSERT INTO {table} ({", ".join(cols)}) VALUES ({", ".join(["?"] * len(cols))})',
            [row[c] for c in cols])
        return 'insert', getattr(cursor, 'lastrowid', None), []
    col_names = [d[0] for d in cur.description]
    ex_row = dict(zip(col_names, ex))
    diffs = []
    for c in compare_cols:
        if c not in row:
            continue
        db_v = ex_row.get(c)
        tpl_v = row[c]
        db_s = '' if db_v is None else str(db_v)
        tpl_s = '' if tpl_v is None else str(tpl_v)
        if db_s != tpl_s:
            diffs.append((c, db_v, tpl_v))
    if not diffs:
        return 'skip', ex_row.get('id'), []
    return 'conflict', ex_row.get('id'), diffs


def _prov(conn, run_id, sheet, src_row, target_table, target_key,
          field, kind, ref, value, status):
    """写一条字段级溯源。direct/system→'na'，llm/manual→'pending'（复核后 confirmed）。"""
    conn.execute(
        '''INSERT INTO ingest_provenance
           (run_id, sheet_name, src_row, target_table, target_key, field_name,
            source_kind, source_ref, field_value, review_status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (run_id, sheet, src_row, target_table, str(target_key)[:128], field,
         kind, (ref or '')[:64],
         None if value is None else str(value), status, _now()))


def _now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _int_or_none(v):
    try:
        return int(str(v).replace(',', ''))
    except (TypeError, ValueError):
        return None


def _existing_doc_text(conn, table_name, column_name):
    """读 schema_column_docs.doc_text 当前值（skip 分支判断富化差异用）。"""
    try:
        cursor = conn.execute(
            'SELECT doc_text FROM schema_column_docs '
            'WHERE table_name = ? AND column_name = ?',
            (table_name, column_name))
        row = cursor.fetchone()
        return row[0] if row else None
    except Exception:
        return None


def convert_run(run_id: str, parsed: dict, validation: dict,
                progress_cb=None, db=None) -> dict:
    """执行转换：按方案口径逐 Sheet 入库 + 逐字段写 ingest_provenance；收集 LLM 标注任务。
    返回 {'stats': {...}, 'llm_tasks': [...]}。dry_run=True 时只规划不落库（preview 用）。"""
    db = db or DatabaseManager()
    ensure_tables(db)
    l1_map, _l2 = _domain_maps(db)
    stats = {'converted': {}, 'skipped': {}, 'provenance': 0, 'conflicts': []}
    llm_tasks = []

    def _emit(stage, msg):
        if progress_cb:
            progress_cb({'stage': stage, 'msg': msg})

    with db.connect_governance() as conn:
        P = lambda *a: stats.__setitem__('provenance', stats['provenance'] + 1) or _prov(conn, *a)

        def _conflict_prov(sheet, src_row, target_table, target_key, diffs):
            """改进1：差异冲突项 → 人工复核队列（模板值不落地，confirm 后才覆盖）。
            同 run 同字段已有相同 pending 冲突时跳过（重跑不刷屏；confirm 后库内=模板值，
            冲突自然消解，不会再产生）。"""
            for field, db_v, tpl_v in diffs:
                dup = conn.execute(
                    "SELECT id FROM ingest_provenance WHERE run_id = ? AND target_table = ?"
                    " AND target_key = ? AND field_name = ? AND review_status = 'pending'"
                    " AND source_kind = 'manual' LIMIT 1",
                    (run_id, target_table, str(target_key)[:128], field)).fetchone()
                if dup:
                    continue
                stats['conflicts'].append({
                    'sheet': sheet, 'src_row': src_row, 'target_table': target_table,
                    'target_key': str(target_key)[:128], 'field': field,
                    'db_value': None if db_v is None else str(db_v),
                    'tpl_value': None if tpl_v is None else str(tpl_v)})
                P(run_id, sheet, src_row, target_table, target_key, field, 'manual',
                  f'冲突：库内[{str(db_v)[:20]}] vs 模板[{str(tpl_v)[:20]}]', tpl_v, 'pending')

        # ---- S2 → schema_table_docs（upsert by table_name） ----
        for rec in parsed.get('S2', {}).get('rows', []):
            r = rec['src_row']
            if _row_failed(validation, 'S2', r):
                continue
            tname, c_tn = _f(rec, '表名')
            cname, c_cn = _f(rec, '中文名')
            dom, c_dom = _f(rec, '所属域')
            sub, c_sub = _f(rec, '二级业务分类')
            layer, c_ly = _f(rec, '层级')
            owner, c_ow = _f(rec, '负责人')
            rows_cnt, c_rc = _f(rec, '数据行数')
            span, c_sp = _f(rec, '数据时间范围')
            freq, c_fr = _f(rec, '更新频率')
            src_sys, c_ss = _f(rec, '来源系统')
            dl1 = l1_map.get(dom)
            m = re.match(r'^([A-Za-z]+\d{2})', str(sub or ''))  # 前缀 3-4 字母（Grid 为 4）
            dl2 = m.group(1) if m else None
            doc_text = (f'表名：{tname}，中文名：{cname}，行数：{rows_cnt}。'
                        f'层级：{layer or ""}；负责人：{owner or ""}；数据时间范围：{span or ""}；'
                        f'更新频率：{freq or ""}；来源系统：{src_sys or ""}')
            row = {'table_name': tname, 'table_comment': cname,
                   'row_count': _int_or_none(rows_cnt), 'doc_text': doc_text,
                   'domain_l1': dl1, 'domain_l2': dl2, 'updated_at': _now()}
            # 改进1：已存在行默认不覆盖；差异 → 冲突项进人工复核
            outcome, _id, diffs = _upsert_guard(
                conn, 'schema_table_docs', ('table_name',), row,
                ('table_comment', 'row_count', 'doc_text', 'domain_l1', 'domain_l2'))
            key = tname
            if outcome == 'conflict':
                _conflict_prov('S2', r, 'schema_table_docs', key, diffs)
                stats['skipped']['S2_conflict'] = stats['skipped'].get('S2_conflict', 0) + 1
                continue
            if outcome == 'skip':
                stats['skipped']['S2_same'] = stats['skipped'].get('S2_same', 0) + 1
                continue
            P(run_id, 'S2', r, 'schema_table_docs', key, 'table_comment', 'direct', c_cn, cname, 'na')
            P(run_id, 'S2', r, 'schema_table_docs', key, 'row_count', 'direct', c_rc, rows_cnt, 'na')
            P(run_id, 'S2', r, 'schema_table_docs', key, 'domain_l1', 'system',
              '所属域中文→business_domains 映射', dl1, 'na')
            P(run_id, 'S2', r, 'schema_table_docs', key, 'domain_l2', 'system',
              '二级分类编码解析', dl2, 'na')
            P(run_id, 'S2', r, 'schema_table_docs', key, 'doc_text', 'system',
              f'字段拼接（层级{c_ly or "-"}/负责人{c_ow or "-"}/时间范围{c_sp or "-"}/频率{c_fr or "-"}/来源{c_ss or "-"}）',
              doc_text, 'na')
            stats['converted']['S2'] = stats['converted'].get('S2', 0) + 1
        _emit('convert', f'S2 完成 {stats["converted"].get("S2", 0)} 行')

        # ---- S2b → schema_column_docs（upsert by table_name+column_name）----
        # 字段级元数据：与 S2（表级）互补。以库内已有为准：doc_text 仅在库内为空时回填富化，
        # 仅 column_comment/data_type/is_pk 三项结构字段差异进冲突复核（避免 4094 行假冲突）
        for rec in parsed.get('S2B', {}).get('rows', []):
            r = rec['src_row']
            if _row_failed(validation, 'S2B', r):
                continue
            tname, c_tn = _f(rec, '表名')
            colname, c_cn = _f(rec, '字段名')
            cmt, c_cmt = _f(rec, '字段中文注释')
            dt, c_dt = _f(rec, '数据类型')
            ispk, c_pk = _f(rec, '是否主键')
            doctext, c_doc = _f(rec, '字段说明文本')
            is_pk_int = 1 if str(ispk or '0').strip() == '1' else 0
            key = f'{tname}.{colname}'
            row = {'table_name': tname, 'column_name': colname,
                   'column_comment': cmt, 'data_type': dt,
                   'is_pk': is_pk_int, 'doc_text': doctext or ''}
            # 已存在行默认不覆盖；结构字段差异 → 冲突项进人工复核
            outcome, _id, diffs = _upsert_guard(
                conn, 'schema_column_docs', ('table_name', 'column_name'), row,
                ('column_comment', 'data_type', 'is_pk'))
            if outcome == 'conflict':
                _conflict_prov('S2B', r, 'schema_column_docs', key, diffs)
                stats['skipped']['S2B_conflict'] = stats['skipped'].get('S2B_conflict', 0) + 1
                continue
            if outcome == 'skip':
                # 结构字段一致时以库内为准：doc_text 仅在库内为空时回填（富化），
                # 库内已有 doc_text 一律保留，不被模板覆盖
                if (doctext or '') and not (_existing_doc_text(conn, tname, colname) or ''):
                    conn.execute(
                        'UPDATE schema_column_docs SET doc_text = ? '
                        'WHERE table_name = ? AND column_name = ?',
                        (doctext, tname, colname))
                    P(run_id, 'S2B', r, 'schema_column_docs', key, 'doc_text', 'direct',
                      c_doc, doctext, 'na')
                stats['skipped']['S2B_same'] = stats['skipped'].get('S2B_same', 0) + 1
                continue
            P(run_id, 'S2B', r, 'schema_column_docs', key, 'column_comment', 'direct', c_cmt, cmt, 'na')
            P(run_id, 'S2B', r, 'schema_column_docs', key, 'data_type', 'direct', c_dt, dt, 'na')
            P(run_id, 'S2B', r, 'schema_column_docs', key, 'is_pk', 'direct', c_pk, is_pk_int, 'na')
            if doctext:
                P(run_id, 'S2B', r, 'schema_column_docs', key, 'doc_text', 'direct', c_doc, doctext, 'na')
            stats['converted']['S2B'] = stats['converted'].get('S2B', 0) + 1
        _emit('convert', f'S2b 完成 {stats["converted"].get("S2B", 0)} 行')

        # ---- S3D → code_values（upsert by code_name）+ code_value_column_form ----
        for rec in parsed.get('S3D', {}).get('rows', []):
            r = rec['src_row']
            if _row_failed(validation, 'S3D', r):
                continue
            cn, c_cn = _f(rec, 'code_name')
            ccn, c_ccn = _f(rec, '中文名')
            dom, c_dom = _f(rec, '所属域')
            dt, c_dt = _f(rec, '数据类型')
            desc, c_desc = _f(rec, '描述')  # '描述'（必填文本）≠'描述列名'（列名）
            code_col, c_cc = _f(rec, '编码列名')
            desc_col, c_dc = _f(rec, '描述列名')
            dl1 = l1_map.get(dom)
            row = {'code_name': cn, 'code_cn_name': ccn, 'data_type': dt,
                   'description': desc, 'domain_l1': dl1}
            # 改进1：域行已存在默认不覆盖；中文名/类型/描述差异 → 冲突项进人工复核
            outcome, _id, diffs = _upsert_guard(
                conn, 'code_values', ('code_name',), row,
                ('code_cn_name', 'data_type', 'description', 'domain_l1'))
            if outcome == 'conflict':
                _conflict_prov('S3D', r, 'code_values', cn, diffs)
                stats['skipped']['S3D_conflict'] = stats['skipped'].get('S3D_conflict', 0) + 1
            elif outcome == 'skip':
                stats['skipped']['S3D_same'] = stats['skipped'].get('S3D_same', 0) + 1
            else:
                for fn, cell, val in (('code_cn_name', c_ccn, ccn), ('data_type', c_dt, dt),
                                      ('description', c_desc, desc)):
                    P(run_id, 'S3D', r, 'code_values', cn, fn, 'direct', cell, val, 'na')
                P(run_id, 'S3D', r, 'code_values', cn, 'domain_l1', 'system',
                  '所属域中文→business_domains 映射', dl1, 'na')
                stats['converted']['S3D'] = stats['converted'].get('S3D', 0) + 1
            # 编码/描述列名 → code_value_column_form（表名留空存 'ANY'，取后者便于 NOT NULL 与去重）
            pair = ('混合' if code_col and desc_col else
                    ('编码' if code_col else ('名称' if desc_col else None)))
            for col_name, cell in ((code_col, c_cc), (desc_col, c_dc)):
                if col_name and pair:
                    cf_row = {'table_name': 'ANY', 'column_name': col_name,
                              'code_name': cn, 'form': pair, 'updated_at': _now()}
                    cf_out, _cf_id, cf_diffs = _upsert_guard(
                        conn, 'code_value_column_form',
                        ('table_name', 'column_name', 'code_name'), cf_row, ('form',))
                    cf_key = f'ANY|{col_name}|{cn}'
                    if cf_out == 'conflict':
                        _conflict_prov('S3D', r, 'code_value_column_form', cf_key, cf_diffs)
                        stats['skipped']['S3D_cf_conflict'] = stats['skipped'].get('S3D_cf_conflict', 0) + 1
                    elif cf_out == 'insert':
                        P(run_id, 'S3D', r, 'code_value_column_form', cf_key,
                          'form', 'system', f'BOTH/CODE ONLY/DESC ONLY→{pair}', pair, 'na')
                        P(run_id, 'S3D', r, 'code_value_column_form', cf_key,
                          'column_name', 'direct', cell, col_name, 'na')
        _emit('convert', f'S3D 完成 {stats["converted"].get("S3D", 0)} 行')

        # ---- S3I → code_value_items（upsert by code_name+item_code，冲突保护） ----
        cv_items_now = _code_value_maps(db)[1]
        for rec in parsed.get('S3I', {}).get('rows', []):
            r = rec['src_row']
            cn, c_cn = _f(rec, 'code_name')
            ic, c_ic = _f(rec, 'item_code')
            inm, c_inm = _f(rec, 'item_name')
            sort, c_sort = _f(rec, '排序')
            if _row_failed(validation, 'S3I', r):
                # 改进2 桥接：fail 级映射冲突（item_name 库内≠模板）虽阻断直接转换，
                # 仍生成 manual/pending 冲突项进人工复核队列，confirm 后才允许模板值落地
                if (cn and ic and any('映射冲突' in i['msg'] and i['row'] == r
                                      for i in (validation.get('S3I') or {}).get('issues', []))):
                    db_name = cv_items_now.get((str(cn), str(ic)), '')
                    _conflict_prov('S3I', r, 'code_value_items', f'{cn}:{ic}',
                                   [('item_name', db_name, inm)])
                    stats['skipped']['S3I_conflict'] = stats['skipped'].get('S3I_conflict', 0) + 1
                continue
            row = {'code_name': cn, 'item_code': str(ic), 'item_name': inm,
                   'sort_order': _int_or_none(sort)}
            # 改进1：明细已存在默认不覆盖；item_name 差异 → 冲突项进人工复核（事故防复发点）
            outcome, _id, diffs = _upsert_guard(
                conn, 'code_value_items', ('code_name', 'item_code'), row,
                ('item_name', 'sort_order'))
            if outcome == 'conflict':
                _conflict_prov('S3I', r, 'code_value_items', f'{cn}:{ic}', diffs)
                stats['skipped']['S3I_conflict'] = stats['skipped'].get('S3I_conflict', 0) + 1
                continue
            if outcome == 'skip':
                stats['skipped']['S3I_same'] = stats['skipped'].get('S3I_same', 0) + 1
                continue
            for fn, cell, val in (('item_name', c_inm, inm), ('sort_order', c_sort, sort)):
                P(run_id, 'S3I', r, 'code_value_items', f'{cn}:{ic}', fn, 'direct', cell, val, 'na')
            stats['converted']['S3I'] = stats['converted'].get('S3I', 0) + 1
        _emit('convert', f'S3I 完成 {stats["converted"].get("S3I", 0)} 行')

        # ---- S4 → schema_relationship_docs（按 title 去重插） ----
        for rec in parsed.get('S4', {}).get('rows', []):
            r = rec['src_row']
            if _row_failed(validation, 'S4', r):
                continue
            st, c_st = _f(rec, '源表')
            sc, c_sc = _f(rec, '源列')
            tt, c_tt = _f(rec, '目标表')
            tc, c_tc = _f(rec, '目标列')
            src, c_src = _f(rec, '来源')
            title = f'{st} → {tt}'
            path = json.dumps([st, tt], ensure_ascii=False)
            join = json.dumps([f'{st}.{sc} = {tt}.{tc}'], ensure_ascii=False)
            scen = json.dumps([f'素材提资：{src or "未标注"}'], ensure_ascii=False)
            doc_text = f'表关联路径：{title}\nJOIN 条件：\n  - {st}.{sc} = {tt}.{tc}\n来源：{src or ""}'
            dup = conn.execute('SELECT id FROM schema_relationship_docs WHERE title = ? LIMIT 1',
                               (title,)).fetchone()
            if dup:
                stats['skipped']['S4_dup'] = stats['skipped'].get('S4_dup', 0) + 1
                continue
            conn.execute(
                'INSERT INTO schema_relationship_docs (title, path, join_conditions, business_scenarios, doc_text)'
                ' VALUES (?, ?, ?, ?, ?)', (title, path, join, scen, doc_text))
            P(run_id, 'S4', r, 'schema_relationship_docs', title, 'title', 'system', '源表→目标表 组装', title, 'na')
            P(run_id, 'S4', r, 'schema_relationship_docs', title, 'path', 'system', '二元组 JSON 组装', path, 'na')
            P(run_id, 'S4', r, 'schema_relationship_docs', title, 'join_conditions', 'direct',
              f'{c_st}/{c_sc}/{c_tt}/{c_tc}', f'{st}.{sc} = {tt}.{tc}', 'na')
            P(run_id, 'S4', r, 'schema_relationship_docs', title, 'business_scenarios', 'direct', c_src, src, 'na')
            stats['converted']['S4'] = stats['converted'].get('S4', 0) + 1
        _emit('convert', f'S4 完成 {stats["converted"].get("S4", 0)} 行')

        # ---- S5 → qa_pairs（按 question 去重插；llm 标注 difficulty/tags/explanation） ----
        table_domains = _table_domain_map(db)
        for rec in parsed.get('S5', {}).get('rows', []):
            r = rec['src_row']
            if _row_failed(validation, 'S5', r):
                continue
            q, c_q = _f(rec, '自然语言问题', '问题')
            sql, c_sql = _f(rec, '标准SQL')
            keeper, c_kp = _f(rec, '维护人')
            usable, c_us = _f(rec, '是否可用')
            src, c_src = _f(rec, '来源（选填）', '来源')
            dup = conn.execute('SELECT id FROM qa_pairs WHERE question = ? LIMIT 1', (q,)).fetchone()
            if dup:
                stats['skipped']['S5_dup'] = stats['skipped'].get('S5_dup', 0) + 1
                continue
            tables = _extract_tables_from_sql(sql)
            answer = _probe_answer(db, sql)
            dl1, dl2 = _infer_domains(tables, table_domains)
            is_usable = 0 if str(usable).strip() == '0' else 1
            row = {'question': q, 'standard_sql': sql, 'is_usable': is_usable,
                   'source': (src or '素材提资'), 'generation_method': '素材提资',
                   'ingest_time': datetime.now().isoformat(),
                   'objects_involved': json.dumps(tables, ensure_ascii=False),
                   'answer': answer, 'domain_l1': dl1, 'domain_l2': dl2}
            _existed, qa_id = _upsert(conn, 'qa_pairs', ('question',), row)
            key = q[:120]
            P(run_id, 'S5', r, 'qa_pairs', key, 'question', 'direct', c_q, q, 'na')
            P(run_id, 'S5', r, 'qa_pairs', key, 'standard_sql', 'direct', c_sql, sql, 'na')
            P(run_id, 'S5', r, 'qa_pairs', key, 'is_usable', 'direct', c_us or '固定1', is_usable, 'na')
            P(run_id, 'S5', r, 'qa_pairs', key, 'source', 'direct', c_src or '固定赋值', row['source'], 'na')
            P(run_id, 'S5', r, 'qa_pairs', key, 'objects_involved', 'system', 'SQL 提取表名', row['objects_involved'], 'na')
            P(run_id, 'S5', r, 'qa_pairs', key, 'answer', 'system', 'marketing_40 probe LIMIT5+COUNT', answer, 'na')
            P(run_id, 'S5', r, 'qa_pairs', key, 'domain_l1', 'system', '涉及表 schema_table_docs 标签推导', dl1, 'na')
            P(run_id, 'S5', r, 'qa_pairs', key, 'domain_l2', 'system', '涉及表 schema_table_docs 标签推导', dl2, 'na')
            if keeper in (None, ''):
                # 维护人空缺行：转人工复核（source_kind=manual, review_status=pending）
                P(run_id, 'S5', r, 'qa_pairs', key, 'maintainer', 'manual', '维护人空缺，待人工补录', '', 'pending')
            else:
                P(run_id, 'S5', r, 'qa_pairs', key, 'maintainer', 'direct', c_kp, keeper, 'na')
            llm_tasks.append({'kind': 'qa', 'target_key': key, 'qa_id': qa_id,
                              'question': q, 'sql': sql, 'src_row': r})
            stats['converted']['S5'] = stats['converted'].get('S5', 0) + 1
        _emit('convert', f'S5 完成 {stats["converted"].get("S5", 0)} 行')

        # ---- S6 → sql_knowledge（trigger_words+sql_rule 自证为规则；llm 抽取触发词） ----
        for rec in parsed.get('S6', {}).get('rows', []):
            r = rec['src_row']
            if _row_failed(validation, 'S6', r):
                continue
            desc, c_desc = _f(rec, '业务规则描述', '规则描述')
            pos, c_pos = _f(rec, '正例SQL')
            neg, c_neg = _f(rec, '反例SQL')
            # 自证为规则：sql_rule 不能以 SELECT/WITH 开头（否则被内容分类为模板行），
            # 正/反例以"正例SQL：/反例SQL："前缀文本承载（SQL 原文完整保留）
            sql_rule = ''
            if pos:
                sql_rule += f'正例SQL：\n{pos}'
            if neg:
                sql_rule += f'\n反例SQL：\n{neg}'
            sql_rule = sql_rule or None
            # 改进1：sql_knowledge 同样默认不覆盖；sql_rule 差异 → 冲突项进人工复核
            outcome, kn_id, diffs = _upsert_guard(
                conn, 'sql_knowledge', ('name', 'description'),
                {'name': 'custom_rule', 'description': desc, 'sql_rule': sql_rule,
                 'enabled': 1, 'updated_at': _now()},
                ('sql_rule',))
            if outcome == 'conflict':
                _conflict_prov('S6', r, 'sql_knowledge', kn_id, diffs)
                stats['skipped']['S6_conflict'] = stats['skipped'].get('S6_conflict', 0) + 1
                continue
            if outcome == 'skip':
                stats['skipped']['S6_dup'] = stats['skipped'].get('S6_dup', 0) + 1
                continue
            key = str(kn_id or desc[:100])
            P(run_id, 'S6', r, 'sql_knowledge', key, 'description', 'direct', c_desc, desc, 'na')
            if pos:
                P(run_id, 'S6', r, 'sql_knowledge', key, 'sql_rule', 'direct', c_pos, sql_rule, 'na')
            llm_tasks.append({'kind': 'rule', 'target_key': key, 'kn_id': kn_id,
                              'description': desc, 'src_row': r})
            stats['converted']['S6'] = stats['converted'].get('S6', 0) + 1
        _emit('convert', f'S6 完成 {stats["converted"].get("S6", 0)} 行')

        conn.commit()
    return {'stats': stats, 'llm_tasks': llm_tasks}


def _row_failed(validation, sheet_key, src_row):
    v = validation.get(sheet_key) or {}
    return any(i['row'] == src_row and i['level'] == 'fail' for i in v.get('issues', []))


def _table_domain_map(db):
    out = {}
    with db.connect_governance() as conn:
        for t, l1, l2 in conn.execute(
                'SELECT table_name, domain_l1, domain_l2 FROM schema_table_docs'):
            out[t] = (l1, l2)
    return out


def _infer_domains(tables, table_domains):
    """S5：按涉及表在 schema_table_docs 的标签推导（首个非空标签）。"""
    l1 = l2 = None
    for t in tables:
        d = table_domains.get(t)
        if d:
            l1 = l1 or d[0]
            l2 = l2 or d[1]
    return l1, l2


def _probe_answer(db, sql: str) -> str:
    """S5 answer：marketing_40 probe COUNT + LIMIT 5 采样，回填 JSON（与现有 qa_pairs.answer 同构）。"""
    out = {'success': False, 'row_count': None, 'headers': [], 'sample_rows': []}
    try:
        base = re.sub(r'\s+LIMIT\s+\d+\s*$', '', sql.rstrip(';').strip(), flags=re.IGNORECASE)
        with db.connect_business() as conn:
            out['row_count'] = conn.execute(f'SELECT COUNT(*) FROM ({base}) _t').fetchone()[0]
            cursor = conn.execute(base + ' LIMIT 5')
            out['headers'] = [d[0] for d in cursor.description or []]
            out['sample_rows'] = [[str(v) if v is not None else None for v in row]
                                  for row in cursor.fetchall()]
            out['success'] = True
    except Exception as e:
        out['error'] = str(e)[:200]
    return json.dumps(out, ensure_ascii=False)


# ==================== LLM 批量标注 ====================

def _llm_call(prompt: str) -> str:
    """复用 engine/llm_config 的 current()/build_request 调一次 LLM，返回 content 文本。
    未配置/异常抛错（调用方降级 manual/pending）。"""
    import requests
    from core import llm_config
    cfg = llm_config.current()
    if not cfg.get('api_key'):
        raise RuntimeError('LLM 未配置 api_key')
    url, headers, payload = llm_config.build_request(
        [{'role': 'user', 'content': prompt}], max_tokens=4000, thinking=False)
    session = requests.Session()
    session.trust_env = False
    resp = session.post(url, json=payload, headers=headers, timeout=120)
    resp.raise_for_status()
    return resp.json()['choices'][0]['message']['content']


def _parse_json_array(text: str) -> list:
    """从 LLM 输出宽容提取 JSON 数组。"""
    m = re.search(r'\[.*\]', text or '', re.DOTALL)
    if not m:
        raise ValueError('输出不含 JSON 数组')
    arr = json.loads(m.group(0))
    if not isinstance(arr, list):
        raise ValueError('输出不是数组')
    return arr


def annotate_llm(run_id: str, llm_tasks: list, progress_cb=None, db=None) -> dict:
    """LLM 批量标注（每批≤10 条，要求 JSON 数组输出）：
    - qa 任务 → qa_pairs.difficulty/tags/explanation
    - rule 任务 → sql_knowledge.trigger_words
    失败降级：对应字段 source_kind=manual、review_status=pending（target 字段留空待人工）。
    返回 {'annotated': n, 'degraded': n}。"""
    db = db or DatabaseManager()
    ensure_tables(db)
    result = {'annotated': 0, 'degraded': 0}

    def _emit(msg):
        if progress_cb:
            progress_cb({'stage': 'annotate', 'msg': msg})

    def _degrade(conn, task, fields):
        for fn in fields:
            _prov(conn, run_id, 'S5' if task['kind'] == 'qa' else 'S6', task['src_row'],
                  'qa_pairs' if task['kind'] == 'qa' else 'sql_knowledge', task['target_key'],
                  fn, 'manual', 'llm 未配置/调用失败降级，待人工', '', 'pending')
        result['degraded'] += 1

    try:
        cfg = __import__('core.llm_config', fromlist=['current']).current()
        model = cfg.get('model', '')
    except Exception:
        model = ''

    with db.connect_governance() as conn:
        for i in range(0, len(llm_tasks), 10):
            batch = llm_tasks[i:i + 10]
            kind = batch[0]['kind']
            _emit(f'LLM 标注 {kind} 批次 {i // 10 + 1}（{len(batch)} 条）')
            try:
                if kind == 'qa':
                    items = [{'i': n, 'question': t['question'], 'sql': t['sql']}
                             for n, t in enumerate(batch)]
                    prompt = (
                        '你是电力营销数据专家。为下列每个问答对标注：difficulty（基础题/进阶题/挑战题）、'
                        'tags（1~3 个中文标签数组）、explanation（一句话说明该 SQL 如何回答此问题）。'
                        '严格输出 JSON 数组，元素形如 {"i":0,"difficulty":"基础题","tags":["单表查询"],"explanation":"..."}，不要输出其他内容。\n'
                        + json.dumps(items, ensure_ascii=False, indent=1))
                else:
                    items = [{'i': n, 'rule': t['description']} for n, t in enumerate(batch)]
                    prompt = (
                        '你是电力营销数据专家。为下列每条业务规则抽取 3~6 个中文触发关键词'
                        '（用户问题出现这些词时应套用该规则）。'
                        '严格输出 JSON 数组，元素形如 {"i":0,"trigger_words":["日用电量","电量"]}，不要输出其他内容。\n'
                        + json.dumps(items, ensure_ascii=False, indent=1))
                arr = _parse_json_array(_llm_call(prompt))
                by_idx = {int(x.get('i', -1)): x for x in arr if isinstance(x, dict)}
            except Exception as e:
                _emit(f'LLM 批次失败，降级人工复核: {str(e)[:120]}')
                for t in batch:
                    _degrade(conn, t, ('difficulty', 'tags', 'explanation') if kind == 'qa' else ('trigger_words',))
                conn.commit()
                continue

            for n, t in enumerate(batch):
                got = by_idx.get(n)
                if not got:
                    _degrade(conn, t, ('difficulty', 'tags', 'explanation') if kind == 'qa' else ('trigger_words',))
                    continue
                if kind == 'qa':
                    tags = got.get('tags')
                    tags_txt = json.dumps(tags, ensure_ascii=False) if isinstance(tags, list) else str(tags or '')
                    vals = {'difficulty': str(got.get('difficulty') or ''),
                            'tags': tags_txt, 'explanation': str(got.get('explanation') or '')}
                    conn.execute(
                        'UPDATE qa_pairs SET difficulty = ?, tags = ?, explanation = ? WHERE id = ?',
                        (vals['difficulty'], vals['tags'], vals['explanation'], t['qa_id']))
                    for fn, v in vals.items():
                        _prov(conn, run_id, 'S5', t['src_row'], 'qa_pairs', t['target_key'],
                              fn, 'llm', f'llm:{model}', v, 'pending')
                else:
                    tw = got.get('trigger_words')
                    tw_txt = json.dumps(tw, ensure_ascii=False) if isinstance(tw, list) else str(tw or '')
                    conn.execute('UPDATE sql_knowledge SET trigger_words = ? WHERE id = ?',
                                 (tw_txt, t['kn_id']))
                    _prov(conn, run_id, 'S6', t['src_row'], 'sql_knowledge', t['target_key'],
                          'trigger_words', 'llm', f'llm:{model}', tw_txt, 'pending')
                result['annotated'] += 1
            conn.commit()
    return result


# ==================== 运行管理 / 预览 / 复核 ====================

def create_run(file_name: str, db=None) -> str:
    db = db or DatabaseManager()
    ensure_tables(db)
    run_id = uuid.uuid4().hex[:12]
    with db.connect_governance() as conn:
        conn.execute(
            'INSERT INTO ingest_runs (run_id, file_name, status, created_at) VALUES (?, ?, ?, ?)',
            (run_id, file_name, 'parsed', _now()))
        conn.commit()
    return run_id


def update_run_stats(run_id: str, status: str = None, stats: dict = None, db=None, finished=False):
    db = db or DatabaseManager()
    sets, params = [], []
    if status:
        sets.append('status = ?')
        params.append(status)
    if stats:
        for col in ('total_rows', 'ok_rows', 'review_rows', 'fail_rows'):
            if col in stats:
                sets.append(f'{col} = ?')
                params.append(stats[col])
    if finished:
        sets.append('finished_at = ?')
        params.append(_now())
    if not sets:
        return
    params.append(run_id)
    with db.connect_governance() as conn:
        conn.execute(f'UPDATE ingest_runs SET {", ".join(sets)} WHERE run_id = ?', params)
        conn.commit()


def get_run(run_id: str, db=None):
    db = db or DatabaseManager()
    with db.connect_governance() as conn:
        cursor = conn.execute(
            'SELECT run_id, file_name, status, total_rows, ok_rows, review_rows, fail_rows,'
            ' created_at, finished_at FROM ingest_runs WHERE run_id = ?', (run_id,))
        row = cursor.fetchone()
        if not row:
            return None
        cols = ['run_id', 'file_name', 'status', 'total_rows', 'ok_rows', 'review_rows',
                'fail_rows', 'created_at', 'finished_at']
        return {c: (str(v) if v is not None else None) for c, v in zip(cols, row)}


def build_preview(run_id: str, parsed: dict, validation: dict) -> dict:
    """校验与映射预览：按 Sheet 分组的行级映射 + 字段级溯源图例（direct/system/llm/manual）。
    溯源徽标与 convert_run 同口径（dry-run，不落库）。"""
    sheets = []
    for key, pack in parsed.items():
        v = validation.get(key) or {'ok': 0, 'warn': 0, 'fail': 0, 'issues': []}
        rows = []
        for rec in pack['rows']:
            failed = _row_failed(validation, key, rec['src_row'])
            rows.append({
                'src_row': rec['src_row'],
                'status': 'fail' if failed else 'ok',
                'fields': _preview_fields(key, rec),
            })
        sheets.append({
            'key': key, 'title': pack['title'],
            'stats': {'ok': v['ok'], 'warn': v['warn'], 'fail': v['fail']},
            'issues': v['issues'], 'rows': rows,
        })
    return {'run_id': run_id, 'sheets': sheets}


def _preview_fields(sheet_key, rec):
    """行内字段 → [(name, value, kind_label)]，kind_label 与方案四色对应
    （direct/system 页面合并显示为"直接转换"）。"""
    out = []
    label_map = {'表名': 'direct', '中文名': 'direct', '所属域': 'direct', '层级': 'direct',
                 '负责人': 'direct', '数据行数': 'direct', '数据时间范围': 'direct',
                 '更新频率': 'direct', '来源系统': 'direct', 'code_name': 'direct',
                 '数据类型': 'direct', '编码列名': 'direct', '描述列名': 'direct',
                 'item_code': 'direct', 'item_name': 'direct', '排序': 'direct',
                 '源表': 'direct', '源列': 'direct', '目标表': 'direct', '目标列': 'direct',
                 '来源': 'direct', '自然语言问题': 'direct', '标准SQL': 'direct',
                 '维护人': 'direct', '是否可用': 'direct', '业务规则描述': 'direct',
                 '正例SQL': 'direct', '反例SQL': 'direct',
                 '字段名': 'direct', '字段中文注释': 'direct', '是否主键': 'direct',
                 '字段说明文本': 'direct',
                 '二级业务分类': 'system', '描述': 'direct'}
    for h, item in rec['fields'].items():
        kind = label_map.get(h, 'direct')
        out.append({'name': h, 'value': item['v'], 'kind': kind, 'ref': item['cell']})
    # 推导/标注字段预告（与转换口径一致）
    if sheet_key == 'S2':
        out += [{'name': 'domain_l1', 'value': '←所属域映射', 'kind': 'system', 'ref': 'business_domains 映射'},
                {'name': 'doc_text', 'value': '←字段拼接', 'kind': 'system', 'ref': '层级/负责人/范围/频率/来源'}]
    elif sheet_key == 'S3D':
        out.append({'name': 'domain_l1', 'value': '←所属域映射', 'kind': 'system', 'ref': 'business_domains 映射'})
    elif sheet_key == 'S4':
        out += [{'name': 'title/path', 'value': '←组装', 'kind': 'system', 'ref': '源表→目标表'},
                {'name': 'join_conditions', 'value': '←组装', 'kind': 'system', 'ref': '源列=目标列'}]
    elif sheet_key == 'S5':
        out += [{'name': 'objects_involved', 'value': '←SQL提取', 'kind': 'system', 'ref': 'FROM/JOIN 解析'},
                {'name': 'answer', 'value': '←probe回填', 'kind': 'system', 'ref': 'LIMIT5+COUNT'},
                {'name': 'domain_l1/l2', 'value': '←表标签推导', 'kind': 'system', 'ref': 'schema_table_docs'},
                {'name': 'difficulty/tags/explanation', 'value': '←LLM标注', 'kind': 'llm', 'ref': 'llm:模型'},
                {'name': 'maintainer', 'value': '维护人空缺→人工' if not _f(rec, '维护人')[0] else '见维护人列',
                 'kind': 'manual' if not _f(rec, '维护人')[0] else 'direct', 'ref': '人工复核'}]
    elif sheet_key == 'S6':
        out.append({'name': 'trigger_words', 'value': '←LLM抽取', 'kind': 'llm', 'ref': 'llm:模型'})
    return out


# ==================== 复核优先级 ====================

# 语义类字段：取值变更影响业务含义（码值名称/表字段中文名/业务域/主键等），必须人工逐条确认
_HIGH_FIELDS = {
    ('schema_table_docs', 'table_comment'),
    ('schema_table_docs', 'domain_l1'),
    ('schema_table_docs', 'domain_l2'),
    ('schema_column_docs', 'column_comment'),
    ('schema_column_docs', 'is_pk'),
    ('code_values', 'code_cn_name'),
    ('code_value_items', 'item_name'),
    ('sql_knowledge', 'sql_rule'),
    ('qa_pairs', 'question'),
    ('qa_pairs', 'standard_sql'),
}
# 结构/格式类字段：客观类型与形态，批量确认风险可控
_MEDIUM_FIELDS = {
    ('schema_column_docs', 'data_type'),
    ('code_values', 'data_type'),
    ('code_value_column_form', 'form'),
}
# 其余（doc_text/description/sort_order/maintainer/LLM 标注等）默认低
#
# row_count 是客观事实，不参与人工复核（2026-08-26 用户口径）：
# 冲突时以业务库 information_schema 实际行数自动校准，见 batch_confirm。
_AUTO_CAL_FIELDS = {('schema_table_docs', 'row_count')}

_CONFLICT_RE = re.compile(r'^冲突：库内\[(.*)\] vs 模板\[(.*)\]$')


def _actual_row_count(db, table_name):
    """业务库实际行数（information_schema.table_rows 估计值，元数据展示口径）。"""
    try:
        with db.connect_business() as conn:
            row = conn.execute(
                'SELECT table_rows FROM information_schema.tables '
                'WHERE table_schema = DATABASE() AND table_name = ?', (table_name,)).fetchone()
            return int(row[0]) if row and row[0] is not None else None
    except Exception:
        return None


def compute_priority(target_table, field_name, source_kind, source_ref, field_value):
    """复核优先级 → ('高'|'中'|'低', 原因)。

    规则（2026-08-26 批量复核功能）：
    - row_count 等客观事实字段 → 低（批量确认时自动校准为实际值，无需人工决策）；
    - 模板值为空（确认=用空值覆盖库内值，有数据损失风险）→ 高；
    - 仅大小写/首尾空白差异 → 低（噪声冲突）；
    - 语义类字段 → 高；结构/格式类字段 → 中；其余（doc_text/描述/排序/LLM 标注等）→ 低。
    """
    if (target_table, field_name) in _AUTO_CAL_FIELDS:
        return '低', '客观事实字段，批量确认时按业务库实际值自动校准'
    if field_value in (None, ''):
        return '高', '模板值为空，确认将清空库内值'
    m = _CONFLICT_RE.match(source_ref or '')
    if m and m.group(1).strip().casefold() == m.group(2).strip().casefold():
        return '低', '仅大小写/格式差异'
    key = (target_table, field_name)
    if key in _HIGH_FIELDS:
        return '高', '语义字段取值变更'
    if key in _MEDIUM_FIELDS:
        return '中', '结构/格式字段'
    if source_kind == 'llm':
        return '低', 'LLM 标注'
    return '低', '机械性/描述类字段'


def batch_confirm(prov_ids, db=None) -> dict:
    """批量复核确认：仅允许中/低优先级，高优先级服务端拒绝（必须逐条人工确认）。

    以溯源行当前 field_value 回填目标表（与单条 confirm 同口径）；
    row_count 等客观事实字段例外——回填值改为业务库实际值（自动校准）。
    返回 {'confirmed': n, 'refused': [(id, 原因)], 'failed': [(id, 错误)]}。"""
    db = db or DatabaseManager()
    ensure_tables(db)
    confirmed, refused, failed = 0, [], []
    with db.connect_governance() as conn:
        for pid in prov_ids:
            row = conn.execute(
                'SELECT target_table, target_key, field_name, source_kind, source_ref,'
                ' field_value, review_status'
                ' FROM ingest_provenance WHERE id = ?', (int(pid),)).fetchone()
            if not row or row[6] != 'pending':
                continue
            pri, reason = compute_priority(row[0], row[2], row[3], row[4], row[5])
            if pri == '高':
                refused.append((int(pid), reason))
                continue
            value = row[5]
            if (row[0], row[2]) in _AUTO_CAL_FIELDS:
                actual = _actual_row_count(db, row[1])
                if actual is None:
                    failed.append((int(pid), '业务库实际行数读取失败'))
                    continue
                value = actual
            try:
                confirm_provenance(int(pid), value, db=db)
                confirmed += 1
            except Exception as e:
                failed.append((int(pid), str(e)[:100]))
    return {'confirmed': confirmed, 'refused': refused, 'failed': failed}


def get_review_queue(run_id: str = None, db=None) -> list:
    """人工复核队列：review_status='pending' 的溯源行（可按 run 过滤），附复核优先级。"""
    db = db or DatabaseManager()
    ensure_tables(db)
    where, params = "WHERE review_status = 'pending'", []
    if run_id:
        where += ' AND run_id = ?'
        params.append(run_id)
    with db.connect_governance() as conn:
        cursor = conn.execute(
            f'SELECT id, run_id, sheet_name, src_row, target_table, target_key, field_name,'
            f' source_kind, source_ref, field_value, review_status, created_at'
            f' FROM ingest_provenance {where} ORDER BY id', params)
        cols = ['id', 'run_id', 'sheet_name', 'src_row', 'target_table', 'target_key',
                'field_name', 'source_kind', 'source_ref', 'field_value', 'review_status', 'created_at']
        items = [{c: (str(v) if v is not None else None) for c, v in zip(cols, row)}
                 for row in cursor.fetchall()]
    for it in items:
        pri, reason = compute_priority(it['target_table'], it['field_name'],
                                       it['source_kind'], it['source_ref'], it['field_value'])
        it['priority'] = pri
        it['priority_reason'] = reason
    order = {'高': 0, '中': 1, '低': 2}
    items.sort(key=lambda x: (order.get(x['priority'], 9), int(x['id'])))
    return items


def confirm_provenance(prov_id: int, field_value, db=None) -> dict:
    """复核确认（可改 field_value）：回填目标表对应字段，source_kind→manual、
    review_status→confirmed、reviewed_at=now。返回更新后的溯源行。"""
    db = db or DatabaseManager()
    with db.connect_governance() as conn:
        cursor = conn.execute(
            'SELECT id, run_id, sheet_name, src_row, target_table, target_key, field_name,'
            ' source_kind, source_ref, field_value, review_status FROM ingest_provenance WHERE id = ?',
            (int(prov_id),))
        row = cursor.fetchone()
        if not row:
            raise LookupError(f'溯源记录不存在: {prov_id}')
        (pid, run_id, sheet, src_row, target_table, target_key,
         field_name, _kind, _ref, _val, _status) = row
        # 目标表回填（字段白名单防注入；maintainer 仅复核元数据不回填）
        if field_name in TARGET_FIELDS.get(target_table, set()) and field_name != 'maintainer':
            key_col = {'schema_table_docs': 'table_name', 'code_values': 'code_name',
                       'qa_pairs': 'question', 'schema_relationship_docs': 'title',
                       'sql_knowledge': 'id'}.get(target_table)
            if target_table == 'code_value_items':
                cn, ic = target_key.split(':', 1)
                conn.execute(
                    f'UPDATE {target_table} SET {field_name} = ? WHERE code_name = ? AND item_code = ?',
                    (field_value, cn, ic))
            elif target_table == 'code_value_column_form':
                tn, col, cn = target_key.split('|', 2)
                conn.execute(
                    f'UPDATE {target_table} SET {field_name} = ? WHERE table_name = ? AND column_name = ? AND code_name = ?',
                    (field_value, tn, col, cn))
            elif target_table == 'schema_column_docs':
                # target_key = 'table.column'（表名/列名均不含点）
                tn, col = target_key.split('.', 1)
                conn.execute(
                    f'UPDATE {target_table} SET {field_name} = ? WHERE table_name = ? AND column_name = ?',
                    (field_value, tn, col))
            elif key_col:
                conn.execute(
                    f'UPDATE {target_table} SET {field_name} = ? WHERE {key_col} = ?',
                    (field_value, int(target_key) if key_col == 'id' and str(target_key).isdigit() else target_key))
        conn.execute(
            "UPDATE ingest_provenance SET field_value = ?, source_kind = 'manual',"
            " review_status = 'confirmed', reviewed_at = ? WHERE id = ?",
            (None if field_value is None else str(field_value), _now(), int(prov_id)))
        conn.commit()
        cursor = conn.execute(
            'SELECT id, target_table, target_key, field_name, field_value, source_kind, review_status'
            ' FROM ingest_provenance WHERE id = ?', (int(prov_id),))
        r = cursor.fetchone()
        return {'id': r[0], 'target_table': r[1], 'target_key': r[2], 'field_name': r[3],
                'field_value': r[4], 'source_kind': r[5], 'review_status': r[6]}


def reject_provenance(prov_id: int, db=None) -> dict:
    """复核否决：不同意变更，保留库内现状。不回填目标表，
    仅 review_status→rejected、reviewed_at=now。仅 pending 状态可否决。"""
    db = db or DatabaseManager()
    with db.connect_governance() as conn:
        row = conn.execute(
            'SELECT id, review_status FROM ingest_provenance WHERE id = ?',
            (int(prov_id),)).fetchone()
        if not row:
            raise LookupError(f'溯源记录不存在: {prov_id}')
        if row[1] != 'pending':
            raise ValueError(f'仅 pending 状态可否决（当前为 {row[1]}）')
        conn.execute(
            "UPDATE ingest_provenance SET review_status = 'rejected', reviewed_at = ? WHERE id = ?",
            (_now(), int(prov_id)))
        conn.commit()
        r = conn.execute(
            'SELECT id, target_table, target_key, field_name, field_value, source_kind, review_status'
            ' FROM ingest_provenance WHERE id = ?', (int(prov_id),)).fetchone()
        return {'id': r[0], 'target_table': r[1], 'target_key': r[2], 'field_name': r[3],
                'field_value': r[4], 'source_kind': r[5], 'review_status': r[6]}


def batch_reject(prov_ids, db=None) -> dict:
    """批量复核否决：不同意变更、保留库内现状。否决不改任何数据，无优先级限制
    （与 batch_confirm 对称：确认有数据风险需分级，否决是安全操作）。
    返回 {'rejected': n, 'skipped': n, 'failed': [(id, 错误)]}。"""
    rejected, skipped, failed = 0, 0, []
    for pid in prov_ids:
        try:
            reject_provenance(int(pid), db=db)
            rejected += 1
        except (LookupError, ValueError):
            skipped += 1  # 不存在或非 pending（已确认/已否决）
        except Exception as e:
            failed.append((int(pid), str(e)[:100]))
    return {'rejected': rejected, 'skipped': skipped, 'failed': failed}


def finish_run(run_id: str, db=None) -> dict:
    """收尾：统计复核状态，run → finished。"""
    db = db or DatabaseManager()
    with db.connect_governance() as conn:
        pending = conn.execute(
            "SELECT COUNT(*) FROM ingest_provenance WHERE run_id = ? AND review_status = 'pending'",
            (run_id,)).fetchone()[0]
    update_run_stats(run_id, status='finished', stats={'review_rows': pending}, db=db, finished=True)
    return {'run_id': run_id, 'pending_review': pending, 'status': 'finished'}
