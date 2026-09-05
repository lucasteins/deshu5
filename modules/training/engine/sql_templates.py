# -*- coding: utf-8 -*-
"""常见业务 SQL 模板：用规则模板直接生成高频问题，降低对 LLM 的依赖

P1（模板库重做）：新增签名匹配主通道——从 sql_knowledge 模板行（sql_rule 为完整骨架）
读参数化骨架模板，按"关键词命中 + 签名表全覆盖"筛选后做槽位填充，
执行校验通过即返回（via='signature'）；未命中/填充失败落原正则兜底通道（via='regex'）。
2026-08-19 业务裁定：match_signature/slot_spec 列已删（业务冗余），签名由检索层
knowledge_retriever 加载时推导（example_qa_ids 例题分词），槽位由本类 _fill_skeleton
按占位符约定 + 骨架上下文/列采样推导。
"""
import re
from typing import Dict, List, Optional

from core.database import DatabaseManager

# P2：模板元数据已入库（sql_knowledge 模板行）；match 时尊重 enabled
# （表缺失/异常/空表返回 None = 全部启用，行为与入库前一致）
from modules.resources.providers.sql_template import get_enabled_template_names

# 槽位推导采样缓存（进程级）：列→宿主表、列采样日期形态、列 DISTINCT 采样值
_COL_TABLE_CACHE = {}
_COL_FMT_CACHE = {}
_COL_VALS_CACHE = {}


