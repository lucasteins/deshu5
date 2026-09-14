# -*- coding: utf-8 -*-
"""MySQL 数据访问底座（deshu5 重构版）

四个库的职责划分（与 deshu4 生产库完全一致，存量数据直接可用）：
- MYSQL_DB_BUSINESS   业务库（sc01）：35 张营销共享层表，SQL 只读执行目标
- MYSQL_DB_GOVERNANCE 治理库（sc01_governance）：问答对/错题/码值/Schema 文档/提资溯源
- MYSQL_DB_LOG        日志库（sc01_log）：generation_logs 等运行日志
- MYSQL_DB_ONTOLOGY   本体库（sc01_ontology）：本体模型层存储

连接封装说明：代码层沿用 ? 占位符书写习惯，
由 MySQLConnectionWrapper 统一转换为 MySQL 方言（%s / RAND()）。
"""
import re
import os
from contextlib import contextmanager
from typing import Dict, List

import pymysql

import config
from core import db_profile


class MySQLConnectionWrapper:
    """将 pymysql 连接包装为轻量执行接口。

    - execute()/executemany() 返回 pymysql Cursor
    - 自动将 ? 占位符转换为 %s、RANDOM() 转换为 RAND()
    - 无参数时直接执行，避免 SQL 中的 % 字符被当作格式占位符
    """

    def __init__(self, conn):
        self._conn = conn

    @staticmethod
    def _convert_sql(sql: str) -> str:
        sql = sql.replace('?', '%s')
        sql = re.sub(r'\bRANDOM\s*\(\s*\)', 'RAND()', sql, flags=re.IGNORECASE)
        return sql

    def execute(self, sql, params=None):
        if isinstance(sql, str):
            sql = self._convert_sql(sql)
        cursor = self._conn.cursor()
        if not params:
            cursor.execute(sql)
        else:
            cursor.execute(sql, params)
        return cursor

    def executemany(self, sql, params_list):
        if isinstance(sql, str):
            sql = self._convert_sql(sql)
        cursor = self._conn.cursor()
        cursor.executemany(sql, params_list)
        return cursor

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class DatabaseManager:
    """MySQL 连接工厂：business / governance / log / ontology 四个库的上下文管理器"""

    def _get_mysql_conn(self, database: str):
        return pymysql.connect(
            host=config.MYSQL_HOST,
            port=config.MYSQL_PORT,
            user=config.MYSQL_USER,
            password=config.MYSQL_PASSWORD,
            database=database,
            charset=config.MYSQL_CHARSET,
            cursorclass=pymysql.cursors.Cursor,
        )

    @contextmanager
    def connect_business(self):
        conn = MySQLConnectionWrapper(self._get_mysql_conn(db_profile.current()['business']))
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def connect_governance(self):
        conn = MySQLConnectionWrapper(self._get_mysql_conn(db_profile.current()['governance']))
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def connect_log(self):
        conn = MySQLConnectionWrapper(self._get_mysql_conn(db_profile.current()['log']))
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def connect_ontology(self):
        conn = MySQLConnectionWrapper(self._get_mysql_conn(db_profile.current()['ontology']))
        try:
            yield conn
        finally:
            conn.close()

    # ---------- information_schema 元数据 ----------

    def get_table_columns(self, conn, table_name: str) -> List[Dict]:
        cursor = conn.execute("""
            SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE, COLUMN_DEFAULT, COLUMN_KEY, EXTRA
            FROM information_schema.columns
            WHERE table_schema = DATABASE() AND table_name = %s
            ORDER BY ORDINAL_POSITION
        """, (table_name,))
        columns = []
        for row in cursor.fetchall():
            columns.append({
                'name': row[0],
                'type': row[1].upper() if row[1] else 'TEXT',
                'notnull': row[2] == 'NO',
                'default': row[3],
                'pk': 1 if row[4] == 'PRI' else 0,
                'autoinc': 'auto_increment' in (row[5] or '').lower()
            })
        return columns

    def get_table_indexes(self, conn, table_name: str) -> List[Dict]:
        cursor = conn.execute("""
            SELECT INDEX_NAME, COLUMN_NAME, NON_UNIQUE
            FROM information_schema.statistics
            WHERE table_schema = DATABASE() AND table_name = %s
            ORDER BY INDEX_NAME, SEQ_IN_INDEX
        """, (table_name,))
        idx_map = {}
        for row in cursor.fetchall():
            name = row[0]
            if name not in idx_map:
                idx_map[name] = {'name': name, 'unique': not row[2], 'columns': []}
            idx_map[name]['columns'].append(row[1])
        return list(idx_map.values())

    def get_foreign_keys(self, conn, table_name: str) -> List[Dict]:
        cursor = conn.execute("""
            SELECT COLUMN_NAME, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME
            FROM information_schema.key_column_usage
            WHERE table_schema = DATABASE() AND table_name = %s
              AND REFERENCED_TABLE_NAME IS NOT NULL
        """, (table_name,))
        return [
            {'from_col': row[0], 'ref_table': row[1], 'to_col': row[2]}
            for row in cursor.fetchall()
        ]

    def get_all_tables(self, conn) -> List[str]:
        cursor = conn.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = DATABASE() AND table_type = 'BASE TABLE'
        """)
        return [row[0] for row in cursor.fetchall()]

    def get_table_row_estimates(self, conn) -> Dict[str, int]:
        """information_schema 的行数估计（InnoDB 采样值，严重失真时需 ANALYZE 校准）。"""
        cursor = conn.execute("""
            SELECT table_name, COALESCE(table_rows, 0) FROM information_schema.tables
            WHERE table_schema = DATABASE() AND table_type = 'BASE TABLE'
        """)
        return {row[0]: int(row[1] or 0) for row in cursor.fetchall()}

    def ping(self) -> dict:
        """连通性检查：四个库各取一次 1。返回 {database: ok/error}。"""
        result = {}
        for name, ctx in (('business', self.connect_business),
                          ('governance', self.connect_governance),
                          ('log', self.connect_log),
                          ('ontology', self.connect_ontology)):
            try:
                with ctx() as conn:
                    conn.execute('SELECT 1')
                result[name] = True
            except Exception as e:
                result[name] = str(e)
        return result


# ==================== 表结构初始化（幂等，MySQL DDL）====================

def _ensure_columns(conn, table: str, new_cols: dict):
    """已有表补建缺失列（information_schema 探测后 ALTER）。"""
    cursor = conn.execute('''
        SELECT COLUMN_NAME FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = %s
    ''', (table,))
    existing = {row[0] for row in cursor.fetchall()}
    for col, dtype in new_cols.items():
        if col not in existing:
            try:
                conn.execute(f'ALTER TABLE {table} ADD COLUMN {col} {dtype}')
            except Exception as e:
                print(f"[WARN] 添加 {table}.{col} 失败: {e}")


def _ensure_database(db_name: str):
    """日志库等附属库不存在时在建表前创建（连 MySQL 服务器级，不带库名）。"""
    conn = pymysql.connect(
        host=config.MYSQL_HOST, port=config.MYSQL_PORT,
        user=config.MYSQL_USER, password=config.MYSQL_PASSWORD,
        charset=config.MYSQL_CHARSET)
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{db_name}` "
                f"DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci")
        conn.commit()
    finally:
        conn.close()


def init_error_records_table():
    db = DatabaseManager()
    with db.connect_governance() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS error_records (
                id INT PRIMARY KEY AUTO_INCREMENT,
                question_id VARCHAR(64),
                business_question TEXT,
                generated_sql TEXT,
                correct_sql TEXT,
                error_type VARCHAR(32),
                error_detail TEXT,
                user_feedback TEXT,
                result_preview TEXT,
                tables_involved TEXT,
                fields_involved TEXT,
                sql_pattern TEXT,
                frequency INT DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                is_resolved TINYINT(1) DEFAULT 0,
                resolution_note TEXT,
                last_occurred DATETIME DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        ''')
        for idx_sql in (
            'CREATE INDEX idx_error_type ON error_records(error_type)',
            'CREATE INDEX idx_error_created ON error_records(created_at)',
            'CREATE INDEX idx_error_resolved ON error_records(is_resolved)',
        ):
            try:
                conn.execute(idx_sql)
            except Exception:
                pass  # 索引已存在
        _ensure_columns(conn, 'error_records', {
            'tables_involved': 'TEXT',
            'fields_involved': 'TEXT',
            'sql_pattern': 'TEXT',
            'frequency': 'INT DEFAULT 1',
            'last_occurred': 'DATETIME DEFAULT CURRENT_TIMESTAMP',
        })
        conn.commit()


def init_generation_logs_table():
    db = DatabaseManager()
    _ensure_database(db_profile.current()['log'])
    with db.connect_log() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS generation_logs (
                id INT PRIMARY KEY AUTO_INCREMENT,
                session_id VARCHAR(64),
                user_question TEXT,
                generation_mode VARCHAR(32),
                tables_involved TEXT,
                generation_strategy VARCHAR(32),
                difficulty VARCHAR(16),
                generated_sql TEXT,
                llm_model VARCHAR(64),
                rag_pairs_count INT,
                review_passed TINYINT(1),
                review_issues TEXT,
                execution_status VARCHAR(16),
                row_count INT,
                post_execution_audit TEXT,
                attempts INT DEFAULT 1,
                latency_ms INT,
                user_judgment VARCHAR(8),
                user_feedback TEXT,
                table_choice_correct TINYINT(1) DEFAULT NULL,
                field_choice_correct TINYINT(1) DEFAULT NULL,
                join_path_correct TINYINT(1) DEFAULT NULL,
                where_condition_correct TINYINT(1) DEFAULT NULL,
                aggregation_correct TINYINT(1) DEFAULT NULL,
                human_corrected_sql TEXT,
                annotation_remark TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        ''')
        for idx_sql in (
            'CREATE INDEX idx_gen_session ON generation_logs(session_id)',
            'CREATE INDEX idx_gen_mode ON generation_logs(generation_mode)',
        ):
            try:
                conn.execute(idx_sql)
            except Exception:
                pass
        _ensure_columns(conn, 'generation_logs', {
            'user_feedback': 'TEXT',
            'generation_mode': 'VARCHAR(32)',
            'post_execution_audit': 'TEXT',
            'table_choice_correct': 'TINYINT(1)',
            'field_choice_correct': 'TINYINT(1)',
            'join_path_correct': 'TINYINT(1)',
            'where_condition_correct': 'TINYINT(1)',
            'aggregation_correct': 'TINYINT(1)',
            'human_corrected_sql': 'TEXT',
            'annotation_remark': 'TEXT',
        })
        conn.commit()


def init_qa_pairs_columns():
    db = DatabaseManager()
    with db.connect_governance() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS qa_pairs (
                id INT PRIMARY KEY AUTO_INCREMENT,
                question TEXT,
                standard_sql TEXT,
                difficulty VARCHAR(16),
                source VARCHAR(32),
                tags TEXT,
                ingest_time DATETIME DEFAULT CURRENT_TIMESTAMP,
                generation_method VARCHAR(32),
                is_usable TINYINT(1) DEFAULT 1
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        ''')
        _ensure_columns(conn, 'qa_pairs', {
            'generation_method': 'VARCHAR(32)',
            'source': 'VARCHAR(32)',
            'is_usable': 'TINYINT(1) DEFAULT 1',
        })
        conn.commit()


def init_code_values_tables():
    db = DatabaseManager()
    with db.connect_governance() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS code_values (
                id INT PRIMARY KEY AUTO_INCREMENT,
                code_name VARCHAR(64),
                code_cn_name VARCHAR(128),
                domain_l1 VARCHAR(16),
                domain_l2 VARCHAR(16),
                domain_l3 VARCHAR(16),
                data_type VARCHAR(32),
                description VARCHAR(255),
                UNIQUE KEY uk_code_name (code_name)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        ''')
        # 新模板 S3D 中文名含长描述（实测 146-154 字符），VARCHAR(128) 不够；幂等扩到 255
        try:
            conn.execute('ALTER TABLE code_values MODIFY COLUMN code_cn_name VARCHAR(255)')
        except Exception:
            pass
        conn.execute('''
            CREATE TABLE IF NOT EXISTS code_value_items (
                id INT PRIMARY KEY AUTO_INCREMENT,
                code_name VARCHAR(64),
                item_code VARCHAR(255),
                item_name VARCHAR(255),
                sort_order INT,
                INDEX idx_code_name (code_name),
                INDEX idx_item_name (item_name)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        ''')
        # path_org_code 等路径型码值为多段编码逗号拼接（素材提资实测 33 字符），VARCHAR(32) 不够；幂等扩到 255
        # （注：与上游 94a105e 的 64 方案合并时保留本地 255——已部署库即 255 且实测余量更足，避免缩窄）
        try:
            conn.execute('ALTER TABLE code_value_items MODIFY COLUMN item_code VARCHAR(255)')
        except Exception:
            pass
        conn.execute('''
            CREATE TABLE IF NOT EXISTS code_value_column_form (
                id INT PRIMARY KEY AUTO_INCREMENT,
                table_name VARCHAR(64),
                column_name VARCHAR(64),
                code_name VARCHAR(64),
                form VARCHAR(8),
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uk_tcc (table_name, column_name, code_name)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        ''')
        conn.commit()


def init_report_tables():
    """报告生成模块：模板表 + 运行历史表（治理库，幂等）"""
    db = DatabaseManager()
    with db.connect_governance() as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS report_templates (
                id INT PRIMARY KEY AUTO_INCREMENT,
                name VARCHAR(128),
                trigger_words TEXT COMMENT 'JSON 数组，意图命中词',
                outline TEXT COMMENT 'JSON 数组：[{section_title, hint}]',
                enabled TINYINT(1) DEFAULT 1,
                remark VARCHAR(255),
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS report_runs (
                id INT PRIMARY KEY AUTO_INCREMENT,
                session_id VARCHAR(64),
                intent_text TEXT,
                template_id INT DEFAULT NULL,
                plan_json MEDIUMTEXT,
                detail_json MEDIUMTEXT,
                report_md MEDIUMTEXT,
                status VARCHAR(16) DEFAULT 'running',
                usage_json TEXT,
                duration_ms INT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        ''')
        try:
            conn.execute('CREATE INDEX idx_report_runs_created ON report_runs(created_at)')
        except Exception:
            pass  # 索引已存在
        # 问答对 ← 报告模板 联动列：模板问题入库 qa_pairs 时记录来源模板
        _ensure_columns(conn, 'qa_pairs', {
            'report_template_id': 'INT DEFAULT NULL',
        })
        conn.commit()


def import_code_values_if_empty() -> bool:
    """码值表为空时从 Excel 文件导入（幂等）。返回是否执行了导入。"""
    import pandas as pd

    db = DatabaseManager()
    with db.connect_governance() as conn:
        cnt = conn.execute('SELECT COUNT(*) FROM code_values').fetchone()[0]
        cnt_items = conn.execute('SELECT COUNT(*) FROM code_value_items').fetchone()[0]
        if cnt > 0 and cnt_items > 0:
            return False

        if cnt == 0 and os.path.exists(config.CODE_VALUES_FILE):
            df = pd.read_excel(config.CODE_VALUES_FILE).fillna('')
            rows = [(str(r['code_name']), str(r['code_cn_name']),
                     str(r['data_type']), str(r['description'])) for _, r in df.iterrows()]
            conn.executemany(
                'INSERT INTO code_values (code_name, code_cn_name, data_type, description) VALUES (?, ?, ?, ?)',
                rows)
            print(f"[init] code_values 导入 {len(rows)} 行")

        if cnt_items == 0 and os.path.exists(config.CODE_VALUE_ITEMS_FILE):
            df = pd.read_excel(config.CODE_VALUE_ITEMS_FILE).fillna('')
            rows = [(str(r['code_name']), str(r['item_code']), str(r['item_name']),
                     int(r['sort_order']) if str(r['sort_order']).strip() != '' else 0)
                    for _, r in df.iterrows()]
            conn.executemany(
                'INSERT INTO code_value_items (code_name, item_code, item_name, sort_order) VALUES (?, ?, ?, ?)',
                rows)
            print(f"[init] code_value_items 导入 {len(rows)} 行")

        conn.commit()
        return True


def init_all_tables():
    """初始化所有基础表（幂等）"""
    init_error_records_table()
    init_generation_logs_table()
    init_qa_pairs_columns()
    init_code_values_tables()
    init_report_tables()
    # 指标快照（KPI 真实趋势线/环比的数据源，F1.2 增补）：放最后，失败不影响上述基础表
    try:
        from core.stats_history import init_stats_snapshots_table
        init_stats_snapshots_table()
    except Exception as e:
        print(f"[WARN] stats_snapshots 建表失败（指标快照将不可用）: {e}")
    print("[init] 所有表初始化完成")
