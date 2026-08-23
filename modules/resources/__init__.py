# -*- coding: utf-8 -*-
"""基础数据资源层（数据资源+统计看板模块的底座）。

本层 provider 包装治理库表；消费方读库失败或空表时一律回退代码常量
（见各 provider 模块底部的 get_* 读取口），保证任何环境可启动。
"""
from modules.resources.base import ResourceProvider, ReadOnlyResourceError
from modules.resources.registry import ResourceRegistry, registry
from modules.resources.providers import (
    QAPairProvider, ErrorRecordProvider, CodeValueProvider, CodeValueItemProvider,
    SchemaCatalogProvider, SchemaGraphProvider,
    KeywordTableProvider, BusinessRuleProvider, SQLTemplateProvider,
)

# 注册全部资源（惰性：register 只登记类，首次 get 时才构造实例）
registry.register(QAPairProvider)
registry.register(ErrorRecordProvider)
registry.register(CodeValueProvider)
registry.register(CodeValueItemProvider)
registry.register(SchemaCatalogProvider)
registry.register(SchemaGraphProvider)
registry.register(KeywordTableProvider)
registry.register(BusinessRuleProvider)
registry.register(SQLTemplateProvider)

__all__ = [
    'registry', 'ResourceRegistry', 'ResourceProvider', 'ReadOnlyResourceError',
    'QAPairProvider', 'ErrorRecordProvider', 'CodeValueProvider', 'CodeValueItemProvider',
    'SchemaCatalogProvider', 'SchemaGraphProvider',
    'KeywordTableProvider', 'BusinessRuleProvider', 'SQLTemplateProvider',
]
