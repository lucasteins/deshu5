"""运行时 LLM 设置中心（多 Provider 兼容）。

- 持久化：smart-query-trainer/llm_settings.json（.gitignore，Key 不落代码库）
- 结构：{active: 'kimi'|'deepseek', providers: {kimi: {...}, deepseek: {...}}}
- 文件不存在时回落到 config.py 的 KIMI_*（保持现有行为）
- 调用点每次调用经 build_request() 取配置，前端切换 Provider 免重启
"""
import json
import os
import threading
from pathlib import Path

import config

_SETTINGS_PATH = Path(__file__).parent.parent / 'llm_settings.json'
_LOCK = threading.Lock()

# Provider 预设（Key 不入库，仅默认地址与模型）
PROVIDER_PRESETS = {
    'kimi': {
        'api_url': 'https://api.kimi.com/coding/v1',
        'model': 'kimi-for-coding-highspeed',
    },
    'deepseek': {
        'api_url': 'https://api.deepseek.com',
        'model': 'deepseek-v4-flash',  # 2026-08-14 起默认 flash：v4-pro 太慢、chat 无推理准确率低；flash 兼顾
    },
}


def _defaults() -> dict:
    """无设置文件时的默认配置：kimi 取自 config.py/env"""
    providers = {name: dict(preset) for name, preset in PROVIDER_PRESETS.items()}
    providers['kimi']['api_url'] = config.KIMI_API_URL
    providers['kimi']['api_key'] = config.KIMI_API_KEY
    providers['kimi']['model'] = config.KIMI_MODEL
    for name in providers:
        providers[name].setdefault('api_key', '')
        providers[name].setdefault('temperature', None)
    return {'active': 'kimi', 'providers': providers}


def _load() -> dict:
    if not _SETTINGS_PATH.exists():
        return _defaults()
    try:
        data = json.loads(_SETTINGS_PATH.read_text(encoding='utf-8'))
        merged = _defaults()
        if data.get('active') in merged['providers']:
            merged['active'] = data['active']
        for name, p in (data.get('providers') or {}).items():
            if name in merged['providers'] and isinstance(p, dict):
                merged['providers'][name].update(p)
        return merged
    except Exception:
        return _defaults()


def current() -> dict:
    """当前生效的 provider 配置：{provider, api_url, api_key, model, temperature}"""
    data = _load()
    active = data['active']
    cfg = dict(data['providers'][active])
    cfg['provider'] = active
    return cfg


def all_settings() -> dict:
    """全部配置（api_key 脱敏）——供设置页展示"""
    data = _load()
    masked = {'active': data['active'], 'providers': {}}
    for name, p in data['providers'].items():
        q = dict(p)
        key = q.get('api_key') or ''
        q['api_key_masked'] = f"sk-****{key[-4:]}" if len(key) >= 8 else ('已配置' if key else '')
        q['has_key'] = bool(key)
        q.pop('api_key', None)
        masked['providers'][name] = q
    return masked


def save(payload: dict) -> dict:
    """保存设置。payload: {provider, api_url?, model?, api_key?, temperature?, set_active?}
    api_key 传空/脱敏值时保留原值。返回脱敏后的全部配置。"""
    provider = (payload.get('provider') or '').strip()
    if provider not in PROVIDER_PRESETS:
        raise ValueError(f'不支持的 provider: {provider}')
    with _LOCK:
        data = _load()
        p = data['providers'][provider]
        _merge_into(p, payload)
        if payload.get('set_active'):
            data['active'] = provider
        _SETTINGS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    return all_settings()


def preview_config(payload: dict) -> dict:
    """按 payload 合成一份临时配置（不落库，供连通性测试）"""
    provider = (payload.get('provider') or '').strip()
    if provider not in PROVIDER_PRESETS:
        raise ValueError(f'不支持的 provider: {provider}')
    data = _load()
    p = dict(data['providers'][provider])
    _merge_into(p, payload)
    p['provider'] = provider
    return p


def _merge_into(p: dict, payload: dict):
    if payload.get('api_url'):
        p['api_url'] = payload['api_url'].strip().rstrip('/')
    if payload.get('model'):
        p['model'] = payload['model'].strip()
    key = (payload.get('api_key') or '').strip()
    if key and not key.startswith('sk-****') and key != '已配置':
        p['api_key'] = key
    if 'temperature' in payload:
        t = payload['temperature']
        p['temperature'] = float(t) if t not in (None, '') else None


