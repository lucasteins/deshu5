# -*- coding: utf-8 -*-
"""关键词-表映射提取器：分词提取 → keyword_table_map。

数据源（两部分，对应需求）：
1. 治理库 schema_table_docs（表名 / 中文名 table_comment / 表描述 doc_text）
   + schema_column_docs 核心字段（PK 列名 column_name / 中文注释 column_comment）
2. 仿真业务库（fz01）中「非生产库表」（不在生产业务库 sc01 里的表）：
   从 information_schema 提取表名 + 列名 + 列注释

分词策略：
- 中文：jieba，过滤停用词与单字噪声
- 英文标识符：snake_case / 非字母数字拆分，过滤层前缀（dim/dwd/ads…）与通用后缀（id/no/code…）
- 列名整体作为关键词保留（如 meter_asset_no / pap_e，与存量数据口径一致）

写入：INSERT IGNORE 幂等追加（不破坏既有映射）；写后失效 keyword 缓存。
"""
import os
import re
import sys

import pymysql

# 优先使用项目本地 .vendor 下的 jieba（与 core/rag_retriever 同口径）
_VENDOR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), '.vendor')
if os.path.isdir(_VENDOR) and _VENDOR not in sys.path:
    sys.path.insert(0, _VENDOR)

try:
    import jieba
    _JIEBA_AVAILABLE = True
except ImportError:
    jieba = None
    _JIEBA_AVAILABLE = False

import config
from core import db_profile
from core.database import DatabaseManager
from modules.resources.providers.keyword_table_map import _ensure_table, invalidate_keyword_cache

# 中文停用词（短虚词 + 高频动词，对表定位无判别力）
_STOP_CN = {
    '的', '了', '是', '在', '有', '和', '与', '或', '为', '对', '从', '到', '及', '等',
    '这', '那', '中', '上', '下', '查询', '统计', '获取', '查找', '列出', '所有',
    '信息', '数据', '记录', '描述', '说明', '表', '字段', '名称', '行数', '时间', '范围',
    '层级', '负责人', '频率', '来源', '系统', '中文名', '表名',
}

# 英文层前缀（表名前缀，无业务判别力）
_LAYER_PREFIXES = {
    'dim', 'dwd', 'dws', 'ads', 'ast', 'ods', 'stg', 'tmp', 'bak',
    'cst', 'grid', 'prj', 'ngdis', 'equ', 't', 'ts', 'sg', 'da', 'lc',
}

# 英文通用后缀/噪声词（列名常见后缀，作为独立关键词判别力低）
_NOISE_EN = {
    'id', 'no', 'code', 'name', 'desc', 'flag', 'stat', 'type', 'date', 'time',
    'num', 'cnt', 'data', 'txt', 'text', 'val', 'value', 'info', 'dt', 'ym',
    'b', 'h', 'p', 'v', 'a', 'xz', 'new', 'old', 'tmp', 'cur', 'prev',
    'is', 'has', 'of', 'to', 'for', 'and', 'or', 'the', 'a', 'an',
    # CIM 模型通用元数据列名（树结构/同步/分类等，无业务判别力）
    'sync', 'pd', 'odps', 'tddc', 'parent', 'root', 'alias', 'class',
    'nature', 'team', 'master', 'use', 'depart', 'section', 'district',
}


def _cn_tokens(text: str) -> set:
    """中文分词：jieba 优先，未安装时按标点/空格切分兜底。仅保留含 CJK 的 token
    （纯 ASCII 标识符交由 _en_tokens 处理，避免 jieba 把 snake_case 整体当成一个词）。"""
    if not text:
        return set()
    if _JIEBA_AVAILABLE:
        words = jieba.lcut(str(text))
    else:
        words = re.split(r'[\s,，.。!！?？;；:：（）()【】\[\]\-/]+', str(text))
    out = set()
    for tok in words:
        tok = tok.strip()
        if len(tok) < 2:
            continue
        if tok in _STOP_CN:
            continue
        if not re.search(r'[\u4e00-\u9fff]', tok):  # 跳过纯 ASCII（交给 _en_tokens）
            continue
        if re.fullmatch(r'[\d\W]+', tok):  # 纯数字/标点
            continue
        out.add(tok)
    return out


def _en_tokens(text: str) -> set:
    """英文标识符分词：snake_case / 非字母数字拆分，过滤层前缀与通用噪声。"""
    if not text:
        return set()
    out = set()
    for raw in re.split(r'[^a-zA-Z0-9]+', str(text)):
        raw = raw.strip().lower()
        if len(raw) < 2:
            continue
        if raw in _LAYER_PREFIXES or raw in _NOISE_EN:
            continue
        if raw.isdigit():
            continue
        out.add(raw)
    return out


# ==================== 数据读取 ====================

