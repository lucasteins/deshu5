# -*- coding: utf-8 -*-
"""智能出题模块：LLM 基于 35 张表业务内容自由生成自然语言业务问题。

设计思路（2026-08-14 优化：覆盖感知 + 批量生成 + Prompt 瘦身 + 去重缓存）：
1. 静态上下文（初始化时构建一次）：全量表描述 + 主外键关系（SchemaPreloader，与 SQL 生成同源）。
2. 覆盖感知锚点：按 qa_pairs 已出题数对表加权（欠覆盖表权重高），无放回抽样，
   保证长尾表/业务域随出题轮次逐渐被覆盖（解决旧版纯随机导致的覆盖不均）。
3. 批量生成 + 队列：一次 LLM 调用产出 QGEN_BATCH_SIZE 道题（每题指定不同锚点组），
   校验去重后入队；generate() 优先弹出队列——队列命中时近即时返回（解决逐题单调的时延）。
4. Prompt 瘦身：锚点表字段摘要按优先级截断（PK>描述列>日期列>度量列，默认 30 列/表），
   大表（如用电客户 145 列）不再全量入 prompt。
5. 去重缓存：规范化集合 + jieba 分词缓存初始化一次构建，出题/入库时增量更新。
6. 从实际业务库采样枚举值/年月作为真实条件值，供 LLM 参考，避免编造。
7. 重试耗尽返回 None，由 /api/next-question 回退到历史题库。

题型清单仅供 LLM 格式参考，不构成内容方向；题目内容由 LLM 从锚点表业务内容自由生成。

注：本模块不再生成 SQL，SQL 生成完全交给 engine/sql_generator.py。
"""
import json
import os
import random
import re
import sys
from datetime import datetime
from difflib import SequenceMatcher
from typing import Dict, List, Optional

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from core.database import DatabaseManager
from core.schema_loader import SchemaLoader
from core.schema_kb import SchemaKnowledgeBase


# ---------- 业务术语映射 ----------
BUSINESS_TABLE_NAMES = {
    'dim_cst_cust': '客户',
    'dim_cst_elec_cons_cust': '用电客户',
    'dim_cst_mgt_org': '供电单位',
    'dim_cst_inst_elec_cons': '安装点',
    'dwd_cst_meter_run': '计量点运行',
    'dwd_cst_meter_energy_day_h_xz': '日电量',
    'dwd_cst_rcvbl_acct': '应收电费',
    'dwd_cst_rcvd_acct': '实收电费',
    'dim_cst_dev': '设备',
}

# 常见字段的中文名称覆盖（避免使用数据库中不准确的注释）
COLUMN_NAME_OVERRIDES = {
    'dim_cst_mgt_org': {
        'mgt_org_code': '单位代码',
        'mgt_org_name': '单位名称',
        'city_name': '城市',
        'county_name': '区县',
    },
    'dwd_cst_meter_run': {
        'meter_asset_no': '资产编号',
        'meter_id': '计量点标识',
        'inst_id': '安装点标识',
    },
    'dim_cst_dev': {
        'dev_id': '设备标识',
        'dev_stat_desc': '设备状态',
        'dev_cls_desc': '设备分类',
    },
    'dim_cst_elec_cons_cust': {
        'cust_no': '客户编号',
        'cust_name': '客户名称',
        'cust_id': '客户标识',
        'cust_cls_desc': '客户分类',
        'ec_categ_desc': '用电类别',
        'cust_volt_desc': '电压等级',
    },
}

# 难度 → 可选题型（外部指定难度时从其题型池随机）
DIFFICULTY_TYPE_MAP = {
    '基础题': ['单表条件查询'],
    '进阶题': ['多表关联查询', '分组聚合统计', '分组计数'],
    '挑战题': ['TOP-N 排名', '时间趋势分析', '对比分析', '极值查询', '占比分析'],
}

# 难度配比 基础:进阶:挑战 = 1:2:1
DIFFICULTY_WEIGHTS = {'基础题': 1, '进阶题': 2, '挑战题': 1}

