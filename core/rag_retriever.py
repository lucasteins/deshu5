"""RAG 检索器：精确匹配 + 相似度匹配 + Schema 感知字段校验"""
import os
import sys
import re
from difflib import SequenceMatcher
from typing import List, Dict, Tuple, Optional, Set
from core.database import DatabaseManager

# 优先使用项目本地安装的 jieba
_VENDOR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.vendor')
if os.path.isdir(_VENDOR) and _VENDOR not in sys.path:
    sys.path.insert(0, _VENDOR)

# 尝试导入 jieba，未安装时使用简单分词回退
try:
    import jieba
    _JIEBA_AVAILABLE = True
except ImportError:
    _JIEBA_AVAILABLE = False


# 码值索引世代号（R7 热生效）：码值表 CRUD 后 bump，各 RAGRetriever 实例下次加载自动重建
_CV_INDEX_GEN = 0


def invalidate_code_value_index():
    """码值表（code_values / code_value_items / code_value_column_form）写操作后调用：
    bump 世代号，所有 RAGRetriever 实例下次加载码值索引时自动重建（码值改动免重启）。"""
    global _CV_INDEX_GEN
    _CV_INDEX_GEN += 1


# 简单中文停用词（模块级，供 extract_keywords 与 RAGRetriever 共用）
_STOP_WORDS = {
    '的', '了', '是', '在', '有', '和', '与', '或', '为', '对', '从', '到', '及', '等',
    '这', '那', '中', '上', '下', '查询', '统计', '获取', '查找', '列出', '所有'
}


def _simple_tokenize(text: str) -> List[str]:
    """简单分词（jieba 不可用时的回退方案）"""
    words = re.split(r'[\s,，.。!！?？;:；：\(\)（）]+', text)
    return [w for w in words if w]


def extract_keywords(text: str) -> List[str]:
    """从文本中提取关键词（去除停用词和短词）。模块级，供本体服务等外部调用方复用。"""
    if not text:
        return []
    words = jieba.lcut(text) if _JIEBA_AVAILABLE else _simple_tokenize(text)
    keywords = []
    for word in words:
        word = word.strip().lower()
        if len(word) >= 2 and word not in _STOP_WORDS:
            keywords.append(word)
    return keywords


# 码值反向匹配停用词：通用业务词不参与反向匹配，否则"客户/用电"会灌爆命中列表
_CV_REVERSE_STOP = {
    '用电', '客户', '用户', '供电', '电力', '电费', '电量', '单位', '公司',
    '记录', '数据', '信息', '情况', '业务', '哪些', '哪个', '如何', '怎么',
    '什么', '是否', '所有', '相关', '查询', '统计', '分析', '多少', '几个'
}


