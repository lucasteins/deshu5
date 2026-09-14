# -*- coding: utf-8 -*-
"""生产库 sc01 → 仿真库 fz01 数据同步（生产优先，冲突以生产库为准）

- sc01            → fz01              （业务）
- sc01_governance → fz01_governance   （治理）

（原名 sync_prod_to_database01.py；2026-09-14 库重命名后 database01 = fz01，故更名）

策略：
- 有主键/唯一键的表：INSERT ... ON DUPLICATE KEY UPDATE（全列以生产值为准）
- 无任何键的表：追加（无法定义冲突，报告提醒）
- 目标表缺失时按生产 SHOW CREATE TABLE 建表；缺列时 ALTER ADD（可空）
- 只读生产库，只写仿真库；逐表 try/except 失败不中断
"""
import sys

import pymysql

sys.path.insert(0, r'D:\codex\deshu5')
import config

PAIRS = [
    ('sc01', 'fz01'),
    ('sc01_governance', 'fz01_governance'),
]
BATCH = 1000


def _conn(db):
    return pymysql.connect(host=config.MYSQL_HOST, port=config.MYSQL_PORT,
                           user=config.MYSQL_USER, password=config.MYSQL_PASSWORD,
                           database=db, charset='utf8mb4')


def _tables(conn, db):
    with conn.cursor() as cur:
        cur.execute('''SELECT table_name FROM information_schema.tables
                       WHERE table_schema=%s AND table_type='BASE TABLE' ORDER BY table_name''', (db,))
        return [r[0] for r in cur.fetchall()]


def _columns(conn, db, table):
    with conn.cursor() as cur:
        cur.execute('''SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, EXTRA
                       FROM information_schema.columns
                       WHERE table_schema=%s AND table_name=%s ORDER BY ORDINAL_POSITION''', (db, table))
        return {r[0]: {'type': r[1], 'nullable': r[2] == 'YES', 'default': r[3], 'extra': r[4]}
                for r in cur.fetchall()}


def _keys(conn, db, table):
    """返回 (pk_cols, unique_cols)；无主键时尝试唯一键。"""
    with conn.cursor() as cur:
        cur.execute('''SELECT CONSTRAINT_NAME, COLUMN_NAME FROM information_schema.key_column_usage
                       WHERE table_schema=%s AND table_name=%s AND REFERENCED_TABLE_NAME IS NULL
                       ORDER BY CONSTRAINT_NAME, ORDINAL_POSITION''', (db, table))
        by_constraint = {}
        for cname, col in cur.fetchall():
            by_constraint.setdefault(cname, []).append(col)
    pk = next((cols for name, cols in by_constraint.items() if name == 'PRIMARY'), None)
    if pk:
        return pk, []
    # 首个唯一键兜底（不含主键）
    for name, cols in by_constraint.items():
        if name.startswith('PRIMARY'):
            continue
        with conn.cursor() as cur:
            cur.execute('''SELECT COUNT(*) FROM information_schema.statistics
                           WHERE table_schema=%s AND table_name=%s AND index_name=%s AND NON_UNIQUE=0''',
                        (db, table, name))
            if cur.fetchone()[0] > 0:
                return [], cols
    return [], []


def _align_types(src, dst, src_db, dst_db, table):
    """列类型对齐：仿真列类型与生产不一致时 MODIFY 为生产类型（生产权威）。

    保留 AUTO_INCREMENT；转换失败（如 TEXT→数值 数据不合法）保留仿真类型不阻断。
    """
    src_cols = _columns(src, src_db, table)
    dst_cols = _columns(dst, dst_db, table)
    aligned = []
    for cname, sinfo in src_cols.items():
        dcol = dst_cols.get(cname)
        if dcol is None or dcol['type'] == sinfo['type']:
            continue
        auto_inc = ' AUTO_INCREMENT' if (dcol.get('extra') == 'auto_increment') else ''
        try:
            with dst.cursor() as cur:
                cur.execute(
                    f'ALTER TABLE `{table}` MODIFY COLUMN `{cname}` {sinfo["type"]} NULL{auto_inc}')
            aligned.append(f'{cname}:{dcol["type"]}→{sinfo["type"]}')
        except Exception as e:
            print(f'    [类型保留] {table}.{cname}: {str(e)[:80]}')
    if aligned:
        dst.commit()
        print(f'    [类型对齐] {table}: {aligned}')


