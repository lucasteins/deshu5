# -*- coding: utf-8 -*-
"""Schema 知识库：构建和检索表结构、字段语义、表关系、SQL 模式"""
import os
import json
import re
import sys
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.database import DatabaseManager


class DDLCommentParser:
    """解析 DDL 文件中的表和字段注释"""
    
    def __init__(self, ddl_path: Optional[str] = None):
        if ddl_path is None:
            import config
            ddl_path = getattr(
                config, 'DDL_SCHEMA_FILE',
                os.path.join(
                    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                    'ddl', '35张营销共享层表_重构版v2.0_20260519.sql'
                )
            )
        self.ddl_path = ddl_path
        self.table_comments: Dict[str, str] = {}
        self.column_comments: Dict[str, Dict[str, str]] = {}
        self._parse()
    
    def _parse(self):
        """解析 DDL 文件"""
        if not os.path.exists(self.ddl_path):
            return
        
        with open(self.ddl_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 按 CREATE TABLE 语句分割
        table_blocks = re.split(r'CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+', content, flags=re.IGNORECASE)
        
        for block in table_blocks[1:]:
            # 提取表名
            table_match = re.match(r'`?(\w+)`?\s*\(', block)
            if not table_match:
                continue
            table_name = table_match.group(1)
            
            # 提取表注释
            comment_match = re.search(r'\)\s*COMMENT\s+[\'"]([^\'"]+)[\'"]', block, re.IGNORECASE)
            if comment_match:
                self.table_comments[table_name] = comment_match.group(1).strip()
            
            # 提取字段注释
            self.column_comments[table_name] = {}
            for line in block.split('\n'):
                line = line.strip()
                if not line or line.startswith('--') or line.startswith('primary key') or line.startswith(')'):
                    continue
                
                col_match = re.match(r'`?(\w+)`?\s+\w+.*?COMMENT\s+[\'"]([^\'"]+)[\'"]', line, re.IGNORECASE)
                if col_match:
                    col_name = col_match.group(1)
                    col_comment = col_match.group(2).strip()
                    self.column_comments[table_name][col_name] = col_comment
    
    def get_table_comment(self, table: str) -> str:
        return self.table_comments.get(table, '')
    
    def get_column_comment(self, table: str, column: str) -> str:
        return self.column_comments.get(table, {}).get(column, '')

class SchemaKnowledgeBase:
    """Schema 知识库：构建、存储、检索"""
    
    def __init__(self):
        self.db = DatabaseManager()
        self.ddl_parser = DDLCommentParser()
        self._init_tables()
    
    def _init_tables(self):
        """初始化知识库表（MySQL 方言）"""
        with self.db.connect_governance() as conn:
            # 2026-08-19 精简：doc_json（table/relationship）、top_values（column）已删除
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

            # 2026-08-15：sql_patterns 已并入 sql_knowledge（kind='template'），
            # 旧表归档 marketing_log._bak_20260815_sql_patterns（见 _knowledge_merge.py），不再建表。
            conn.commit()
    
    def build_all(self):
        """构建完整知识库"""
        print("构建 Schema 知识库...")
        self.build_table_docs()
        self.build_column_docs()
        self.build_relationship_docs()
        print("Schema 知识库构建完成")
    
    def build_table_docs(self):
        """构建表文档"""
        schema = self._load_business_schema()
        
        with self.db.connect_governance() as conn:
            for table, info in schema.items():
                comment = self.ddl_parser.get_table_comment(table) or ''
                row_count = info.get('stats', {}).get('row_count', 0)
                col_count = len(info.get('columns', []))
                
                business_meaning = self._infer_table_meaning(table, comment)
                doc_text = f"表名：{table}"
                if comment:
                    doc_text += f"，中文名：{comment}"
                doc_text += f"，行数：{row_count}，字段数：{col_count}。"
                doc_text += f"业务含义：{business_meaning}"
                
                conn.execute('''
                    INSERT OR REPLACE INTO schema_table_docs
                    (table_name, table_comment, row_count, column_count, doc_text)
                    VALUES (?, ?, ?, ?, ?)
                ''', (table, comment, row_count, col_count, doc_text))
            
            conn.commit()
        print(f"  表文档：{len(schema)} 张")
    
    def build_column_docs(self):
        """构建字段文档"""
        schema = self._load_business_schema()
        
        with self.db.connect_governance() as conn:
            count = 0
            for table, info in schema.items():
                for col in info.get('columns', []):
                    col_name = col['name']
                    comment = self.ddl_parser.get_column_comment(table, col_name) or col.get('comment', '')
                    is_pk = col.get('pk', 0) > 0
                    
                    doc_text = f"表 {table} 的字段 {col_name}"
                    if comment:
                        doc_text += f"，中文名：{comment}"
                    doc_text += f"，类型：{col.get('type', '')}"
                    if is_pk:
                        doc_text += "，主键"
                    
                    conn.execute('''
                        INSERT OR REPLACE INTO schema_column_docs
                        (table_name, column_name, column_comment, data_type, is_pk, doc_text)
                        VALUES (?, ?, ?, ?, ?, ?)
                    ''', (
                        table, col_name, comment, col.get('type', ''),
                        is_pk,
                        doc_text
                    ))
                    count += 1
            conn.commit()
        print(f"  字段文档：{count} 个")
    
    def build_relationship_docs(self):
        """构建表关系文档"""
        paths = self._build_core_relationship_paths()
        
        with self.db.connect_governance() as conn:
            # 清空旧数据
            conn.execute('DELETE FROM schema_relationship_docs')
            
            for path in paths:
                title = ' → '.join(path['path'])
                doc_text = f"表关联路径：{title}\n"
                doc_text += "JOIN 条件：\n"
                for jc in path['join_conditions']:
                    doc_text += f"  - {jc}\n"
                doc_text += "适用业务场景：" + '、'.join(path['business_scenarios'])
                
                conn.execute('''
                    INSERT INTO schema_relationship_docs
                    (title, path, join_conditions, business_scenarios, doc_text)
                    VALUES (?, ?, ?, ?, ?)
                ''', (
                    title,
                    json.dumps(path['path'], ensure_ascii=False),
                    json.dumps(path['join_conditions'], ensure_ascii=False),
                    json.dumps(path['business_scenarios'], ensure_ascii=False),
                    doc_text
                ))
            conn.commit()
        print(f"  关系文档：{len(paths)} 条")
    
    def build_sql_patterns(self, qa_pairs: List[Dict]):
        """从 QA 对构建 SQL 模式（遗留路径：写旧 sql_patterns 表；2026-08-15 起
        线上数据已并入 sql_knowledge，MySQL 模式请勿再跑此构建）"""
        patterns = self._extract_patterns_from_qa(qa_pairs)
        
        with self.db.connect_governance() as conn:
            conn.execute('DELETE FROM sql_patterns')
            
            for p in patterns:
                conn.execute('''
                    INSERT INTO sql_patterns
                    (pattern_type, pattern_name, table_path, aggregation_pattern, filter_pattern,
                     order_limit_pattern, related_question_ids, doc_text, doc_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    p['pattern_type'],
                    p['pattern_name'],
                    json.dumps(p['table_path'], ensure_ascii=False),
                    p['aggregation_pattern'],
                    p['filter_pattern'],
                    p['order_limit_pattern'],
                    json.dumps(p['related_question_ids'], ensure_ascii=False),
                    p['doc_text'],
                    json.dumps(p, ensure_ascii=False)
                ))
            conn.commit()
        print(f"  SQL 模式：{len(patterns)} 条")
    
    def _load_business_schema(self) -> Dict:
        """加载业务库 schema"""
        from core.schema_loader import SchemaLoader
        loader = SchemaLoader()
        return loader.load_schema()
    
    def _infer_table_meaning(self, table: str, comment: str) -> str:
        """推断表业务含义"""
        if comment:
            return comment
        if table.startswith('dim_cst_'):
            return '维度表'
        if table.startswith('dwd_cst_'):
            return '事实表'
        return '业务表'
    
    def _build_core_relationship_paths(self) -> List[Dict]:
        """构建核心表关系路径"""
        schema = self._load_business_schema()
        
        # 手动定义核心主链（基于外键和实际业务）
        core_paths = [
            {
                'path': ['dim_cst_cust', 'dim_cst_elec_cons_cust'],
                'join_conditions': ['dim_cst_cust.cust_id = dim_cst_elec_cons_cust.cust_id'],
                'business_scenarios': ['客户基本信息查询', '用电客户信息查询']
            },
            {
                'path': ['dim_cst_elec_cons_cust', 'dim_cst_inst_elec_cons'],
                'join_conditions': ['dim_cst_elec_cons_cust.cust_id = dim_cst_inst_elec_cons.cust_id'],
                'business_scenarios': ['客户安装点查询', '客户计量点查询']
            },
            {
                'path': ['dim_cst_inst_elec_cons', 'dwd_cst_meter_run'],
                'join_conditions': ['dim_cst_inst_elec_cons.inst_id = dwd_cst_meter_run.inst_id'],
                'business_scenarios': ['安装点表计查询', '计量点运行查询']
            },
            {
                'path': ['dwd_cst_meter_run', 'dwd_cst_meter_energy_day_h_xz'],
                'join_conditions': ['dwd_cst_meter_run.meter_asset_no = dwd_cst_meter_energy_day_h_xz.meter_asset_no'],
                'business_scenarios': ['计量点日电量查询', '客户用电量统计']
            },
            {
                'path': ['dwd_cst_meter_run', 'dwd_cst_es_meter_energy_day_p'],
                'join_conditions': ['dwd_cst_meter_run.meter_asset_no = dwd_cst_es_meter_energy_day_p.meter_asset_no'],
                'business_scenarios': ['公变日电量查询']
            },
            {
                'path': ['dim_cst_elec_cons_cust', 'dwd_cst_rcvbl_acct'],
                'join_conditions': ['dim_cst_elec_cons_cust.mgt_org_code = dwd_cst_rcvbl_acct.mgt_org_code'],
                'business_scenarios': ['客户应收电费查询', '供电单位应收统计']
            },
            {
                'path': ['dwd_cst_rcvbl_acct', 'dwd_cst_rcvd_acct'],
                'join_conditions': ['dwd_cst_rcvbl_acct.rcvbl_acct_id = dwd_cst_rcvd_acct.rcvbl_acct_id'],
                'business_scenarios': ['应收实收对比', '欠费分析']
            },
            {
                'path': ['dim_cst_inst_elec_cons', 'dwd_cst_inst_bilg_card'],
                'join_conditions': ['dim_cst_inst_elec_cons.inst_id = dwd_cst_inst_bilg_card.inst_id'],
                'business_scenarios': ['安装点计费查询']
            },
            {
                'path': ['dim_cst_inst_elec_cons', 'dwd_cst_mr_data'],
                'join_conditions': ['dim_cst_inst_elec_cons.inst_id = dwd_cst_mr_data.inst_id'],
                'business_scenarios': ['抄表数据查询', '抄表示数查询']
            },
            {
                'path': ['dim_cst_cust', 'dim_cst_mgt_org'],
                'join_conditions': ['dim_cst_cust.mgt_org_code = dim_cst_mgt_org.mgt_org_code'],
                'business_scenarios': ['客户所属供电单位查询', '按供电单位统计客户']
            },
            {
                'path': ['dim_cst_elec_cons_cust', 'dim_cst_mgt_org'],
                'join_conditions': ['dim_cst_elec_cons_cust.mgt_org_code = dim_cst_mgt_org.mgt_org_code'],
                'business_scenarios': ['用电客户所属供电单位查询', '按供电单位统计用电客户']
            }
        ]
        
        # 自动发现外键关系（补充）
        discovered = []
        for table, info in schema.items():
            for fk in info.get('foreign_keys', []):
                ref_table = fk['ref_table']
                from_col = fk['from_col']
                to_col = fk['to_col']
                # 避免和核心路径重复
                if table == ref_table:
                    continue
                path = [table, ref_table]
                jc = f"{table}.{from_col} = {ref_table}.{to_col}"
                
                # 检查是否已存在
                exists = False
                for cp in core_paths:
                    if cp['path'] == path or cp['path'] == list(reversed(path)):
                        exists = True
                        break
                if not exists:
                    discovered.append({
                        'path': path,
                        'join_conditions': [jc],
                        'business_scenarios': [f'{table} 与 {ref_table} 关联查询']
                    })
        
        # 合并并生成更长路径
        all_paths = core_paths + discovered
        extended = self._extend_paths(all_paths, schema)
        
        return all_paths + extended
    
    def _extend_paths(self, paths: List[Dict], schema: Dict) -> List[Dict]:
        """通过拼接生成更长的关联路径"""
        # 简单拼接：如果 path1 的末尾等于 path2 的开头
        extended = []
        for i, p1 in enumerate(paths):
            for j, p2 in enumerate(paths):
                if i == j:
                    continue
                if p1['path'][-1] == p2['path'][0] and len(p1['path']) + len(p2['path']) - 1 <= 5:
                    new_path = p1['path'] + p2['path'][1:]
                    new_jc = p1['join_conditions'] + p2['join_conditions']
                    new_scenarios = [f"{s1} + {s2}" for s1 in p1['business_scenarios'][:1] for s2 in p2['business_scenarios'][:1]]
                    
                    # 去重
                    if new_path not in [ep['path'] for ep in extended]:
                        extended.append({
                            'path': new_path,
                            'join_conditions': new_jc,
                            'business_scenarios': new_scenarios[:3]
                        })
        return extended
    
    def _extract_patterns_from_qa(self, qa_pairs: List[Dict]) -> List[Dict]:
        """从 QA 对提取 SQL 模式"""
        patterns = []
        
        for qa in qa_pairs:
            sql = qa.get('standard_sql', '')
            if not sql:
                continue
            
            tables = self._extract_tables(sql)
            agg_pattern = self._extract_aggregation_pattern(sql)
            filter_pattern = self._extract_filter_pattern(sql)
            order_limit_pattern = self._extract_order_limit_pattern(sql)
            
            # 确定模式类型
            pattern_type = '简单查询'
            if 'JOIN' in sql.upper():
                pattern_type = 'JOIN查询'
            if agg_pattern:
                pattern_type = '聚合统计'
            if 'WITH' in sql.upper():
                pattern_type = 'CTE'
            if any(w in sql.upper() for w in ['LAG(', 'LEAD(', 'OVER(']):
                pattern_type = '窗口函数'
            
            pattern_name = f"{pattern_type}: {' → '.join(tables) if tables else '单表'}"
            
            doc_text = f"SQL模式：{pattern_name}\n"
            doc_text += f"表路径：{' → '.join(tables)}\n"
            if agg_pattern:
                doc_text += f"聚合方式：{agg_pattern}\n"
            if filter_pattern:
                doc_text += f"过滤模式：{filter_pattern}\n"
            if order_limit_pattern:
                doc_text += f"排序限制：{order_limit_pattern}\n"
            doc_text += f"参考问题：{qa.get('question', '')}"
            
            patterns.append({
                'pattern_type': pattern_type,
                'pattern_name': pattern_name,
                'table_path': tables,
                'aggregation_pattern': agg_pattern,
                'filter_pattern': filter_pattern,
                'order_limit_pattern': order_limit_pattern,
                'related_question_ids': [qa.get('question_id', str(qa.get('id', '')))],
                'doc_text': doc_text
            })
        
        return patterns
    
    def _extract_tables(self, sql: str) -> List[str]:
        tables = set()
        for match in re.finditer(r'\bFROM\s+(\w+)', sql, re.IGNORECASE):
            tables.add(match.group(1))
        for match in re.finditer(r'\bJOIN\s+(\w+)', sql, re.IGNORECASE):
            tables.add(match.group(1))
        return sorted(tables)
    
    def _extract_aggregation_pattern(self, sql: str) -> str:
        aggs = re.findall(r'\b(SUM|COUNT|AVG|MAX|MIN)\s*\(', sql, re.IGNORECASE)
        if not aggs:
            return ''
        aggs = sorted(set(a.upper() for a in aggs))
        
        group_by = re.search(r'\bGROUP\s+BY\s+(.+?)(?:\bORDER\b|\bLIMIT\b|$)', sql, re.IGNORECASE | re.DOTALL)
        gb = group_by.group(1).strip() if group_by else ''
        
        return f"聚合函数：{', '.join(aggs)}" + (f"，分组：{gb}" if gb else '')
    
    def _extract_filter_pattern(self, sql: str) -> str:
        patterns = []
        where_match = re.search(r'\bWHERE\b(.+?)(?:\bGROUP\b|\bORDER\b|\bLIMIT\b|$)', sql, re.IGNORECASE | re.DOTALL)
        if where_match:
            where_clause = where_match.group(1).strip()
            if re.search(r'DATE_FORMAT|STRFTIME|data_date', where_clause, re.IGNORECASE):
                patterns.append('日期过滤')
            if 'LIKE' in where_clause.upper():
                patterns.append('模糊匹配')
            if re.search(r'(=|!=|<>|>|<|>=|<=)\s*[\'"\d]', where_clause):
                patterns.append('等值/范围过滤')
        
        having_match = re.search(r'\bHAVING\b(.+?)(?:\bORDER\b|\bLIMIT\b|$)', sql, re.IGNORECASE | re.DOTALL)
        if having_match:
            patterns.append('HAVING聚合后过滤')
        
        return '；'.join(patterns)
    
    def _extract_order_limit_pattern(self, sql: str) -> str:
        patterns = []
        order_match = re.search(r'\bORDER\s+BY\b(.+?)(?:\bLIMIT\b|$)', sql, re.IGNORECASE | re.DOTALL)
        if order_match:
            order_part = order_match.group(1).strip()
            desc = 'DESC' in order_part.upper()
            patterns.append(f"{'降序' if desc else '升序'}排序")
        
        limit_match = re.search(r'\bLIMIT\s+(\d+)', sql, re.IGNORECASE)
        if limit_match:
            patterns.append(f"LIMIT {limit_match.group(1)}")
        
        return '；'.join(patterns)
    
    # ==================== 检索接口 ====================
    
    def retrieve_table_docs(self, keywords: List[str], limit: int = 10) -> List[Dict]:
        """检索相关表文档"""
        keyword_set = set(k.lower() for k in keywords)
        results = []
        
        with self.db.connect_governance() as conn:
            cursor = conn.execute('SELECT table_name, table_comment, doc_text FROM schema_table_docs')
            for row in cursor.fetchall():
                table, comment, doc_text = row
                text = f"{table} {comment} {doc_text}".lower()
                score = sum(1 for kw in keyword_set if kw in text)
                if score > 0:
                    results.append({
                        'type': 'table',
                        'table_name': table,
                        'table_comment': comment,
                        'doc_text': doc_text,
                        'score': score
                    })
        
        results.sort(key=lambda x: x['score'], reverse=True)
        return results[:limit]
    
    def retrieve_column_docs(self, keywords: List[str], tables: List[str] = None, limit: int = 20) -> List[Dict]:
        """检索相关字段文档"""
        keyword_set = set(k.lower() for k in keywords)
        results = []
        
        with self.db.connect_governance() as conn:
            if tables:
                placeholders = ','.join('?' * len(tables))
                cursor = conn.execute(
                    f'SELECT table_name, column_name, column_comment, doc_text FROM schema_column_docs WHERE table_name IN ({placeholders})',
                    tuple(tables)
                )
            else:
                cursor = conn.execute('SELECT table_name, column_name, column_comment, doc_text FROM schema_column_docs')
            
            for row in cursor.fetchall():
                table, col, comment, doc_text = row
                text = f"{col} {comment} {doc_text}".lower()
                score = sum(1 for kw in keyword_set if kw in text)
                if score > 0:
                    results.append({
                        'type': 'column',
                        'table_name': table,
                        'column_name': col,
                        'column_comment': comment,
                        'doc_text': doc_text,
                        'score': score
                    })
        
        results.sort(key=lambda x: x['score'], reverse=True)
        return results[:limit]
    
    def retrieve_relationship_docs(self, keywords: List[str], tables: List[str] = None, limit: int = 10) -> List[Dict]:
        """检索相关表关系文档"""
        keyword_set = set(k.lower() for k in keywords)
        results = []
        
        with self.db.connect_governance() as conn:
            cursor = conn.execute('SELECT title, path, join_conditions, business_scenarios, doc_text FROM schema_relationship_docs')
            for row in cursor.fetchall():
                title, path_json, jc_json, scenarios_json, doc_text = row
                text = f"{title} {doc_text}".lower()
                
                # 如果指定了表，优先匹配路径中包含这些表的关系
                table_bonus = 0
                if tables:
                    path = json.loads(path_json)
                    table_bonus = len(set(tables) & set(path)) * 2
                
                score = sum(1 for kw in keyword_set if kw in text) + table_bonus
                if score > 0:
                    results.append({
                        'type': 'relationship',
                        'title': title,
                        'path': json.loads(path_json),
                        'join_conditions': json.loads(jc_json),
                        'business_scenarios': json.loads(scenarios_json),
                        'doc_text': doc_text,
                        'score': score
                    })
        
        results.sort(key=lambda x: x['score'], reverse=True)
        return results[:limit]
    
    def retrieve_sql_patterns(self, keywords: List[str], limit: int = 10) -> List[Dict]:
        """检索相关 SQL 模式（2026-08-19 起读 sql_knowledge 瘦身版 13 列；
        返回结构向后兼容：pattern_name/name、doc_text→description、sql_skeleton→sql_rule、
        table_path→sql_tables 解析、replay_pass 派生（有 sql_rule 记 1）、doc_json 已无返回 {}）"""
        keyword_set = set(k.lower() for k in keywords)
        results = []

        with self.db.connect_governance() as conn:
            cursor = conn.execute(
                "SELECT name, description, sql_tables, sql_rule, example_qa_ids "
                "FROM sql_knowledge")
            for row in cursor.fetchall():
                pname, doc_text, tables_txt, sql_rule, example_ids = row
                text = f"{pname} {doc_text}".lower()
                score = sum(1 for kw in keyword_set if kw in text)
                if score > 0:
                    try:
                        path = json.loads(tables_txt) if tables_txt else []
                    except Exception:
                        path = []
                    results.append({
                        'type': 'pattern',
                        'pattern_type': None,  # 列已删，保留键兼容
                        'pattern_name': pname,
                        'table_path': path,
                        'doc_text': doc_text,
                        'doc_json': {},
                        'sql_skeleton': sql_rule,
                        'example_qa_ids': example_ids,
                        'replay_pass': 1 if sql_rule else 0,
                        'score': score
                    })

        results.sort(key=lambda x: x['score'], reverse=True)
        return results[:limit]
    
    def retrieve(self, user_question: str, tables: List[str] = None,
                 top_k_tables: int = 5, top_k_columns: int = 15,
                 top_k_relationships: int = 5, top_k_patterns: int = 5) -> Dict[str, List[Dict]]:
        """统一检索接口"""
        from core.rag_retriever import RAGRetriever
        retriever = RAGRetriever(top_k=10)
        keywords = retriever.extract_keywords(user_question)
        
        return {
            'tables': self.retrieve_table_docs(keywords, limit=top_k_tables),
            'columns': self.retrieve_column_docs(keywords, tables=tables, limit=top_k_columns),
            'relationships': self.retrieve_relationship_docs(keywords, tables=tables, limit=top_k_relationships),
            'patterns': self.retrieve_sql_patterns(keywords, limit=top_k_patterns),
            'keywords': keywords
        }
    
    def get_table_names(self) -> List[str]:
        """获取所有表名。

        权威源 = 业务库 information_schema（经 SchemaPreloader 单例缓存）。
        """
        from core.schema_preloader import SchemaPreloader
        return SchemaPreloader.get_instance().get_table_names()

    def get_column_comments(self) -> Dict[str, Dict[str, str]]:
        """获取所有字段注释。

        权威源 = 业务库 information_schema（经 SchemaPreloader 单例缓存）。
        """
        from core.schema_preloader import SchemaPreloader
        preloader = SchemaPreloader.get_instance()
        return {
            t: {c['name']: c.get('comment') or '' for c in preloader.get_columns(t)}
            for t in preloader.get_table_names()
        }


def build_schema_kb():
    """一键构建 Schema 知识库"""
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from benchmark.dataset import QADataset
    
    kb = SchemaKnowledgeBase()
    kb.build_all()
    
    dataset = QADataset()
    qa_pairs = dataset.load_all_qa_pairs()
    kb.build_sql_patterns(qa_pairs)
    
    print("Schema 知识库和 SQL 模式库构建完成")


if __name__ == '__main__':
    build_schema_kb()
