# -*- coding: utf-8 -*-
"""kjgl01 系列数据库引导/重同步 CLI（薄封装，逻辑在 modules/resources/kjgl_sync.py）

用法：
    python tools/bootstrap_kjgl01.py     # 建四库（幂等）+ 全量重拷业务库 + 重建外键 + 行数校验
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.resources import kjgl_sync  # noqa: E402


def main():
    print(f'[1/3] 预建四库（前缀 {kjgl_sync.TARGET_PREFIX}）')
    kjgl_sync.ensure_databases()
    for name in (kjgl_sync.TARGET_DB, kjgl_sync.GOV_DB,
                 f'{kjgl_sync.TARGET_PREFIX}_log', f'{kjgl_sync.TARGET_PREFIX}_ontology'):
        print(f'  [库] {name} 就绪')

    print(f'[2/3] 全量重同步 {kjgl_sync.SOURCE_DB} -> {kjgl_sync.TARGET_DB}'
          '（DROP 重拷 + 外键重建）')
    stats = kjgl_sync.rebuild_business()
    for t in stats['tables']:
        print(f'  [拷] {t["name"]:<32} {t["rows"]} 行')
    print(f'  外键重建 {stats["fk_added"]} 个，耗时 {stats["duration_ms"]} ms')

    print('[3/3] 行数校验')
    if stats['all_match']:
        print(f'  全部一致 OK（{len(stats["tables"])} 张表）')
    else:
        print(f'  存在不一致: {stats["mismatched"]}')
    sys.exit(0 if stats['all_match'] else 1)


if __name__ == '__main__':
    main()