def _ensure_table(src, dst, src_db, dst_db, table):
    """目标表不存在则按生产建；缺列则 ALTER ADD（可空防阻塞）。"""
    with dst.cursor() as cur:
        cur.execute('''SELECT COUNT(*) FROM information_schema.tables
                       WHERE table_schema=%s AND table_name=%s''', (dst_db, table))
        exists = cur.fetchone()[0] > 0
    if not exists:
        with src.cursor() as cur:
            cur.execute(f'SHOW CREATE TABLE `{table}`')
            ddl = cur.fetchone()[1].replace('CREATE TABLE', 'CREATE TABLE IF NOT EXISTS', 1)
        with dst.cursor() as cur:
            cur.execute(ddl)
        dst.commit()
        print(f'    [建表] {table}')
    # 补列
    src_cols = _columns(src, src_db, table)
    dst_cols = _columns(dst, dst_db, table)
    added = []
    for cname, info in src_cols.items():
        if cname not in dst_cols:
            ctype = info['type']
            with dst.cursor() as cur:
                cur.execute(f'ALTER TABLE `{table}` ADD COLUMN `{cname}` {ctype} NULL')
            added.append(cname)
    if added:
        dst.commit()
        print(f'    [补列] {table}: {added}')


def sync_table(src_db, dst_db, table):
    src = _conn(src_db)
    dst = _conn(dst_db)
    try:
        _ensure_table(src, dst, src_db, dst_db, table)
        _align_types(src, dst, src_db, dst_db, table)
        with src.cursor() as cur:
            cur.execute(f'SELECT * FROM `{table}`')
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
        if not rows:
            print(f'    {table}: 生产无数据，跳过')
            return
        pk, uniq = _keys(src, src_db, table)
        key_cols = pk or uniq
        quoted = ', '.join(f'`{c}`' for c in cols)
        placeholders = ', '.join(['%s'] * len(cols))

        if key_cols:
            # 精确统计：冲突=生产与仿真键交集
            key_sel = ', '.join('`%s`' % c for c in key_cols)
            with src.cursor() as cur:
                cur.execute(f'SELECT {key_sel} FROM `{table}`')
                src_keys = {tuple(r) for r in cur.fetchall()}
            with dst.cursor() as cur:
                cur.execute(f'SELECT {key_sel} FROM `{table}`')
                dst_keys = {tuple(r) for r in cur.fetchall()}
            updated = len(src_keys & dst_keys)
            inserted = len(src_keys - dst_keys)
            # 生产优先：全列覆盖
            upd_clause = ', '.join(f'`{c}`=VALUES(`{c}`)' for c in cols if c not in key_cols)
            sql = (f'INSERT INTO `{table}` ({quoted}) VALUES ({placeholders}) '
                   f'ON DUPLICATE KEY UPDATE {upd_clause}')
        else:
            updated, inserted = 0, len(rows)
            sql = f'INSERT IGNORE INTO `{table}` ({quoted}) VALUES ({placeholders})'
        with dst.cursor() as cur:
            for i in range(0, len(rows), BATCH):
                cur.executemany(sql, rows[i:i + BATCH])
        dst.commit()
        tag = '' if key_cols else '（无键表，仅追加）'
        print(f'    {table}: 生产 {len(rows)} 行 → 插入 {inserted} / 覆盖 {updated} {tag}')
    except Exception as e:
        dst.rollback()
        print(f'    [FAIL] {table}: {e}')
    finally:
        src.close()
        dst.close()


def main():
    for src_db, dst_db in PAIRS:
        src = _conn(src_db)
        try:
            tables = _tables(src, src_db)
        finally:
            src.close()
        print(f'== {src_db} → {dst_db}（{len(tables)} 表）==')
        for t in tables:
            sync_table(src_db, dst_db, t)
    print('[done] 同步完成')


if __name__ == '__main__':
    main()
