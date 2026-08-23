"""Schema 加载器：从营销4.0数据库提取表结构、字段、外键、数据分布"""
import json
from typing import Dict, List, Any, Optional
from core.database import DatabaseManager


class SchemaLoader:
    """加载和缓存数据库Schema"""
    
    def __init__(self):
        self.db = DatabaseManager()
        self._schema_cache: Optional[Dict[str, Any]] = None
    
    def load_schema(self, force_refresh: bool = False) -> Dict[str, Any]:
        """加载所有表的Schema信息"""
        if self._schema_cache is not None and not force_refresh:
            return self._schema_cache
        
        schema = {}
        with self.db.connect_business() as conn:
            # 获取所有表（使用 DatabaseManager 的统一方法，兼容 SQLite 和 MySQL）
            tables = self.db.get_all_tables(conn)
            
            for table in tables:
                schema[table] = self._load_table_schema(conn, table)
        
        self._schema_cache = schema
        return schema
    
    def _load_table_schema(self, conn, table: str) -> Dict[str, Any]:
        """加载单张表的Schema（使用 DatabaseManager 的统一方法）"""
        # 字段信息（兼容 SQLite 和 MySQL）
        columns = self.db.get_table_columns(conn, table)
        pk_cols = [c['name'] for c in columns if c.get('pk', 0) > 0]
        
        # 加载字段中文注释（MySQL 走 information_schema / SQLite 走治理库文档，见 _load_field_comments）
        field_comments = self._load_field_comments(table)
        for col in columns:
            col['comment'] = field_comments.get(col['name'], '')
        
        # 外键信息（兼容 SQLite 和 MySQL）
        foreign_keys = self.db.get_foreign_keys(conn, table)
        fk_list = []
        for fk in foreign_keys:
            fk_list.append({
                'from_col': fk['from_col'],
                'ref_table': fk['ref_table'],
                'to_col': fk['to_col']
            })
        
        # 索引信息（兼容 SQLite 和 MySQL）
        indexes = self.db.get_table_indexes(conn, table)
        idx_list = []
        for idx in indexes:
            idx_list.append({
                'name': idx['name'],
                'unique': idx['unique'],
                'origin': 'c'
            })
        
        # 数据分布统计（采样）
        stats = {}
        try:
            cursor = conn.execute(f'SELECT COUNT(*) FROM `{table}`')
            stats['row_count'] = cursor.fetchone()[0]
            
            for col in columns:
                col_name = col['name']
                try:
                    cursor = conn.execute(
                        f'SELECT COUNT(DISTINCT `{col_name}`) FROM `{table}` WHERE `{col_name}` IS NOT NULL'
                    )
                    distinct_count = cursor.fetchone()[0]
                    
                    cursor = conn.execute(
                        f'SELECT COUNT(*) FROM `{table}` WHERE `{col_name}` IS NOT NULL'
                    )
                    non_null_count = cursor.fetchone()[0]
                    
                    cursor = conn.execute(
                        f'SELECT `{col_name}`, COUNT(*) as cnt FROM `{table}` '
                        f'WHERE `{col_name}` IS NOT NULL '
                        f'GROUP BY `{col_name}` ORDER BY cnt DESC LIMIT 5'
                    )
                    top_values = [row[0] for row in cursor.fetchall() if row[0] is not None]
                    
                    stats[col_name] = {
                        'distinct_count': distinct_count,
                        'non_null_count': non_null_count,
                        'top_values': top_values
                    }
                except Exception:
                    pass
        except Exception:
            stats['row_count'] = 0
        
        return {
            'columns': columns,
            'pk': pk_cols,
            'foreign_keys': fk_list,
            'indexes': idx_list,
            'stats': stats
        }
    
    def _load_field_comments(self, table: str) -> Dict[str, str]:
        """加载字段中文注释。

        MySQL 模式权威源 = 业务库 information_schema（注释随物理表 COMMENT 落库），
        经 SchemaPreloader 单例缓存读取，避免逐表重复查询；
        SQLite 模式保持 governance.schema_column_docs 文档路径不变。
        """
        comments = {}
        try:
            if self.db.get_dialect() == 'mysql':
                from core.schema_preloader import SchemaPreloader
                for col in SchemaPreloader.get_instance().get_columns(table):
                    comments[col['name']] = col.get('comment') or ''
            else:
                with self.db.connect_governance() as gconn:
                    cursor = gconn.execute(
                        'SELECT column_name, column_comment FROM schema_column_docs WHERE table_name = ?',
                        (table,)
                    )
                    for row in cursor.fetchall():
                        comments[row[0]] = row[1] or ''
        except Exception:
            pass
        return comments
    
    def get_table_names(self) -> List[str]:
        """获取所有表名"""
        schema = self.load_schema()
        return sorted(schema.keys())
    
    def get_column_names(self, table: str) -> List[str]:
        """获取表的字段名列表"""
        schema = self.load_schema()
        if table not in schema:
            return []
        return [c['name'] for c in schema[table]['columns']]
    
    def get_foreign_keys(self, table: str) -> List[Dict]:
        """获取表的外键列表"""
        schema = self.load_schema()
        if table not in schema:
            return []
        return schema[table]['foreign_keys']
    
    def build_schema_context(self, tables: List[str], max_cols_per_table: int = 8) -> str:
        """为LLM构建精简的Schema上下文文本（每张表最多保留前8个关键字段，含中文注释）"""
        schema = self.load_schema()
        lines = []
        
        for table in tables:
            if table not in schema:
                continue
            info = schema[table]
            
            # 表头
            lines.append(f"-- {table}")
            lines.append(f"CREATE TABLE {table} (")
            
            # 字段：最多保留前 max_cols_per_table 个
            col_lines = []
            for col in info['columns'][:max_cols_per_table]:
                col_def = f"  {col['name']} {col['type']}"
                if col.get('comment'):
                    col_def += f" /* {col['comment']} */"
                if col.get('pk', 0) > 0:
                    col_def += " PK"
                col_lines.append(col_def)
            
            if len(info['columns']) > max_cols_per_table:
                col_lines.append(f"  ... ({len(info['columns']) - max_cols_per_table} more columns)")
            
            lines.append(",\n".join(col_lines))
            lines.append(");")
            lines.append("")
        
        return "\n".join(lines)
    
    def find_related_tables(self, keywords: List[str]) -> List[str]:
        """根据关键词查找相关表"""
        schema = self.load_schema()
        related = set()
        
        for table, info in schema.items():
            for kw in keywords:
                if kw.lower() in table.lower():
                    related.add(table)
            
            for col in info['columns']:
                for kw in keywords:
                    if kw.lower() in col['name'].lower():
                        related.add(table)
        
        return sorted(related)
    
    def get_join_paths(self, from_table: str, to_table: str) -> List[List[Dict]]:
        """查找两张表之间的JOIN路径（BFS）"""
        schema = self.load_schema()
        if from_table not in schema or to_table not in schema:
            return []
        
        from collections import deque
        queue = deque([(from_table, [])])
        visited = {from_table}
        paths = []
        
        while queue:
            current, path = queue.popleft()
            
            if current == to_table and path:
                paths.append(path)
                continue
            
            if current in schema:
                for fk in schema[current]['foreign_keys']:
                    next_table = fk['ref_table']
                    if next_table not in visited:
                        visited.add(next_table)
                        new_path = path + [fk]
                        queue.append((next_table, new_path))
                
                for t, info in schema.items():
                    for fk in info['foreign_keys']:
                        if fk['ref_table'] == current:
                            if t not in visited:
                                visited.add(t)
                                reverse_fk = {
                                    'from_col': fk['to_col'],
                                    'ref_table': t,
                                    'to_col': fk['from_col']
                                }
                                new_path = path + [reverse_fk]
                                queue.append((t, new_path))
        
        return paths
