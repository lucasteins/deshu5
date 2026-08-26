# -*- coding: utf-8 -*-
"""统一知识检索层：sql_knowledge 全表统一打分，查询不按类型过滤（表内也无类型列——
2026-08-19 两次瘦身：先删 kind 等 16 列，再删 match_signature/slot_spec 两列）。

业务裁定（2026-08-19）：match_signature（机器编码的问题适用特征）与 slot_spec（槽位填充
指令）属业务冗余——同样信息存在于 example_qa_ids（溯源例题）+ sql_tables（依赖表）+
sql_rule 槽位标记（{{ym}}、{{kw:字段}}）。签名在加载时运行时推导（见 _derive_signature）：
- tables ← sql_tables（JSON 解析）
- keywords ← example_qa_ids 关联的 qa_pairs.question 经 jieba 分词取业务关键词
  （停用词/年月/数字剔除；题号无效或缺失时退回 name+description 分词）
- agg ← sql_rule 中出现 SUM/COUNT/AVG/MAX/MIN 则记 ['agg']
推导签名喂给既有 score_signature_rows，判定阈值不变。

唯一入口 retrieve(question, tables=None, top_k=5) ->
    {'direct_match': item | None, 'items': [scored items]}

分向量（仅对 enabled=1 行打分）：
- signature_score：模板行（sql_rule 以 SELECT/WITH 开头）参与，守卫+判分逻辑抽取自
  SQLTemplateMatcher._match_by_signature（见 score_signature_rows，与原实现同口径：
  关键词命中≥1、签名表被问题关键词反查表全覆盖、G1~G4 语义形态守卫、
  无槽位模板要求签名关键词全命中）。signature_score>0 即"达到 TemplateMatcher 现行阈值"。
- trigger_score：trigger_words 子串命中比例。
- text_score：name/description/sql_rule 与问题关键词（jieba，经 RAGRetriever.extract_keywords，
  未装 jieba 时其内部回退简单分词）的重叠覆盖度。
- table_score：sql_tables 与入参 tables 的交集比例（按条目表集合归一）。
- total = 0.5*signature + 0.2*trigger + 0.2*text + 0.1*table。

direct_match 判定：总分最高行 item_type='template' 且 signature_score>0 → 判为直接命中
（供 template_first 直出）。item 携带 item_type（由内容推断：sql_rule 为完整查询骨架→
'template'，否则 trigger_words 非空→'rule'，否则 'note'），仅供调用方决定如何
渲染/使用 payload（骨架类/片段类均取 sql_rule），不是查询条件。

缓存：模块级全表缓存（含推导签名与例题问题，加载时一次性计算）；
resources 层 provider CRUD 后由各自 invalidate_* 联动失效。
"""
import json
import re
import threading

from core.database import DatabaseManager

# 合成权重（起步值，按实测调）
W_SIGNATURE = 0.5
W_TRIGGER = 0.2
W_TEXT = 0.2
W_TABLE = 0.1

_rows_cache = None         # list[dict]，enabled=1 全表行（含推导签名，加载时一次算好）
_cache_lock = threading.Lock()
_kw_extractor = None       # RAGRetriever 单例（仅取 extract_keywords；构造无 I/O）


def invalidate_knowledge_cache():
    """sql_knowledge 写操作后调用（由各 provider 的 invalidate_* 联动）：下次读取重新查库。
    推导签名与例题问题同属该缓存，一并重建。"""
    global _rows_cache
    with _cache_lock:
        _rows_cache = None


def _get_rows() -> list:
    """sql_knowledge enabled=1 全表行（推导后的 prepared 形态，进程级缓存）。
    表缺失/异常返回 []（调用方各通道自行回退）。"""
    global _rows_cache
    with _cache_lock:
        if _rows_cache is None:
            rows = []
            try:
                from modules.resources.providers.sql_template import _ensure_table
                from modules.resources.base import rows_to_dicts
                _ensure_table()
                db = DatabaseManager()
                with db.connect_governance() as conn:
                    rows = rows_to_dicts(conn.execute(
                        'SELECT * FROM sql_knowledge WHERE enabled = 1'))
            except Exception as e:
                print(f'[WARN] 读取 sql_knowledge 失败（检索层返回空）: {e}')
            _rows_cache = _prepare_all(rows)
        return _rows_cache