def _read_schema_docs(db: DatabaseManager):
    """治理库 schema_table_docs + schema_column_docs(PK 核心字段)。"""
    tables = []  # [(table_name, comment, doc_text)]
    columns = []  # [(table_name, column_name, column_comment)]
    with db.connect_governance() as conn:
        try:
            for tname, comment, doc in conn.execute(
                    'SELECT table_name, table_comment, doc_text FROM schema_table_docs'):
                tables.append((tname, comment or '', doc or ''))
        except Exception as e:
            print(f'[keyword] 读 schema_table_docs 失败: {e}')
        try:
            for tname, cname, ccomment in conn.execute(
                    'SELECT table_name, column_name, column_comment '
                    'FROM schema_column_docs WHERE is_pk = 1'):
                columns.append((tname, cname, ccomment or ''))
        except Exception as e:
            print(f'[keyword] 读 schema_column_docs 失败: {e}')
    return tables, columns


def _read_staging_only_tables() -> list:
    """仿真业务库中非生产业务库的表：[(table_name, comment, [(col_name, col_comment, is_pk)])]。

    生产/仿真业务库名取自 db_profile.PROFILES；任一库不可达则返回 []。
    """
    prod_biz = db_profile.PROFILES['production']['business']
    stag_biz = db_profile.PROFILES['staging']['business']
    try:
        prod_tables = _information_schema_tables(prod_biz)
        stag_tables = _information_schema_tables(stag_biz)
    except Exception as e:
        print(f'[keyword] 仿真/生产业务库读取失败，跳过非生产表提取: {e}')
        return []
    only = sorted(set(stag_tables) - set(prod_tables))
    if not only:
        return []
    # 读这些表的列信息（PK 标记 + 列名 + 注释）
    cols_by_table = _information_schema_columns(stag_biz, only)
    out = []
    for t in only:
        comment = stag_tables.get(t, '')
        out.append((t, comment, cols_by_table.get(t, [])))
    return out


def _information_schema_tables(db_name: str) -> dict:
    """{table_name: table_comment}。"""
    conn = pymysql.connect(host=config.MYSQL_HOST, port=config.MYSQL_PORT,
                           user=config.MYSQL_USER, password=config.MYSQL_PASSWORD,
                           database=db_name, charset=config.MYSQL_CHARSET)
    try:
        with conn.cursor() as cur:
            cur.execute('''SELECT table_name, table_comment FROM information_schema.tables
                           WHERE table_schema = %s AND table_type = 'BASE TABLE' ''', (db_name,))
            return {r[0]: (r[1] or '') for r in cur.fetchall()}
    finally:
        conn.close()


def _information_schema_columns(db_name: str, tables: list) -> dict:
    """{table_name: [(column_name, column_comment, is_pk)]}。"""
    if not tables:
        return {}
    conn = pymysql.connect(host=config.MYSQL_HOST, port=config.MYSQL_PORT,
                           user=config.MYSQL_USER, password=config.MYSQL_PASSWORD,
                           database=db_name, charset=config.MYSQL_CHARSET)
    try:
        with conn.cursor() as cur:
            cur.execute('''SELECT table_name, column_name, column_comment, column_key
                           FROM information_schema.columns
                           WHERE table_schema = %s ORDER BY table_name, ordinal_position''',
                        (db_name,))
            out = {}
            for tname, cname, ccomment, ckey in cur.fetchall():
                out.setdefault(tname, []).append(
                    (cname, ccomment or '', (ckey or '').upper() == 'PRI'))
            return {t: out.get(t, []) for t in tables}
    finally:
        conn.close()


# ==================== 主入口 ====================

# 来源权重：表名/中文名最强（关键词是该表的身份标识），列名整体/列注释次之，表描述/列名拆分最弱
_W_TABLE_NAME = 5
_W_TABLE_COMMENT = 5
_W_DOC_TEXT = 1
_W_COL_FULL = 3
_W_COL_COMMENT = 3
_W_COL_NAME_SPLIT = 1


