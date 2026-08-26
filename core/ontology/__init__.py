# -*- coding: utf-8 -*-
"""本体模型层：基础数据层语义的持久化本体（marketing_ontology 库）。

- model       本体内存模型与 diff
- fingerprint 底座结构指纹（漂移检测）
- builder     从底座提炼本体快照
- store       marketing_ontology 库读写（正式版 + 提案）
- service     问数模块统一访问门面（单例）
- export      OWL/RDF 多格式导出
"""
