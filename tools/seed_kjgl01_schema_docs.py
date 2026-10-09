# -*- coding: utf-8 -*-
"""kjgl01_governance Schema 文档刷新 CLI（薄封装，逻辑在 modules/resources/kjgl_sync.py）

以 kjgl01 业务库 information_schema 为权威源，重建 schema_table_docs /
schema_column_docs / schema_relationship_docs 三张治理表。检索方实时查库，跑完即生效。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.resources import kjgl_sync  # noqa: E402


def main():
    docs = kjgl_sync.refresh_schema_docs()
    print(f"完成: 表文档 {docs['table_docs']} 条, "
          f"列文档 {docs['column_docs']} 条, 关系文档 {docs['relationship_docs']} 条")


if __name__ == '__main__':
    main()
