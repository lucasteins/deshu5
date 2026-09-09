# -*- coding: utf-8 -*-
"""LLM HTTP 传输层：会话、主调用、流式、推理截断降级与重试兜底
（自 modules/training/engine/sql_generator.py 下沉，签名由调用方薄封装保持不变）。

_LLM_HTTP：trust_env=False 绕过本机系统代理（127.0.0.1:7890 故障时会把 OpenSSL 握手
重置为 SSL EOF；curl 不走注册表代理故正常）。仅影响传输层，不影响生成逻辑。
"""
import json
import time
from typing import Dict, Optional

import requests

import config

_LLM_HTTP = requests.Session()
_LLM_HTTP.trust_env = False


def call_llm(prompt: str, user_question: str = None, max_tokens: int = 8000, thinking: Optional[bool] = None, default_thinking: bool = False, timeout: Optional[int] = None, stream_cb=None, model_override: Optional[str] = None, effort: Optional[str] = None, usage_cb=None, table_names_fn=None) -> Dict:
    from core.llm_config import current as _llm_current, build_request as _llm_build, preview_config as _llm_preview
    cfg = _llm_current()
    # 辅助任务模型覆盖（仅 deepseek provider 下生效）：修复/定位等快速调用走非推理模型
    if model_override and cfg.get('provider') == 'deepseek':
        cfg = _llm_preview({'provider': 'deepseek', 'model': model_override})
    if not cfg.get('api_key'):
        raise ValueError(f"{cfg['provider']} API Key 未配置（请在前端设置页配置）")
    print(f"[DEBUG] prompt_length={len(prompt)}", flush=True)
    last_error = None
    t_start = time.perf_counter()
    http_timeout = timeout if timeout is not None else config.LLM_TIMEOUT_FAST

    # Phase 1: 主调用（thinking 默认跟随配置；辅助任务如表定位传 thinking=False 走快速模式）
    # Provider 间参数差异（temperature/thinking 参数形态）统一由 llm_config.build_request 封装
    use_thinking = default_thinking if thinking is None else thinking
    url, headers, payload = _llm_build(
        [{'role': 'user', 'content': prompt}], max_tokens=max_tokens, thinking=use_thinking,
        cfg=cfg, effort=effort)

    # 推理截断降级（2026-08-14）：恒推理模型推理超过 max_tokens 上限时返回空内容，
    # 原样重试必重蹈覆辙——降级到非推理辅助模型（AUX_MODEL）重试一次
    def _degrade_with_aux():
        aux = getattr(config, 'AUX_MODEL', None)
        if not aux or cfg.get('provider') != 'deepseek' or payload.get('model') == aux:
            return None
        try:
            cfg2 = _llm_preview({'provider': 'deepseek', 'model': aux})
            u2, h2, p2 = _llm_build([{'role': 'user', 'content': prompt}],
                                    max_tokens=max_tokens, thinking=False, cfg=cfg2)
            t0 = time.perf_counter()
            r2 = _LLM_HTTP.post(u2, headers=h2, json=p2, timeout=http_timeout)
            r2.raise_for_status()
            d2 = r2.json()
            c2 = d2['choices'][0]['message']['content']
            print(f"[DEBUG] 推理截断降级 {aux} content_len={len(c2) if c2 else 0} elapsed={(time.perf_counter() - t0) * 1000:.1f}ms", flush=True)
            if c2 and c2.strip():
                return {'content': c2, 'usage': d2.get('usage', {})}
        except Exception as e:
            print(f"[DEBUG] 降级重试失败: {e}", flush=True)
        return None

    # 流式通道（仅主生成调用传入 stream_cb）：SSE 增量转发 reasoning_content/content 到思考窗口
    if stream_cb is not None:
        spayload = dict(payload, stream=True)
        resp = None
        try:
            t0 = time.perf_counter()
            resp = _LLM_HTTP.post(url, headers=headers, json=spayload, timeout=http_timeout, stream=True)
            resp.raise_for_status()
            resp.encoding = 'utf-8'
            parts = {'reasoning': [], 'content': []}
            full_content = []
            stream_fr = None
            last_flush = time.perf_counter()

            def _flush(force=False):
                nonlocal last_flush
                now = time.perf_counter()
                if not force and now - last_flush < 0.25:
                    return
                last_flush = now
                for ph in ('reasoning', 'content'):
                    if parts[ph]:
                        chunk = ''.join(parts[ph])
                        parts[ph] = []
                        try:
                            stream_cb(ph, chunk)
                        except Exception:
                            pass

            for raw in resp.iter_lines(decode_unicode=True):
                if not raw:
                    continue
                body = raw.strip()
                if not body.startswith('data:'):
                    continue
                body = body[5:].strip()
                if body == '[DONE]':
                    break
                try:
                    sevt = json.loads(body)
                    choice0 = (sevt.get('choices') or [{}])[0]
                    delta = choice0.get('delta') or {}
                    if choice0.get('finish_reason'):
                        stream_fr = choice0['finish_reason']
                except Exception:
                    continue
                rc = delta.get('reasoning_content')
                dc = delta.get('content')
                if rc:
                    parts['reasoning'].append(rc)
                if dc:
                    parts['content'].append(dc)
                    full_content.append(dc)
                _flush()
            _flush(force=True)
            content = ''.join(full_content)
            elapsed = (time.perf_counter() - t0) * 1000
            print(f"[DEBUG] provider={cfg['provider']} model={payload['model']} stream thinking={use_thinking} content_len={len(content)} elapsed={elapsed:.1f}ms", flush=True)
            if content.strip():
                return {'content': content, 'usage': {}}
            # 流式空响应：推理截断直接降级辅助模型（不再走非流式原样重试，省 2 分钟）
            if stream_fr == 'length':
                res = _degrade_with_aux()
                if res:
                    return res
            raise ValueError('流式返回内容为空')
        except Exception as e:
            print(f"[DEBUG] stream error={e}，回退非流式主调用", flush=True)
        finally:
            try:
                if resp is not None:
                    resp.close()
            except Exception:
                pass

    for empty_retry in range(2):  # content 为空属边缘情况（推理截断/端点抖动），原样重试一次
        t0 = time.perf_counter()
        try:
            response = _LLM_HTTP.post(url, headers=headers, json=payload, timeout=http_timeout)
            response.raise_for_status()
            data = response.json()
            if usage_cb: usage_cb(data.get('usage'))
            content = data['choices'][0]['message']['content']
            finish_reason = (data['choices'][0].get('finish_reason') or '')
            elapsed = (time.perf_counter() - t0) * 1000
            print(f"[DEBUG] provider={cfg['provider']} model={payload['model']} thinking={use_thinking} content_len={len(content) if content else 0} elapsed={elapsed:.1f}ms", flush=True)
            if content and content.strip():
                return {'content': content, 'usage': data.get('usage', {})}
            last_error = '返回内容为空'
            # 长耗时空响应（恒推理模型推理截断，finish_reason=length）重试必重蹈覆辙，直接放弃；
            # 仅快速返回的空响应（端点抖动）原样重试一次
            if empty_retry == 0 and finish_reason != 'length' and elapsed <= config.LLM_EMPTY_RETRY_MAX_MS:
                print(f"[DEBUG] content 为空（{elapsed:.0f}ms, finish={finish_reason}），原样重试一次", flush=True)
                continue
            print(f"[DEBUG] content 为空（{elapsed:.0f}ms, finish={finish_reason}），判定推理截断，放弃重试", flush=True)
            break
        except requests.exceptions.HTTPError as e:
            elapsed = (time.perf_counter() - t0) * 1000
            print(f"[DEBUG] thinking={use_thinking} http_error={response.status_code} elapsed={elapsed:.1f}ms {response.text[:120]}", flush=True)
            last_error = e
            break
        except Exception as e:
            elapsed = (time.perf_counter() - t0) * 1000
            print(f"[DEBUG] thinking={use_thinking} error={e} elapsed={elapsed:.1f}ms", flush=True)
            last_error = e
            break

    # 主调用失败：先尝试降级辅助模型（推理截断/空响应场景），再视配置走重试兜底
    res = _degrade_with_aux()
    if res:
        return res

    # Phase 2: 重试兜底（默认关闭，避免极端慢请求）
    if not getattr(config, 'USE_REASONING_FALLBACK', False):
        total_elapsed = (time.perf_counter() - t_start) * 1000
        print(f"[DEBUG] 主调用失败（{total_elapsed:.1f}ms），重试兜底已关闭", flush=True)
        raise ValueError(f"LLM 调用失败: {last_error or '返回为空或被拒绝'}")

    for attempt in range(2):
        url, headers, payload = _llm_build(
            [{'role': 'user', 'content': prompt}], max_tokens=16000, thinking=True)
        t0 = time.perf_counter()
        try:
            response = _LLM_HTTP.post(url, headers=headers, json=payload, timeout=config.LLM_TIMEOUT_REASONING)
            response.raise_for_status()
            data = response.json()
            if usage_cb: usage_cb(data.get('usage'))
            content = data['choices'][0]['message']['content']
            elapsed = (time.perf_counter() - t0) * 1000
            print(f"[DEBUG] reasoning attempt={attempt} content_len={len(content) if content else 0} elapsed={elapsed:.1f}ms", flush=True)
            if content and content.strip():
                return {'content': content, 'usage': data.get('usage', {})}
            short_question = user_question or ""
            short_prompt = (
                f"你是电力营销 SQL 专家。根据问题生成一行可执行 SELECT。\n"
                f"问题：{short_question}\n"
                f"可用表：{', '.join((table_names_fn() if table_names_fn else [])[:10])}\n"
                f"只输出一行 SELECT。"
            )
            prompt = short_prompt
            print(f"[DEBUG] retrying with short_prompt, len={len(short_prompt)}", flush=True)
        except Exception as e:
            last_error = e
            elapsed = (time.perf_counter() - t0) * 1000
            print(f"[DEBUG] reasoning attempt={attempt} error={e} elapsed={elapsed:.1f}ms", flush=True)
            if attempt < 1:
                time.sleep(min(2 ** attempt, 8))
    raise ValueError(f"LLM 调用失败或返回内容为空: {last_error}")