class SQLTemplateMatcher:
    """基于问题模板的 SQL 生成器"""

    def __init__(self, concept_map: dict = None):
        # concept_map：本体层概念面注入（ontology 档）；None 时签名守卫自行读治理库/常量
        self._concept_map = concept_map
        self.templates = self._build_templates()
    
    def _build_templates(self) -> List[Dict]:
        """定义常见模板"""
        return [
            {
                'name': '高压客户用电量TOP-N',
                'patterns': [
                    r'(\d{4})年(\d{1,2})月.*?高压客户.*?用电.*?top\s*(\d+)',
                    r'(\d{4})年(\d{1,2})月.*?高压客户.*?用电.*?前\s*(\d+)',
                    r'(\d{4})年(\d{1,2})月.*?用电.*?前\s*(\d+).*?高压客户',
                    r'(\d{4})年(\d{1,2})月.*?排名.*?(\d+).*?高压客户',
                ],
                'slots': ['year', 'month', 'n'],
                'sql_builder': self._topn_hv_cust_energy,
            },
            {
                'name': 'TOP-N 用电量客户',
                'patterns': [
                    r'(\d{4})年(\d{1,2})月.*?客户.*?用电.*?(?:top|前|排名)\s*(\d+)',
                    r'(\d{4})年(\d{1,2})月.*?用电.*?(?:top|前|排名)\s*(\d+).*?客户',
                ],
                'slots': ['year', 'month', 'n'],
                'sql_builder': self._topn_energy_by_cust,
            },
            {
                'name': 'TOP-N 用电量计量点',
                'patterns': [
                    r'(\d{4})年(\d{1,2})月.*?计量点.*?用电.*?top\s*(\d+)',
                    r'(\d{4})年(\d{1,2})月.*?计量点.*?用电.*?前\s*(\d+)',
                    r'(\d{4})年(\d{1,2})月.*?用电.*?计量点.*?top\s*(\d+)',
                    r'(\d{4})年(\d{1,2})月.*?用电.*?计量点.*?前\s*(\d+)',
                    r'(\d{4})年(\d{1,2})月.*?用电.*?top\s*(\d+)',
                    r'(\d{4})年(\d{1,2})月.*?用电.*?前\s*(\d+)',
                ],
                'slots': ['year', 'month', 'n'],
                'sql_builder': self._topn_energy_by_meter,
            },
            {
                'name': '按供电单位统计高压客户用电量总额',
                'patterns': [
                    r'各供电单位.*?(\d{4})年(\d{1,2})月.*?高压客户.*?用电',
                    r'(\d{4})年(\d{1,2})月.*?各供电单位.*?高压客户.*?用电',
                    r'各供电单位.*?(\d{4})年(\d{1,2})月.*?高压.*?用电',
                    r'.*?高压客户.*?(\d{4})年(\d{1,2})月.*?用电',
                    r'(\d{4})年(\d{1,2})月.*?高压客户.*?用电',
                ],
                'slots': ['year', 'month'],
                'sql_builder': self._hv_cust_energy_by_mgt_org,
            },
            {
                'name': '按供电单位统计高压客户应收电费总额',
                'patterns': [
                    r'各供电单位.*?(\d{4})年(\d{1,2})月.*?高压客户.*?应收电费',
                    r'(\d{4})年(\d{1,2})月.*?各供电单位.*?高压客户.*?应收电费',
                    r'各供电单位.*?(\d{4})年(\d{1,2})月.*?高压.*?应收电费',
                    r'.*?高压客户.*?(\d{4})年(\d{1,2})月.*?应收电费',
                    r'(\d{4})年(\d{1,2})月.*?高压客户.*?应收电费',
                ],
                'slots': ['year', 'month'],
                'sql_builder': self._rcvbl_hv_cust_by_mgt_org,
            },
            {
                'name': '按具体供电单位统计应收电费总额',
                'patterns': [
                    r'.*?([一-龥]{2,}供电(?:公司|分公司|所|部)).*?(\d{4})年(\d{1,2})月.*?应收电费',
                    r'.*?([一-龥]{2,}供电(?:公司|分公司|所|部)).*?(\d{4})-?(\d{2}).*?应收电费',
                ],
                'slots': ['org_name', 'year', 'month'],
                'sql_builder': self._rcvbl_by_specific_mgt_org,
            },
            {
                'name': '按供电单位统计应收电费总额',
                'patterns': [
                    r'各供电单位.*?(\d{4})年(\d{1,2})月.*?应收电费.*?总额',
                    r'(\d{4})年(\d{1,2})月.*?各供电单位.*?应收电费.*?总额',
                    r'(\d{4})年(\d{1,2})月.*?各供电单位.*?应收电费',
                    r'各供电单位.*?(\d{4})年(\d{1,2})月.*?应收电费',
                    r'各供电单位.*?(\d{4})-?(\d{2}).*?应收电费',
                ],
                'slots': ['year', 'month'],
                'sql_builder': self._rcvbl_by_mgt_org,
            },
            {
                'name': '按供电单位统计高压客户数量',
                'patterns': [
                    r'各供电单位.*高压客户.*数量',
                    r'各供电单位.*高压.*客户数',
                    r'统计各供电单位.*高压客户',
                ],
                'sql_builder': self._hv_cust_count_by_mgt_org,
            },
            {
                'name': '按供电单位统计客户数量',
                'patterns': [
                    r'各供电单位.*客户.*数量',
                    r'各供电单位.*客户数',
                    r'每个供电单位.*多少客户',
                    r'每个供电单位.*客户数量',
                ],
                'sql_builder': self._cust_count_by_mgt_org,
            },
            {
                'name': '按供电单位统计计量点数量',
                'patterns': [
                    r'各供电单位.*计量点.*数量',
                    r'各供电单位.*计量点数',
                    r'统计各供电单位.*计量点',
                ],
                'sql_builder': self._mp_count_by_mgt_org,
            },
        ]
    
    def match(self, question: str) -> Optional[Dict]:
        """匹配模板并返回 SQL 结果。

        通道顺序：签名匹配（P1 骨架模板，via='signature'）→ 正则兜底（via='regex'）。
        签名通道异常/未命中/填充或校验失败都落回正则，行为向前兼容。
        """
        try:
            hit = self._match_by_signature(question)
            if hit:
                return hit
        except Exception as e:
            print(f"[WARN] 模板签名匹配异常，落正则兜底: {e}", flush=True)
        return self._match_by_regex(question)

    def _match_by_regex(self, question: str) -> Optional[Dict]:
        """正则兜底通道（原匹配逻辑；跳过注册表中已禁用的模板；注册表为空=全部启用）"""
        q = question.lower()
        enabled_names = get_enabled_template_names()
        for tmpl in self.templates:
            if enabled_names is not None and tmpl['name'] not in enabled_names:
                continue  # 注册表中已禁用
            for pattern in tmpl['patterns']:
                match = re.search(pattern, q)
                if match:
                    try:
                        sql = tmpl['sql_builder'](*match.groups())
                        return {
                            'sql': sql,
                            'template_name': tmpl['name'],
                            'via': 'regex',
                            'success': True
                        }
                    except Exception:
                        continue
        return None

    # ==================== P1：签名匹配主通道 ====================

    def _match_by_signature(self, question: str) -> Optional[Dict]:
        """签名匹配：2026-08-15 阶段2 起统一走 engine.knowledge_retriever.retrieve——
        direct_match 即命中候选首位（最高分行是 template 且过签名阈值）；填充/校验失败
        按签名打分序依次尝试其余候选。守卫/判分逻辑已抽取至
        knowledge_retriever.score_signature_rows（不在此复制）。
        复杂/分析型问题直接跳过（与 sql_generator 模板兜底调用点的口径一致：
        规则匹配只能抓表面条件，复杂意图交给 LLM 全流程）。"""
        # 分析型问题守卫（词表镜像 analytical_kw 规则 / sql_generator._DEFAULT_ANALYTICAL_KW）
        from modules.resources.providers.business_rule import get_analytical_keywords
        analytical_kw = get_analytical_keywords() or (
            '分析', '趋势', '分布', '关联', '对比', '占比', '异常', '波动', '画像', '核查', '差异', '情况')
        if len(question) > 30 or any(k in question for k in analytical_kw):
            return None

        from core.knowledge_retriever import retrieve
        # top_k 给足（≥全表行数）：签名候选需全量进入填充尝试（原实现遍历全部过守卫模板，
        # 检索层 top_k 截断只影响注入类消费方，不能截掉模板候选）
        res = retrieve(question, top_k=500, concept_map=self._concept_map)
        candidates = [it for it in res['items']
                      if it.get('item_type') == 'template' and it.get('signature_score', 0) > 0]
        if not candidates:
            return None
        candidates.sort(key=lambda it: it.get('_sig_sort') or (0, 0, 0), reverse=True)
        # direct_match 优先（检索层判定的直接命中），其余按签名打分序
        direct = res.get('direct_match')
        if direct is not None and direct in candidates:
            candidates.remove(direct)
            candidates.insert(0, direct)

        # 按打分从高到低逐个尝试：填充失败或执行校验不过则试下一个
        for it in candidates:
            tmpl = {'id': it['id'], 'name': it.get('name') or '',
                    'skeleton': it.get('sql_rule') or '',
                    'table_list': it.get('table_list') or [],
                    'signature': it.get('signature') or {}}
            sql = self._fill_skeleton(tmpl, question)
            if not sql:
                continue
            if not self._probe_ok(sql):
                continue
            self._record_hit(tmpl['id'])
            return {'sql': sql, 'template_name': tmpl['name'], 'via': 'signature', 'success': True}
        return None

    # ==================== 槽位推导填充 ====================
    # 2026-08-19 业务裁定：slot_spec 列已删（业务冗余），槽位按占位符名称约定 +
    # 骨架上下文/列采样在运行时推导。采样结果进程级缓存（表结构/值域稳定，重启失效即可）。

    def _fill_skeleton(self, tmpl: Dict, question: str) -> Optional[str]:
        """槽位填充（运行时推导版）。占位符名称约定（#N 后缀 = 同位重复槽位，填同值）：
        - {{ym}} / {{ymd:字段}} / {{date*}}：问题日期归一渲染；格式推导优先级：
          ① 骨架上下文包裹函数（strftime('%X', col)='{{槽位}}' / DATE_FORMAT(col,'%X')）的格式串
          ② 等值上下文（col='{{槽位}}'）或名内字段的列采样形态（YYYY-MM/YYYYMM 等）
          ③ 约定列：sql_tables 首个含 data_date 或 *_ym 列的表的那列采样
          ④ 默认 dash 形态（与原 slot_spec 缺 format 时的行为一致）
        - {{limit*}}：问题中 前N/TOP N，缺省 10
        - {{dim:字段}} / {{kw:字段}} / {{like:字段}}：该字段 DISTINCT 采样值中命中问题者
        - {{in:字段}}：同上取全部命中，逗号分隔加引号
        槽位名不符合约定或任一槽位填不出 → 返回 None（放弃直出，落骨架注入/正则兜底通道，
        保底行为与删列前一致）。"""
        skeleton = tmpl['skeleton']
        placeholders = list(dict.fromkeys(re.findall(r'\{\{([^}]+)\}\}', skeleton)))
        fills = {}
        for ph in placeholders:
            val = self._derive_slot_value(ph, skeleton, tmpl, question)
            if val is None:
                return None
            fills[ph] = val
        sql = skeleton
        for ph, val in fills.items():
            sql = sql.replace('{{' + ph + '}}', str(val))
        return None if '{{' in sql else sql

    def _derive_slot_value(self, ph: str, skeleton: str, tmpl: Dict, question: str) -> Optional[str]:
        base = re.sub(r'#\d+$', '', ph)          # ym#2 / ymd:data_date#2 → 同位重复槽位
        kind, _, field = base.partition(':')      # 名内字段：ymd:this_read_act_date / kw:voltage_desc
        if kind in ('ym', 'ymd', 'date'):
            comp = self._extract_question_date(question)
            if not comp:
                return None
            y, m, d = comp
            if kind in ('ymd', 'date') and not d:
                return None
            fmt = self._resolve_slot_format(ph, skeleton, tmpl, field or None)
            if kind == 'ym':
                return self._render_date(fmt or '%Y-%m', y, m, None)
            return self._render_date(fmt or '%Y-%m-%d', y, m, d)
        if kind in ('date_from', 'date_to'):
            comp = self._extract_question_date(question)
            if not comp:
                return None
            y, m, _d = comp
            fmt = self._resolve_slot_format(ph, skeleton, tmpl, field or None)
            return self._render_date(fmt or '%Y-%m-%d', y, m, '01' if kind == 'date_from' else '31')
        if kind.startswith('limit'):
            mn = re.search(r'前\s*(\d+)', question) or re.search(r'top\s*(\d+)', question, re.I)
            return mn.group(1) if mn else '10'
        if kind in ('dim', 'kw', 'like'):
            if not field:
                return None
            vals = self._sample_col_values(tmpl, field)
            return next((v for v in vals if v and v in question), None)
        if kind == 'in':
            if not field:
                return None
            hits = [v for v in self._sample_col_values(tmpl, field) if v and v in question]
            return ', '.join(f"'{h}'" for h in hits) if hits else None
        return None  # 未知槽位约定：保守放弃

    @staticmethod
    def _render_date(fmt: str, y: str, m: str, d: Optional[str]) -> str:
        """按 strftime 风格格式串渲染日期；d=None（ym 槽位）时砍掉日段。"""
        out = fmt.replace('%Y', y).replace('%m', m)
        if d is not None:
            out = out.replace('%d', d)
        else:
            out = re.sub(r'[-/ ]?%d', '', out)
        return out

    def _resolve_slot_format(self, ph: str, skeleton: str, tmpl: Dict, field: Optional[str]) -> Optional[str]:
        """日期槽位格式推导（优先级见 _fill_skeleton）。返回 strftime 风格格式串或 None。"""
        esc = re.escape('{{' + ph + '}}')
        m = re.search(r"(?:strftime\s*\(\s*'([^']+)'\s*,\s*[\w.]+\s*\)"
                      r"|DATE_FORMAT\s*\(\s*[\w.]+\s*,\s*'([^']+)'\s*\))\s*=\s*'" + esc + "'",
                      skeleton, re.I)
        if m:
            return m.group(1) or m.group(2)  # ① 骨架包裹函数自带格式串
        if not field:
            m2 = re.search(r"([\w.]+)\s*=\s*'" + esc + "'", skeleton)  # ② 等值上下文取列
            field = m2.group(1) if m2 else None
        if field:
            fmt = self._sample_col_format(tmpl, field)
            if fmt:
                return fmt
        conv = self._resolve_convention_date_col(tmpl.get('table_list') or [])  # ③ 约定列
        if conv:
            fmt = self._sample_col_format_by(conv[0], conv[1])
            if fmt:
                return fmt
        return None  # ④ 调用方用默认 dash 形态

    @staticmethod
    def _strip_field(field: Optional[str]) -> Optional[str]:
        return field.split('.')[-1].strip('`') if field else None

    def _resolve_col_table(self, tables, col):
        """列 → sql_tables 内宿主表（information_schema 核对；异常返回 None）。"""
        col = self._strip_field(col)
        if not col or not tables:
            return None
        key = (tuple(tables), col)
        if key in _COL_TABLE_CACHE:
            return _COL_TABLE_CACHE[key]
        hit = None
        try:
            db = DatabaseManager()
            with db.connect_business() as conn:
                cursor = conn.execute(
                    'SELECT DISTINCT TABLE_NAME FROM information_schema.COLUMNS '
                    'WHERE TABLE_SCHEMA = DATABASE() AND COLUMN_NAME = ?', (col,))
                have = {r[0] for r in cursor.fetchall()}
                hit = next((t for t in tables if t in have), None)
        except Exception:
            hit = None
        _COL_TABLE_CACHE[key] = hit
        return hit

    def _resolve_convention_date_col(self, tables):
        """约定列：sql_tables 首个含 data_date 或 *_ym 列的表，返回 (table, column) 或 None。"""
        for t in (tables or []):
            key = ('_conv_date', t)
            if key not in _COL_TABLE_CACHE:
                col = None
                try:
                    db = DatabaseManager()
                    with db.connect_business() as conn:
                        # 不用 LIKE '%_ym'：pymysql 带参执行会对 SQL 字面 % 做格式化
                        cursor = conn.execute(
                            "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = ? "
                            "AND (COLUMN_NAME = 'data_date' OR SUBSTRING(COLUMN_NAME, -3) = '_ym') "
                            "ORDER BY COLUMN_NAME LIMIT 1", (t,))
                        row = cursor.fetchone()
                        col = row[0] if row else None
                except Exception:
                    col = None
                _COL_TABLE_CACHE[key] = col
            col = _COL_TABLE_CACHE[key]
            if col:
                return t, col
        return None

    def _sample_col_format_by(self, table, col):
        """列采样值形态 → strftime 格式串（采样一次缓存）。识别 YYYY-MM-DD/YYYYMMDD/YYYY-MM/YYYYMM。"""
        key = (table, col)
        if key in _COL_FMT_CACHE:
            return _COL_FMT_CACHE[key]
        fmt = None
        try:
            db = DatabaseManager()
            with db.connect_business() as conn:
                cursor = conn.execute(f'SELECT {col} FROM {table} WHERE {col} IS NOT NULL LIMIT 3')
                for r in cursor.fetchall():
                    s = str(r[0])
                    if re.match(r'^\d{4}-\d{2}-\d{2}', s):
                        fmt = '%Y-%m-%d'
                    elif re.match(r'^\d{8}$', s):
                        fmt = '%Y%m%d'
                    elif re.match(r'^\d{4}-\d{2}$', s):
                        fmt = '%Y-%m'
                    elif re.match(r'^\d{6}$', s):
                        fmt = '%Y%m'
                    if fmt:
                        break
        except Exception:
            fmt = None
        _COL_FMT_CACHE[key] = fmt
        return fmt

    def _sample_col_format(self, tmpl, field):
        col = self._strip_field(field)
        table = self._resolve_col_table(tmpl.get('table_list') or [], col)
        return self._sample_col_format_by(table, col) if table and col else None

    def _sample_col_values(self, tmpl, field):
        """列 DISTINCT 采样值（上限 500，进程级缓存）；供 dim/kw/like/in 槽位取问题命中值。"""
        col = self._strip_field(field)
        table = self._resolve_col_table(tmpl.get('table_list') or [], col)
        if not table or not col:
            return []
        key = (table, col)
        if key in _COL_VALS_CACHE:
            return _COL_VALS_CACHE[key]
        vals = []
        try:
            db = DatabaseManager()
            with db.connect_business() as conn:
                cursor = conn.execute(
                    f'SELECT DISTINCT {col} FROM {table} WHERE {col} IS NOT NULL LIMIT 500')
                vals = [str(r[0]) for r in cursor.fetchall() if r[0] is not None]
        except Exception:
            vals = []
        _COL_VALS_CACHE[key] = vals
        return vals

    @staticmethod
    def _extract_question_date(question: str):
        """从问题提取日期：(y, m, d|None)。支持 YYYY年M月(D日) / YYYY-MM(-DD) / YYYYMM(DD)。"""
        m = re.search(r'(\d{4})年(\d{1,2})月(?:(\d{1,2})日)?', question)
        if not m:
            m = re.search(r'(\d{4})-(\d{1,2})(?:-(\d{1,2}))?', question)
        if not m:
            m = re.search(r'\b(\d{4})(\d{2})(\d{2})\b', question)
        if not m:
            m = re.search(r'\b(\d{4})(\d{2})\b', question)
        if not m:
            return None
        return m.group(1), f'{int(m.group(2)):02d}', (f'{int(m.group(3)):02d}' if m.group(3) else None)

    @staticmethod
    def _normalize_for_dialect(sql: str) -> str:
        """骨架以 strftime 风格存储（金标快照侧挖掘）；执行前统一转为 MySQL DATE_FORMAT。
        （与 sql_generator._normalize_sql_dialect 同口径，此处独立一份避免反向依赖）"""
        def _replace(match):
            return f"DATE_FORMAT({match.group(2).strip()}, '{match.group(1)}')"
        return re.sub(r"strftime\s*\(\s*['\"]([^'\"]+)['\"]\s*,\s*([^)]+)\s*\)",
                      _replace, sql, flags=re.IGNORECASE)

    def _probe_ok(self, sql: str) -> bool:
        """执行校验（LIMIT 1 探测，相当于 _validate_sql 的轻量版）"""
        if not sql or not re.match(r'(?i)^\s*(SELECT|WITH)\b', sql.strip()):
            return False
        try:
            db = DatabaseManager()
            test_sql = re.sub(r'\s+LIMIT\s+\d+\s*$', '', sql.rstrip(';').strip(), flags=re.IGNORECASE)
            test_sql = self._normalize_for_dialect(test_sql) + ' LIMIT 1'
            with db.connect_business() as conn:
                conn.execute(test_sql)
            return True
        except Exception:
            return False

    @staticmethod
    def _record_hit(template_id):
        """命中计数：2026-08-19 瘦身后 hit_count/last_hit_at 列已删除，此方法改为 no-op
        （保留调用点与签名，便于后续如需命中统计时另建日志表恢复）。"""
        return
    
    def _rcvbl_by_mgt_org(self, year: str, month: str) -> str:
        ym = f"{year}{int(month):02d}"
        return f"""SELECT m.mgt_org_code, m.mgt_org_name,
       SUM(r.rcvbl_amt) AS total_rcvbl,
       SUM(r.rcvd_amt) AS total_rcvd,
       SUM(r.arer_bal) AS total_arer
FROM dwd_cst_rcvbl_acct r
LEFT JOIN dim_cst_mgt_org m ON r.mgt_org_code = m.mgt_org_code
WHERE r.rcvbl_ym = '{ym}'
GROUP BY m.mgt_org_code, m.mgt_org_name"""
    
    def _hv_cust_count_by_mgt_org(self) -> str:
        return """SELECT m.mgt_org_code, m.mgt_org_name,
       COUNT(DISTINCT c.cust_id) AS cust_count
FROM dim_cst_elec_cons_cust c
LEFT JOIN dim_cst_mgt_org m ON c.mgt_org_code = m.mgt_org_code
WHERE c.cust_cls_desc = '高压'
GROUP BY m.mgt_org_code, m.mgt_org_name
ORDER BY cust_count DESC"""
    
    def _cust_count_by_mgt_org(self) -> str:
        return """SELECT m.mgt_org_code, m.mgt_org_name,
       COUNT(DISTINCT c.cust_id) AS cust_count
FROM dim_cst_cust c
LEFT JOIN dim_cst_mgt_org m ON c.mgt_org_code = m.mgt_org_code
GROUP BY m.mgt_org_code, m.mgt_org_name
ORDER BY cust_count DESC"""
    
    def _mp_count_by_mgt_org(self) -> str:
        return """SELECT m.mgt_org_code, m.mgt_org_name,
       COUNT(DISTINCT inst.inst_id) AS inst_count
FROM dim_cst_inst_elec_cons inst
LEFT JOIN dim_cst_mgt_org m ON inst.mgt_org_code = m.mgt_org_code
GROUP BY m.mgt_org_code, m.mgt_org_name
ORDER BY inst_count DESC"""
    
    def _rcvbl_by_specific_mgt_org(self, org_name: str, year: str, month: str) -> str:
        # 去掉可能误捕获的前导动词
        for prefix in ['统计', '查询', '列出', '获取']:
            if org_name.startswith(prefix):
                org_name = org_name[len(prefix):]
        ym = f"{year}{int(month):02d}"
        return f"""SELECT m.mgt_org_code, m.mgt_org_name,
       SUM(r.rcvbl_amt) AS total_rcvbl,
       SUM(r.rcvd_amt) AS total_rcvd,
       SUM(r.arer_bal) AS total_arer
FROM dwd_cst_rcvbl_acct r
LEFT JOIN dim_cst_mgt_org m ON r.mgt_org_code = m.mgt_org_code
WHERE r.rcvbl_ym = '{ym}'
  AND m.mgt_org_name LIKE '%{org_name}%'
GROUP BY m.mgt_org_code, m.mgt_org_name"""
    
    def _rcvbl_hv_cust_by_mgt_org(self, year: str, month: str) -> str:
        ym = f"{year}{int(month):02d}"
        return f"""SELECT m.mgt_org_code, m.mgt_org_name,
       SUM(r.rcvbl_amt) AS total_rcvbl
FROM dwd_cst_rcvbl_acct r
LEFT JOIN dim_cst_mgt_org m ON r.mgt_org_code = m.mgt_org_code
LEFT JOIN dim_cst_inst_elec_cons inst ON r.inst_id = inst.inst_id
LEFT JOIN dim_cst_elec_cons_cust c ON inst.cust_id = c.cust_id
WHERE r.rcvbl_ym = '{ym}'
  AND c.cust_cls_desc = '高压'
GROUP BY m.mgt_org_code, m.mgt_org_name"""
    
    def _hv_cust_energy_by_mgt_org(self, year: str, month: str) -> str:
        ym = f"{year}-{int(month):02d}"
        return f"""SELECT m.mgt_org_code, m.mgt_org_name,
       SUM(e.pap_e) AS total_pap_e
FROM dim_cst_elec_cons_cust c
JOIN dim_cst_inst_elec_cons inst ON c.cust_id = inst.cust_id
JOIN dwd_cst_meter_run mrun ON inst.inst_id = mrun.inst_id
JOIN dwd_cst_meter_energy_day_h_xz e ON mrun.meter_asset_no = e.meter_asset_no
LEFT JOIN dim_cst_mgt_org m ON c.mgt_org_code = m.mgt_org_code
WHERE c.cust_cls_desc = '高压'
  AND strftime('%Y-%m', e.data_date) = '{ym}'
GROUP BY m.mgt_org_code, m.mgt_org_name"""
    
    def _topn_energy_by_meter(self, year: str, month: str, n: str) -> str:
        ym = f"{year}-{int(month):02d}"
        return f"""SELECT e.meter_asset_no,
       inst.inst_name,
       SUM(e.pap_e) AS total_pap_e,
       AVG(e.pap_e) AS avg_pap_e
FROM dwd_cst_meter_energy_day_h_xz e
LEFT JOIN dwd_cst_meter_run mrun ON e.meter_asset_no = mrun.meter_asset_no
LEFT JOIN dim_cst_inst_elec_cons inst ON mrun.inst_id = inst.inst_id
WHERE strftime('%Y-%m', e.data_date) = '{ym}'
GROUP BY e.meter_asset_no
ORDER BY total_pap_e DESC
LIMIT {n}"""
    
    def _topn_energy_by_cust(self, year: str, month: str, n: str) -> str:
        ym = f"{year}-{int(month):02d}"
        return f"""SELECT c.cust_no, c.cust_name,
       SUM(e.pap_e) AS total_pap_e
FROM dim_cst_elec_cons_cust c
JOIN dim_cst_inst_elec_cons inst ON c.cust_id = inst.cust_id
JOIN dwd_cst_meter_run mrun ON inst.inst_id = mrun.inst_id
JOIN dwd_cst_meter_energy_day_h_xz e ON mrun.meter_asset_no = e.meter_asset_no
WHERE strftime('%Y-%m', e.data_date) = '{ym}'
GROUP BY c.cust_no, c.cust_name
ORDER BY total_pap_e DESC
LIMIT {n}"""
    
    def _topn_hv_cust_energy(self, year: str, month: str, n: str) -> str:
        ym = f"{year}-{int(month):02d}"
        return f"""SELECT c.cust_no, c.cust_name,
       SUM(e.pap_e) AS total_pap_e
FROM dim_cst_elec_cons_cust c
JOIN dim_cst_inst_elec_cons inst ON c.cust_id = inst.cust_id
JOIN dwd_cst_meter_run mrun ON inst.inst_id = mrun.inst_id
JOIN dwd_cst_meter_energy_day_h_xz e ON mrun.meter_asset_no = e.meter_asset_no
WHERE c.cust_cls_desc = '高压'
  AND strftime('%Y-%m', e.data_date) = '{ym}'
GROUP BY c.cust_no, c.cust_name
ORDER BY total_pap_e DESC
LIMIT {n}"""


if __name__ == '__main__':
    matcher = SQLTemplateMatcher()
    samples = [
        '统计各供电单位2026年4月的应收电费总额',
        '统计各供电单位下的高压客户数量',
        '每个供电单位有多少客户？',
        '查询2026年5月用电量TOP10的计量点',
        '查询2026年4月用电量TOP5的客户',
    ]
    for q in samples:
        print('\nQ:', q)
        r = matcher.match(q)
        if r:
            print(r['template_name'])
            print(r['sql'])
        else:
            print('未匹配')
