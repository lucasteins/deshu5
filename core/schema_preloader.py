# -*- coding: utf-8 -*-
"""Schema 预加载器：元数据加载（以业务库 information_schema 为权威），注入全局上下文。

职责：
1. 元数据权威来源 = 业务库 marketing_40 的 information_schema：
   表/列中文注释随物理表 COMMENT 落库，表名/注释/行数/字段/类型/主键直接读取；
   主外键关系 = 物理外键图（information_schema.key_column_usage，合法 JOIN 边的权威）
   ∪ 治理库（marketing_governance）schema_relationship_docs 的业务语义/逻辑关系，
   双源合并、冲突以物理外键为准（见 _merge_relationships）。
2. DDL 文件（35张营销共享层表_重构版v2.0_20260519.sql 等）仅用于初次导入或
   preload(force=True) 显式刷新：解析文件 → 重写 governance 库文档（文档落库链路不变）。
3. 在内存中维护全局 Schema 上下文，供 SQLGenerator / QuestionGenerator / SQLReviewer 快速获取。
4. 字段级详细信息仍由调用方在定位到具体表后，从业务库或 governance 库按需精确加载。
"""
import json
import os
import re
from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple

import config
from core.database import DatabaseManager


# 业务库中可能存在、但权威 DDL 未覆盖的字段，兜底中文注释
_COLUMN_COMMENT_FALLBACK: Dict[str, str] = {
    'area': '面积',
    'bus_city_code_xz': '业务市码（县支）',
    'bus_city_name_xz': '业务市名称（县支）',
    'bus_cmny_code_xz': '业务小区码（县支）',
    'bus_cmny_name_xz': '业务小区名称（县支）',
    'bus_county_code_xz': '业务区县码（县支）',
    'bus_county_name_xz': '业务区县名称（县支）',
    'bus_neighbor_comm_code_xz': '业务社区码（县支）',
    'bus_neighbor_comm_name_xz': '业务社区名称（县支）',
    'bus_prov_code_xz': '业务省码（县支）',
    'bus_prov_name_xz': '业务省名称（县支）',
    'bus_rd_code_xz': '业务道路码（县支）',
    'bus_rd_name_xz': '业务道路名称（县支）',
    'bus_st_code_xz': '业务街道码（县支）',
    'bus_st_name_xz': '业务街道名称（县支）',
    'city_code': '市码',
    'city_name': '市名称',
    'cost_ctrl_flag': '费控标志',
    'county_code': '区县码',
    'county_name': '区县名称',
    'cust_cls': '客户分类',
    'cust_ind_cls': '行业分类',
    'cust_ind_ustry_cls': '工业行业分类',
    'cust_volt_code': '电压分类代码',
    'dereg_attr_cls': '市场化属性分类',
    'dist_lv': '配网层级',
    'dist_lv_desc': '配网层级描述',
    'ec_categ': '用电类别',
    'ecc_stat': '用电户状态',
    'graded_settle_flag': '分次结算标志',
    'high_ec_ind_cls': '高耗能行业分类',
    'impt_lv': '重要性等级',
    'is_one_credit_code': '是否统一社会信用代码一致',
    'is_one_id_card': '是否身份证一致',
    'is_one_vat_no': '是否增值税号一致',
    'is_one_vat_tax_no': '是否增值税税号一致',
    'load_char': '负荷性质',
    'load_charts': '负荷特性',
    'lock_stat': '锁定状态',
    'main_hshd_flag': '主户标志',
    'mgt_org_char': '管理单位性质',
    'mgt_org_char_desc': '管理单位性质描述',
    'mgt_org_name': '管理单位名称',
    'orgn_cust_no': '原客户编号',
    'prod_shift': '生产班次',
    'province_code': '省码',
    'province_name': '省名称',
    'rcvr_supl_mode': '复供方式',
    'stop_supl_flag': '停供标志',
    'stop_supl_mode': '停供方式',
    'tmp_ec_flag': '临时用能标志',
    'transfer_flag': '转供户标志',
    'urbanrural_flag': '城乡类别',
}


def _fallback_column_comment(column_name: str) -> str:
    """为 DDL 未覆盖字段生成兜底中文注释。"""
    return _COLUMN_COMMENT_FALLBACK.get(column_name, '')


