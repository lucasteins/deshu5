# -*- coding: utf-8 -*-
"""暂存治理库重建脚本（幂等，可重复执行）：database01_governance ← marketing_governance 表结构

- 除 business_domains（保留 99 行 SG-CIM4.5 参考数据）外，暂存治理库全部表 DROP 后
  按生产库 SHOW CREATE TABLE 结构重建 —— 消除 SchemaPreloader 启动自建表的列漂移
  （如 schema_table_docs 缺 domain_l1/domain_l2/domain_l3）
- 治理内容一律留给素材提资流程写入，本脚本只保证表结构与参考数据
- 只读生产库，只写暂存库
"""
import sys

import pymysql

sys.path.insert(0, r'D:\codex\deshu5')
import config

SRC_DB = 'marketing_governance'
DST_DB = 'database01_governance'
KEEP = {'business_domains'}  # 保留参考数据，不重建


def main():
    src = pymysql.connect(host=config.MYSQL_HOST, port=config.MYSQL_PORT,
                          user=config.MYSQL_USER, password=config.MYSQL_PASSWORD,
                          database=SRC_DB, charset='utf8mb4')
    dst = pymysql.connect(host=config.MYSQL_HOST, port=config.MYSQL_PORT,
                          user=config.MYSQL_USER, password=config.MYSQL_PASSWORD,
                          database=DST_DB, charset='utf8mb4')
    try:
        with src.cursor() as cur:
            cur.execute('''SELECT table_name FROM information_schema.tables
                           WHERE table_schema=%s AND table_type='BASE TABLE' ORDER BY table_name''',
                        (SRC_DB,))
            tables = [r[0] for r in cur.fetchall()]

        # 1) 重建（business_domains 之外的表先 DROP 再按生产结构建）
        for t in tables:
            if t in KEEP:
                continue
            with dst.cursor() as dc:
                dc.execute(f'DROP TABLE IF EXISTS `{t}`')
            with src.cursor() as cur:
                cur.execute(f'SHOW CREATE TABLE `{t}`')
                ddl = cur.fetchone()[1].replace('CREATE TABLE', 'CREATE TABLE IF NOT EXISTS', 1)
            with dst.cursor() as dc:
                dc.execute(ddl)
            dst.commit()
            print(f'  [rebuild] {t}')
        print(f'[structure] {len(tables) - len(KEEP)} 张表已按生产结构重建')

        # 2) business_domains 参考数据（空表才复制）
        with dst.cursor() as dc:
            dc.execute('SELECT COUNT(*) FROM business_domains')
            cnt = dc.fetchone()[0]
        if cnt == 0:
            with src.cursor() as cur:
                cur.execute('SELECT * FROM business_domains')
                rows = cur.fetchall()
                cols = [d[0] for d in cur.description]
                quoted = ', '.join(f'`{c}`' for c in cols)
                placeholders = ', '.join(['%s'] * len(cols))
            with dst.cursor() as dc:
                dc.executemany(f'INSERT INTO business_domains ({quoted}) VALUES ({placeholders})', rows)
            dst.commit()
            print(f'[business_domains] 复制 {len(rows)} 行参考数据')
        else:
            print(f'[business_domains] 已有 {cnt} 行，跳过')
    finally:
        src.close()
        dst.close()
    print('[done] 暂存治理库重建完成')


if __name__ == '__main__':
    main()
