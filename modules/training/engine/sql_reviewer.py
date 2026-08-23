"""SQL 审查器：检查生成的 SQL 是否存在错题集中的典型错误"""
import re
import json
from typing import Dict, List, Optional, Tuple
import config
from core.database import DatabaseManager


# ========== sql-lint 规则集成 ==========
# SQL 保留关键字（简化版）
SQL_RESERVED_KEYWORDS = {
    'SELECT', 'FROM', 'WHERE', 'INSERT', 'UPDATE', 'DELETE', 'CREATE', 'DROP',
    'ALTER', 'TABLE', 'INDEX', 'VIEW', 'JOIN', 'LEFT', 'RIGHT', 'INNER', 'OUTER',
    'ON', 'AND', 'OR', 'NOT', 'NULL', 'TRUE', 'FALSE', 'AS', 'BY', 'GROUP',
    'ORDER', 'HAVING', 'LIMIT', 'OFFSET', 'UNION', 'ALL', 'DISTINCT', 'COUNT',
    'SUM', 'AVG', 'MAX', 'MIN', 'CASE', 'WHEN', 'THEN', 'ELSE', 'END', 'IF',
    'EXISTS', 'IN', 'BETWEEN', 'LIKE', 'IS', 'ASC', 'DESC', 'WITH'
}

# ========== security-classifier 规则集成 ==========
# 敏感字段模式（字段名正则匹配）
SENSITIVE_FIELD_PATTERNS = [
    # L4 级：身份证号
    {'pattern': r'(?i)(id_card|idcard|identity|sfz|身份证号)', 'level': 'L4', 'name': '身份证号'},
    # L4 级：银行卡号
    {'pattern': r'(?i)(bank_card|bankcard|card_no|yhk|银行卡号)', 'level': 'L4', 'name': '银行卡号'},
    # L3 级：手机号
    {'pattern': r'(?i)(phone|mobile|tel|cell|手机号|电话)', 'level': 'L3', 'name': '手机号'},
    # L3 级：姓名
    {'pattern': r'(?i)(name|姓名|cust_name|user_name|contact_name)', 'level': 'L3', 'name': '姓名'},
    # L3 级：地址
    {'pattern': r'(?i)(address|addr|地址|location)', 'level': 'L3', 'name': '地址'},
    # L4 级：密码/密钥
    {'pattern': r'(?i)(password|pwd|passwd|secret|token|密钥|密码)', 'level': 'L4', 'name': '密码/密钥'},
    # L3 级：金额/余额
    {'pattern': r'(?i)(amount|balance|amt|money|金额|余额|收入)', 'level': 'L3', 'name': '金额/余额'},
]


