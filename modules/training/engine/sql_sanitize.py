# -*- coding: utf-8 -*-
"""SQL 清洗与校验协作者：LLM 输出的提取/方言规范化/码值翻译/别名纪律 + LIMIT 1 试执行探测
（自 engine/sql_generator.py 下沉；SQLGenerator 经同名薄委托转发，调用点零改动）"""
import re
from typing import Dict, List, Optional


class SqlSanitizer:
    """db：DatabaseManager（业务库/治理库连接）；rag：RAGRetriever（码值索引）；
    code_value_translations_fn：名称→编码 翻译表来源（知识源 ontology/legacy 解析留在 SQLGenerator 侧）"""

    def __init__(self, db, rag=None, code_value_translations_fn=None):
        self._db = db
        self._rag = rag
        self._cv_trans_fn = code_value_translations_fn

    @staticmethod
    def extract_sql(content: str) -> str:
        if not content:
            return ''
        # Strip thinking tags if present
        content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL)
        content = re.sub(r'```sql\n?', '', content, flags=re.IGNORECASE)
        content = re.sub(r'```\n?', '', content)
        lines = content.split('\n')
        sql_lines = []
        in_sql = False
        allowed_prefixes = ['SELECT', 'WITH']
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith('--') or stripped.startswith('//'):
                continue
            if any(stripped.upper().startswith(p) for p in allowed_prefixes):
                in_sql = True
            if in_sql:
                sql_lines.append(stripped)
        if sql_lines:
            sql = ' '.join(sql_lines)
            sql = re.sub(r'\s+', ' ', sql)
            return sql.strip()
        return ''

    @staticmethod
    def extract_explanation(content: str) -> str:
        if not content:
            return ''
        lines = content.split('\n')
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('--'):
                return stripped.lstrip('--').strip()
        return ''

    @staticmethod
    def extract_tables_from_sql(sql: str) -> List[str]:
        tables = set()
        for match in re.finditer(r'FROM\s+(\w+)', sql, re.IGNORECASE):
            tables.add(match.group(1))
        for match in re.finditer(r'JOIN\s+(\w+)', sql, re.IGNORECASE):
            tables.add(match.group(1))
        # 排除 CTE 别名（WITH xxx AS (...) / , xxx AS (...)）——它们不是真实表
        cte_names = set()
        for match in re.finditer(r'(?:\bWITH|,)\s*(\w+)\s+AS\s*\(', sql, re.IGNORECASE):
            cte_names.add(match.group(1))
        return sorted(tables - cte_names)

    @staticmethod
    def extract_condition_fields(sql: str) -> str:
        """提取SQL中具体的条件值字段，用于标注'需要替换'"""
        patterns = []
        
        # 提取等值条件中的具体值
        eq_matches = re.findall(r"(\w+\.?\w*)\s*=\s*['\"]([^'\"]+)['\"]", sql, re.IGNORECASE)
        for field, value in eq_matches:
            short_field = field.split('.')[-1] if '.' in field else field
            patterns.append(f"{short_field}='{value}'")
        
        # 提取日期条件
        date_matches = re.findall(r"DATE_FORMAT\([^)]+\)\s*=\s*['\"]([^'\"]+)['\"]", sql, re.IGNORECASE)
        for value in date_matches:
            patterns.append(f"日期='{value}'")
        
        # 提取 LIKE 条件
        like_matches = re.findall(r"(\w+\.?\w*)\s+LIKE\s+['\"]([^'\"]+)['\"]", sql, re.IGNORECASE)
        for field, value in like_matches:
            short_field = field.split('.')[-1] if '.' in field else field
            patterns.append(f"{short_field} LIKE '{value}'")
        
        return '，'.join(patterns[:5]) if patterns else ''

    @staticmethod
    def clean_aliases(sql: str) -> str:
        """别名纪律（确定性后处理）：核心词组、≤8 个汉字、禁标点。
        LLM 常把列注释整段搬进别名（'AS 地区名称（全国/省/城市）'、'AS 行政区划代码（GB/T 2260）'，
        全角括号/空格/斜杠在 MySQL 裸标识符中非法或截断残留 → 1064）。
        两步：① AS 核心词（全角注释段） → 只留核心词；② 含中文且 >8 字的别名截到 8 字。"""
        if not sql:
            return sql
        sql = re.sub(r'(?i)\bAS\s+([^\s,（）()]+)\s*（[^）]*）', r'AS \1', sql)
        sql = re.sub(r'(?i)\bAS\s+([^\s,（）()]+)',
                     lambda m: 'AS ' + m.group(1)[:8]
                     if (len(m.group(1)) > 8 and re.search(r'[一-鿿]', m.group(1))) else m.group(0),
                     sql)
        return sql

    @staticmethod
    def normalize_sql_dialect(sql: str) -> str:
        """规范化日期函数为 MySQL 方言（LLM/模板侧的 strftime 一律转 DATE_FORMAT）"""
        if not sql:
            return sql

        def replace_strftime(match):
            fmt = match.group(1)
            col = match.group(2).strip()
            return f"DATE_FORMAT({col}, '{fmt}')"
        sql = re.sub(r"strftime\s*\(\s*['\"]([^'\"]+)['\"]\s*,\s*([^)]+)\s*\)", replace_strftime, sql, flags=re.IGNORECASE)

        return sql

    @staticmethod
    def is_sentinel_sql(sql: str) -> bool:
        """检测“哨兵 SQL”：模型自认无法回答时编的占位语句（非代码约定，模型自发行为）。
        特征：含 cannot_answer/missing_required_tables 字样，或 SELECT 列表只有一个字符串字面量。"""
        if not sql:
            return False
        low = sql.lower()
        if 'cannot_answer' in low or 'missing_required_tables' in low:
            return True
        body = re.sub(r'/\*.*?\*/', '', sql)  # 去块注释
        m = re.match(r"""(?is)^\s*select\s+('[^']*'|"[^"]*")(\s+(?:as\s+)?\w+)?\s*(from\b|$)""", body)
        return bool(m)

    def clean_sql_pipeline(self, raw_content: str) -> str:
        """LLM 输出清洗管道：提取 SQL → 方言规范化 → 码值字面量翻译（生成/修复/审计三通道共用）"""
        return self.translate_code_value_literals(self.normalize_sql_dialect(self.extract_sql(raw_content)))

    def translate_code_value_literals(self, sql: str) -> str:
        """把存编码列上的中文描述条件值机械翻译成编码（LLM 不守形态规则时的确定性兜底）。
        只替换该列条件表达式中的引号字面量，避免误伤其他位置。开头先过别名纪律清理。
        附带：名称列的唯一前缀补全（LLM 写 report_name='全社会用电量'，
        实际值为'全社会用电量（亿千瓦时）'——唯一前缀时确定性补全，否则不动）。"""
        if not sql:
            return sql
        sql = self.clean_aliases(sql)  # 别名纪律（核心词组、≤8 字、禁标点）
        sql = self.complete_name_literals(sql)
        try:
            mappings = self._cv_trans_fn()
        except Exception as e:
            print(f"[WARN] 码值翻译表获取失败: {e}", flush=True)
            return sql
        for table, column, name_to_code in mappings:
            # 1) 等值条件：col = '名称' → col = '编码'
            for name, code in name_to_code.items():
                if not name or not code or name == code:
                    continue
                pattern = re.compile(
                    r"((?:\w+\.)?" + re.escape(column) + r"\s*=\s*)'" + re.escape(name) + r"'",
                    re.IGNORECASE)
                sql = pattern.sub(lambda m: m.group(1) + f"'{code}'", sql)

            # 2) IN 列表：col IN ('a','b') → 括号内逐项翻译
            def _fix_in(m, _n2c=name_to_code):
                body = m.group(2)
                for name, code in _n2c.items():
                    if not name or not code or name == code:
                        continue
                    body = body.replace(f"'{name}'", f"'{code}'")
                return m.group(1) + body + m.group(3)
            sql = re.sub(
                r"((?:\w+\.)?" + re.escape(column) + r"\s+IN\s*\()([^)]*)(\))",
                _fix_in, sql, flags=re.IGNORECASE)
        return sql

    def complete_name_literals(self, sql: str) -> str:
        """名称形态码值列的唯一前缀补全（确定性）：
        col = 'X' 且 X 不是值域成员、但值域中恰有唯一成员以 X 开头 → 补全为该成员。
        依据 code_value_column_form 的 (table, column, code_name, form='名称') 登记。"""
        try:
            self._rag._load_code_value_index()
            items_map = self._rag._cv_items
            with self._db.connect_governance() as conn:
                rows = conn.execute("SELECT table_name, column_name, code_name "
                                    "FROM code_value_column_form WHERE form='名称'").fetchall()
        except Exception:
            return sql
        for table, column, code_name in rows:
            if table not in sql:
                continue
            names = [name for _c, name in items_map.get(code_name, []) if name]
            if not names:
                continue
            for m in re.finditer(
                    r"((?:\w+\.)?" + re.escape(column) + r"\s*=\s*)'([^']+)'", sql, re.IGNORECASE):
                val = m.group(2)
                if val in names:
                    continue
                cands = [n for n in names if n.startswith(val)]
                if len(cands) == 1:
                    sql = sql.replace(m.group(0), m.group(1) + f"'{cands[0]}'")
        return sql

    def exec_probe(self, sql: str) -> str:
        """试执行 SQL（剥 LIMIT 后加 LIMIT 1），返回数据库报错文本；可执行返回空串"""
        test_sql = re.sub(r'\s+LIMIT\s+\d+\s*$', '', sql.rstrip(';').strip(), flags=re.IGNORECASE) + ' LIMIT 1'
        try:
            with self._db.connect_business() as conn:
                conn.execute(test_sql)
            return ''
        except Exception as e:
            return str(e)

    def probe_sql_error(self, sql: str) -> str:
        """试执行 SQL（LIMIT 1），返回数据库报错文本；可执行则返回空串"""
        if not sql:
            return 'SQL 为空'
        return self.exec_probe(sql)

    # ---------- 两段式工作流：none 草稿 → 执行 → low 档审计（2026-08-15）----------

    def probe_sql_result(self, sql: str, limit: int = 5):
        """试执行 SQL 并取结果证据：返回 (row_count, sample_rows, headers, error)。"""
        try:
            inner = sql.strip().rstrip(';')
            with self._db.connect_business() as conn:
                cur = conn.execute(f'SELECT * FROM ({inner}) AS _t LIMIT {int(limit)}')
                rows = cur.fetchall()
                headers = [d[0] for d in cur.description] if cur.description else []
                cur2 = conn.execute(f'SELECT COUNT(*) FROM ({inner}) AS _t')
                total = cur2.fetchone()[0]
            return total, [list(r) for r in rows], headers, None
        except Exception as e:
            return None, None, None, str(e)

    def validate_sql(self, sql: str) -> bool:
        """尝试执行 SQL（LIMIT 1）验证语法和字段正确性"""
        if not sql:
            return False
        sql_upper = sql.strip().upper()
        if not any(sql_upper.startswith(p) for p in ['SELECT', 'WITH']):
            return False
        
        # 安全检查
        forbidden = ['DROP', 'DELETE', 'UPDATE', 'INSERT', 'ALTER', 'CREATE', 'TRUNCATE']
        for kw in forbidden:
            if kw in sql_upper:
                return False
        
        return not self.exec_probe(sql)
