# -*- coding: utf-8 -*-
"""LLM 实体层提案：以当前档位真实 Schema 为上下文，产出实体映射草稿（绝不落库）。

流程：组装上下文（表/列/注释/行数/物理外键/已有概念映射）→ LLM 聚类设计 →
严格 JSON 解析 → 结构校验 → 草稿落 drafts/ 供人工审阅。
草稿审定后用 apply_entity_defs.py 落库并生成提案。

用法：
    python skills/ontology-distill/scripts/propose_entities.py
        [--note "业务背景补充"] [--max-col 25] [--out drafts/xxx.json]
"""
import argparse
import json
import re
import sys
from datetime import datetime

import ont_common as oc

_PROMPT = '''你是数据治理与本体建模专家。下面是一个业务系统的真实库表结构（{profile_label}，共 {n} 张表）。
请把这些物理表聚合提炼为一组**业务实体**，作为该库本体的实体层。

[设计要求]
1. 分层（layer 三选一）：
   - master：主数据/维度（组织机构、人员、字典这类被反复引用的基础档案）
   - business：业务过程数据（项目、成果、文档、经费、指标等业务对象及其明细）
   - report：统计报表/汇总结果表
2. 实体名 name：英文 PascalCase 标识符（如 Project、Achievement、FundingItem）；label：中文名。
3. member_tables：该实体涵盖的物理表，**只能出现上面表清单中的表名**；
   **每张表都必须归入恰好一个实体**（实体即表的语义分组，一对一归属）。
4. 多对多/关联表（如 项目-人员、成果-署名、文档-成果 这类纯关系表）：就近挂到其主导业务实体，
   并在该实体 comment 中说明它是关系表。
5. comment：80~200 字中文业务描述（实体含义、涵盖表的业务范围、典型问数场景），
   只能基于给定信息概括，禁止编造未出现的表、列或数值。
6. parent 留空字符串（本库暂不建实体层级）。
7. 实体数量按语义自然聚合，不要一表一实体，也不要全部塞进一个实体。

[输出契约]
只输出一个 JSON 对象（不要 markdown 代码围栏、不要任何解释文字），结构：
{{"entities": [{{"name": "...", "label": "...", "layer": "master|business|report",
  "parent": "", "member_tables": ["..."], "comment": "..."}}],
  "notes": "一句话说明整体划分思路"}}

[库表结构]
{schema_text}
{extra_note}'''


def _parse_llm_json(text: str) -> dict:
    """LLM 返回 → dict：json.loads 优先，失败提取首个 {...} 块兜底。"""
    try:
        data = json.loads(text or '')
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    m = re.search(r'\{.*\}', text or '', re.DOTALL)
    if m:
        return json.loads(m.group(0))
    raise ValueError('LLM 返回无法解析为 JSON 对象')


def main():
    ap = argparse.ArgumentParser(description='LLM 实体层提案（产出草稿，不落库）')
    ap.add_argument('--note', default='', help='业务背景补充，注入 prompt（如系统定位、口径）')
    ap.add_argument('--max-col', type=int, default=25, help='每表注入 prompt 的最大列数')
    ap.add_argument('--out', default='', help='草稿输出路径（默认 drafts/entity_defs_<档位>_<时间戳>.json）')
    args = ap.parse_args()

    mgr = oc.db()
    prof = oc.current_profile()
    print(f'[1/4] 组装 Schema 上下文（档位 {prof["name"]} / {prof["label"]}）...', flush=True)
    ctx = oc.assemble_schema_context(mgr, max_col=args.max_col)
    schema_text = oc.render_context_text(ctx)
    print(f'      表 {len(ctx["tables"])} 张，外键 {len(ctx["fks"])} 条，'
          f'已有概念映射 {len(ctx["keyword_map"])} 词；上下文 {len(schema_text)} 字符', flush=True)

    print('[2/4] 调 LLM 生成实体层提案...', flush=True)
    from core.llm_config import call_chat
    prompt = _PROMPT.format(profile_label=prof['label'], n=len(ctx['tables']),
                            schema_text=schema_text,
                            extra_note=(f'\n[业务背景补充]\n{args.note}' if args.note else ''))
    resp = call_chat([{'role': 'user', 'content': prompt}],
                     max_tokens=8000, thinking=False)
    content = (resp.get('content') or '').strip()
    if not content:
        print('[ERR] LLM 返回空内容', flush=True)
        sys.exit(2)
    usage = resp.get('usage') or {}
    if usage:
        print(f'      tokens: {usage}', flush=True)

    print('[3/4] 解析与校验...', flush=True)
    try:
        draft = _parse_llm_json(content)
    except Exception as e:
        print(f'[ERR] LLM 返回解析失败: {e}', flush=True)
        print('---- 原始返回（前 1500 字）----')
        print(content[:1500])
        sys.exit(2)
    entities = draft.get('entities')
    if not isinstance(entities, list) or not entities:
        print('[ERR] 草稿缺少 entities 数组', flush=True)
        sys.exit(2)
    errors, warnings = oc.validate_draft(draft, ctx['tables'])
    draft.setdefault('meta', {})
    draft['meta'].update({
        'profile': prof['name'], 'business_db': prof['business'],
        'created_at': datetime.now().isoformat(timespec='seconds'),
        'source': 'propose_entities.py', 'note': args.note,
        'validation': {'errors': errors, 'warnings': warnings}})

    for e in entities:
        e['member_tables'] = sorted(set(e.get('member_tables') or []))
    print(f'      实体 {len(entities)} 个；校验 {len(errors)} 错误 / {len(warnings)} 警告',
          flush=True)
    for msg in errors:
        print(f'      [错误] {msg}', flush=True)
    for msg in warnings:
        print(f'      [警告] {msg}', flush=True)

    print('[4/4] 落草稿（不落库）...', flush=True)
    if args.out:
        out = oc.Path(args.out)
        if not out.is_absolute():
            out = oc.HERE / out
    else:
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        out = oc.DRAFTS_DIR / f'entity_defs_{prof["name"]}_{stamp}.json'
    oc.save_draft(out, draft)

    print('\n== 实体层提案草稿 ==')
    for e in sorted(entities, key=lambda x: (oc.LAYERS.index(x.get('layer'))
                                             if x.get('layer') in oc.LAYERS else 9,
                                             x.get('name', ''))):
        print(f'  [{e.get("layer"):<8}] {e.get("name")}（{e.get("label")}）'
              f' {len(e.get("member_tables") or [])} 表: '
              f'{", ".join(e.get("member_tables") or [])}')
    if draft.get('notes'):
        print(f'\n划分思路: {draft["notes"]}')
    print(f'\n草稿已写入 {out}')
    print('下一步: 人工审阅/修订草稿 → '
          f'python skills/ontology-distill/scripts/apply_entity_defs.py '
          f'--draft {out.name if not args.out else args.out} --commit --rebuild')
    sys.exit(1 if errors else 0)


if __name__ == '__main__':
    main()
