# -*- coding: utf-8 -*-
"""基础数据资源 Provider 集。

存量资源（P1）：qa_pair / error_record / code_value / schema_catalog / schema_graph
知识资源（P2）：keyword_table_map / business_rules / sql_template / code_value_item
"""
from modules.resources.providers.qa_pair import QAPairProvider
from modules.resources.providers.error_record import ErrorRecordProvider
from modules.resources.providers.code_value import CodeValueProvider
from modules.resources.providers.code_value_item import CodeValueItemProvider
from modules.resources.providers.schema_catalog import SchemaCatalogProvider
from modules.resources.providers.schema_graph import SchemaGraphProvider
from modules.resources.providers.keyword_table_map import KeywordTableProvider
from modules.resources.providers.business_rule import BusinessRuleProvider
from modules.resources.providers.sql_template import SQLTemplateProvider

__all__ = [
    'QAPairProvider', 'ErrorRecordProvider', 'CodeValueProvider', 'CodeValueItemProvider',
    'SchemaCatalogProvider', 'SchemaGraphProvider',
    'KeywordTableProvider', 'BusinessRuleProvider', 'SQLTemplateProvider',
]
