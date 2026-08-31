# -*- coding: utf-8 -*-
"""报告拼装器：子问题取数结果 → Markdown 月报。

首选 LLM 成文（正式月报文风）；LLM 失败时降级为程序拼装，保证必有产出。
"""
from core.llm_config import call_chat

# 拼装 Prompt 中单表行数/单元格长度上限（控制 token）
_PROMPT_MAX_ROWS = 20
_CELL_MAX_LEN = 50

_COMPOSE_SYSTEM = '你是电力营销数据平台的报告撰写专家，擅长把取数结果组织为正式的业务管理月报。'

_COMPOSE_PROMPT = '''根据以下取数结果撰写一份正式的业务管理报告（Markdown 格式）。

【报告标题】{title}
【单位范围】{org_scope}
【统计期间】{period}

【各章节取数结果】
{sections_block}

撰写要求：
1. 输出结构：# 报告标题；开头一段"概述"（总体结论先行）；随后按章节 ## 章节名 展开；
2. 每个章节：先一句话概括该节数据反映的情况，再贴关键数据表格（Markdown 表格，直接从取数结果摘取，不得编造数字），必要时分要点小结；
3. 标注"（取数失败）"或"（查询结果为空）"的章节：保留章节标题，写明该部分本期无数据或数据暂缺，严禁虚构任何数字；
4. 全文使用正式公文文风，所有数字、排名、表格必须逐字来自取数结果，取数结果中没有的指标一律写"本期无数据"；
5. 只输出 Markdown 正文，不要输出解释性文字。'''


def _fmt_cell(cell) -> str:
    s = '' if cell is None else str(cell)
    s = s.replace('|', '\\|').replace('\n', ' ')
    return s[:_CELL_MAX_LEN] + '…' if len(s) > _CELL_MAX_LEN else s


def _to_md_table(detail: dict, max_rows: int) -> str:
    headers = detail.get('headers') or []
    rows = (detail.get('rows') or [])[:max_rows]
    if not headers:
        return '（无数据）'
    lines = ['| ' + ' | '.join(_fmt_cell(h) for h in headers) + ' |',
             '|' + ' --- |' * len(headers)]
    for row in rows:
        lines.append('| ' + ' | '.join(_fmt_cell(c) for c in row) + ' |')
    if detail.get('row_count', 0) > max_rows:
        lines.append(f'\n（仅展示前 {max_rows} 行，共 {detail["row_count"]} 行）')
    return '\n'.join(lines)


def _fallback_report(plan: dict, details: list) -> str:
    """LLM 不可用时的程序拼装兜底。"""
    lines = [f'# {plan.get("report_title", "综合报告")}', '']
    meta = '　'.join(x for x in [
        f'单位：{("、".join(plan.get("org_scope") or [])) or "—"}',
        f'期间：{plan.get("period") or "—"}'] if not x.endswith('：—'))
    if meta:
        lines += [meta, '']
    by_qid = {d['qid']: d for d in details}
    for sec in plan.get('sections', []):
        lines.append(f'## {sec.get("section_title", "")}')
        lines.append('')
        for q in sec.get('questions', []):
            d = by_qid.get(q['qid'])
            if not d:
                continue
            lines.append(f'**{d["question"]}**')
            lines.append('')
            if d['status'] == 'ok':
                if d.get('row_count'):
                    lines.append(_to_md_table(d, _PROMPT_MAX_ROWS))
                else:
                    lines.append('（查询结果为空，本期无数据）')
            else:
                lines.append(f'（取数失败：{d.get("error", "")[:100]}）')
            lines.append('')
    return '\n'.join(lines)


def compose(plan: dict, details: list) -> dict:
    """返回 {'report_md': str, 'usage': dict, 'degraded': bool}。"""
    sections_block_parts = []
    by_qid = {d['qid']: d for d in details}
    for sec in plan.get('sections', []):
        sections_block_parts.append(f'### {sec.get("section_title", "")}')
        for q in sec.get('questions', []):
            d = by_qid.get(q['qid'])
            if not d:
                continue
            sections_block_parts.append(f'问题：{d["question"]}')
            if d['status'] == 'ok':
                if d.get('row_count'):
                    sections_block_parts.append(_to_md_table(d, _PROMPT_MAX_ROWS))
                else:
                    sections_block_parts.append('（查询结果为空，0 行，本期无数据）')
            else:
                sections_block_parts.append('（取数失败）')
        sections_block_parts.append('')

    prompt = _COMPOSE_PROMPT.format(
        title=plan.get('report_title', '综合报告'),
        org_scope='、'.join(plan.get('org_scope') or []) or '—',
        period=plan.get('period') or '—',
        sections_block='\n'.join(sections_block_parts),
    )
    try:
        resp = call_chat(
            [{'role': 'system', 'content': _COMPOSE_SYSTEM},
             {'role': 'user', 'content': prompt}],
            max_tokens=8000, thinking=True)
        return {'report_md': resp['content'].strip(), 'usage': resp.get('usage') or {},
                'degraded': False}
    except Exception as e:
        print(f'[WARN] 报告 LLM 拼装失败，降级程序拼装: {e}', flush=True)
        return {'report_md': _fallback_report(plan, details), 'usage': {}, 'degraded': True}