# 关系 JOIN 条件串解析：'t.col = rt.rcol'（治理关系文档与物理外键归一化共用此格式，
# 与 DDLSchemaParser._build_relationships 的生成式 f'{from_table}.{fc} = {ref_table}.{tc}' 一致）
_JC_RE = re.compile(r'^\s*(\w+)\.(\w+)\s*=\s*(\w+)\.(\w+)\s*$')


def _parse_join_condition(jc: str) -> Optional[Tuple[str, str, str, str]]:
    """把 't.col = rt.rcol' 解析为 (from_table, from_col, to_table, to_col)；解析失败返回 None。"""
    m = _JC_RE.match(jc or '')
    return m.groups() if m else None


class DDLSchemaParser:
    """解析 DDL 文件，抽取表注释、字段注释、主键、外键。"""

    _CREATE_SPLIT_RE = re.compile(
        r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?',
        re.IGNORECASE,
    )
    _TABLE_NAME_RE = re.compile(r'^`?(\w+)`?\s*\(', re.IGNORECASE)
    _TABLE_COMMENT_RE = re.compile(
        r'\)\s*COMMENT\s+\'([^\']+)\'',
        re.IGNORECASE,
    )
    _PK_RE = re.compile(r'primary\s+key\s*\(([^)]+)\)', re.IGNORECASE)
    _INLINE_FK_RE = re.compile(
        r'CONSTRAINT\s+`?\w+`?\s+FOREIGN\s+KEY\s*\(([^)]+)\)\s*'
        r'REFERENCES\s+`?(\w+)`?\s*\(([^)]+)\)',
        re.IGNORECASE,
    )
    _ALTER_FK_RE = re.compile(
        r'ALTER\s+TABLE\s+`?(\w+)`?\s+ADD\s+CONSTRAINT\s+`?\w+`?\s+'
        r'FOREIGN\s+KEY\s*\(([^)]+)\)\s*REFERENCES\s+`?(\w+)`?\s*\(([^)]+)\)',
        re.IGNORECASE,
    )
    _FIELD_RE = re.compile(
        r'^[`\s]*([a-zA-Z_][a-zA-Z0-9_]*)[`\s]*\s+'
        r'([\w()]+(?:\([^)]*\))?)',
        re.IGNORECASE,
    )
    _FIELD_COMMENT_RE = re.compile(r'COMMENT\s+\'([^\']*)\'', re.IGNORECASE)

    def __init__(self, ddl_path: str):
        self.ddl_path = ddl_path
        self.tables: Dict[str, Dict] = {}
        self.relationships: List[Dict] = []
        self._parse()

    def _parse(self):
        if not os.path.exists(self.ddl_path):
            raise FileNotFoundError(f'DDL 文件不存在: {self.ddl_path}')

        with open(self.ddl_path, 'r', encoding='utf-8') as f:
            content = f.read()

        blocks = self._CREATE_SPLIT_RE.split(content)
        all_fks: List[Tuple[str, str, str, str]] = []

        for block in blocks[1:]:
            table_info = self._parse_table_block(block)
            if table_info is None:
                continue
            table_name = table_info['name']
            self.tables[table_name] = table_info
            for fk in table_info.pop('foreign_keys', []):
                all_fks.append((table_name, fk['from_col'], fk['ref_table'], fk['to_col']))

        # ALTER TABLE 外键（通常集中在文件末尾）
        for match in self._ALTER_FK_RE.finditer(content):
            from_table = match.group(1)
            from_cols = [c.strip().strip('`') for c in match.group(2).split(',')]
            ref_table = match.group(3)
            ref_cols = [c.strip().strip('`') for c in match.group(4).split(',')]
            for fc, rc in zip(from_cols, ref_cols):
                all_fks.append((from_table, fc, ref_table, rc))

        self._build_relationships(all_fks)

    def _parse_table_block(self, block: str) -> Optional[Dict]:
        lines = block.split('\n')
        # 表名可能在 CREATE TABLE 的同一行，也可能在下一行；用 \s* 跨行匹配
        m = self._TABLE_NAME_RE.match(block)
        if not m:
            return None

        table_name = m.group(1)
        first = lines[0].strip()

        # 找到 CREATE TABLE 定义体的结束位置（括号深度归零）
        depth = first.count('(') - first.count(')')
        end_idx = 0
        for i, line in enumerate(lines[1:], start=1):
            depth += line.count('(') - line.count(')')
            if depth == 0:
                end_idx = i
                break

        body_lines = lines[: end_idx + 1]
        body = '\n'.join(body_lines)

        # 表注释：只匹配定义体结束行（行首 ')' 后的 COMMENT），
        # 避免 varchar(NN) 字段的 COMMENT 被误当表注释（如 mgt_org_code '管理单位编码'）
        table_comment = ''
        m_close = re.search(r'^\s*\)\s*COMMENT\s+\'([^\']+)\'', body, re.MULTILINE)
        if m_close:
            table_comment = m_close.group(1).strip()

        # 主键
        pk_match = self._PK_RE.search(body)
        pk_cols = []
        if pk_match:
            pk_cols = [c.strip().strip('`') for c in pk_match.group(1).split(',')]

        columns = []
        fks = []
        pk_set = set(pk_cols)

        for line in body_lines[1:]:  # 跳过第一行的 "table_name ("
            l = line.strip()
            if not l or l.startswith('--') or l.startswith(')'):
                continue
            # 约束行
            if re.match(r'^(PRIMARY\s+KEY|FOREIGN\s+KEY|CONSTRAINT|UNIQUE|INDEX)', l, re.IGNORECASE):
                # 捕获行内 FOREIGN KEY
                for fm in self._INLINE_FK_RE.finditer(l):
                    from_cols = [c.strip().strip('`') for c in fm.group(1).split(',')]
                    ref_table = fm.group(2)
                    ref_cols = [c.strip().strip('`') for c in fm.group(3).split(',')]
                    for fc, rc in zip(from_cols, ref_cols):
                        fks.append({'from_col': fc, 'ref_table': ref_table, 'to_col': rc})
                continue

            cm = self._FIELD_RE.match(l)
            if not cm:
                continue

            col_name = cm.group(1)
            col_type = cm.group(2).upper()
            cmt = self._FIELD_COMMENT_RE.search(l)
            col_comment = cmt.group(1).strip() if cmt else ''
            is_pk = col_name in pk_set

            columns.append({
                'name': col_name,
                'type': col_type,
                'comment': col_comment,
                'pk': is_pk,
            })

        return {
            'name': table_name,
            'comment': table_comment,
            'columns': columns,
            'pk': pk_cols,
            'foreign_keys': fks,
        }

    def _build_relationships(self, fks: List[Tuple[str, str, str, str]]):
        """把外键列表整理成关系文档，并自动去重。"""
        grouped: Dict[Tuple[str, str], Set[Tuple[str, str]]] = defaultdict(set)
        for from_table, from_col, ref_table, to_col in fks:
            grouped[(from_table, ref_table)].add((from_col, to_col))

        for (from_table, ref_table), col_pairs in grouped.items():
            join_conditions = [
                f'{from_table}.{fc} = {ref_table}.{tc}' for fc, tc in col_pairs
            ]
            self.relationships.append({
                'path': [from_table, ref_table],
                'join_conditions': join_conditions,
                'business_scenarios': [f'{from_table} 与 {ref_table} 关联查询'],
            })


