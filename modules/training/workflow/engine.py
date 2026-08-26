# -*- coding: utf-8 -*-
"""声明式工作流配置引擎：预设加载 + 深合并 + 解析。

DEFAULTS 逐键等于 config.py / 生成代码现值（merged 调优态）；预设 JSON 只写差异键，
与 DEFAULTS 深合并后交给 SQLGenerator 消费。键路径与消费点对照：

- locate.enabled        LLM 表定位通道开关          sql_generator._locate 闭包（原 config.LLM_LOCATE_ENABLED）
- locate.wait_max       定位结果等待总时长硬顶(秒)  fut_locate.result(timeout=...)（原 config.LLM_LOCATE_WAIT_MAX）
- locate.timeout        定位调用 HTTP 超时(秒)      _locate_tables_with_llm（原 config.LLM_TIMEOUT_LOCATE）
- draft_direct.enabled  草稿直出总开关              use_draft_direct 判定
- draft_direct.guards   草稿直出守门列表            'filter_signal'（过滤信号守门）/ 'bare_select_star'（裸 SELECT * 守门）
- prompt.budget         Prompt 总预算硬顶           _build_v3_prompt（原 PROMPT_BUDGET=10500）
- prompt.code_value_max 码值上下文上限              _build_code_value_context（原 CODE_VALUE_CONTEXT_MAX=2000）
- prompt.qa_max         问答对 RAG 上下文上限        _build_qa_context（原 QA_CONTEXT_MAX=1000）
- prompt.error_max      错题集上下文上限             _build_error_context（原 ERROR_CONTEXT_MAX=600）
- prompt.column_max_per_table  单表字段数硬封顶      _build_columns_context（原 COLUMN_MAX_PER_TABLE=30）
- prompt.full_column_top_n     全量字段表数          _merge_located_tables（原 FULL_COLUMN_TOP_N=3）
- generate.thinking     thinking 模式开关           self._thinking（原 config.KIMI_THINKING）
- generate.max_tokens   主生成 max_tokens           _generate_impl / _generate_with_intent 主调用（原 _call_llm 默认 8000）
- repair.enabled        快速修复通道总开关          _generate_with_intent 修复段
- repair.max_rounds     修复轮数                    for repair_round in range(...)（原 2）
- fallback.template     模板兜底开关                template_fallback 分支
- fallback.draft        草稿兜底开关                intent_draft_fallback 分支
- fallback.v24          v2.4 回退通道开关           _generate_impl 的 v2.4 分支
- rag.top_k             RAG 召回条数                __init__ 构造 RAGRetriever（原 config.RAG_TOP_K=5）
- knowledge.source      知识来源                    'ontology'（本体层，默认）/ 'legacy'（底座直读，回退档）
"""
import copy
import json
import os

import config

# 内置默认工作流 = 当前生产行为（merged 调优态）。
# 取值经 getattr(config, ...) 读取以保留环境变量覆盖语义（与现状逐点等价），
# 字面量兜底 = config.py 现值；预设只写差异键。
DEFAULTS = {
    'locate': {
        'enabled': getattr(config, 'LLM_LOCATE_ENABLED', False),   # 现值 false（DeepSeek 恒推理长尾，已关闭）
        'wait_max': getattr(config, 'LLM_LOCATE_WAIT_MAX', 15),
        'timeout': getattr(config, 'LLM_TIMEOUT_LOCATE', 25),
    },
    'draft_direct': {
        'enabled': True,
        'guards': ['filter_signal', 'bare_select_star'],
    },
    'prompt': {
        'budget': getattr(config, 'PROMPT_BUDGET', 10500),
        'code_value_max': getattr(config, 'CODE_VALUE_CONTEXT_MAX', 2000),
        'qa_max': getattr(config, 'QA_CONTEXT_MAX', 1000),
        'error_max': getattr(config, 'ERROR_CONTEXT_MAX', 600),
        'column_max_per_table': getattr(config, 'COLUMN_MAX_PER_TABLE', 30),
        'full_column_top_n': getattr(config, 'FULL_COLUMN_TOP_N', 3),
    },
    'generate': {
        'thinking': getattr(config, 'KIMI_THINKING', True),
        'max_tokens': 8000,   # _call_llm 主生成默认 max_tokens
    },
    'repair': {
        'enabled': True,
        'max_rounds': 2,
    },
    'fallback': {
        'template': True,
        'draft': True,
        'v24': True,
    },
    'template_first': {
        'enabled': False,   # 模板前置：签名模板命中且过校验则跳过 LLM 主生成（默认关=现状）
    },
    'rag': {
        'top_k': getattr(config, 'RAG_TOP_K', 5),
    },
    'knowledge': {
        # 知识来源：ontology = 本体层（marketing_ontology 库已生效版本）；
        # legacy = 数据底座直读（SchemaPreloader / 治理库，回退档）
        'source': getattr(config, 'KNOWLEDGE_SOURCE', 'ontology'),
        # 报表层优先：省/市/县三级统计语义问题优先检索本体 report 层实体（统计报表），
        # 无命中回退明细层汇总
        'report_first': getattr(config, 'KNOWLEDGE_REPORT_FIRST', True),
    },
}