def match_code_value_index(cv_domains: Dict, cv_items: Dict, cv_col_map: Dict,
                           cv_form_map: Dict, question: str, tables: List[str],
                           per_domain: int = 8, max_domains: int = 8,
                           tokenize=None) -> List[Dict]:
    """码值维度匹配（模块级，RAGRetriever 与本体 OntologyService 共用同一套逻辑、各持索引）。

    通道一（问题→值）：问题中出现的码值名称（如"e户通代扣"）直接命中其域；
    通道二（表→值域）：定位表中的码值列（如 valid_flag_desc）带上其值域。
    返回: [{code_name, cn_name, columns, values, matched, form, pairs, total}]
    """
    if not cv_domains:
        return []
    tokenize = tokenize or extract_keywords
    hits = {}  # code_name -> {'matched': set()}

    # 通道一：问题子串命中码值名称（长度>=2 避免单字误命中）
    for code_name, items in cv_items.items():
        for _, item_name in items:
            if len(item_name) >= 2 and item_name in question:
                hits.setdefault(code_name, {'matched': set()})['matched'].add(item_name)

    # 通道一增强：问题分词反向命中长码值名
    # （如"杭州"命中"国网浙江省电力有限公司杭州供电公司"——码值名未完整出现在问题中，但其片段是问题中的词）
    try:
        tokens = [t for t in tokenize(question)
                  if len(t) >= 2 and t not in _CV_REVERSE_STOP]
    except Exception:
        tokens = []
    if tokens:
        for code_name, items in cv_items.items():
            for _, item_name in items:
                if len(item_name) < 4:
                    continue
                for tok in tokens:
                    if tok in item_name:
                        hits.setdefault(code_name, {'matched': set()})['matched'].add(item_name)
                        break

    # 通道二：定位表中的码值列
    located = set(tables or [])
    for code_name, cols in cv_col_map.items():
        if any(t in located for t, _ in cols):
            hits.setdefault(code_name, {'matched': set()})

    # 排序：有问题命中的域优先，其次按命中数
    ordered = sorted(hits.items(), key=lambda kv: (not kv[1]['matched'], -len(kv[1]['matched'])))
    result = []
    for code_name, info in ordered:
        dom = cv_domains.get(code_name)
        if not dom:
            continue  # 明细表中的孤儿域（code_values 无定义，如 bp_type）跳过
        items = [name for _, name in cv_items.get(code_name, [])]
        matched = sorted(info['matched'])
        # 命中的值排前面，其余按 sort_order 补足 per_domain
        values = matched + [v for v in items if v not in matched][:max(0, per_domain - len(matched))]
        values = values[:per_domain]
        if not values and not matched:
            continue  # 无值域明细的编码类域（如 mgt_org_code）不展示
        cols = cv_col_map.get(code_name, [])
        # 优先展示定位表中的列
        cols = sorted(cols, key=lambda tc: tc[0] not in located)
        # 存储形态：取第一个已知形态的映射列（已按定位表优先排序）
        form = None
        for tc in cols:
            f = cv_form_map.get(tc)
            if f:
                form = f
                break
        # 展示值的 名称=编码 对照（存编码的列生成 WHERE 时必需）
        code_of = {name: code for code, name in cv_items.get(code_name, [])}
        pairs = [(n, code_of.get(n, '')) for n in values]
        result.append({
            'code_name': code_name,
            'cn_name': cv_domains[code_name]['cn_name'],
            'columns': cols,
            'values': values,
            'matched': matched,
            'form': form,
            'pairs': pairs,
            'total': len(items),
        })
        if len(result) >= max_domains:
            break
    return result