def _load_example_questions(rows: list) -> dict:
    """example_qa_ids → qa_pairs.question 文本（加载时一次性批量取，跟随本缓存失效）。
    失败返回 {}（签名关键词退回 name+description 分词）。"""
    ids = sorted({int(x) for r in rows
                  for x in re.split(r'[,，\s]+', r.get('example_qa_ids') or '') if x.isdigit()})
    if not ids:
        return {}
    try:
        db = DatabaseManager()
        with db.connect_governance() as conn:
            cursor = conn.execute(
                f"SELECT id, question FROM qa_pairs WHERE id IN ({', '.join(str(i) for i in ids)})")
            return {r[0]: (r[1] or '') for r in cursor.fetchall()}
    except Exception as e:
        print(f'[WARN] 例题问题加载失败（签名关键词退回 name/description 分词）: {e}')
        return {}


# 推导签名关键词的清洗口径（对齐原策展签名的用词习惯：只留业务判别词——
# 实测不剔除 数量/总额 等泛化意图词会使无槽位模板的全命中守卫掉题）
_GENERIC_INTENT_KW = ('数量', '多少', '总数', '总额', '个数', '笔数', '排名')


def _derive_keywords(texts: list) -> list:
    """问题文本列表 → 业务关键词（保序去重；剔除停用词/年月日/含数字 token/泛化意图词）。"""
    out, seen = [], set()
    for t in texts:
        for w in _question_keywords(t or ''):
            if (w in seen or w in _GENERIC_INTENT_KW
                    or any(c.isdigit() for c in w) or set(w) <= set('年月日')):
                continue
            seen.add(w)
            out.append(w)
    return out


def _derive_signature(r: dict, qtexts: dict) -> dict:
    """模板行签名运行时推导（替代已删的 match_signature 列，业务裁定 2026-08-19）：
    tables ← sql_tables；keywords ← 溯源例题问题分词清洗（无例题退回 name+description）；
    agg ← sql_rule 含聚合函数则记 ['agg']。"""
    qids = [int(x) for x in re.split(r'[,，\s]+', r.get('example_qa_ids') or '') if x.isdigit()]
    texts = [qtexts[i] for i in qids if i in qtexts]
    if not texts:
        texts = [f"{r.get('name') or ''} {r.get('description') or ''}"]
    agg = ['agg'] if re.search(r'\b(SUM|COUNT|AVG|MAX|MIN)\s*\(', r.get('sql_rule') or '', re.I) else []
    return {'tables': list(r['table_list']), 'keywords': _derive_keywords(texts), 'agg': agg}


def _prepare_all(rows: list) -> list:
    qtexts = _load_example_questions(rows)
    return [_prepare(r, qtexts) for r in rows]


def _json_loads(text, default=None):
    if not text:
        return default
    try:
        return json.loads(text)
    except Exception:
        return default


def _question_keywords(question: str) -> list:
    """问题关键词：复用 RAGRetriever.extract_keywords（jieba 可用时走 jieba，否则简单分词）。"""
    global _kw_extractor
    if _kw_extractor is None:
        from core.rag_retriever import RAGRetriever
        _kw_extractor = RAGRetriever(top_k=1)
    return _kw_extractor.extract_keywords(question)