class SQLReviewer:
    """
    SQL 审查器（Phase 9 增强版）
    
    四层审查：
    1. L1 语法审查：SQL 语法正确性、禁止危险操作
    2. L2 错题集审查：是否与历史错误模式相似
    3. L3 规范审查：sql-lint 规则（命名、SELECT *、LIKE模糊、保留字）
    4. L4 安全审查：security-classifier 敏感字段暴露检查
    """
    
    def __init__(self):
        self.db = DatabaseManager()
    
    def review(self, sql: str, tables_involved: List[str] = None) -> Dict:
        """
        审查 SQL（执行前审查）
        
        四层审查：L1 语法 + L2 错题集模式 + L3 sql-lint 规范 + L4 安全
        
        返回:
            {
                'passed': True/False,
                'level': 'passed'/'warning'/'failed',
                'issues': [...],
                'action': 'pass'/'warn'/'regenerate'
            }
        """
        all_issues = []
        
        # L1: 语法审查
        l1_issues = self._check_syntax(sql)
        all_issues.extend(l1_issues)
        
        # L2: 错题集审查
        l2_issues = self._check_error_patterns(sql)
        all_issues.extend(l2_issues)
        
        # L3: 规范审查（sql-lint 集成）
        l3_issues = self._check_schema_compliance(sql, tables_involved)
        all_issues.extend(l3_issues)
        
        # L4: 安全审查（security-classifier 集成）
        l4_issues = self._check_security(sql, tables_involved)
        all_issues.extend(l4_issues)
        
        # 综合判断
        errors = [i for i in all_issues if i['level'] == 'error']
        warnings = [i for i in all_issues if i['level'] == 'warning']
        
        if errors:
            return {
                'passed': False,
                'level': 'failed',
                'issues': all_issues,
                'action': 'regenerate',
                'message': errors[0]['message']
            }
        
        if warnings:
            # 检查是否有高危警告（相似度 > 0.8）
            high_risk = [w for w in warnings if w.get('similarity', 0) > 0.8]
            if high_risk:
                return {
                    'passed': False,
                    'level': 'warning',
                    'issues': all_issues,
                    'action': 'regenerate',
                    'message': high_risk[0]['message']
                }
            
            return {
                'passed': True,
                'level': 'warning',
                'issues': all_issues,
                'action': 'warn',
                'message': warnings[0]['message']
            }
        
        return {
            'passed': True,
            'level': 'passed',
            'issues': [],
            'action': 'pass',
            'message': '审查通过'
        }
    
    def audit_post_execution(self, sql: str, user_question: str, tables_involved: List[str] = None, execution_error: str = None, row_count: int = 0) -> Dict:
        """
        执行后审计（深度审计）：执行结果为空或报错时触发
        
        重点：
        1. 检索错题集中相似的历史错误
        2. 分析 SQL 结构是否可能导致空结果
        3. 给出具体修复建议
        
        返回:
            {
                'triggered': True/False,
                'reason': 'empty_result'/'execution_error'/'pattern_match',
                'issues': [...],
                'suggestions': [...],
                'matched_errors': [...]
            }
        """
        result = {
            'triggered': False,
            'reason': '',
            'issues': [],
            'suggestions': [],
            'matched_errors': []
        }
        
        # 确定触发原因
        if execution_error:
            result['triggered'] = True
            result['reason'] = 'execution_error'
            result['issues'].append({
                'level': 'error',
                'type': '执行错误',
                'message': f'SQL 执行失败: {execution_error}',
                'source': 'L1_执行'
            })
        elif row_count == 0:
            result['triggered'] = True
            result['reason'] = 'empty_result'
            result['issues'].append({
                'level': 'warning',
                'type': '空结果',
                'message': 'SQL 执行返回 0 条记录，可能存在以下问题：表选择错误、字段不匹配、WHERE 条件过严、JOIN 条件错误',
                'source': 'L2_结果分析'
            })
        
        if not result['triggered']:
            return result
        
        # === 深度错题集检索 ===
        matched_errors = self._find_similar_errors(sql, user_question, tables_involved)
        result['matched_errors'] = matched_errors
        
        if matched_errors:
            for err in matched_errors:
                result['issues'].append({
                    'level': 'warning',
                    'type': '错题集匹配',
                    'message': f'历史错题 #{err["id"]} 提示：{err["message"]}',
                    'similarity': err.get('similarity', 0.5),
                    'source': 'L2_错题集'
                })
                if err.get('suggestion'):
                    result['suggestions'].append(err['suggestion'])
        
        # === 空结果专项分析 ===
        if row_count == 0:
            empty_analysis = self._analyze_empty_result(sql, tables_involved)
            result['issues'].extend(empty_analysis.get('issues', []))
            result['suggestions'].extend(empty_analysis.get('suggestions', []))
        
        return result
    
    def _find_similar_errors(self, sql: str, user_question: str, tables_involved: List[str] = None) -> List[Dict]:
        """检索错题集中与当前 SQL 相似的记录"""
        error_records = self._load_error_records(limit=50)
        if not error_records:
            return []
        
        matched = []
        sql_lower = sql.lower()
        tables_in_sql = set(self._extract_tables(sql))
        fields_in_sql = set(self._extract_fields(sql))
        
        for record in error_records:
            score = 0
            reasons = []
            suggestion = None
            
            record_sql = (record.get('generated_sql') or '').lower()
            record_question = (record.get('business_question') or '').lower()
            
            # 1. 表名重叠度
            record_tables = set(self._extract_tables(record_sql))
            common_tables = tables_in_sql & record_tables
            if common_tables:
                score += len(common_tables) * 0.2
                reasons.append(f'相同表: {", ".join(common_tables)}')
            
            # 2. 字段重叠度
            record_fields = set(self._extract_fields(record_sql))
            common_fields = fields_in_sql & record_fields
            if common_fields:
                score += len(common_fields) * 0.15
                reasons.append(f'相同字段: {", ".join(common_fields)}')
            
            # 3. 问题相似度（关键词匹配）
            user_keywords = set(self._extract_keywords(user_question))
            record_keywords = set(self._extract_keywords(record_question))
            common_keywords = user_keywords & record_keywords
            if common_keywords:
                score += len(common_keywords) * 0.1
            
            # 4. SQL 结构相似度
            if record_sql and sql_lower:
                # 简化 SQL（去掉 WHERE 值）后比较
                simplified_sql = re.sub(r"=\s*['\"][^'\"]*['\"]", "= ?", sql_lower)
                simplified_record = re.sub(r"=\s*['\"][^'\"]*['\"]", "= ?", record_sql)
                if simplified_sql == simplified_record:
                    score += 0.5
                    reasons.append('SQL 结构高度相似')
            
            # 阈值：相似度 > 0.3 才认为匹配
            if score > 0.3:
                error_type = record.get('error_type', '未知')
                error_detail = record.get('error_detail', '')
                
                # 根据错误类型生成建议
                if error_type == '表选择错误':
                    suggestion = f'建议检查表选择是否正确。历史错误: {error_detail[:80]}'
                elif error_type == '关联条件错误':
                    suggestion = f'建议检查 JOIN ON 条件。历史错误: {error_detail[:80]}'
                elif error_type == '字段选择错误':
                    suggestion = f'建议检查字段名是否正确。历史错误: {error_detail[:80]}'
                elif error_type == 'WHERE条件错误':
                    suggestion = f'建议检查 WHERE 条件。历史错误: {error_detail[:80]}'
                else:
                    suggestion = f'历史类似错误: {error_detail[:80]}'
                
                matched.append({
                    'id': record['id'],
                    'score': round(score, 2),
                    'error_type': error_type,
                    'message': f'[{error_type}] {error_detail[:100]}',
                    'suggestion': suggestion,
                    'similarity': score,
                    'reasons': reasons
                })
        
        # 按相似度排序，取前 3
        matched.sort(key=lambda x: x['score'], reverse=True)
        return matched[:3]
    
    def _analyze_empty_result(self, sql: str, tables_involved: List[str] = None) -> Dict:
        """分析 SQL 为什么返回空结果"""
        result = {'issues': [], 'suggestions': []}
        sql_lower = sql.lower()
        
        # 1. 检查 WHERE 条件是否过严
        where_match = re.search(r'where\s+(.+?)(?:\s+group|\s+order|\s+limit|$)', sql_lower)
        if where_match:
            where_clause = where_match.group(1)
            
            # 检查是否有精确匹配（=）而非 LIKE
            exact_matches = re.findall(r'\b(\w+)\s*=\s*["\']?([^"\',\s)]+)["\']?', where_clause)
            if exact_matches:
                for field, value in exact_matches:
                    result['issues'].append({
                        'level': 'warning',
                        'type': 'WHERE条件',
                        'message': f'WHERE 使用精确匹配 {field} = {value}，如果值不存在则返回空',
                        'source': 'L2_空结果分析'
                    })
                    result['suggestions'].append(f'建议确认 {field} = "{value}" 的值是否存在于数据库中')
            
            # 检查 LIKE 是否使用了全匹配
            like_matches = re.findall(r'\b(\w+)\s+like\s+["\']([^"\']+)["\']', where_clause, re.IGNORECASE)
            for field, pattern in like_matches:
                if not pattern.startswith('%') and not pattern.endswith('%'):
                    result['issues'].append({
                        'level': 'warning',
                        'type': 'WHERE条件',
                        'message': f'LIKE 模式 "{pattern}" 不包含通配符 %，等价于精确匹配',
                        'source': 'L2_空结果分析'
                    })
                    result['suggestions'].append(f'建议 LIKE 使用通配符: LIKE "%{pattern}%" 或 LIKE "{pattern}%"')
        
        # 2. 检查 JOIN 条件
        join_count = len(re.findall(r'\bjoin\b', sql_lower))
        if join_count > 0:
            result['issues'].append({
                'level': 'warning',
                'type': 'JOIN分析',
                'message': f'SQL 包含 {join_count} 个 JOIN，JOIN 条件错误是空结果的常见原因',
                'source': 'L2_空结果分析'
            })
            result['suggestions'].append('建议检查 JOIN ON 条件中的字段映射是否正确')
        
        # 3. 检查是否有 GROUP BY + HAVING
        having_match = re.search(r'\bhaving\b', sql_lower)
        if having_match:
            result['issues'].append({
                'level': 'warning',
                'type': 'HAVING分析',
                'message': 'HAVING 条件过滤可能导致结果为空',
                'source': 'L2_空结果分析'
            })
            result['suggestions'].append('建议先去掉 HAVING 验证是否有聚合结果')
        
        return result
    
    def _extract_keywords(self, text: str) -> List[str]:
        """提取问题中的关键词（用于相似度匹配）"""
        if not text:
            return []
        # 去掉停用词
        stop_words = {'的', '了', '是', '在', '有', '和', '与', '或', '为', '对', '从', '到', '及', '等',
                      '这', '那', '中', '上', '下', '查询', '统计', '获取', '查找', '列出', '所有'}
        words = re.findall(r'[\u4e00-\u9fff]{2,}|[a-zA-Z_]+', text)
        return [w for w in words if w.lower() not in stop_words and len(w) >= 2]
        """
        审查 SQL
        
        返回:
            {
                'passed': True/False,
                'level': 'passed'/'warning'/'failed',
                'issues': [...],
                'action': 'pass'/'warn'/'regenerate'
            }
        """
        all_issues = []
        
        # L1: 语法审查
        l1_issues = self._check_syntax(sql)
        all_issues.extend(l1_issues)
        
        # L2: 错题集审查
        l2_issues = self._check_error_patterns(sql)
        all_issues.extend(l2_issues)
        
        # L3: 规范审查（sql-lint 集成）
        l3_issues = self._check_schema_compliance(sql, tables_involved)
        all_issues.extend(l3_issues)
        
        # L4: 安全审查（security-classifier 集成）
        l4_issues = self._check_security(sql, tables_involved)
        all_issues.extend(l4_issues)
        
        # 综合判断
        errors = [i for i in all_issues if i['level'] == 'error']
        warnings = [i for i in all_issues if i['level'] == 'warning']
        
        if errors:
            return {
                'passed': False,
                'level': 'failed',
                'issues': all_issues,
                'action': 'regenerate',
                'message': errors[0]['message']
            }
        
        if warnings:
            # 检查是否有高危警告（相似度 > 0.8）
            high_risk = [w for w in warnings if w.get('similarity', 0) > 0.8]
            if high_risk:
                return {
                    'passed': False,
                    'level': 'warning',
                    'issues': all_issues,
                    'action': 'regenerate',
                    'message': high_risk[0]['message']
                }
            
            return {
                'passed': True,
                'level': 'warning',
                'issues': all_issues,
                'action': 'warn',
                'message': warnings[0]['message']
            }
        
        return {
            'passed': True,
            'level': 'passed',
            'issues': [],
            'action': 'pass',
            'message': '审查通过'
        }
    
    # ========== L1: 语法审查 ==========
    
    def _check_syntax(self, sql: str) -> List[Dict]:
        """L1: 语法审查"""
        issues = []
        sql_upper = sql.upper().strip()
        
        # 检查是否以 SELECT 开头
        dialect = getattr(config, 'DB_TYPE', 'sqlite').lower()
        allowed_prefixes = ['SELECT', 'WITH'] if dialect == 'mysql' else ['SELECT', 'WITH', 'PRAGMA']
        if not any(sql_upper.startswith(prefix) for prefix in allowed_prefixes):
            issues.append({
                'level': 'error',
                'type': '语法错误',
                'message': f'SQL 必须以 {" / ".join(allowed_prefixes)} 开头',
                'similarity': 1.0,
                'source': 'L1_语法'
            })
        
        # 检查危险操作
        dangerous = ['DROP ', 'DELETE ', 'UPDATE ', 'INSERT ', 'ALTER ', 'CREATE ', 'TRUNCATE ']
        for op in dangerous:
            if op.upper() in sql_upper:
                issues.append({
                    'level': 'error',
                    'type': '安全违规',
                    'message': f'SQL 包含危险操作: {op.strip()}',
                    'similarity': 1.0,
                    'source': 'L1_语法'
                })
        
        return issues
    
    # ========== L2: 错题集审查 ==========
    
    def _check_error_patterns(self, sql: str) -> List[Dict]:
        """L2: 错题集模式检查"""
        issues = []
        
        # 加载错题集
        error_records = self._load_error_records()
        if not error_records:
            return issues
        
        tables_in_sql = self._extract_tables(sql)
        fields_in_sql = self._extract_fields(sql)
        joins_in_sql = self._extract_joins(sql)
        
        for record in error_records:
            error_type = record.get('error_type', '')
            error_sql = record.get('generated_sql', '')
            error_detail = record.get('error_detail', '')
            # 跳过占位/测试垃圾记录（如 'SELECT 1'/'SELECT 2'），避免误匹配全库
            if len((error_sql or '').strip()) < 20:
                continue
            
            # 规则1：表选择错误
            if error_type == '表选择错误':
                wrong_table = self._extract_wrong_table(error_detail, error_sql)
                if wrong_table and wrong_table in tables_in_sql:
                    issues.append({
                        'level': 'warning',
                        'type': '表选择错误',
                        'message': f'历史错题 #{record["id"]} 提示：该表可能存在选择问题 - {error_detail[:100]}',
                        'similarity': 0.85,
                        'source': 'L2_错题集'
                    })
            
            # 规则2：关联条件错误
            elif error_type == '关联条件错误':
                wrong_joins = self._extract_joins(error_sql)
                for wj in wrong_joins:
                    for sj in joins_in_sql:
                        if (wj['from_table'] == sj['from_table'] and 
                            wj['to_table'] == sj['to_table'] and
                            wj['on_condition'] != sj['on_condition']):
                            issues.append({
                                'level': 'warning',
                                'type': '关联条件错误',
                                'message': f'历史错题 #{record["id"]} 提示：ON 条件可能不正确 - {error_detail[:100]}',
                                'similarity': 0.80,
                                'source': 'L2_错题集'
                            })
            
            # 规则3：字段选择错误
            elif error_type == '字段选择错误':
                wrong_field = self._extract_field_from_error(error_detail)
                if wrong_field and wrong_field in fields_in_sql:
                    issues.append({
                        'level': 'warning',
                        'type': '字段选择错误',
                        'message': f'历史错题 #{record["id"]} 提示：字段 {wrong_field} 可能选择不当 - {error_detail[:100]}',
                        'similarity': 0.75,
                        'source': 'L2_错题集'
                    })
            
            # 规则4：WHERE 条件错误
            elif error_type == 'WHERE条件错误':
                where_conditions = self._extract_where_conditions(sql)
                error_conditions = self._extract_where_conditions(error_sql)
                
                for wc in where_conditions:
                    for ec in error_conditions:
                        if wc['field'] == ec['field'] and wc['operator'] == ec['operator']:
                            issues.append({
                                'level': 'warning',
                                'type': 'WHERE条件错误',
                                'message': f'历史错题 #{record["id"]} 提示：WHERE 条件 {wc["field"]} 可能有问题 - {error_detail[:100]}',
                                'similarity': 0.70,
                                'source': 'L2_错题集'
                            })
        
        return issues
    
    # ========== L3: 规范审查（sql-lint 集成） ==========
    
    def _check_schema_compliance(self, sql: str, tables_involved: List[str] = None) -> List[Dict]:
        """L3: Schema 规范审查 + sql-lint 规则"""
        issues = []
        
        # --- sql-lint LINT_001: no_select_star ---
        if re.search(r'SELECT\s+\*', sql, re.IGNORECASE):
            issues.append({
                'level': 'warning',
                'type': '规范问题',
                'message': 'LINT_001: 建议使用显式字段名，避免 SELECT *',
                'similarity': 0.5,
                'source': 'L3_sql-lint'
            })
        
        # --- sql-lint LINT_011: like_left_wildcard ---
        if re.search(r"LIKE\s+['\"]?%", sql, re.IGNORECASE):
            issues.append({
                'level': 'warning',
                'type': '性能问题',
                'message': 'LINT_011: LIKE 使用左模糊匹配 %xxx，无法利用索引',
                'similarity': 0.6,
                'source': 'L3_sql-lint'
            })
        
        # --- sql-lint LINT_015: reserved_keyword ---
        # 检查 SELECT 后的字段名是否是保留关键字
        fields = self._extract_fields(sql)
        for field in fields:
            if field.upper() in SQL_RESERVED_KEYWORDS:
                issues.append({
                    'level': 'warning',
                    'type': '命名规范',
                    'message': f'LINT_015: 字段名 "{field}" 是 SQL 保留关键字，建议修改',
                    'similarity': 0.7,
                    'source': 'L3_sql-lint'
                })
        
        # --- 原有规范检查 ---
        # 检查聚合函数是否有 GROUP BY
        if re.search(r'(SUM|COUNT|AVG|MAX|MIN)\s*\(', sql, re.IGNORECASE):
            if not re.search(r'GROUP\s+BY', sql, re.IGNORECASE):
                issues.append({
                    'level': 'warning',
                    'type': '规范问题',
                    'message': '使用聚合函数但缺少 GROUP BY',
                    'similarity': 0.6,
                    'source': 'L3_规范'
                })
        
        # 检查 ORDER BY 配合 LIMIT
        if re.search(r'LIMIT\s+\d+', sql, re.IGNORECASE):
            if not re.search(r'ORDER\s+BY', sql, re.IGNORECASE):
                issues.append({
                    'level': 'warning',
                    'type': '规范问题',
                    'message': '使用 LIMIT 但缺少 ORDER BY，结果可能不稳定',
                    'similarity': 0.5,
                    'source': 'L3_规范'
                })
        
        return issues
    
    # ========== L4: 安全审查（security-classifier 集成） ==========
    
    def _check_security(self, sql: str, tables_involved: List[str] = None) -> List[Dict]:
        """L4: 安全审查 - 检查敏感字段暴露"""
        issues = []
        
        # 提取 SQL 中涉及的所有字段
        all_fields = self._extract_all_field_references(sql)
        
        for field_info in all_fields:
            field_name = field_info['name']
            context = field_info['context']  # 'select' or 'where'
            
            for pattern in SENSITIVE_FIELD_PATTERNS:
                if re.search(pattern['pattern'], field_name):
                    level = pattern['level']
                    name = pattern['name']
                    
                    if level == 'L4':
                        # L4 级敏感字段：警告级
                        issues.append({
                            'level': 'warning',
                            'type': '安全审查',
                            'message': f'SECURITY: SQL 包含 L4 级敏感字段 "{field_name}"（{name}），请确认访问授权',
                            'similarity': 0.9,
                            'source': 'L4_security-classifier'
                        })
                    elif level == 'L3' and context == 'select':
                        # L3 级敏感字段在 SELECT 中：提示级
                        issues.append({
                            'level': 'warning',
                            'type': '安全审查',
                            'message': f'SECURITY: SQL 返回 L3 级敏感字段 "{field_name}"（{name}），建议脱敏展示',
                            'similarity': 0.7,
                            'source': 'L4_security-classifier'
                        })
        
        return issues
    
    def _is_keyword_or_function(self, name: str) -> bool:
        """判断是否为 SQL 关键字或函数名"""
        return name.upper() in SQL_RESERVED_KEYWORDS
    
    def _load_error_records(self, limit: int = 100) -> List[Dict]:
        """加载最近的错题记录"""
        with self.db.connect_governance() as conn:
            cursor = conn.execute(
                'SELECT id, error_type, error_detail, generated_sql, business_question '
                'FROM error_records '
                'WHERE is_resolved = 0 '
                'ORDER BY frequency DESC, last_occurred DESC '
                'LIMIT ?',
                (limit,)
            )
            rows = cursor.fetchall()
        
        return [{
            'id': row[0],
            'error_type': row[1],
            'error_detail': row[2] or '',
            'generated_sql': row[3] or '',
            'business_question': row[4] or ''
        } for row in rows]
    
    def _extract_tables(self, sql: str) -> List[str]:
        """从 SQL 中提取表名"""
        tables = set()
        for match in re.finditer(r'FROM\s+(\w+)', sql, re.IGNORECASE):
            tables.add(match.group(1))
        for match in re.finditer(r'JOIN\s+(\w+)', sql, re.IGNORECASE):
            tables.add(match.group(1))
        return list(tables)
    
    def _extract_fields(self, sql: str) -> List[str]:
        """从 SQL 中提取字段名（仅 SELECT 子句），排除 AS 等关键字"""
        fields = set()
        select_match = re.search(r'SELECT\s+(.*?)\s+FROM', sql, re.IGNORECASE | re.DOTALL)
        if select_match:
            select_part = select_match.group(1)
            for match in re.finditer(r'(?:[\w_]+\.)?([\w_]+)', select_part):
                fn = match.group(1)
                if fn.upper() != 'AS' and not self._is_keyword_or_function(fn):
                    fields.add(fn)
        return list(fields)
    
    def _extract_all_field_references(self, sql: str) -> List[Dict]:
        """从 SQL 中提取所有字段引用（含上下文：select/where/join），排除 AS 等关键字"""
        fields = []
        
        # SELECT 子句中的字段
        select_match = re.search(r'SELECT\s+(.*?)\s+FROM', sql, re.IGNORECASE | re.DOTALL)
        if select_match:
            select_part = select_match.group(1)
            for match in re.finditer(r'(?:[\w_]+\.)?([\w_]+)', select_part):
                fn = match.group(1)
                if fn.upper() != 'AS' and not self._is_keyword_or_function(fn):
                    fields.append({'name': fn, 'context': 'select'})
        
        # WHERE 子句中的字段
        where_match = re.search(r'WHERE\s+(.+?)(?:\s+GROUP|\s+ORDER|\s+LIMIT|$)', sql, re.IGNORECASE | re.DOTALL)
        if where_match:
            where_part = where_match.group(1)
            for match in re.finditer(r'(?:[\w_]+\.)?([\w_]+)', where_part):
                fn = match.group(1)
                if fn.upper() != 'AS' and not self._is_keyword_or_function(fn):
                    fields.append({'name': fn, 'context': 'where'})
        
        return fields
    
    def _extract_joins(self, sql: str) -> List[Dict]:
        """从 SQL 中提取 JOIN 信息"""
        joins = []
        for match in re.finditer(
            r'(\w+)\s+JOIN\s+(\w+)\s+ON\s+(.+?)(?:\s+(?:LEFT|RIGHT|INNER|JOIN|WHERE|GROUP|ORDER|LIMIT)|$)',
            sql, re.IGNORECASE | re.DOTALL
        ):
            joins.append({
                'from_table': match.group(1),
                'to_table': match.group(2),
                'on_condition': match.group(3).strip()
            })
        return joins
    
    def _extract_where_conditions(self, sql: str) -> List[Dict]:
        """从 SQL 中提取 WHERE 条件"""
        conditions = []
        where_match = re.search(r'WHERE\s+(.+?)(?:\s+GROUP|\s+ORDER|\s+LIMIT|$)', sql, re.IGNORECASE | re.DOTALL)
        if where_match:
            where_part = where_match.group(1)
            for match in re.finditer(r'([\w_]+)\s*(=|<>|!=|>|<|>=|<=|LIKE|IN)\s*', where_part, re.IGNORECASE):
                conditions.append({
                    'field': match.group(1),
                    'operator': match.group(2).upper()
                })
        return conditions
    
    def _extract_wrong_table(self, error_detail: str, error_sql: str) -> Optional[str]:
        """从错误详情中提取错误的表名"""
        tables = self._extract_tables(error_sql)
        if tables:
            return tables[0]
        
        match = re.search(r'(\w+)(?:\s+表)?\s*(?:选择|使用|错误)', error_detail, re.IGNORECASE)
        if match:
            return match.group(1)
        
        return None
    
    def _extract_field_from_error(self, error_detail: str) -> Optional[str]:
        """从错误详情中提取错误的字段名"""
        match = re.search(r'(\w+)(?:\s+字段)?\s*(?:选择|使用|错误)', error_detail, re.IGNORECASE)
        if match:
            return match.group(1)
        return None