class RAGRetriever:
    """
    RAG 检索器：从 qa_pairs 表中检索相似问答对
    
    支持三层检索策略：
    1. 精确匹配（关键词匹配）：基于关键词在问题中的命中次数
    2. 相似度匹配（文本相似度）：基于 difflib.SequenceMatcher 的文本相似度
    3. Schema 感知校验：校验 SQL 中的字段是否存在于 营销4.0 实际 schema 中
    
    最终排序：综合得分 = 关键词得分 * 0.4 + 相似度得分 * 0.4 + schema_valid * 0.2
    """
    
    def __init__(self, top_k: int = 5):
        self.db = DatabaseManager()
        self.top_k = top_k
        # 加载停用词（简单中文停用词，模块级常量共享）
        self.stop_words = _STOP_WORDS
        # 缓存 营销4.0 的所有有效字段
        self._valid_fields: Optional[Set[str]] = None
        self._valid_tables: Optional[Set[str]] = None

    def reset(self):
        """清空实例内缓存（数据库档位切换后调用，下次检索从新库重载）。"""
        self._valid_fields = None
        self._valid_tables = None
        self._cv_ready = False
        invalidate_code_value_index()
    
    def _load_valid_schema(self) -> Tuple[Set[str], Set[str]]:
        """加载 营销4.0 数据库中所有有效的表名和字段名（业务库 information_schema）"""
        if self._valid_fields is not None:
            return self._valid_tables, self._valid_fields
        
        valid_tables = set()
        valid_fields = set()
        
        with self.db.connect_business() as conn:
            # 使用 DatabaseManager 的统一方法获取所有表
            tables = self.db.get_all_tables(conn)
            
            for table in tables:
                valid_tables.add(table.lower())
                # 使用 DatabaseManager 的统一方法获取字段
                columns = self.db.get_table_columns(conn, table)
                for col in columns:
                    valid_fields.add(col['name'].lower())
        
        self._valid_tables = valid_tables
        self._valid_fields = valid_fields
        return valid_tables, valid_fields
    
    def _extract_sql_fields(self, sql: str) -> List[str]:
        """从 SQL 中提取可能引用的字段名（排除函数名、关键字）"""
        if not sql:
            return []
        
        fields = set()
        
        # SELECT 子句中的字段
        select_match = re.search(r'SELECT\s+(.*?)\s+FROM', sql, re.IGNORECASE | re.DOTALL)
        if select_match:
            select_part = select_match.group(1)
            # 匹配字段名（排除聚合函数和关键字）
            for match in re.finditer(r'(?:[\w_]+\.)?([\w_]+)', select_part):
                fn = match.group(1)
                if not self._is_keyword_or_function(fn):
                    fields.add(fn.lower())
        
        # WHERE 子句中的字段
        where_match = re.search(r'WHERE\s+(.+?)(?:\s+GROUP|\s+ORDER|\s+LIMIT|$)', sql, re.IGNORECASE | re.DOTALL)
        if where_match:
            where_part = where_match.group(1)
            for match in re.finditer(r'(?:[\w_]+\.)?([\w_]+)', where_part):
                fn = match.group(1)
                if not self._is_keyword_or_function(fn):
                    fields.add(fn.lower())
        
        # JOIN ON 中的字段
        for match in re.finditer(r'ON\s+(.+?)(?:\s+(?:LEFT|RIGHT|INNER|JOIN|WHERE|GROUP|ORDER|LIMIT)|$)', sql, re.IGNORECASE | re.DOTALL):
            on_part = match.group(1)
            for m in re.finditer(r'(?:[\w_]+\.)?([\w_]+)', on_part):
                fn = m.group(1)
                if not self._is_keyword_or_function(fn):
                    fields.add(fn.lower())
        
        # GROUP BY 中的字段
        group_match = re.search(r'GROUP\s+BY\s+(.+?)(?:\s+ORDER|\s+LIMIT|$)', sql, re.IGNORECASE | re.DOTALL)
        if group_match:
            for m in re.finditer(r'(?:[\w_]+\.)?([\w_]+)', group_match.group(1)):
                fn = m.group(1)
                if not self._is_keyword_or_function(fn):
                    fields.add(fn.lower())
        
        return list(fields)
    
    def _is_keyword_or_function(self, name: str) -> bool:
        """判断是否为 SQL 关键字或函数名"""
        keywords = {
            'SELECT', 'FROM', 'WHERE', 'AND', 'OR', 'NOT', 'NULL', 'TRUE', 'FALSE',
            'JOIN', 'LEFT', 'RIGHT', 'INNER', 'OUTER', 'ON', 'AS', 'BY', 'GROUP',
            'ORDER', 'HAVING', 'LIMIT', 'OFFSET', 'UNION', 'ALL', 'DISTINCT',
            'COUNT', 'SUM', 'AVG', 'MAX', 'MIN', 'CASE', 'WHEN', 'THEN', 'ELSE', 'END',
            'LIKE', 'IN', 'BETWEEN', 'EXISTS', 'IS', 'ASC', 'DESC', 'WITH'
        }
        return name.upper() in keywords
    
    def _validate_sql_schema(self, sql: str) -> Tuple[float, List[str]]:
        """
        Schema 感知校验：检查 SQL 中的字段是否存在于 营销4.0 中
        
        返回: (valid_score, invalid_fields)
        - valid_score: 1.0 = 全部有效, 0.0 = 全部无效
        """
        valid_tables, valid_fields = self._load_valid_schema()
        sql_fields = self._extract_sql_fields(sql)
        
        if not sql_fields:
            return 1.0, []
        
        invalid_fields = []
        for field in sql_fields:
            # 表名不需要校验为字段
            if field in valid_tables:
                continue
            if field not in valid_fields:
                invalid_fields.append(field)
        
        valid_count = len(sql_fields) - len(invalid_fields)
        valid_score = valid_count / len(sql_fields) if sql_fields else 1.0
        
        return valid_score, invalid_fields
    
    def extract_keywords(self, text: str) -> List[str]:
        """从文本中提取关键词（去除停用词和短词）。委托模块级实现。"""
        return extract_keywords(text)

    def _simple_tokenize(self, text: str) -> List[str]:
        """简单分词（回退方案）。委托模块级实现。"""
        return _simple_tokenize(text)
    
    def _keyword_match_score(self, user_keywords: List[str], question: str) -> float:
        """计算关键词匹配得分"""
        question_lower = question.lower()
        matched_count = sum(1 for kw in user_keywords if kw in question_lower)
        return matched_count / len(user_keywords) if user_keywords else 0.0
    
    def _similarity_score(self, text1: str, text2: str) -> float:
        """计算文本相似度得分"""
        if not text1 or not text2:
            return 0.0
        return SequenceMatcher(None, text1.lower(), text2.lower()).ratio()
    
    def _partial_similarity_score(self, text1: str, text2: str) -> float:
        """计算部分匹配相似度"""
        keywords = self.extract_keywords(text1)
        if not keywords:
            return self._similarity_score(text1, text2)
        question_lower = text2.lower()
        matched = sum(1 for kw in keywords if kw in question_lower)
        return matched / len(keywords)
    
    def retrieve(self, user_question: str, top_k: Optional[int] = None) -> List[Dict]:
        """
        主检索函数：三层检索 + 综合排序 + Schema 感知校验
        """
        k = top_k or self.top_k
        user_keywords = self.extract_keywords(user_question)
        qa_pairs = self._load_all_qa_pairs()
        
        if not qa_pairs:
            return []
        
        # 第一层：精确匹配（关键词）
        keyword_matches = {}
        for qa in qa_pairs:
            kw_score = self._keyword_match_score(user_keywords, qa['question'])
            if kw_score > 0:
                keyword_matches[qa['id']] = {
                    'qa': qa,
                    'keyword_score': kw_score,
                    'match_type': '精确'
                }
        
        # 第二层：相似度匹配
        similarity_matches = {}
        for qa in qa_pairs:
            sim_score = self._similarity_score(user_question, qa['question'])
            partial_score = self._partial_similarity_score(user_question, qa['question'])
            final_sim = max(sim_score, partial_score)
            
            if final_sim > 0.3:
                similarity_matches[qa['id']] = {
                    'qa': qa,
                    'similarity_score': final_sim,
                    'match_type': '相似'
                }
        
        # 第三层：Schema 感知校验 + 合并去重
        all_matches = {}
        for qa_id in set(list(keyword_matches.keys()) + list(similarity_matches.keys())):
            qa = None
            kw_score = 0.0
            sim_score = 0.0
            match_type = ''
            
            if qa_id in keyword_matches:
                qa = keyword_matches[qa_id]['qa']
                kw_score = keyword_matches[qa_id]['keyword_score']
                match_type = '精确'
            
            if qa_id in similarity_matches:
                qa = similarity_matches[qa_id]['qa']
                sim_score = similarity_matches[qa_id]['similarity_score']
                if match_type:
                    match_type = '精确+相似'
                else:
                    match_type = '相似'
            
            # Schema 校验
            schema_score, invalid_fields = self._validate_sql_schema(qa['standard_sql'])
            
            # 综合得分：关键词 0.35 + 相似度 0.35 + schema 有效 0.3
            combined = kw_score * 0.35 + sim_score * 0.35 + schema_score * 0.3
            
            # 如果存在无效字段，显著降低得分
            if invalid_fields:
                combined *= 0.3  # 严重惩罚
            
            all_matches[qa_id] = {
                'id': qa_id,
                'question': qa['question'],
                'standard_sql': qa['standard_sql'],
                'difficulty': qa['difficulty'],
                'keyword_score': round(kw_score, 3),
                'similarity_score': round(sim_score, 3),
                'schema_score': round(schema_score, 3),
                'combined_score': round(combined, 3),
                'match_type': match_type,
                'invalid_fields': invalid_fields
            }
        
        # 按综合得分降序排序
        results = sorted(all_matches.values(), key=lambda x: x['combined_score'], reverse=True)
        
        # 过滤掉综合得分过低（< 0.05）且包含无效字段的结果
        filtered = [r for r in results if not (r['invalid_fields'] and r['combined_score'] < 0.05)]
        
        return filtered[:k]
    
    def retrieve_error_records(self, user_question: str, limit: int = 3) -> List[Dict]:
        """检索错题集中与当前问题相似的错误记录（未解决的）"""
        records = self._load_error_records()
        if not records:
            return []
        
        user_keywords = self.extract_keywords(user_question)
        scored = []
        
        for record in records:
            question = record.get('business_question', '')
            # 问题相似度
            q_sim = self._similarity_score(user_question, question)
            # 关键词匹配
            kw_match = self._keyword_match_score(user_keywords, question)
            # 综合得分：相似度 60% + 关键词 40%
            score = q_sim * 0.6 + kw_match * 0.4
            
            if score > 0.1:
                scored.append({
                    'id': record['id'],
                    'question': question,
                    'wrong_sql': record.get('generated_sql', ''),
                    'correct_sql': record.get('correct_sql', ''),
                    'error_type': record.get('error_type', ''),
                    'reason': record.get('error_detail', ''),
                    'score': round(score, 3)
                })
        
        scored.sort(key=lambda x: x['score'], reverse=True)
        return scored[:limit]
    
    def _load_error_records(self, limit: int = 200) -> List[Dict]:
        """从 governance.db 加载未解决的错题记录（correct_sql 或 error_detail 有内容的）"""
        with self.db.connect_governance() as conn:
            cursor = conn.execute(
                'SELECT id, business_question, generated_sql, correct_sql, error_type, error_detail '
                'FROM error_records '
                'WHERE is_resolved = 0 '
                '  AND (correct_sql IS NOT NULL AND correct_sql != "" '
                '       OR error_detail IS NOT NULL AND error_detail != "") '
                'ORDER BY frequency DESC, last_occurred DESC '
                'LIMIT ?',
                (limit,)
            )
            rows = cursor.fetchall()
        
        records = []
        for row in rows:
            correct_sql = row[3] or ''
            error_detail = row[5] or ''
            # 如果 correct_sql 为空，尝试从 error_detail 中提取 SQL 片段作为 correct_sql
            if not correct_sql and error_detail:
                # 尝试提取 LIKE 表达式或 WHERE 条件
                sql_match = re.search(r"([a-zA-Z_][a-zA-Z0-9_]*\s+LIKE\s+'[^']+'|WHERE\s+.*|SELECT\s+.*)", error_detail, re.IGNORECASE)
                if sql_match:
                    correct_sql = sql_match.group(0)
                else:
                    correct_sql = error_detail  # 退而求其次，使用整个 error_detail
            records.append({
                'id': row[0],
                'business_question': row[1] or '',
                'generated_sql': row[2] or '',
                'correct_sql': correct_sql,
                'error_type': row[4] or '',
                'error_detail': error_detail
            })
        return records
    
    def _load_all_qa_pairs(self) -> List[Dict]:
        """从治理库加载可用问答对（可用性门禁为 is_usable 单列，2026-08-14 起 question_rating 列已下线）"""
        with self.db.connect_governance() as conn:
            cursor = conn.execute(
                """
                SELECT id, question, standard_sql, difficulty, source, tags
                FROM qa_pairs
                WHERE standard_sql IS NOT NULL AND standard_sql != ''
                  AND (is_usable IS NULL OR is_usable = 1)
                """
            )
            rows = cursor.fetchall()
        
        return [{
            'id': row[0],
            'question': row[1] or '',
            'standard_sql': row[2] or '',
            'difficulty': row[3] or '未知',
            'source': row[4] or '',
            'tags': row[5] or ''
        } for row in rows]
    
    # ==================== 多路 RAG 检索：Schema + 模式 ====================
    
    def retrieve_schema_docs(self, user_question: str, tables: List[str] = None,
                             top_k_tables: int = 5, top_k_columns: int = 15,
                             top_k_relationships: int = 5) -> Dict[str, List[Dict]]:
        """检索 Schema 知识库"""
        from core.schema_kb import SchemaKnowledgeBase
        kb = SchemaKnowledgeBase()
        return kb.retrieve(
            user_question=user_question,
            tables=tables,
            top_k_tables=top_k_tables,
            top_k_columns=top_k_columns,
            top_k_relationships=top_k_relationships,
            top_k_patterns=0
        )
    
    def retrieve_sql_patterns(self, user_question: str, limit: int = 5) -> List[Dict]:
        """检索 SQL 模式"""
        from core.schema_kb import SchemaKnowledgeBase
        kb = SchemaKnowledgeBase()
        return kb.retrieve_sql_patterns(self.extract_keywords(user_question), limit=limit)
    
    def retrieve_all(self, user_question: str, tables: List[str] = None,
                     qa_top_k: int = None, error_limit: int = 3,
                     schema_top_k_tables: int = 5, schema_top_k_columns: int = 15,
                     schema_top_k_relationships: int = 5, pattern_limit: int = 5) -> Dict:
        """
        统一多路检索接口
        
        返回：
        {
            'qa_pairs': [...],
            'error_records': [...],
            'schema': {
                'tables': [...],
                'columns': [...],
                'relationships': [...]
            },
            'patterns': [...],
            'keywords': [...]
        }
        """
        k = qa_top_k or self.top_k
        keywords = self.extract_keywords(user_question)
        
        return {
            'qa_pairs': self.retrieve(user_question, top_k=k),
            'error_records': self.retrieve_error_records(user_question, limit=error_limit),
            'schema': self.retrieve_schema_docs(
                user_question, tables=tables,
                top_k_tables=schema_top_k_tables,
                top_k_columns=schema_top_k_columns,
                top_k_relationships=schema_top_k_relationships
            ),
            'patterns': self.retrieve_sql_patterns(user_question, limit=pattern_limit),
            'keywords': keywords
        }

    # ==================== 码值维度检索 ====================

    def _load_code_value_index(self):
        """加载码值库到内存（惰性）：域元数据、明细、维度→表字段映射。
        R7 热生效：模块级世代号 `_CV_INDEX_GEN` 在码值表 CRUD 后 bump，
        实例下次加载时世代不一致则重建（码值改动免重启）。"""
        if getattr(self, '_cv_ready', False) and getattr(self, '_cv_gen', -1) == _CV_INDEX_GEN:
            return
        self._cv_domains = {}   # code_name -> {cn_name, domain}
        self._cv_items = {}     # code_name -> [(item_code, item_name)]
        self._cv_col_map = {}   # code_name -> [(table, column)]
        try:
            with self.db.connect_governance() as conn:
                for code_name, cn_name, domain in conn.execute(
                        'SELECT code_name, code_cn_name, COALESCE(domain_l2, domain_l1) AS domain FROM code_values'):
                    self._cv_domains[code_name] = {'cn_name': cn_name or '', 'domain': domain or ''}
                for code_name, item_code, item_name in conn.execute(
                        'SELECT code_name, item_code, item_name FROM code_value_items ORDER BY code_name, sort_order'):
                    if item_name:
                        self._cv_items.setdefault(code_name, []).append((str(item_code or ''), str(item_name)))
            # 维度与数据表间关系：列名 ∩ 码值域名
            try:
                from core.schema_preloader import SchemaPreloader
                preloader = SchemaPreloader.get_instance()
                for t in preloader.get_table_names():
                    for c in preloader.get_columns(t):
                        if c['name'] in self._cv_domains:
                            self._cv_col_map.setdefault(c['name'], []).append((t, c['name']))
            except Exception as e:
                print(f"[WARN] 构建码值-表字段映射失败: {e}")
            # 列存储形态（名称/编码/混合，由码值校核写入）
            self._cv_form_map = {}
            try:
                with self.db.connect_governance() as conn:
                    for t, c, form in conn.execute(
                            'SELECT table_name, column_name, form FROM code_value_column_form'):
                        self._cv_form_map[(t, c)] = form
            except Exception as e:
                print(f"[WARN] 加载列存储形态失败（需先运行码值校核）: {e}")
            # 补充映射：码值域与存储列名不一致（经实际数据核实）
            supplement = {
                # 行业分类门类值存于 _1 后缀列
                'cust_ind_cls_desc': [('dim_cst_cust', 'cust_ind_cls_desc_1'),
                                      ('dim_cst_elec_cons_cust', 'cust_ind_cls_desc_1')],
                # 行业分类_电价 的名称列（prc_ind_cls_desc_1 已统一更名为 prc_ind_cls_desc）
                'prc_ind_cls': [('dwd_cst_addl_charg', 'prc_ind_cls_desc'),
                                ('dwd_cst_sgmt_qty_charg', 'prc_ind_cls_desc'),
                                ('dwd_cst_special_expense', 'prc_ind_cls_desc')],
                # 市场化属性描述
                'dereg_attr_cls': [('dim_cst_elec_cons_cust', 'dereg_attr_cls_desc')],
                # 行政区划代码域的名称列（地市/区县/供电所）
                'city_code': [('dim_cst_mgt_org', 'city_name')],
                'county_code': [('dim_cst_mgt_org', 'county_name')],
                'station_code': [('dim_cst_mgt_org', 'station_name')],
                # 线路公专标志的描述列
                'line_publ_clg_flag': [('dim_cst_pipeline', 'line_publ_clg_flag_desc')],
            }
            for code_name, cols in supplement.items():
                if code_name in self._cv_domains:
                    bucket = self._cv_col_map.setdefault(code_name, [])
                    for tc in cols:
                        if tc not in bucket:
                            bucket.append(tc)
        except Exception as e:
            print(f"[WARN] 加载码值库失败: {e}")
        self._cv_ready = True
        self._cv_gen = _CV_INDEX_GEN

    def retrieve_code_values(self, question: str, tables: List[str],
                             per_domain: int = 8, max_domains: int = 8) -> List[Dict]:
        """按语义检索问题相关的码值维度（委托模块级 match_code_value_index）。

        通道一（问题→值）：问题中出现的码值名称（如"e户通代扣"）直接命中其域；
        通道二（表→值域）：定位表中的码值列（如 valid_flag_desc）带上其值域。
        返回: [{code_name, cn_name, columns, values, matched, form, pairs, total}]
        """
        self._load_code_value_index()
        return match_code_value_index(
            self._cv_domains, self._cv_items, self._cv_col_map, self._cv_form_map,
            question, tables, per_domain=per_domain, max_domains=max_domains,
            tokenize=self.extract_keywords)

    def get_code_value_translations(self) -> List[Tuple[str, str, Dict[str, str]]]:
        """存编码列的 名称→编码 翻译表：[(table, column, {item_name: item_code})]。
        供 SQL 后处理把中文描述条件值机械翻译成编码。"""
        self._load_code_value_index()
        # 反查 (table, column) -> code_name
        col_to_domain = {}
        for code_name, cols in self._cv_col_map.items():
            for tc in cols:
                col_to_domain.setdefault(tc, code_name)
        result = []
        for (t, c), form in self._cv_form_map.items():
            if form != '编码':
                continue
            dn = col_to_domain.get((t, c))
            if not dn:
                continue
            n2c = {name: code for code, name in self._cv_items.get(dn, []) if name and code}
            if n2c:
                result.append((t, c, n2c))
        return result


def test_retriever():
    """测试 RAG 检索器"""
    retriever = RAGRetriever(top_k=5)
    
    test_questions = [
        "查询所有客户的名称和编号",
        "统计高压客户的用电量",
        "某管理单位下所有客户的电量数据",
    ]
    
    for q in test_questions:
        print(f"\n{'='*60}")
        print(f"问题: {q}")
        print(f"{'='*60}")
        
        results = retriever.retrieve(q)
        print(f"关键词: {retriever.extract_keywords(q)}")
        print(f"检索结果 ({len(results)} 条):")
        for r in results:
            flag = " [含无效字段!]" if r.get('invalid_fields') else ""
            print(f"  [{r['match_type']}] 综合得分={r['combined_score']} | Schema={r['schema_score']}{flag}")
            if r.get('invalid_fields'):
                print(f"    无效字段: {r['invalid_fields']}")
            print(f"  Q: {r['question'][:50]}...")
            print(f"  SQL: {r['standard_sql'][:80]}...")
            print()


if __name__ == '__main__':
    test_retriever()