def score_signature_rows(question: str, rows: list, concept_map: dict = None) -> dict:
    """签名守卫+打分（自 SQLTemplateMatcher._match_by_signature 抽取，判分逻辑不变）。

    rows: 含 signature(dict)/has_slots/sql_rule（模板骨架）的条目（prepared 形态）。
    concept_map: 可选概念→表映射注入（本体层概念面）；None 时读治理库 keyword_table_map。
    返回 {id: (kw_ratio, sig_sort_tuple)}；未过现行守卫（关键词命中≥1、签名表全覆盖、
    G1~G4、无槽位模板全命中）的行不在结果中——signature_score>0 即达到原匹配阈值。"""
    # 语义形态守卫（template-first A/B 实测误配驱动，2026-08-10）：
    # G1 TOPN 语义题只配 ORDER+LIMIT 骨架；G2 时间序语义题只配窗口/最新日期子查询骨架；
    # G3 计数语义题只配 COUNT 骨架；G4 问题带过滤信号而骨架无实质 WHERE 条件 → 拒答
    topn_hit = bool(re.search(r'前\s*\d+|top\s*\d+|排名|最高|最低|最多|最少', question, re.I))
    recency_hit = any(k in question for k in ('最近', '最新', '一期', '连续'))
    count_hit = any(k in question for k in ('多少', '数量', '笔数', '个数', '总数'))
    from modules.resources.providers.business_rule import get_draft_filter_signals
    filter_signals = get_draft_filter_signals() or ()
    filter_hit = any(k in question for k in filter_signals)

    # 问题关键词 → 表（概念映射：注入值优先——本体层概念面；否则治理库；库空回退代码常量）
    if concept_map:
        kw_map = concept_map
    else:
        from modules.resources.providers.keyword_table_map import get_keyword_table_map
        kw_map = get_keyword_table_map('intent')
        if not kw_map:
            from modules.training.engine.intent_parser import DEFAULT_CONCEPT_TO_TABLES
            kw_map = DEFAULT_CONCEPT_TO_TABLES
    q_tables = set()
    for kw, tables in kw_map.items():
        if kw and kw in question:
            q_tables.update(tables)

    out = {}
    for row in rows:
        sig = row.get('signature') or {}
        sig_tables = set(sig.get('tables') or [])
        sig_kws = [k for k in (sig.get('keywords') or []) if k]
        if not sig_tables or not sig_tables <= q_tables:
            continue  # 签名表须被问题关键词全覆盖
        kw_hits = [k for k in sig_kws if k in question]
        if not kw_hits:
            continue  # 关键词至少命中 1 个
        sk = row.get('sql_rule') or ''  # 瘦身版：模板骨架存于 sql_rule 列
        sk_up = sk.upper()
        if topn_hit and not ('ORDER BY' in sk_up and 'LIMIT' in sk_up):
            continue  # G1
        if recency_hit and not (' OVER' in sk_up or 'ROW_NUMBER' in sk_up
                                or re.search(r'MAX\s*\([^)]*(DATE|YM|TIME)', sk_up)):
            continue  # G2
        if count_hit and 'COUNT' not in sk_up:
            continue  # G3
        if filter_hit:
            where_m = re.search(r'\bWHERE\b(.+?)(?:\bGROUP\b|\bORDER\b|\bLIMIT\b|$)', sk, re.I | re.DOTALL)
            where_txt = where_m.group(1) if where_m else ''
            has_real_cond = re.search(r"(=|LIKE|IN|BETWEEN|>=|<=|>|<)\s*'?(\{\{|[\w一-鿿])", where_txt, re.I)
            # G4：骨架为"琐碎 WHERE + 无 GROUP BY"的裸表导出，而问题带过滤信号 → 拒答
            # （带 GROUP BY 的聚合骨架豁免：分组维度题如"各抄表方式客户数"的 gold 本就只配 IS NOT NULL 过滤）
            if not has_real_cond and 'GROUP BY' not in sk_up:
                continue
        # 打分：关键词命中率优先（防 jieba 兜底长词表噪声模板误伤），
        # 其次签名表数（更具体的结构优先）、命中数
        kw_ratio = len(kw_hits) / len(sig_kws) if sig_kws else 0.0
        # 无槽位模板（纯表查询骨架）语义锚点少，要求签名关键词全命中
        # （2026-08-19 起"有无槽位"由骨架是否含 {{占位符}} 现算，替代已删的 slot_spec 列）
        if not row.get('has_slots') and kw_ratio < 1.0:
            continue
        out[row['id']] = (kw_ratio, (kw_ratio, len(sig_tables), len(kw_hits)))
    return out