_PRESET_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'presets')


def _deep_merge(base: dict, override: dict) -> dict:
    """深合并：override 的 dict 键递归合并，其余直接覆盖。"""
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def load_preset(name: str) -> dict:
    """读取 workflow/presets/<name>.json，与 DEFAULTS 深合并（预设缺省键回落 DEFAULTS）。"""
    path = os.path.join(_PRESET_DIR, f'{name}.json')
    if not os.path.exists(path):
        available = sorted(f[:-5] for f in os.listdir(_PRESET_DIR) if f.endswith('.json'))
        raise ValueError(f'未知工作流预设: {name}（可用: {available}）')
    with open(path, 'r', encoding='utf-8') as f:
        preset = json.load(f)
    wf = _deep_merge(copy.deepcopy(DEFAULTS), preset)
    wf['_name'] = name
    return wf


def resolve_workflow(workflow=None) -> dict:
    """解析工作流配置：
    None  -> 环境变量 SQL_WORKFLOW -> 持久化的当前预设（current.json）-> 'default'；
    str   -> 预设名（load_preset）；
    dict  -> 与 DEFAULTS 深合并后直接使用（缺省键回落 DEFAULTS）。
    """
    if workflow is None:
        workflow = os.environ.get('SQL_WORKFLOW') or get_current_name()
    if isinstance(workflow, str):
        return load_preset(workflow)
    if isinstance(workflow, dict):
        wf = _deep_merge(copy.deepcopy(DEFAULTS), workflow)
        wf['_name'] = workflow.get('_name', 'custom')
        return wf
    raise TypeError(f'workflow 参数仅支持 None/str/dict，收到: {type(workflow)}')


# ===== 预设枚举与当前预设持久化（供 /api/workflows 热切换） =====

_STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'current.json')


def list_presets() -> list:
    """枚举 workflow/presets/*.json，返回 [{name, note, overrides}]（overrides 为与 DEFAULTS 的差异键）。"""
    out = []
    if not os.path.isdir(_PRESET_DIR):
        return out
    for f in sorted(os.listdir(_PRESET_DIR)):
        if not f.endswith('.json'):
            continue
        name = f[:-5]
        try:
            with open(os.path.join(_PRESET_DIR, f), 'r', encoding='utf-8') as fp:
                data = json.load(fp)
        except Exception:
            continue
        out.append({
            'name': name,
            'note': data.get('_note', ''),
            'overrides': {k: v for k, v in data.items() if not k.startswith('_')},
        })
    return out


def get_current_name() -> str:
    """当前生效预设名：环境变量优先，其次 current.json 持久化选择，兜底 default。"""
    env = os.environ.get('SQL_WORKFLOW')
    if env:
        return env
    try:
        with open(_STATE_PATH, 'r', encoding='utf-8') as f:
            name = (json.load(f).get('current') or '').strip()
            if name:
                return name
    except Exception:
        pass
    return 'default'


def set_current(name: str) -> dict:
    """切换当前预设并持久化（current.json）。预设不存在抛 ValueError。返回解析后的完整配置。"""
    wf = load_preset(name)  # 不存在会抛 ValueError
    with open(_STATE_PATH, 'w', encoding='utf-8') as f:
        json.dump({'current': name}, f, ensure_ascii=False)
    return wf
