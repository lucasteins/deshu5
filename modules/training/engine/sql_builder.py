# -*- coding: utf-8 -*-
"""结构化 SQL 生成器：基于 SQLIntent 和 RAG 上下文构建 SQL"""
import re
import json
import os
import sys
from typing import Dict, List, Optional, Any, Set

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.schema_kb import SchemaKnowledgeBase


class SQLBuilder:
    """根据意图和 Schema 知识构建 SQL"""
    
    def __init__(self):
        self.kb = SchemaKnowledgeBase()
        self.column_comments = self.kb.get_column_comments()
    
    def build(self, intent: Dict, schema_docs: Optional[Dict] = None) -> Dict:
        """
        根据意图构建 SQL
        
        返回：
        {
            'sql': '生成的 SQL',
            'tables_involved': ['表名'],
            'success': True/False,
            'error': '错误信息'
        }
        """
        try:
            tables = intent.get('tables', [])
            if not tables:
                return {'sql': '', 'tables_involved': [], 'success': False, 'error': '意图中未识别到表'}
            
            # 生成 SELECT 字段（用于判断哪些表真正被用到）
            select_clause = self._build_select(intent, tables[0])
            where_clause = self._build_where(intent)
            group_clause = self._build_group_by(intent)
            order_clause = self._build_order_by(intent)
            
            # 剪枝：移除没有被使用的表（保留主表和用于连接的中间表）
            tables = self._prune_tables(
                tables,
                select_clause,
                where_clause,
                group_clause,
                order_clause,
                schema_docs
            )
            intent['tables'] = tables
            
            # 补全最小 JOIN 路径（必须基于剪枝后的表重新计算，避免意图携带冗余 JOIN）
            joins = self._minimal_joins(tables, schema_docs) if schema_docs else []
            intent['joins'] = joins
            
            # 确定主表
            main_table = tables[0]
            
            # 重新生成子句（因为表可能已剪枝）
            select_clause = self._build_select(intent, main_table)
            
            # 生成 FROM 和 JOIN
            from_clause = self._build_from_joins(main_table, tables, joins)
            
            # 生成 WHERE
            where_clause = self._build_where(intent)
            
            # 生成 GROUP BY
            group_clause = self._build_group_by(intent)
            
            # 生成 ORDER BY
            order_clause = self._build_order_by(intent)
            
            # 生成 LIMIT
            limit_clause = self._build_limit(intent)
            
            # 组装 SQL
            parts = [f'SELECT {select_clause}', f'FROM {from_clause}']
            if where_clause:
                parts.append(f'WHERE {where_clause}')
            if group_clause:
                parts.append(f'GROUP BY {group_clause}')
            if order_clause:
                parts.append(f'ORDER BY {order_clause}')
            if limit_clause:
                parts.append(limit_clause)
            
            sql = '\n'.join(parts)
            
            return {
                'sql': sql,
                'tables_involved': tables,
                'success': True,
                'error': ''
            }
        except Exception as e:
            return {'sql': '', 'tables_involved': [], 'success': False, 'error': str(e)}
    
    def _build_select(self, intent: Dict, main_table: str) -> str:
        """构建 SELECT 子句"""
        fields = intent.get('fields', [])
        
        if not fields:
            # 默认选择主表所有字段（但避免 SELECT *）
            return f'{main_table}.*'
        
        select_items = []
        has_aggregate = False
        
        for f in fields:
            name = f.get('name', '')
            role = f.get('role', 'select')
            agg = f.get('agg', '')
            
            if role == 'aggregate' and agg:
                has_aggregate = True
                # 聚合字段需要确定目标字段
                target_col = self._infer_aggregation_column(name, intent['tables'])
                if target_col:
                    alias = self._generate_alias(target_col, agg)
                    select_items.append(f'{agg}({target_col}) AS {alias}')
                else:
                    select_items.append(f'{agg}(*) AS total')
            elif name:
                select_items.append(name)
            else:
                select_items.append('*')
        
        # 如果有聚合，只保留聚合项和 GROUP BY 维度
        if has_aggregate:
            group_by_cols = set()
            for g in intent.get('group_by', []):
                group_by_cols.add(g.split('.')[-1].lower())
                group_by_cols.add(g.lower())
            filtered_items = []
            for item in select_items:
                if item.upper().startswith(('SUM(', 'COUNT(', 'AVG(', 'MAX(', 'MIN(')):
                    filtered_items.append(item)
                    continue
                # 保留 GROUP BY 中的维度
                item_col = item.split('.')[-1].lower() if '.' in item else item.lower()
                if item_col in group_by_cols:
                    filtered_items.append(item)
            # 如果 SELECT 中没有非聚合维度但 GROUP BY 存在，补全
            for g in intent.get('group_by', []):
                g_resolved = g if '.' in g else self._resolve_column_table(g, intent['tables'])
                if g_resolved and g_resolved not in filtered_items:
                    filtered_items.append(g_resolved)
            select_items = filtered_items
        
        # 如果有聚合但没有 GROUP BY 字段，自动添加
        if has_aggregate and not intent.get('group_by'):
            # 尝试从 SELECT 字段中找到非聚合维度
            for f in fields:
                if f.get('role') == 'select' and f.get('name'):
                    intent.setdefault('group_by', []).append(f['name'])
        
        return ', '.join(select_items) if select_items else f'{main_table}.*'
    
    def _infer_aggregation_column(self, hint: str, tables: List[str]) -> Optional[str]:
        """推断聚合字段"""
        # 如果 hint 就是字段名
        if hint and self._is_valid_column(hint, tables):
            return hint
        
        # 根据常见业务规则推断
        if any('电量' in (self.column_comments.get(t, {}).get('pap_e', '') or '') or 
               'pap_e' in [c.lower() for c in self.column_comments.get(t, {})]
               for t in tables):
            for t in tables:
                if 'pap_e' in [c.lower() for c in self.column_comments.get(t, {})]:
                    return f'{t}.pap_e'
        
        # 默认找金额字段
        for t in tables:
            for col, comment in self.column_comments.get(t, {}).items():
                if any(k in comment for k in ['金额', '电量', 'amt', 'e']) and 'id' not in col.lower():
                    return f'{t}.{col}'
        
        return None
    
    def _generate_alias(self, column: str, agg: str) -> str:
        """生成聚合字段别名"""
        agg_names = {
            'SUM': 'total',
            'COUNT': 'cnt',
            'AVG': 'avg',
            'MAX': 'max',
            'MIN': 'min'
        }
        prefix = agg_names.get(agg.upper(), 'val')
        col_short = column.split('.')[-1]
        return f'{prefix}_{col_short}'
    
    def _build_from_joins(self, main_table: str, tables: List[str], joins: List[Dict]) -> str:
        """构建 FROM 和 JOIN 子句"""
        from_clause = main_table
        joined_tables = {main_table}
        
        # 根据 joins 构建
        for join in joins:
            left_table = join.get('left_table')
            right_table = join.get('right_table')
            left_col = join.get('left_col')
            right_col = join.get('right_col')
            
            if not all([left_table, right_table, left_col, right_col]):
                continue
            
            # 决定 join 方向
            if left_table in joined_tables and right_table not in joined_tables:
                from_clause += f"\nLEFT JOIN {right_table} ON {left_table}.{left_col} = {right_table}.{right_col}"
                joined_tables.add(right_table)
            elif right_table in joined_tables and left_table not in joined_tables:
                from_clause += f"\nLEFT JOIN {left_table} ON {right_table}.{right_col} = {left_table}.{left_col}"
                joined_tables.add(left_table)
        
        # 对于未 join 的表，尝试基于关系路径补充
        remaining = [t for t in tables if t not in joined_tables]
        for t in remaining:
            # 尝试找到已连接表和该表的关系
            added = False
            for jt in list(joined_tables):
                rels = self.kb.retrieve_relationship_docs(keywords=[], tables=[jt, t], limit=5)
                for rel in rels:
                    for jc in rel['join_conditions']:
                        match = re.match(r'(\w+)\.(\w+)\s*=\s*(\w+)\.(\w+)', jc)
                        if match:
                            lt, lc, rt, rc = match.groups()
                            if lt in joined_tables and rt == t:
                                from_clause += f"\nLEFT JOIN {rt} ON {lt}.{lc} = {rt}.{rc}"
                                joined_tables.add(t)
                                added = True
                                break
                            elif rt in joined_tables and lt == t:
                                from_clause += f"\nLEFT JOIN {lt} ON {rt}.{rc} = {lt}.{lc}"
                                joined_tables.add(t)
                                added = True
                                break
                    if added:
                        break
                if added:
                    break
            
            # 补充硬编码关联
            if not added:
                for jt in list(joined_tables):
                    key = (jt, t)
                    if key in self._HARDCODED_JOINS:
                        lc, rc = self._HARDCODED_JOINS[key]
                        from_clause += f"\nLEFT JOIN {t} ON {jt}.{lc} = {t}.{rc}"
                        joined_tables.add(t)
                        break
                    key_rev = (t, jt)
                    if key_rev in self._HARDCODED_JOINS:
                        rc, lc = self._HARDCODED_JOINS[key_rev]
                        from_clause += f"\nLEFT JOIN {t} ON {jt}.{lc} = {t}.{rc}"
                        joined_tables.add(t)
                        break
        
        return from_clause
    
    def _build_where(self, intent: Dict) -> str:
        """构建 WHERE 子句"""
        filters = intent.get('filters', [])
        if not filters:
            return ''
        
        conditions = []
        for f in filters:
            field = f.get('field', '') or f.get('field_hint', '')
            op = f.get('op', '=')
            value = f.get('value')
            
            if not field:
                continue
            
            # 原始表达式直接加入
            if f.get('raw'):
                conditions.append(str(value))
                continue
            
            # 如果没有表前缀，尝试加上
            if '.' not in field:
                resolved = self._resolve_column_table(field, intent['tables'])
                if resolved is None:
                    continue
                field = resolved
            else:
                # 校验带前缀的字段是否真实存在
                col = field.split('.')[-1]
                table = field.split('.')[0]
                if table not in intent['tables'] or col not in self.column_comments.get(table, {}):
                    # 尝试重新解析
                    resolved = self._resolve_column_table(col, intent['tables'])
                    if resolved is None:
                        continue
                    field = resolved
            
            if op.upper() == 'BETWEEN' and isinstance(value, list) and len(value) == 2:
                conditions.append(f"{field} BETWEEN '{value[0]}' AND '{value[1]}'")
            elif op.upper() == 'LIKE':
                conditions.append(f"{field} LIKE '{value}'")
            elif op.upper() == 'IN' and isinstance(value, list):
                vals = ', '.join(f"'{v}'" for v in value)
                conditions.append(f"{field} IN ({vals})")
            elif isinstance(value, str):
                conditions.append(f"{field} {op} '{value}'")
            else:
                conditions.append(f"{field} {op} {value}")
        
        return ' AND '.join(conditions)
    
    def _build_group_by(self, intent: Dict) -> str:
        """构建 GROUP BY 子句"""
        group_by = intent.get('group_by', [])
        if not group_by:
            return ''
        
        resolved = []
        for g in group_by:
            if '.' not in g:
                g = self._resolve_column_table(g, intent['tables'])
            if g:
                resolved.append(g)
        
        return ', '.join(resolved) if resolved else ''
    
    def _build_order_by(self, intent: Dict) -> str:
        """构建 ORDER BY 子句"""
        order_by = intent.get('order_by', [])
        if not order_by:
            return ''
        
        items = []
        for o in order_by:
            field = o.get('field', '')
            direction = o.get('direction', 'ASC')
            if not field:
                # 如果有聚合字段，按聚合别名排序
                for f in intent.get('fields', []):
                    if f.get('role') == 'aggregate':
                        agg_col = self._infer_aggregation_column(f.get('name', ''), intent['tables'])
                        if agg_col:
                            field = self._generate_alias(agg_col, f.get('agg', 'SUM'))
                        break
            if field:
                items.append(f'{field} {direction}')
        
        return ', '.join(items) if items else ''
    
    def _build_limit(self, intent: Dict) -> str:
        """构建 LIMIT 子句"""
        limit = intent.get('limit')
        if limit:
            return f'LIMIT {limit}'
        return ''
    
    def _resolve_column_table(self, column: str, tables: List[str]) -> Optional[str]:
        """解析字段属于哪个表，找不到时返回 None"""
        # 如果已经带表前缀
        if '.' in column:
            return column
        
        # 查找字段注释匹配
        for t in tables:
            if column in self.column_comments.get(t, {}):
                return f'{t}.{column}'
        
        return None
    
    def _is_valid_column(self, column: str, tables: List[str]) -> bool:
        """检查字段是否有效"""
        col = column.split('.')[-1]
        for t in tables:
            if col in self.column_comments.get(t, {}):
                return True
        return False
    
    def _prune_tables(self, tables: List[str], select_clause: str, where_clause: str,
                      group_clause: str, order_clause: str, schema_docs: Dict) -> List[str]:
        """移除没有被 SELECT/WHERE/GROUP/ORDER 直接使用的表，但保留连接所需的中间表"""
        if len(tables) <= 1:
            return tables
        
        used_columns = self._extract_columns_from_clauses(select_clause, where_clause, group_clause, order_clause)
        table_usage = {t: 0 for t in tables}
        for col_ref in used_columns:
            if '.' in col_ref:
                table = col_ref.split('.')[0]
                if table in tables:
                    table_usage[table] += 1
        
        # 选择使用最多的表作为主表（通常是承载核心数据的表）
        main_table = max(tables, key=lambda t: table_usage.get(t, 0))
        if table_usage[main_table] == 0:
            main_table = tables[0]
        
        used_tables = {main_table}
        
        # 如果过滤条件引用了某张表中的字段，该表必须保留
        for t in tables:
            for col in self.column_comments.get(t, {}):
                if f'{t}.{col}' in used_columns or col in used_columns:
                    used_tables.add(t)
        
        # 通过关系路径把被使用表连通起来
        kept = [t for t in tables if t in used_tables]
        connected = self._connect_tables(kept, tables, schema_docs)
        
        # 确保主表排在第一位
        if main_table in connected:
            connected.remove(main_table)
            connected = [main_table] + connected
        
        return connected
    
    def _extract_columns_from_clauses(self, *clauses: str) -> Set[str]:
        """从子句中提取字段引用"""
        columns = set()
        for clause in clauses:
            if not clause:
                continue
            for match in re.finditer(r'(\w+)\.(\w+)', clause):
                columns.add(f'{match.group(1)}.{match.group(2)}')
            for match in re.finditer(r'\b([a-zA-Z_][a-zA-Z0-9_]*)\b', clause):
                columns.add(match.group(1))
        return columns
    
    def _connect_tables(self, required: List[str], candidates: List[str], schema_docs: Dict) -> List[str]:
        """基于关系文档，用候选表把 required 表连通（最小 Steiner 树近似）"""
        if not required:
            return candidates[:1]
        
        required_set = set(required)
        all_tables = set(candidates)
        
        # 构建候选表之间的邻接关系
        table_neighbors = {t: {} for t in all_tables}
        for rel in schema_docs.get('relationships', []):
            for jc in rel.get('join_conditions', []):
                match = re.match(r'(\w+)\.(\w+)\s*=\s*(\w+)\.(\w+)', jc)
                if match:
                    t1, c1, t2, c2 = match.groups()
                    if t1 in all_tables and t2 in all_tables:
                        table_neighbors.setdefault(t1, {})[t2] = (c1, c2)
                        table_neighbors.setdefault(t2, {})[t1] = (c2, c1)
        
        # 补充硬编码关联
        for (t1, t2), (c1, c2) in self._HARDCODED_JOINS.items():
            if t1 in all_tables and t2 in all_tables:
                table_neighbors.setdefault(t1, {})[t2] = (c1, c2)
                table_neighbors.setdefault(t2, {})[t1] = (c2, c1)
        
        connected = set(required)
        remaining_required = required_set - {required[0]}
        
        # 迭代连接每个尚未连通的 required 表
        while remaining_required:
            best_path = None
            best_len = float('inf')
            
            for start in connected:
                for target in remaining_required:
                    path = self._shortest_path(start, target, table_neighbors)
                    if path and len(path) < best_len:
                        best_len = len(path)
                        best_path = path
            
            if not best_path:
                break
            
            for node in best_path:
                connected.add(node)
            remaining_required -= connected
        
        return [t for t in candidates if t in connected]
    
    def _shortest_path(self, start: str, end: str, neighbors: Dict) -> List[str]:
        """BFS 最短路径"""
        from collections import deque
        queue = deque([(start, [start])])
        visited = {start}
        
        while queue:
            current, path = queue.popleft()
            if current == end:
                return path
            for neighbor in neighbors.get(current, {}):
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append((neighbor, path + [neighbor]))
        
        return []
    
    # 硬编码常见关联，补充关系文档可能缺失的场景
    _HARDCODED_JOINS = {
        ('dwd_cst_meter_run', 'dim_cst_dev'): ('meter_id', 'dev_id'),
        ('dim_cst_dev', 'dwd_cst_meter_run'): ('dev_id', 'meter_id'),
    }
    
    def _minimal_joins(self, tables: List[str], schema_docs: Dict) -> List[Dict]:
        """根据关系文档构建最小 JOIN 树（MST），避免冗余 LEFT JOIN"""
        if len(tables) <= 1:
            return []
        
        table_set = set(tables)
        edges = []
        seen_edges = set()
        
        for rel in schema_docs.get('relationships', []):
            for jc in rel.get('join_conditions', []):
                match = re.match(r'(\w+)\.(\w+)\s*=\s*(\w+)\.(\w+)', jc)
                if not match:
                    continue
                t1, c1, t2, c2 = match.groups()
                if t1 in table_set and t2 in table_set:
                    edge_key = tuple(sorted([f'{t1}.{c1}', f'{t2}.{c2}']))
                    if edge_key not in seen_edges:
                        seen_edges.add(edge_key)
                        edges.append({
                            'left_table': t1,
                            'left_col': c1,
                            'right_table': t2,
                            'right_col': c2
                        })
        
        # 补充硬编码关联
        for (t1, t2), (c1, c2) in self._HARDCODED_JOINS.items():
            if t1 in table_set and t2 in table_set:
                edge_key = tuple(sorted([f'{t1}.{c1}', f'{t2}.{c2}']))
                if edge_key not in seen_edges:
                    seen_edges.add(edge_key)
                    edges.append({
                        'left_table': t1,
                        'left_col': c1,
                        'right_table': t2,
                        'right_col': c2
                    })
        
        # Kruskal-like MST：BFS 连接所有表
        joined = {tables[0]}
        selected = []
        remaining_tables = set(tables[1:])
        
        while remaining_tables:
            added = False
            for edge in edges:
                t1, t2 = edge['left_table'], edge['right_table']
                if t1 in joined and t2 in remaining_tables:
                    selected.append(edge)
                    joined.add(t2)
                    remaining_tables.discard(t2)
                    added = True
                    break
                elif t2 in joined and t1 in remaining_tables:
                    selected.append(edge)
                    joined.add(t1)
                    remaining_tables.discard(t1)
                    added = True
                    break
            if not added:
                break
        
        return selected


if __name__ == '__main__':
    from modules.training.engine.intent_parser import IntentParser
    
    parser = IntentParser()
    builder = SQLBuilder()
    
    questions = [
        "统计各供电单位下高压客户的总用电量",
        "查询2026年4月用电量排名前10的计量点",
    ]
    
    for q in questions:
        print(f"\n问题: {q}")
        intent = parser.parse(q, use_llm=False)
        result = builder.build(intent, intent.get('_schema_docs'))
        print("SQL:")
        print(result['sql'])
        print("表:", result['tables_involved'])