def _prepare(row: dict, qtexts: dict) -> dict:
    """解析 JSON 列 + 运行时推导（签名/槽位有无/类型），替代已删的 match_signature/slot_spec 列。
    item_type 分类口径与 resources 层 TPL_WHERE/RULE_WHERE 一致（仅渲染用，非查询条件）。"""
    r = dict(row)
    r['trigger_list'] = _json_loads(r.get('trigger_words'), []) or []
    tables = _json_loads(r.get('sql_tables'), []) or []
    r['table_list'] = [t for t in tables if t]
    is_template = bool(re.match(r'(?i)^\s*(SELECT|WITH)\b', r.get('sql_rule') or ''))
    r['item_type'] = 'template' if is_template else ('rule' if r['trigger_list'] else 'note')
    r['has_slots'] = '{{' in (r.get('sql_rule') or '')
    r['signature'] = _derive_signature(r, qtexts) if is_template else {}
    return r


def retrieve(question: str, tables: list = None, top_k: int = 5, concept_map: dict = None) -> dict:
    """统一知识检索：sql_knowledge 全表（enabled=1）统一打分，不按类型过滤。

    concept_map: 可选概念→表映射注入（本体层概念面，ontology 档由 SQLGenerator 传入）；
    None 时签名守卫读治理库 keyword_table_map（空表回退代码常量）。

    返回 {'direct_match': item|None, 'items': [按 total_score 降序的条目]}；
    item 在原始行字段上附加：item_type（内容推断，渲染判定时使用）、signature（推导）、
    has_slots/trigger_list/table_list、scores（各分向量与总分）、signature_score、
    total_score、_sig_sort。
    """
    prepared = _get_rows()
    if not prepared or not question:
        return {'direct_match': None, 'items': []}

    sig_rows = [p for p in prepared if p['item_type'] == 'template' and p.get('sql_rule')]
    sig_scores = score_signature_rows(question, sig_rows, concept_map=concept_map)
    q_kws = [k for k in _question_keywords(question) if k]
    in_tables = set(tables or [])

    items = []
    for p in prepared:
        sig = sig_scores.get(p['id'])
        signature_score = sig[0] if sig else 0.0

        triggers = [t for t in p['trigger_list'] if t]
        trigger_hits = [t for t in triggers if t in question]
        trigger_score = len(trigger_hits) / len(triggers) if triggers else 0.0

        blob = f"{p.get('name') or ''} {p.get('description') or ''} {p.get('sql_rule') or ''}".lower()
        text_hits = [k for k in q_kws if k.lower() in blob]
        text_score = len(text_hits) / len(q_kws) if q_kws else 0.0

        item_tables = set(p['table_list'])
        table_score = (len(item_tables & in_tables) / len(item_tables)
                       if item_tables and in_tables else 0.0)

        total = (W_SIGNATURE * signature_score + W_TRIGGER * trigger_score
                 + W_TEXT * text_score + W_TABLE * table_score)
        if total <= 0:
            continue
        p['scores'] = {'signature': round(signature_score, 4), 'trigger': round(trigger_score, 4),
                       'text': round(text_score, 4), 'table': round(table_score, 4),
                       'total': round(total, 4)}
        p['signature_score'] = signature_score
        p['total_score'] = total
        p['_sig_sort'] = sig[1] if sig else None
        items.append(p)

    items.sort(key=lambda x: x['total_score'], reverse=True)
    items = items[:max(int(top_k), 1)]

    direct = None
    if items and items[0].get('item_type') == 'template' and items[0]['signature_score'] > 0:
        direct = items[0]  # 最高分行是模板且过现行签名阈值 → 直接命中（template_first 直出）
    return {'direct_match': direct, 'items': items}
