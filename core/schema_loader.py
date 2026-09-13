"""Schema 加载器：从营销4.0数据库提取表结构、字段、外键、数据分布"""
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
            # 获取所有表 + information_schema 行数估计（经 DatabaseManager 统一方法）
            tables = self.db.get_all_tables(conn)
            row_estimates = self.db.get_table_row_estimates(conn)
            
            for table in tables:
                schema[table] = self._load_table_schema(conn, table, row_estimates.get(table))
        
        self._schema_cache = schema
        return schema
    
    def _load_table_schema(self, conn, table: str, row_estimate: Optional[int] = None) -> Dict[str, Any]:
        """加载单张表的Schema（使用 DatabaseManager 的统一方法）"""
        # 字段信息（information_schema）
        columns = self.db.get_table_columns(conn, table)
        pk_cols = [c['name'] for c in columns if c.get('pk', 0) > 0]

        # 加载字段中文注释（information_schema，经 SchemaPreloader 缓存，见 _load_field_comments）
        field_comments = self._load_field_comments(table)
        for col in columns:
            col['comment'] = field_comments.get(col['name'], '')

        # 外键信息（information_schema）
        foreign_keys = self.db.get_foreign_keys(conn, table)
        fk_list = []
        for fk in foreign_keys:
            fk_list.append({
                'from_col': fk['from_col'],
                'ref_table': fk['ref_table'],
                'to_col': fk['to_col']
            })
        
        # 索引信息（information_schema）
        indexes = self.db.get_table_indexes(conn, table)
        idx_list = []
        for idx in indexes:
            idx_list.append({
                'name': idx['name'],
                'unique': idx['unique'],
                'origin': 'c'
            })
        
        # 行数统计（逐字段 distinct/top_values 采样已删除：无消费方，
        # 且大表上每列 3 次全表扫描会让启动预热卡数分钟）
        stats = {}
        try:
            cursor = conn.execute(f'SELECT COUNT(*) FROM `{table}`')
            stats['row_count'] = cursor.fetchone()[0]
            self._calibrate_row_estimate(conn, table, row_estimate, stats['row_count'])
        except Exception:
            stats['row_count'] = 0
        
        return {
            'columns': columns,
            'pk': pk_cols,
            'foreign_keys': fk_list,
            'indexes': idx_list,
            'stats': stats
        }
    
    @staticmethod
    def _calibrate_row_estimate(conn, table: str, estimate: Optional[int], exact: int):
        """information_schema 行数估计严重失真时自动 ANALYZE 校准。

        information_schema.TABLES.TABLE_ROWS 是 InnoDB 采样估计值、不可直接写入，
        ANALYZE TABLE（重采样持久化统计）是刷新该估计的标准手段。
        估计本就接近时 InnoDB 自身 auto_recalc 会维护，此处只兜底严重失真
        （偏差 >2x 且实际 >1000 行；曾实测 438k 行的表估计仅 320）。
        """
        if estimate is None or exact <= 1000:
            return
        if 0 < estimate <= exact * 2 and estimate >= exact * 0.5:
            return
        try:
            conn.execute(f'ANALYZE TABLE `{table}`').fetchall()
            print(f'[schema] 行数估计失真，已 ANALYZE 校准: {table}'
                  f'（估计 {estimate} → 实际 {exact}）', flush=True)
        except Exception as e:
            print(f'[WARN] ANALYZE TABLE {table} 失败: {e}')
    
    def _load_field_comments(self, table: str) -> Dict[str, str]:
        """加载字段中文注释。

        权威源 = 业务库 information_schema（注释随物理表 COMMENT 落库），
        经 SchemaPreloader 单例缓存读取，避免逐表重复查询。
        """
        comments = {}
        try:
            from core.schema_preloader import SchemaPreloader
            for col in SchemaPreloader.get_instance().get_columns(table):
                comments[col['name']] = col.get('comment') or ''
        except Exception:
            pass
        return comments
    
    def get_table_names(self) -> List[str]:
        """获取所有表名"""
        schema = self.load_schema()
        return sorted(schema.keys())

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