class _MergedSchema:
    """合并两个解析结果：表/关系来自精简版 DDL，字段来自完整版 DDL。"""

    def __init__(self, tables: Dict[str, Dict], relationships: List[Dict]):
        self.tables = tables
        self.relationships = relationships


class SchemaPreloader:
    """Schema 预加载器：解析 + 持久化 + 全局上下文。"""

    _instance: Optional['SchemaPreloader'] = None

    def __init__(
        self,
        column_source_path: Optional[str] = None,
        table_ddl_path: Optional[str] = None,
    ):
        self.column_source_path = column_source_path or config.DDL_SCHEMA_FILE
        self.table_ddl_path = table_ddl_path or config.DDL_TABLE_REL_FILE
        self.db = DatabaseManager()
        self.parser: Optional[_MergedSchema] = None
        self._global_context: str = ''
        self._ensure_tables()

    @classmethod
    def get_instance(
        cls,
        column_source_path: Optional[str] = None,
        table_ddl_path: Optional[str] = None,
    ) -> 'SchemaPreloader':
        if cls._instance is None:
            cls._instance = cls(column_source_path, table_ddl_path)
        return cls._instance

    def _ensure_tables(self):
        """确保 governance 中 schema 文档表存在（MySQL 方言）。"""
        with self.db.connect_governance() as conn:
            # 2026-08-19 精简：doc_json（table/relationship 文档）、top_values（column 文档）已删除
            conn.execute('''
                CREATE TABLE IF NOT EXISTS schema_table_docs (
                    id INT PRIMARY KEY AUTO_INCREMENT,
                    table_name VARCHAR(128) UNIQUE,
                    table_comment VARCHAR(255),
                    row_count BIGINT,
                    column_count INT,
                    doc_text TEXT,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS schema_column_docs (
                    id INT PRIMARY KEY AUTO_INCREMENT,
                    table_name VARCHAR(128),
                    column_name VARCHAR(128),
                    column_comment VARCHAR(255),
                    data_type VARCHAR(64),
                    is_pk TINYINT(1),
                    doc_text TEXT,
                    UNIQUE(table_name, column_name)
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS schema_relationship_docs (
                    id INT PRIMARY KEY AUTO_INCREMENT,
                    title VARCHAR(255),
                    path TEXT,
                    join_conditions TEXT,
                    business_scenarios TEXT,
                    doc_text TEXT
                )
            ''')
            conn.commit()

    def preload(self, force: bool = False) -> Dict:
        """执行预加载，返回统计摘要。

        加载策略（2026-08-12 起）：表/列元数据以业务库 information_schema
        为权威来源（注释随物理表 COMMENT 落库），主外键关系 = 物理外键图 ∪ 治理库
        schema_relationship_docs（冲突以物理外键为准）。
        force=True 或库中无元数据时才回退「DDL 文件解析 → 落库」的初始导入/刷新路径。
        """
        if self.parser is not None and not force:
            return self._summary()

        if not force and self._load_from_db():
            self._global_context = self._build_global_context()
            print('[SchemaPreloader] 表/列元数据已从业务库 information_schema 加载，'
                  '关系=物理外键图∪治理关系文档（DB-first，免文件依赖）', flush=True)
            return self._summary()

        column_parser = DDLSchemaParser(self.column_source_path)
        table_parser = DDLSchemaParser(self.table_ddl_path)
        self.parser = self._merge_parsers(column_parser, table_parser)
        self._persist()
        self._global_context = self._build_global_context()
        return self._summary()

    def _load_from_db(self) -> bool:
        """重建内存 Schema（DB 优先路径）。库中无元数据时返回 False。

        表/列元数据权威源 = 业务库 information_schema，关系 = 物理外键图
        ∪ 治理关系文档（冲突以物理外键为准）。
        """
        return self._load_from_information_schema()

    def _load_gov_comment_maps(self) -> Tuple[Dict[str, str], Dict[Tuple[str, str], str]]:
        """治理库文档注释兜底：schema_table_docs / schema_column_docs 的策展中文名。

        业务库 information_schema 注释为权威源，但暂存库等环境物理表可能未落 COMMENT，
        此时用治理文档补全（仅填空，不覆盖非空注释）。
        """
        table_map: Dict[str, str] = {}
        column_map: Dict[Tuple[str, str], str] = {}
        try:
            with self.db.connect_governance() as conn:
                for t, c in conn.execute(
                        "SELECT table_name, table_comment FROM schema_table_docs "
                        "WHERE table_comment IS NOT NULL AND table_comment != ''"):
                    table_map[t] = c.strip()
                for t, c, cc in conn.execute(
                        "SELECT table_name, column_name, column_comment FROM schema_column_docs "
                        "WHERE column_comment IS NOT NULL AND column_comment != ''"):
                    column_map[(t, c)] = cc.strip()
        except Exception as e:
            print(f'[WARN] 治理库文档注释读取失败（跳过补全）: {e}', flush=True)
        return table_map, column_map

    def _load_from_information_schema(self) -> bool:
        """从业务库 information_schema 重建表/列元数据；关系双源合并。
        注释兜底：物理表 COMMENT 为空时回退治理库 schema_*_docs 策展注释。"""
        try:
            tables: Dict[str, Dict] = {}
            with self.db.connect_business() as conn:
                trows = conn.execute(
                    "SELECT table_name, table_comment, table_rows "
                    "FROM information_schema.tables "
                    "WHERE table_schema = DATABASE() AND table_type = 'BASE TABLE' "
                    "ORDER BY table_name").fetchall()
                if not trows:
                    return False
                for name, comment, table_rows in trows:
                    tables[name] = {
                        'name': name, 'comment': comment or '',
                        'row_count': table_rows or 0, 'columns': [], 'pk': [],
                    }
                crows = conn.execute(
                    "SELECT table_name, column_name, column_comment, column_type, column_key "
                    "FROM information_schema.columns "
                    "WHERE table_schema = DATABASE() "
                    "ORDER BY table_name, ordinal_position").fetchall()
            for tname, cname, cmt, ctype, ckey in crows:
                if tname not in tables:
                    continue
                is_pk = (ckey or '').upper() == 'PRI'
                if is_pk:
                    tables[tname]['pk'].append(cname)
                tables[tname]['columns'].append({
                    'name': cname, 'comment': cmt or '',
                    'type': (ctype or '').upper(), 'pk': is_pk,
                })
            if not tables:
                return False
            # 治理文档注释兜底（仅填空）：暂存库物理表无 COMMENT 时仍有中文名
            t_map, c_map = self._load_gov_comment_maps()
            filled_t = filled_c = 0
            for name, info in tables.items():
                if not info['comment'] and name in t_map:
                    info['comment'] = t_map[name]
                    filled_t += 1
                for col in info['columns']:
                    if not col['comment'] and (name, col['name']) in c_map:
                        col['comment'] = c_map[(name, col['name'])]
                        filled_c += 1
            if filled_t or filled_c:
                print(f'[SchemaPreloader] 治理文档注释兜底：表 {filled_t} / 列 {filled_c}', flush=True)
            rels = self._merge_relationships(self._load_relationship_docs(), self._load_physical_fks())
            self.parser = _MergedSchema(tables=tables, relationships=rels)
            return True
        except Exception as e:
            print(f'[WARN] 从业务库 information_schema 加载元数据失败，回退 DDL 文件解析: {e}', flush=True)
            return False

    def _load_relationship_docs(self) -> List[Dict]:
        """从治理库 schema_relationship_docs 读取关系文档。

        作为双源合并的语义源（业务场景/推荐路径/逻辑关系），
        与物理外键图在 _merge_relationships 合并。
        """
        rels: List[Dict] = []
        with self.db.connect_governance() as conn:
            rrows = conn.execute(
                'SELECT path, join_conditions, business_scenarios FROM schema_relationship_docs').fetchall()
        for path, jc, bs in rrows:
            try:
                rels.append({
                    'path': json.loads(path),
                    'join_conditions': json.loads(jc),
                    'business_scenarios': json.loads(bs or '[]'),
                })
            except Exception:
                continue
        return rels

    def _load_physical_fks(self) -> List[Dict]:
        """MySQL 模式：从业务库 information_schema 读取物理外键，归一化为关系文档同构 dict。

        join_conditions 采用与治理文档相同的 't.col = rt.rcol' 字符串格式；
        同一 (from_table, to_table) 的多个列对合并为一条关系；source='physical_fk'。
        """
        with self.db.connect_business() as conn:
            rows = conn.execute(
                "SELECT table_name, column_name, referenced_table_name, referenced_column_name "
                "FROM information_schema.key_column_usage "
                "WHERE table_schema = DATABASE() AND referenced_table_name IS NOT NULL "
                "ORDER BY table_name, ordinal_position").fetchall()
        grouped: Dict[Tuple[str, str], List[str]] = {}
        for ft, fc, tt, tc in rows:
            grouped.setdefault((ft, tt), []).append(f'{ft}.{fc} = {tt}.{tc}')
        return [
            {
                'path': [ft, tt],
                'join_conditions': jcs,
                'business_scenarios': [],
                'source': 'physical_fk',
            }
            for (ft, tt), jcs in grouped.items()
        ]

    def _merge_relationships(self, doc_rels: List[Dict], fk_rels: List[Dict]) -> List[Dict]:
        """合并治理关系文档与物理外键图（仅 MySQL 模式调用），输出合并统计。

        规则：
        - 两边相同的边（join_condition 两端集合一致，书写方向不敏感）→ 保留文档的
          business_scenarios/path，source='both'；
        - 冲突裁决：同一 (from_table, from_column)（条件串左侧，= path[0] 侧）在双源中指向
          不同 (to_table, to_column) 时，物理 FK 为准，该治理边从合并视图剔除并 WARN 留痕；
          一条关系的全部条件都被剔除时整条剔除；
        - 其余治理边（逻辑关系、共享父表路径等）全部保留，source='governance_doc'；
        - 物理独有的边补充进合并视图，source='physical_fk'。
        """
        # 物理边索引：有向边集合 + from 列 → to 列集
        phys_directed: Set[Tuple[str, str, str, str]] = set()
        fk_from: Dict[Tuple[str, str], Set[Tuple[str, str]]] = defaultdict(set)
        for rel in fk_rels:
            for jc in rel['join_conditions']:
                e = _parse_join_condition(jc)
                if e:
                    phys_directed.add(e)
                    fk_from[(e[0], e[1])].add((e[2], e[3]))

        consumed: Set[Tuple[str, str, str, str]] = set()
        n_both = n_doc_only = n_conflict = 0
        merged: List[Dict] = []
        for rel in doc_rels:
            kept_jc: List[str] = []
            rel_both = False
            for jc in rel['join_conditions']:
                e = _parse_join_condition(jc)
                if e is None:
                    # 解析不了的治理条件无法参与对账，原样保留
                    kept_jc.append(jc)
                    n_doc_only += 1
                    continue
                lt, lc, rt, rc = e
                if (lt, lc, rt, rc) in phys_directed:
                    consumed.add((lt, lc, rt, rc))
                    kept_jc.append(jc)
                    rel_both = True
                    n_both += 1
                elif (rt, rc, lt, lc) in phys_directed:
                    # 治理条件书写方向与物理 FK 相反（父表在左），同一条边
                    consumed.add((rt, rc, lt, lc))
                    kept_jc.append(jc)
                    rel_both = True
                    n_both += 1
                elif (lt, lc) in fk_from and (rt, rc) not in fk_from[(lt, lc)]:
                    n_conflict += 1
                    phys_to = sorted(f'{t}.{c}' for t, c in fk_from[(lt, lc)])
                    print(f'[WARN] 关系冲突以物理外键为准，剔除治理边 {jc}'
                          f'（物理库 {lt}.{lc} -> {phys_to}）', flush=True)
                else:
                    kept_jc.append(jc)
                    n_doc_only += 1
            if not kept_jc:
                print(f"[WARN] 治理关系 {' → '.join(rel['path'])} 的全部 JOIN 条件"
                      f'均与物理外键冲突，整条剔除', flush=True)
                continue
            new_rel = dict(rel)
            new_rel['join_conditions'] = kept_jc
            new_rel['source'] = 'both' if rel_both else 'governance_doc'
            merged.append(new_rel)

        # 物理独有的边（治理文档未覆盖的合法 JOIN 边）
        n_phys_only = 0
        for rel in fk_rels:
            jcs = [jc for jc in rel['join_conditions']
                   if _parse_join_condition(jc) not in consumed]
            if jcs:
                n_phys_only += len(jcs)
                merged.append({
                    'path': rel['path'],
                    'join_conditions': jcs,
                    'business_scenarios': [],
                    'source': 'physical_fk',
                })
        print(f'[SchemaPreloader] 关系合并：both={n_both} doc_only={n_doc_only} '
              f'physical_only={n_phys_only} 冲突剔除={n_conflict}，合并后 {len(merged)} 条', flush=True)
        return merged

    def _merge_parsers(
        self,
        column_parser: DDLSchemaParser,
        table_parser: DDLSchemaParser,
    ) -> _MergedSchema:
        """合并解析结果：字段以完整版为准，表注释/PK/关系以精简版为准。"""
        tables: Dict[str, Dict] = {}

        # 先用完整版建立全量字段信息
        for table_name, col_info in column_parser.tables.items():
            tables[table_name] = {
                'name': table_name,
                'comment': col_info['comment'],
                'columns': col_info['columns'],
                'pk': col_info['pk'],
            }

        # 用精简版覆盖表级注释、主键，保留完整字段
        for table_name, tbl_info in table_parser.tables.items():
            if table_name in tables:
                tables[table_name]['comment'] = tbl_info['comment'] or tables[table_name]['comment']
                tables[table_name]['pk'] = tbl_info['pk'] or tables[table_name]['pk']
            else:
                tables[table_name] = {
                    'name': table_name,
                    'comment': tbl_info['comment'],
                    'columns': tbl_info['columns'],
                    'pk': tbl_info['pk'],
                }

        return _MergedSchema(tables=tables, relationships=table_parser.relationships)

    def _summary(self) -> Dict:
        parser = self.parser
        if parser is None:
            return {'loaded': False}

        # 以 governance 库实际数据为准统计覆盖率
        with self.db.connect_governance() as conn:
            table_count = conn.execute('SELECT COUNT(*) FROM schema_table_docs').fetchone()[0]
            column_count = conn.execute('SELECT COUNT(*) FROM schema_column_docs').fetchone()[0]
            missing_table_comments = conn.execute(
                "SELECT COUNT(*) FROM schema_table_docs WHERE table_comment IS NULL OR table_comment = ''"
            ).fetchone()[0]
            missing_column_comments = conn.execute(
                "SELECT COUNT(*) FROM schema_column_docs WHERE column_comment IS NULL OR column_comment = ''"
            ).fetchone()[0]
            relationship_count = conn.execute('SELECT COUNT(*) FROM schema_relationship_docs').fetchone()[0]

        return {
            'loaded': True,
            'column_source': self.column_source_path,
            'table_source': self.table_ddl_path,
            'tables': table_count,
            'columns': column_count,
            'relationships': relationship_count,
            'missing_table_comments': missing_table_comments,
            'missing_column_comments': missing_column_comments,
        }

    def _persist(self):
        """将解析结果持久化到 governance 库。"""
        parser = self.parser
        if parser is None:
            return

        with self.db.connect_governance() as conn:
            # 1) 表文档：保留已有 row_count / column_count，仅补全注释和 doc_text
            for table, info in parser.tables.items():
                comment = info['comment'] or ''
                col_count = len(info['columns'])
                doc_text = f"表名：{table}"
                if comment:
                    doc_text += f"，中文名：{comment}"
                doc_text += f"，字段数：{col_count}。"

                conn.execute('''
                    INSERT INTO schema_table_docs
                    (table_name, table_comment, column_count, doc_text)
                    VALUES (%s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        table_comment=VALUES(table_comment),
                        column_count=VALUES(column_count),
                        doc_text=VALUES(doc_text),
                        updated_at=CURRENT_TIMESTAMP
                ''', (table, comment, col_count, doc_text))

            # 2) 字段文档：以 DDL 注释为准，保留 top_values
            for table, info in parser.tables.items():
                for col in info['columns']:
                    comment = col['comment'] or ''
                    doc_text = f"表 {table} 的字段 {col['name']}"
                    if comment:
                        doc_text += f"，中文名：{comment}"
                    doc_text += f"，类型：{col['type']}"
                    if col['pk']:
                        doc_text += "，主键"

                    conn.execute('''
                        INSERT INTO schema_column_docs
                        (table_name, column_name, column_comment, data_type, is_pk, doc_text)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                            column_comment=VALUES(column_comment),
                            data_type=VALUES(data_type),
                            is_pk=VALUES(is_pk),
                            doc_text=VALUES(doc_text)
                    ''', (table, col['name'], comment, col['type'], 1 if col['pk'] else 0, doc_text))

            # 2.5) 字段/表文档对账：删除解析结果中已不存在的陈旧行（列更名/删列/删表后自动同步）
            current_cols = {(t, c['name']) for t, info in parser.tables.items() for c in info['columns']}
            current_tables = set(parser.tables.keys())
            for t, c in conn.execute('SELECT table_name, column_name FROM schema_column_docs').fetchall():
                if (t, c) not in current_cols:
                    conn.execute('DELETE FROM schema_column_docs WHERE table_name = ? AND column_name = ?', (t, c))
            for (t,) in conn.execute('SELECT table_name FROM schema_table_docs').fetchall():
                if t not in current_tables:
                    conn.execute('DELETE FROM schema_table_docs WHERE table_name = ?', (t,))

            # 3) 关系文档：清空后重新写入（由 DDL 重新生成，最权威）
            conn.execute('DELETE FROM schema_relationship_docs')
            for rel in parser.relationships:
                title = ' → '.join(rel['path'])
                doc_text = f"表关联路径：{title}\n"
                doc_text += "JOIN 条件：\n"
                for jc in rel['join_conditions']:
                    doc_text += f"  - {jc}\n"
                doc_text += "适用业务场景：" + '、'.join(rel['business_scenarios'])

                conn.execute('''
                    INSERT INTO schema_relationship_docs
                    (title, path, join_conditions, business_scenarios, doc_text)
                    VALUES (?, ?, ?, ?, ?)
                ''', (
                    title,
                    json.dumps(rel['path'], ensure_ascii=False),
                    json.dumps(rel['join_conditions'], ensure_ascii=False),
                    json.dumps(rel['business_scenarios'], ensure_ascii=False),
                    doc_text,
                ))

            conn.commit()

        # 4) 兜底填充 DDL 未覆盖字段的中文注释
        self._fill_missing_column_comments()

    def _fill_missing_column_comments(self):
        """用兜底映射为业务库中 DDL 未覆盖字段补全中文注释。"""
        with self.db.connect_governance() as conn:
            cursor = conn.execute(
                "SELECT table_name, column_name FROM schema_column_docs "
                "WHERE column_comment IS NULL OR column_comment = ''"
            )
            rows = cursor.fetchall()
            updated = 0
            for row in rows:
                table_name = row[0]
                column_name = row[1]
                fallback = _fallback_column_comment(column_name)
                if not fallback:
                    fallback = column_name
                doc_text = f"表 {table_name} 的字段 {column_name}，中文名：{fallback}"
                conn.execute('''
                    UPDATE schema_column_docs
                    SET column_comment = %s, doc_text = %s
                    WHERE table_name = %s AND column_name = %s
                ''', (fallback, doc_text, table_name, column_name))
                updated += 1
            conn.commit()
            if updated:
                print(f"[SchemaPreloader] 兜底补全 {updated} 个字段中文注释")

    def _build_global_context(self) -> str:
        """构建全局表级/关系级上下文，供 prompt 注入。"""
        parser = self.parser
        if parser is None:
            return ''

        lines = [
            "【全局 Schema 上下文】",
            f"本库共 {len(parser.tables)} 张营销共享层表，核心数据链如下：",
            "客户(dim_cst_cust) → 用电客户(dim_cst_elec_cons_cust) → 安装点(dim_cst_inst_elec_cons) → 计量点运行(dwd_cst_meter_run) → 日电量(dwd_cst_meter_energy_day_h_xz)",
            "",
            "【表清单】",
        ]

        # 按维度/事实分组
        dims = []
        facts = []
        others = []
        for table in sorted(parser.tables.keys()):
            info = parser.tables[table]
            entry = f"- {table}：{info['comment']}" if info['comment'] else f"- {table}"
            if table.startswith('dim_'):
                dims.append(entry)
            elif table.startswith('dwd_'):
                facts.append(entry)
            else:
                others.append(entry)

        if dims:
            lines.append("维度表：")
            lines.extend(dims)
        if facts:
            lines.append("事实表：")
            lines.extend(facts)
        if others:
            lines.append("其他：")
            lines.extend(others)

        lines.append("")
        lines.append("【主外键关联关系（全量，JOIN 条件必须从这里取，禁止捏造）】")
        for rel in parser.relationships:
            lines.append(f"- {' → '.join(rel['path'])}")
            for jc in rel['join_conditions']:
                lines.append(f"  {jc}")

        lines.append("")
        lines.append("【供电单位归属】")
        lines.append("多数表通过 mgt_org_code 关联 dim_cst_mgt_org 获取供电单位名称。")

        return '\n'.join(lines)

    # ==================== 检索接口 ====================

    def get_global_context(self) -> str:
        """获取已构建的全局上下文文本。未预加载时自动触发一次。"""
        if not self._global_context:
            self.preload()
        return self._global_context

    def get_table_comment(self, table: str) -> str:
        if self.parser is None:
            self.preload()
        return self.parser.tables.get(table, {}).get('comment', '')

    def get_column_comment(self, table: str, column: str) -> str:
        if self.parser is None:
            self.preload()
        for col in self.parser.tables.get(table, {}).get('columns', []):
            if col['name'] == column:
                return col['comment'] or ''
        return ''

    def get_columns(self, table: str) -> List[Dict]:
        if self.parser is None:
            self.preload()
        return list(self.parser.tables.get(table, {}).get('columns', []))

    def get_table_names(self) -> List[str]:
        if self.parser is None:
            self.preload()
        return sorted(self.parser.tables.keys())

    def get_relationships(self, from_table: Optional[str] = None, to_table: Optional[str] = None) -> List[Dict]:
        if self.parser is None:
            self.preload()
        result = []
        for rel in self.parser.relationships:
            a, b = rel['path']
            if from_table and from_table not in (a, b):
                continue
            if to_table and to_table not in (a, b):
                continue
            result.append(rel)
        return result


def preload_schema(
    column_source_path: Optional[str] = None,
    table_ddl_path: Optional[str] = None,
    force: bool = False,
) -> Dict:
    """便捷入口：执行预加载并打印摘要。"""
    preloader = SchemaPreloader.get_instance(column_source_path, table_ddl_path)
    summary = preloader.preload(force=force)
    print(f"[SchemaPreloader] 预加载完成: {summary}")
    return summary


if __name__ == '__main__':
    summary = preload_schema(force=True)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    preloader = SchemaPreloader.get_instance()
    print(preloader.get_global_context()[:2000])