def build_request(messages: list, max_tokens: int = 8000, thinking=None, cfg: dict = None, effort: str = None) -> tuple:
    """按 provider 约定构建请求。返回 (url, headers, payload)。

    thinking: None=跟随 provider 默认（kimi 读 config.KIMI_THINKING）；False=辅助任务快速模式。
    cfg: 缺省用 current()；测试场景可传 preview_config() 的临时配置。
    effort（DeepSeek 思考强度）: none/low/high/max；缺省读环境变量 LLM_REASONING_EFFORT 或 config。
    差异封装：
    - kimi：thinking→temp 1.0 + {type:enabled, budget_tokens}；非 thinking→0.6 + disabled（端点硬约束）
    - deepseek：思考模式/强度按文档下发（仅 v4/reasoner 系模型）；推理占 max_tokens（floor 16000）
    """
    cfg = cfg or current()
    provider = cfg['provider']
    if thinking is None:
        thinking = getattr(config, 'KIMI_THINKING', True)
    temperature_setting = cfg.get('temperature')

    payload = {
        'model': cfg['model'],
        'messages': messages,
        'max_tokens': max_tokens,
    }
    if provider == 'kimi':
        if thinking and payload['max_tokens'] < 16000:
            payload['max_tokens'] = 16000  # reasoning_content 占额度，预留不足会返回空
        payload['temperature'] = 1.0 if thinking else 0.6  # 端点硬约束（实测其他值 400）
        payload['thinking'] = (
            {'type': 'enabled', 'budget_tokens': getattr(config, 'KIMI_THINKING_BUDGET', 6144)}
            if thinking else {'type': 'disabled'}
        )
    else:  # deepseek（及其他 OpenAI 兼容模型）
        # 思考模式控制（DeepSeek 文档）。优先级：显式 effort 形参 > thinking 形参 > 环境/配置默认 effort。
        # none=关闭思考；low/high/max=思考强度；模型不支持思考参数则完全不发送
        effort_env = os.environ.get('LLM_REASONING_EFFORT', '') or getattr(config, 'LLM_REASONING_EFFORT', '')
        model_lc = (payload['model'] or '').lower()
        supports_thinking = ('v4' in model_lc) or ('reasoner' in model_lc)
        if supports_thinking:
            if effort == 'none':
                payload['thinking'] = {'type': 'disabled'}
            elif effort in ('low', 'high', 'max'):
                payload['thinking'] = {'type': 'enabled'}
                payload['reasoning_effort'] = effort
            elif thinking is False:
                payload['thinking'] = {'type': 'disabled'}
            elif thinking is True:
                payload['thinking'] = {'type': 'enabled'}
                if effort_env in ('low', 'high', 'max'):
                    payload['reasoning_effort'] = effort_env
            elif effort_env == 'none':
                payload['thinking'] = {'type': 'disabled'}
            elif effort_env in ('low', 'high', 'max'):
                payload['thinking'] = {'type': 'enabled'}
                payload['reasoning_effort'] = effort_env
        thinking_on = supports_thinking and payload.get('thinking', {}).get('type') != 'disabled'
        # 推理占 max_tokens：思考开启时地板 16000；关闭时按调用方给定值（辅助任务小预算快速返回）
        if thinking_on and payload['max_tokens'] < 16000:
            payload['max_tokens'] = 16000
        if temperature_setting is not None:
            payload['temperature'] = float(temperature_setting)

    url = cfg['api_url'] + '/chat/completions'
    headers = {
        'Authorization': f"Bearer {cfg['api_key']}",
        'Content-Type': 'application/json',
    }
    return url, headers, payload


def call_chat(messages: list, max_tokens: int = 8000, thinking=None, cfg: dict = None,
              effort: str = None, timeout: int = None) -> dict:
    """基于 build_request() 的最简同步 LLM 调用封装（供报告生成等新模块复用）。

    返回 {'content': str, 'usage': dict}；空响应重试 1 次。
    timeout 缺省用 config.LLM_TIMEOUT_FAST。
    """
    import requests

    cfg = cfg or current()
    if not cfg.get('api_key'):
        raise ValueError(f"{cfg.get('provider')} API Key 未配置（请在前端设置页配置）")
    url, headers, payload = build_request(
        messages, max_tokens=max_tokens, thinking=thinking, cfg=cfg, effort=effort)
    timeout = timeout or getattr(config, 'LLM_TIMEOUT_FAST', 120)

    last_err = None
    for _attempt in range(2):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
            message = data['choices'][0]['message']
            content = message.get('content') or ''
            # 推理模型可能把正文放在 content、推理放 reasoning_content；正文为空视为可重试
            if not content.strip():
                last_err = ValueError('LLM 返回空内容')
                continue
            return {'content': content, 'usage': data.get('usage') or {}}
        except Exception as e:
            last_err = e
    raise last_err if last_err else ValueError('LLM 调用失败')