# 可调旋钮（config 可覆盖）：批量生成每批题数、锚点表字段摘要列数上限、出题专用 Provider
BATCH_SIZE = int(getattr(config, 'QGEN_BATCH_SIZE', '3'))
DIGEST_COL_CAP = int(getattr(config, 'QGEN_DIGEST_COL_CAP', '30'))
QGEN_PROVIDER = getattr(config, 'QGEN_PROVIDER', 'deepseek')
QGEN_MODEL = getattr(config, 'QGEN_MODEL', 'deepseek-chat')  # 出题专用模型（非推理版，恒推理模型实测太慢）

# 字段摘要优先级：PK > 描述列 > 日期/年月列 > 度量列 > 其他
_MEASURE_HINTS = ('_amt', '_qty', '_cap', '_bal', '_num', '_cnt', '_count',
                  'pap_', 'rap_', '_e', 'exp', 'price', 'rate')


class QuestionGenerator:
    """基于 LLM + Schema 知识自由出题的生成器（模板仅供格式参考）"""

    def __init__(self):
        self.db = DatabaseManager()
        self.schema_loader = SchemaLoader()
        self.schema_kb = SchemaKnowledgeBase()
        self.schema = self.schema_loader.load_schema()
        self.table_comments = self._load_table_comments()
        self.column_comments = self.schema_kb.get_column_comments()
        self.existing_questions = self._load_existing_questions()
        self._value_cache: Dict[str, List[str]] = {}
        self._static_schema_context: Optional[str] = None
        # 覆盖与去重状态（2026-08-14 新增）
        self._coverage = self._load_coverage()     # table -> 已出题数（qa_pairs.objects_involved）
        self._queue: List[Dict] = []               # 批量出题队列
        self._norm_set = set()                     # 规范化问题集合（精确去重 O(1)）
        self._kw_sets = []                         # [(norm, jieba 词集合)]（模糊去重缓存）
        for q in self.existing_questions:
            self._register_question(q)
        # 数据资源资产（工作流驱动出题：选业务分类→找表→找字段→找码值）
        self._domain_of_table, self._domain_names = self._load_domain_map()  # 表→二级业务域；域码→域中文名
        self._domain_coverage = self._load_domain_coverage()  # 二级业务域 -> 已出题数（qa_pairs.domain_l2）
        self._neighbors = self._load_neighbors()   # 表 -> 关联表集合（物理外键 ∪ 治理关系文档）

    # ---------- 数据加载 ----------
    def _load_table_comments(self) -> Dict[str, str]:
        """加载表注释。

        MySQL 模式权威源 = 业务库 information_schema（经 SchemaPreloader 单例缓存）；
        SQLite 模式保持 governance.schema_table_docs 文档路径不变。
        """
        comments = {}
        try:
            if self.db.get_dialect() == 'mysql':
                from core.schema_preloader import SchemaPreloader
                preloader = SchemaPreloader.get_instance()
                for name in preloader.get_table_names():
                    comments[name] = preloader.get_table_comment(name) or BUSINESS_TABLE_NAMES.get(name, name)
            else:
                with self.db.connect_governance() as conn:
                    for row in conn.execute('SELECT table_name, table_comment FROM schema_table_docs'):
                        name, comment = row
                        comments[name] = comment or BUSINESS_TABLE_NAMES.get(name, name)
        except Exception as e:
            print(f"[WARN] 加载表注释失败: {e}")
            comments = dict(BUSINESS_TABLE_NAMES)
        return comments

    def _load_existing_questions(self) -> List[str]:
        """加载可用问题文本，用于生成时去重（可用性门禁为 is_usable 单列，2026-08-14 起 question_rating 列已下线）。"""
        questions = []
        try:
            with self.db.connect_governance() as conn:
                cursor = conn.execute(
                    """
                    SELECT question FROM qa_pairs
                    WHERE question IS NOT NULL AND question != ''
                      AND (is_usable IS NULL OR is_usable = 1)
                    """
                )
                questions = [row[0] for row in cursor.fetchall()]
        except Exception as e:
            print(f"[WARN] 加载已有问题失败: {e}")
        return questions

    # ---------- 数据资源资产加载（业务分类/码值库/关系图谱） ----------
    def _load_domain_map(self):
        """表 → 二级业务域映射 + 域码 → 域中文名（来源：schema_table_docs.domain_l2 × business_domains）。"""
        dmap, dnames = {}, {}
        try:
            with self.db.connect_governance() as conn:
                for t, l2 in conn.execute(
                        'SELECT table_name, domain_l2 FROM schema_table_docs WHERE domain_l2 IS NOT NULL'):
                    dmap[t] = l2
                for code, name in conn.execute(
                        "SELECT domain_code, domain_name FROM business_domains WHERE level IN (1, 2)"):
                    dnames[code] = name
        except Exception as e:
            print(f"[WARN] 加载业务分类映射失败（退回无域引导出题）: {e}")
        return dmap, dnames

    def _load_domain_coverage(self) -> Dict[str, int]:
        """二级业务域已出题数（来源：qa_pairs.domain_l2）。"""
        cov: Dict[str, int] = {}
        try:
            with self.db.connect_governance() as conn:
                for l2, cnt in conn.execute(
                        'SELECT domain_l2, COUNT(*) FROM qa_pairs WHERE domain_l2 IS NOT NULL GROUP BY domain_l2'):
                    cov[l2] = cnt
        except Exception as e:
            print(f"[WARN] 加载业务域覆盖统计失败: {e}")
        return cov

    def _load_neighbors(self) -> Dict[str, set]:
        """表 → 可关联表集合（SchemaPreloader 合并关系视图：物理外键 ∪ 治理关系文档）。"""
        nb = {}
        try:
            from core.schema_preloader import SchemaPreloader
            preloader = SchemaPreloader.get_instance()
            for rel in preloader.get_relationships():
                path = rel.get('path') or []
                for a, b in zip(path, path[1:]):
                    nb.setdefault(a, set()).add(b)
                    nb.setdefault(b, set()).add(a)
        except Exception as e:
            print(f"[WARN] 加载表关系邻接失败: {e}")
        return nb

    def _load_coverage(self) -> Dict[str, int]:
        """统计每张表已被出题的次数（覆盖感知锚点选择的数据基础）。

        来源：qa_pairs.objects_involved（含金标题与已回流生成题）。"""
        cov: Dict[str, int] = {}
        try:
            with self.db.connect_governance() as conn:
                for (objs,) in conn.execute(
                        "SELECT objects_involved FROM qa_pairs WHERE objects_involved IS NOT NULL AND objects_involved != ''"):
                    try:
                        tables = json.loads(objs)
                    except Exception:
                        continue
                    for t in tables:
                        cov[t] = cov.get(t, 0) + 1
        except Exception as e:
            print(f"[WARN] 加载题目覆盖统计失败: {e}")
        return cov

    # ---------- Schema 工具 ----------
    def _table_comment(self, table: str) -> str:
        # 优先使用业务术语表，避免不准确的库注释
        return BUSINESS_TABLE_NAMES.get(table, self.table_comments.get(table, table))

    def _column_comment(self, table: str, col: str) -> str:
        return COLUMN_NAME_OVERRIDES.get(table, {}).get(col) \
            or self.column_comments.get(table, {}).get(col, col)

    def _get_static_schema_context(self) -> str:
        """全量表描述 + 主外键关系（初始化时构建一次，进程内缓存）"""
        if self._static_schema_context is None:
            try:
                from core.schema_preloader import SchemaPreloader
                preloader = SchemaPreloader.get_instance()
                lines = ["【数据库全景】"]
                for t in preloader.get_table_names():
                    comment = preloader.get_table_comment(t)
                    lines.append(f"- {t}（{comment}）" if comment else f"- {t}")
                lines.append("")
                lines.append("【主外键关联关系】")
                for rel in preloader.get_relationships():
                    lines.append(f"- {' → '.join(rel['path'])}：" + "；".join(rel['join_conditions']))
                self._static_schema_context = '\n'.join(lines)
            except Exception as e:
                print(f"[WARN] 构建出题静态上下文失败: {e}")
                self._static_schema_context = ''
        return self._static_schema_context

    # ---------- 工作流式锚点选择：选业务分类 → 找表（+关系扩展） ----------
    def _weighted_sample(self, items: List[str], k: int) -> List[str]:
        """按覆盖权重无放回抽样：权重 = 1/(1+该表已出题数)，欠覆盖表优先。"""
        pool = list(items)
        picked = []
        for _ in range(min(k, len(pool))):
            weights = [1.0 / (1.0 + self._coverage.get(t, 0)) for t in pool]
            choice = random.choices(pool, weights=weights, k=1)[0]
            pool.remove(choice)
            picked.append(choice)
        return picked

    def _pick_domain(self) -> Optional[str]:
        """覆盖感知业务域选择：权重 = 1/(1+该域已出题数)。无域数据时返回 None（退回全域随机）。"""
        domains = sorted({self._domain_of_table[t] for t in self._domain_of_table})
        if not domains:
            return None
        weights = [1.0 / (1.0 + self._domain_coverage.get(d, 0)) for d in domains]
        return random.choices(domains, weights=weights, k=1)[0]

    def _pick_anchor_tables(self, domain: Optional[str] = None, diff: Optional[str] = None) -> List[str]:
        """覆盖感知锚点表 2-4 张（至少 1 张 dwd 事实表）。

        工作流：先定业务域（候选表收敛到该域），再按表级覆盖权重抽样；
        进阶/挑战题经关系图谱扩展 1 张关联表，保证多表题有合法 JOIN 路径。"""
        from core.schema_preloader import SchemaPreloader
        names = SchemaPreloader.get_instance().get_table_names()
        if domain:
            pool = [t for t in names if self._domain_of_table.get(t) == domain]
            if len(pool) < 3:
                # 域内表太少：优先用该域表的关联表补足（保持业务主题连贯），再退回全域
                related = {n for t in pool for n in self._neighbors.get(t, ())}
                pool = pool + sorted(related - set(pool))
            if len(pool) < 2:
                pool = list(names)
        else:
            pool = list(names)
        facts = [t for t in pool if t.startswith('dwd_')] or [t for t in names if t.startswith('dwd_')]
        dims = [t for t in pool if not t.startswith('dwd_')] or [t for t in names if not t.startswith('dwd_')]
        n_facts = random.choice([1, 1, 2])
        anchors = self._weighted_sample(facts, k=min(n_facts, len(facts)))
        n_total = random.randint(2, 4)
        anchors += self._weighted_sample(dims, k=min(n_total - len(anchors), len(dims)))
        # 关系扩展：进阶/挑战题追加 1 张与锚点有合法 JOIN 路径的关联表
        if diff in ('进阶题', '挑战题') and self._neighbors:
            cand = sorted({n for a in anchors for n in self._neighbors.get(a, ()) if n not in anchors})
            if cand:
                anchors += self._weighted_sample(cand, k=1)
        return anchors

    # ---------- 字段摘要（Prompt 瘦身） ----------
    @staticmethod
    def _col_priority(col: Dict) -> int:
        name = col['name']
        if col.get('pk'):
            return 0
        if name.endswith('_desc'):
            return 1
        if name == 'data_date' or name.endswith('_ym') or name.endswith('_date'):
            return 2
        if any(h in name for h in _MEASURE_HINTS):
            return 3
        return 4

    def _build_anchor_columns_digest(self, tables: List[str]) -> str:
        """锚点表的字段摘要（按优先级截断至 DIGEST_COL_CAP 列/表，供 LLM 了解业务内容）"""
        from core.schema_preloader import SchemaPreloader
        preloader = SchemaPreloader.get_instance()
        lines = ["【锚点表字段（本题必须围绕这些表的业务内容出题）】"]
        for t in tables:
            comment = self._table_comment(t)
            cols = preloader.get_columns(t)
            ordered = sorted(cols, key=self._col_priority)
            shown = ordered[:DIGEST_COL_CAP]
            lines.append(f"表 {t}（{comment}）：")
            for c in shown:
                pk = ' PK' if c.get('pk') else ''
                cc = self._column_comment(t, c['name'])
                lines.append(f"  - {c['name']}{pk}：{cc}" if cc else f"  - {c['name']}{pk}")
            if len(cols) > len(shown):
                lines.append(f"  - …（共 {len(cols)} 列，仅示关键 {len(shown)} 列）")
        return '\n'.join(lines)

    # ---------- 采样辅助 ----------
    def _sample_values(self, table: str, column: str, n: int = 15) -> List[str]:
        key = f"{table}.{column}"
        if key not in self._value_cache:
            try:
                with self.db.connect_business() as conn:
                    # 列名来自 schema，内部调用可安全拼接
                    rows = conn.execute(
                        f"SELECT DISTINCT {column} FROM {table} "
                        f"WHERE {column} IS NOT NULL AND {column} != '' LIMIT 100"
                    ).fetchall()
                    self._value_cache[key] = [str(r[0]) for r in rows]
            except Exception as e:
                print(f"[WARN] 采样 {table}.{column} 失败: {e}")
                self._value_cache[key] = []
        vals = self._value_cache[key]
        if not vals:
            return []
        return random.sample(vals, min(n, len(vals)))

    def _pick_value(self, table: str, column: str) -> Optional[str]:
        vals = self._sample_values(table, column)
        return random.choice(vals) if vals else None

    def _sample_year_month(self, table: str, date_col: str) -> Optional[str]:
        """从日期字段采样一个 YYYY-MM 字符串，不依赖 SQL 日期函数。"""
        try:
            with self.db.connect_business() as conn:
                rows = conn.execute(
                    f"SELECT DISTINCT {date_col} FROM {table} "
                    f"WHERE {date_col} IS NOT NULL AND {date_col} != '' LIMIT 50"
                ).fetchall()
                yms = set()
                for r in rows:
                    v = str(r[0])
                    m = re.search(r'(\d{4})[-/](\d{2})', v)
                    if m:
                        yms.add(f"{m.group(1)}-{m.group(2)}")
                return random.choice(list(yms)) if yms else None
        except Exception as e:
            print(f"[WARN] 采样年月 {table}.{date_col} 失败: {e}")
            return None

    def _sample_ym_value(self, table: str, ym_col: str) -> Optional[str]:
        """从 YYYYMM 整型/字符串字段采样。"""
        try:
            with self.db.connect_business() as conn:
                rows = conn.execute(
                    f"SELECT DISTINCT {ym_col} FROM {table} "
                    f"WHERE {ym_col} IS NOT NULL AND {ym_col} != '' LIMIT 20"
                ).fetchall()
                yms = [str(r[0]) for r in rows if r[0]]
                return random.choice(yms) if yms else None
        except Exception as e:
            print(f"[WARN] 采样年月 {table}.{ym_col} 失败: {e}")
            return None

    def _sample_code_values(self, tables: List[str], cap_per_table: int = 2) -> List[str]:
        """码值库驱动采样（治理资产）：锚点表的码值列 → 真实枚举值（含存储形态）。

        来源：code_value_column_form（列↔码值域映射）× code_value_items（码值明细）。
        存编码的列给出 名称=编码 对照，供 LLM 在题目中使用名称、回流 SQL 使用编码。"""
        entries = []
        try:
            with self.db.connect_governance() as conn:
                marks = ','.join('?' * len(tables))
                rows = conn.execute(
                    f'SELECT table_name, column_name, code_name, form FROM code_value_column_form '
                    f'WHERE table_name IN ({marks})', tuple(tables)).fetchall()
                by_table: Dict[str, list] = {}
                for t, c, cn, form in rows:
                    by_table.setdefault(t, []).append((c, cn, form))
                for t in tables:
                    for c, cn, form in random.sample(by_table.get(t, []),
                                                     min(cap_per_table, len(by_table.get(t, [])))):
                        items = conn.execute(
                            'SELECT item_code, item_name FROM code_value_items '
                            'WHERE code_name = ? ORDER BY sort_order, id LIMIT 8', (cn,)).fetchall()
                        if not items:
                            continue
                        if form == '编码':
                            vals = '、'.join(f'{name}={code}' for code, name in items)
                        else:
                            vals = '、'.join(name for _, name in items)
                        col_cn = self._column_comment(t, c)
                        entries.append(f"- {t}.{c}（{col_cn}，存储形态={form or '未知'}）：{vals}")
        except Exception as e:
            print(f"[WARN] 码值库采样失败: {e}")
        return entries

    def _sample_anchor_values(self, tables: List[str]) -> Dict[str, list]:
        """锚点表真实条件值：优先码值库（治理资产），无映射列退回业务库 DISTINCT 采样；日期列采年月。"""
        from core.schema_preloader import SchemaPreloader
        preloader = SchemaPreloader.get_instance()
        code_entries = self._sample_code_values(tables)
        covered_cols = set()
        for e in code_entries:
            m = re.match(r'- (\w+)\.(\w+)', e)
            if m:
                covered_cols.add((m.group(1), m.group(2)))
        raw_samples = {}
        for t in tables:
            cols = preloader.get_columns(t)
            desc_cols = [c['name'] for c in cols
                         if c['name'].endswith('_desc') and (t, c['name']) not in covered_cols]
            for col in random.sample(desc_cols, min(1 if code_entries else 2, len(desc_cols))):
                v = self._pick_value(t, col)
                if v:
                    raw_samples[f"{t}.{col}"] = v
            date_cols = [c['name'] for c in cols
                         if c['name'] == 'data_date' or c['name'].endswith('_ym')]
            for col in date_cols[:1]:
                ym = self._sample_year_month(t, col) if col == 'data_date' \
                    else self._sample_ym_value(t, col)
                if ym:
                    raw_samples[f"{t}.{col}"] = ym
        return {'code_values': code_entries, 'raw': raw_samples}

    # ---------- LLM 出题（批量） ----------
    def _pick_question_type(self, difficulty: Optional[str]) -> tuple:
        """先按 1:2:1 加权随机难度，再在难度内随机题型；外部指定难度时从其题型池随机"""
        if difficulty and difficulty in DIFFICULTY_TYPE_MAP:
            return random.choice(DIFFICULTY_TYPE_MAP[difficulty]), difficulty
        diff = random.choices(
            list(DIFFICULTY_WEIGHTS.keys()),
            weights=list(DIFFICULTY_WEIGHTS.values())
        )[0]
        return random.choice(DIFFICULTY_TYPE_MAP[diff]), diff

    def _call_llm(self, prompt: str) -> str:
        """出题专用 LLM 调用：Provider 由 config.QGEN_PROVIDER 指定（默认 deepseek）。

        配置取自 llm_config 持久化设置（Key 在前端设置页维护，不落代码库）；
        deepseek 为恒推理模型，max_tokens 由 build_request 自动抬到 16000。"""
        from core.llm_config import preview_config as _llm_preview, build_request as _llm_build
        payload_cfg = {'provider': QGEN_PROVIDER}
        if QGEN_MODEL:
            payload_cfg['model'] = QGEN_MODEL
        cfg = _llm_preview(payload_cfg)
        if not cfg.get('api_key'):
            raise ValueError(f"{QGEN_PROVIDER} API Key 未配置（请在前端设置页配置 {QGEN_PROVIDER} 的 Key）")
        url, headers, payload = _llm_build(
            [{'role': 'user', 'content': prompt}], max_tokens=2000, thinking=None, cfg=cfg)
        resp = requests.post(url, headers=headers, json=payload, timeout=config.LLM_TIMEOUT_FAST)
        resp.raise_for_status()
        return resp.json()['choices'][0]['message']['content']

    def _generate_batch(self, difficulty: Optional[str], batch_size: int) -> List[Dict]:
        """一次 LLM 调用批量产出 batch_size 道题。

        工作流（每题）：选业务分类（覆盖感知）→ 找表（域内覆盖抽样 + 关系图谱扩展）
        → 找字段（优先级截断摘要）→ 找码值（码值库真实枚举，含存储形态）。"""
        tasks = []
        for _ in range(batch_size):
            domain = self._pick_domain()
            qtype, diff = self._pick_question_type(difficulty)
            anchors = self._pick_anchor_tables(domain, diff)
            tasks.append({
                'domain': domain,
                'anchors': anchors,
                'qtype': qtype,
                'diff': diff,
                'digest': self._build_anchor_columns_digest(anchors),
                'samples': self._sample_anchor_values(anchors),
            })

        task_blocks = []
        for i, t in enumerate(tasks, 1):
            s = t['samples']
            samples_text = '\n'.join(s['code_values'] +
                                     [f"- {k}：{v}" for k, v in s['raw'].items()]) or '（无）'
            domain_text = (f"业务分类：{t['domain']}（{self._domain_names.get(t['domain'], '')}）"
                           if t['domain'] else '业务分类：（不限）')
            task_blocks.append(
                f"【任务{i}】{domain_text}；题型：{t['qtype']}；难度：{t['diff']}\n"
                f"{t['digest']}\n【可参考的真实条件值（来自码值库治理资产/业务库采样，可选用，也可不用）】\n{samples_text}")
        tasks_text = '\n\n'.join(task_blocks)

        prompt = f"""你是电力营销业务专家。请基于下方数据库内容，设计 {batch_size} 道互不相同的自然业务查询问题，第 i 道必须围绕【任务i】给出的锚点表业务内容。

{self._get_static_schema_context()}

{tasks_text}

【要求】（每道题均须满足）
1. 围绕本任务锚点表的业务内容出题，可结合其关联表；问题必须能用库中真实表和字段回答
2. 使用自然的中文业务语言，像业务人员提出的真实问题；问题简洁明了（不超过两句话，筛选条件不超过 3 个）；不要出现表名、字段名等技术术语
3. 题目中的条件值（枚举值、年月）优先使用上方真实采样值，不要编造
4. 涉及"异常/波动/偏差/偏高/偏低/频繁/长期/连续"等模糊概念时，必须同时给出可计算的具体规则：明确的度量字段、阈值、比较基准、时间窗（如"当天用电量为0""较上月平均用电量偏差超过50%""连续3天无抄表数据"），且规则必须能用锚点表字段表达——三类难度统一适用
5. {batch_size} 道题之间主题不得重复
6. 推理从简：每题直接按要求给出结果，不要长篇权衡分析
7. 只输出 JSON：{{"questions": [{{"anchor": 1, "question": "...", "tables_involved": ["..."]}}, ...]}}，共 {batch_size} 道，不要输出任何其他内容"""

        content = self._call_llm(prompt)
        m = re.search(r'\{.*\}', content, re.S)
        if not m:
            return []
        data = json.loads(m.group(0))
        items = data.get('questions')
        if not isinstance(items, list) and data.get('question'):
            items = [data]  # 兜底：模型返回了单题格式
        if not items:
            return []

        known = set(self.schema_loader.get_table_names())
        accepted = []
        for i, item in enumerate(items):
            question = (item.get('question') or '').strip()
            if not question:
                continue
            anchor_idx = item.get('anchor')
            fallback_anchors = tasks[anchor_idx - 1]['anchors'] \
                if isinstance(anchor_idx, int) and 1 <= anchor_idx <= len(tasks) \
                else tasks[min(i, len(tasks) - 1)]['anchors']
            tables = [t for t in item.get('tables_involved', []) if isinstance(t, str) and t in known]
            accepted.append({
                'question': question,
                'difficulty': tasks[min(i, len(tasks) - 1)]['diff'],
                'tags': [tasks[min(i, len(tasks) - 1)]['qtype']],
                'tables_involved': tables or fallback_anchors,
                'sampled_values': tasks[min(i, len(tasks) - 1)]['samples'],
            })
        return accepted

    # ---------- 主入口 ----------
    def generate(self, difficulty: Optional[str] = None, max_retries: int = 3) -> Optional[Dict]:
        """生成一道自然语言业务题。队列优先（批量出题的余量），失败重试后返回 None。"""
        if self._queue:
            cand = self._queue.pop(0)
            self._register_question(cand['question'])  # 占位防重（同批/后续批次不再复用）
            return cand
        for _ in range(max_retries):
            try:
                accepted = self._generate_batch(difficulty, BATCH_SIZE)
            except Exception as e:
                print(f"[WARN] LLM 批量出题失败: {e}")
                continue
            for cand in accepted:
                if self._is_novel(cand):
                    self._queue.append(cand)
            if self._queue:
                cand = self._queue.pop(0)
                self._register_question(cand['question'])
                return cand
        return None

    # ---------- 去重（缓存加速） ----------
    @staticmethod
    def _normalize_question(text: str) -> str:
        """问题标准化：去除空格、标点、英文大小写，用于严格去重"""
        text = text.strip().lower()
        # 去除常见中文/英文标点、空格、数字（保留汉字/字母用于语义比较）
        text = re.sub(r'[\s,，.。!！?？;；:：""''()（）\[\]【】\-/_]+', '', text)
        return text

    @staticmethod
    def _jieba_set(text: str) -> set:
        try:
            import jieba
            return set(jieba.lcut(text))
        except Exception:
            return set(text)

    def _register_question(self, question: str):
        """把问题纳入去重缓存（初始化批量构建；出题弹出/入库时增量更新）。"""
        norm = self._normalize_question(question)
        if norm in self._norm_set:
            return
        self._norm_set.add(norm)
        self._kw_sets.append((norm, self._jieba_set(question)))
        self.existing_questions.append(question)

    def _is_novel(self, candidate: Dict, char_threshold: float = 0.78, keyword_threshold: float = 0.85) -> bool:
        """
        与已有问题去重。
        - 完全相同：一律禁止。
        - 字符相似 / 关键词重叠：50% 概率放行，用于检验 SQL 生成的鲁棒性。
        """
        question = candidate['question'].strip()
        norm_q = self._normalize_question(question)

        # 1) 精确去重（规范化集合 O(1)）
        if norm_q in self._norm_set:
            print(f"[QuestionGen] 去重：与已有问题完全相同 -> {question}")
            return False

        # 2) 非严格重复：50% 概率放行，检验 SQL 生成鲁棒性
        cand_kw = self._jieba_set(question)
        for ex_norm, ex_kw in self._kw_sets:
            if SequenceMatcher(None, ex_norm, norm_q).ratio() >= char_threshold:
                if random.random() < 0.5:
                    print(f"[QuestionGen] 放行（字符相似）：{question}")
                    return True
                print(f"[QuestionGen] 去重：字符相似度过高 -> {question}")
                return False

            if cand_kw and ex_kw:
                inter = cand_kw & ex_kw
                overlap = len(inter) / min(len(cand_kw), len(ex_kw))
                if overlap >= keyword_threshold:
                    if random.random() < 0.5:
                        print(f"[QuestionGen] 放行（关键词重叠）：{question}")
                        return True
                    print(f"[QuestionGen] 去重：关键词重叠度过高 -> {question}")
                    return False

        return True

    def save_to_qa_pairs(self, candidate: Dict) -> int:
        """将生成的自然语言题目持久化到 qa_pairs 表，标准 SQL 留空，等待后续回流。"""
        with self.db.connect_governance() as conn:
            cursor = conn.execute(
                """
                INSERT INTO qa_pairs (
                    question, standard_sql, difficulty, source, tags,
                    generation_method, ingest_time, is_usable
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate['question'],
                    '',  # 出题模块不生成 SQL，但字段非空，先存空字符串
                    candidate['difficulty'],
                    '智能出题',
                    json.dumps(candidate.get('tags', []), ensure_ascii=False),
                    'schema-driven',
                    datetime.now().isoformat(),
                    0,  # 新生成题目默认可用性为 0，需经人工评价为“合理”后才进入 RAG/去重池
                )
            )
            conn.commit()
            new_id = cursor.lastrowid
        # 覆盖计数与去重缓存增量更新（不入库的题目在弹出时已占位，这里幂等）
        for t in candidate.get('tables_involved', []):
            self._coverage[t] = self._coverage.get(t, 0) + 1
        self._register_question(candidate['question'])
        return new_id


if __name__ == '__main__':
    gen = QuestionGenerator()
    for _ in range(5):
        q = gen.generate()
        if q:
            print(f"\n难度: {q['difficulty']}")
            print(f"问题: {q['question']}")
            print(f"标签: {q['tags']}")
            print(f"涉及表: {q['tables_involved']}")
