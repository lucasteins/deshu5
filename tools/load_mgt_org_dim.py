# -*- coding: utf-8 -*-
r"""管理单位维表 dim_cst_mgt_org 全量装载（database01 + marketing_40 双库）

来源：解密后的 管理单位dim_cst_mgt_org.xlsx（5533 行浙江机构全树，工具内部路径常量）。
源文件为 Office 密码加密文档；解密再生方式（密码向数据提供方索取，勿入库勿入码）：
  python -c "import sys; sys.path.insert(0, r'.vendor/msoffcrypto'); import msoffcrypto, io; \
of=msoffcrypto.OfficeFile(open(r'<源文件>','rb')); of.load_key(password='<密码>'); \
out=io.BytesIO(); of.decrypt(out); open(r'dim_cst_mgt_org_decrypted.xlsx','wb').write(out.getvalue())"
规则：
- Excel 第 1 行中文说明表头、第 2 行英文字段名、第 3 行起数据
- '\N'（导出 NULL 字面量）与空值 → NULL；代码列按字符串原样保留（前导零安全）
- mgt_org_code='0' 的脏行剔除（2 行：'国网公司/总部'、'综合室/地市'，日志留痕）
- 全量替换：DELETE + INSERT（原有 16/8 行为残损子集，以本文件为权威）
- 幂等，可重跑；--dry-run 只打印不写库
"""
import argparse
import sys

sys.path.insert(0, r'D:\codex\deshu5')
import pandas as pd
import pymysql

import config

XLS = r'D:\codex\deshu5\dim_cst_mgt_org_decrypted.xlsx'
TARGET_DBS = ['database01', 'marketing_40']

# Excel 英文名行 → 库表列（29 列）
COLMAP = {
    'mgt_org_id': 'mgt_org_id', 'mgt_org_code': 'mgt_org_code', 'mgt_org_name': 'mgt_org_name',
    'prnt_mgt_org_code': 'prnt_mgt_org_code', 'mgt_org_type_desc': 'mgt_org_type_desc',
    'dist_lv_desc': 'dist_lv_desc', 'valid_flag_desc': 'valid_flag_desc',
    'abbr1': 'mgt_org_chn_abbr1', 'abbr2': 'mgt_org_chn_abbr2',
    'mgt_org_char_desc': 'mgt_org_char_desc', 'maj_attr_desc': 'maj_attr_desc',
    'valid_date': 'valid_date', 'invalid_date': 'invalid_date',
    'relamgt_org_id': 'relamgt_org_id', 'sys_mgt_org_id': 'sys_mgt_org_id',
    'srv_kind': 'srv_kind', 'srv_kind_desc': 'srv_kind_desc', 'area': 'area',
    'province_code': 'province_code', 'province_name': 'province_name', 'province_abbr': 'province_abbr',
    'city_code': 'city_code', 'city_name': 'city_name', 'city_abbr': 'city_abbr',
    'county_code': 'county_code', 'county_name': 'county_name', 'county_abbr': 'county_abbr',
    'station_code': 'station_code', 'station_name': 'station_name',
}

EXCEL_COLS = ['mgt_org_id', 'mgt_org_code', 'mgt_org_name', 'prnt_mgt_org_code', 'mgt_org_type',
              'mgt_org_type_desc', 'dist_lv', 'dist_lv_desc', 'valid_flag', 'valid_flag_desc',
              'abbr1', 'abbr2', 'mgt_org_char', 'mgt_org_char_desc', 'maj_attr', 'maj_attr_desc',
              'valid_date', 'invalid_date', 'relamgt_org_id', 'sys_mgt_org_id', 'srv_kind',
              'srv_kind_desc', 'area', 'province_code', 'province_name', 'province_abbr',
              'city_code', 'city_name', 'city_abbr', 'county_code', 'county_name', 'county_abbr',
              'station_code', 'station_name', 'update_time', 'ingest_time', 'partition_date']


def clean(v):
    r"""NaN/'\N'/空串 → None；整数值浮点 → 整数字符串；其余 strip。"""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, float):
        return str(int(v)) if v == int(v) else str(v)
    s = str(v).strip()
    if s in ('', '\\N', 'NaT', 'nan'):
        return None
    return s