def extract_keywords(progress_cb=None, db=None, rebuild: bool = True) -> dict:
    """提取并写入 keyword_table_map。

    打分消歧：同一关键词若来自多张表，按来源权重累计打分，只保留最高分那张表
    （同分按表名字典序兜底），实现「一关键词一表」。rebuild=True 先清空再写
    （1:1 必须清旧，否则既有同关键词多表行无法被 INSERT IGNORE 移除）。

    progress_cb(stage, msg)：阶段进度回调。
    """
    db = db or DatabaseManager()
    _ensure_table(db)

    scores = {}  # (keyword, table_name) -> 累计分
    cov = {}     # (keyword, table_name) -> 关键词占来源文本的最大比例（同分时优先主体表）
    stats = {'schema_doc_tables': 0, 'schema_doc_columns': 0,
             'staging_only_tables': 0, 'raw_pairs': 0, 'keywords': 0,
             'tables_covered': 0, 'new_rows': 0, 'rebuild': rebuild}

    def _emit(stage, msg):
        if progress_cb:
            try:
                progress_cb(stage, msg)
            except Exception:
                pass

    def _add(kw, table, weight, source_text=None):
        kw = kw[:64]
        if not kw or not table:
            return
        key = (kw, table[:128])
        scores[key] = scores.get(key, 0) + weight
        if source_text:
            ratio = len(kw) / max(len(str(source_text)), 1)
            if ratio > cov.get(key, 0.0):
                cov[key] = ratio

    # ---- 1. 治理库 schema 文档 ----
    _emit('keywords', '从 schema_table_docs / schema_column_docs 提取关键词（打分）')
    tables, columns = _read_schema_docs(db)
    stats['schema_doc_tables'] = len(tables)
    stats['schema_doc_columns'] = len(columns)
    for tname, comment, doc in tables:
        for k in _en_tokens(tname):           # 表名拆分 token（身份标识，权重最高）
            _add(k, tname, _W_TABLE_NAME)
        for k in _cn_tokens(comment):         # 中文名分词
            _add(k, tname, _W_TABLE_COMMENT + (1 if k == (comment or '').strip() else 0), source_text=comment)
        for k in _cn_tokens(doc):             # 表描述
            _add(k, tname, _W_DOC_TEXT, source_text=doc)
    for tname, cname, ccomment in columns:
        # 列名整体（精确身份）+ 拆分 token + 中文注释分词
        ident = re.sub(r'[^a-zA-Z0-9_]', '', str(cname)).lower()
        if ident and ident not in _LAYER_PREFIXES and ident not in _NOISE_EN and ('_' in ident or len(ident) >= 4):
            _add(ident, tname, _W_COL_FULL)
        for k in _en_tokens(cname):
            _add(k, tname, _W_COL_NAME_SPLIT)
        for k in _cn_tokens(ccomment):
            _add(k, tname, _W_COL_COMMENT + (1 if k == (ccomment or '').strip() else 0), source_text=ccomment)
    _emit('keywords', f'schema 文档打分 {len(scores)} 个（关键词,表）对')

    # ---- 2. 仿真库非生产表 ----
    _emit('keywords', '从仿真业务库非生产表提取关键词（打分）')
    staging_only = _read_staging_only_tables()
    stats['staging_only_tables'] = len(staging_only)
    for tname, comment, cols in staging_only:
        for k in _en_tokens(tname):
            _add(k, tname, _W_TABLE_NAME)
        for k in _cn_tokens(comment):
            _add(k, tname, _W_TABLE_COMMENT + (1 if k == (comment or '').strip() else 0), source_text=comment)
        pk_cols = [(c, cc) for c, cc, ispk in cols if ispk]
        core = pk_cols or [(c, cc) for c, cc, _ in cols[:5]]
        for cname, ccomment in core:
            ident = re.sub(r'[^a-zA-Z0-9_]', '', str(cname)).lower()
            if ident and ident not in _LAYER_PREFIXES and ident not in _NOISE_EN and ('_' in ident or len(ident) >= 4):
                _add(ident, tname, _W_COL_FULL)
            for k in _en_tokens(cname):
                _add(k, tname, _W_COL_NAME_SPLIT)
            for k in _cn_tokens(ccomment):
                _add(k, tname, _W_COL_COMMENT + (1 if k == (ccomment or '').strip() else 0), source_text=ccomment)
    stats['raw_pairs'] = len(scores)
    _emit('keywords', f'非生产表打分完成，累计 {len(scores)} 个（关键词,表）对')

    # ---- 消歧：每关键词取最高分表（同分按覆盖度优先，再按表名字典序）----
    by_kw = {}
    for (kw, table), sc in scores.items():
        by_kw.setdefault(kw, []).append((sc, cov.get((kw, table), 0.0), table))
    chosen = []  # [(keyword, table)]
    multi_resolved = 0
    for kw, lst in by_kw.items():
        if len(lst) > 1:
            multi_resolved += 1
        # 分数降序 → 覆盖度降序 → 表名字典序升序（确定性）
        lst.sort(key=lambda x: (-x[0], -x[1], x[2]))
        chosen.append((kw, lst[0][2]))
    stats['keywords'] = len(chosen)
    stats['tables_covered'] = len({t for _, t in chosen})
    _emit('keywords', f'消歧完成：{len(chosen)} 关键词（{multi_resolved} 个原多表已收敛为单表）'
                      f'，覆盖 {stats["tables_covered"]} 张表')

    # ---- 写入 ----
    new_rows = 0
    with db.connect_governance() as conn:
        if rebuild:
            conn.execute('DELETE FROM keyword_table_map')
        batch = [(k, t) for k, t in chosen]
        for i in range(0, len(batch), 500):
            chunk = batch[i:i + 500]
            cur = conn.executemany(
                'INSERT IGNORE INTO keyword_table_map (keyword, table_name, enabled) '
                'VALUES (?, ?, 1)', chunk)
            new_rows += cur.rowcount
        conn.commit()
    invalidate_keyword_cache()
    stats['new_rows'] = new_rows
    _emit('keywords', f'写入完成：{"重建" if rebuild else "追加"} {new_rows} 行（{len(chosen)} 消歧后对）')
    return stats


if __name__ == '__main__':
    import json
    print(json.dumps(extract_keywords(), ensure_ascii=False, indent=2))