def load_rows():
    df = pd.read_excel(XLS, header=0, skiprows=[1])
    df.columns = EXCEL_COLS
    rows, dropped = [], []
    for _, r in df.iterrows():
        rec = {db_col: clean(r[xcol]) for xcol, db_col in COLMAP.items()}
        if rec['mgt_org_code'] in (None, '0'):
            dropped.append((rec['mgt_org_code'], rec['mgt_org_name'], rec['dist_lv_desc']))
            continue
        rows.append(rec)
    return rows, dropped


def write_db(conn, db, rows, dry_run):
    cols = list(COLMAP.values())
    with conn.cursor() as cur:
        cur.execute('SELECT COUNT(*) FROM information_schema.columns '
                    'WHERE table_schema=%s AND table_name=%s', (db, 'dim_cst_mgt_org'))
        if cur.fetchone()[0] == 0:
            raise RuntimeError(f'{db}.dim_cst_mgt_org 不存在')
        if dry_run:
            print(f'  [dry] {db}.dim_cst_mgt_org: DELETE ALL + INSERT {len(rows)} 行')
            return
        # marketing_40 存在 FK（dim_cst_dev → mgt_org_code）：临时关 FK 检查做整体置换，
        # 装载后校验被引用码零孤儿（Excel 全覆盖已核实）
        cur.execute('SET FOREIGN_KEY_CHECKS=0')
        cur.execute('DELETE FROM dim_cst_mgt_org')
        ph = ','.join(['%s'] * len(cols))
        sql = f"INSERT INTO dim_cst_mgt_org ({', '.join('`' + c + '`' for c in cols)}) VALUES ({ph})"
        cur.executemany(sql, [[r[c] for c in cols] for r in rows])
        cur.execute('SET FOREIGN_KEY_CHECKS=1')
        conn.commit()
        cur.execute('SELECT COUNT(*), COUNT(DISTINCT mgt_org_code) FROM dim_cst_mgt_org')
        cnt, dc = cur.fetchone()
        print(f'  [ok] {db}.dim_cst_mgt_org: {cnt} 行（distinct code {dc}）')
        cur.execute('SELECT COUNT(*) FROM dim_cst_dev d LEFT JOIN dim_cst_mgt_org o '
                    'ON d.mgt_org_code = o.mgt_org_code WHERE o.mgt_org_code IS NULL'
                    if db == 'marketing_40' else 'SELECT 0')
        orphans = cur.fetchone()[0]
        print(f'  [fk-check] dim_cst_dev 孤儿行: {orphans}（应为 0）')
        if orphans:
            raise RuntimeError(f'{db}: FK 孤儿 {orphans} 行，需核查')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    rows, dropped = load_rows()
    print(f'解析：{len(rows)} 行有效，剔除 {len(dropped)} 行 {dropped}')
    lv = {}
    for r in rows:
        lv[r['dist_lv_desc']] = lv.get(r['dist_lv_desc'], 0) + 1
    print('层级分布:', lv)

    for db in TARGET_DBS:
        conn = pymysql.connect(host=config.MYSQL_HOST, port=config.MYSQL_PORT,
                               user=config.MYSQL_USER, password=config.MYSQL_PASSWORD,
                               database=db, charset='utf8mb4')
        try:
            write_db(conn, db, rows, args.dry_run)
        finally:
            conn.close()

    if not args.dry_run:
        # 治理库 schema_table_docs 行数刷新（两个治理库都刷，dim 表结构未变只动 row_count）
        for gdb, biz in (('database01_governance', 'database01'), ('marketing_governance', 'marketing_40')):
            gov = pymysql.connect(host=config.MYSQL_HOST, port=config.MYSQL_PORT,
                                  user=config.MYSQL_USER, password=config.MYSQL_PASSWORD,
                                  database=gdb, charset='utf8mb4')
            try:
                with gov.cursor() as cur:
                    cur.execute('SELECT COUNT(*) FROM information_schema.tables '
                                'WHERE table_schema=%s AND table_name=%s', (gdb, 'schema_table_docs'))
                    if not cur.fetchone()[0]:
                        continue
                    cur.execute('UPDATE schema_table_docs SET row_count=%s, updated_at=NOW() '
                                'WHERE table_name=%s', (len(rows), 'dim_cst_mgt_org'))
                    print(f'  [ok] {gdb}.schema_table_docs row_count 刷新（{cur.rowcount} 行）')
                gov.commit()
            finally:
                gov.close()
    print('[done] dim_cst_mgt_org 双库装载完成')


if __name__ == '__main__':
    main()
