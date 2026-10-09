# -*- coding: utf-8 -*-
"""实体草稿落库：校验 → 变更单（dry-run 默认）→ --commit 写 ontology_entity_defs →
--rebuild 生成全量重建提案（仍走 Web 审批，脚本绝不自动 approve）。

用法：
    python skills/ontology-distill/scripts/apply_entity_defs.py --draft <草稿.json>
        [--commit] [--rebuild] [--prune]

  --draft   草稿文件：绝对路径，或相对 scripts/ 目录，或直接给 drafts/ 下的文件名
  --commit  真正落库（默认 dry-run 只出变更单）
  --rebuild 落库后生成全量重建提案（打印提案 id；审批生效在 Web 本体管理页进行）
  --prune   草稿之外的库存定义置 enabled=0（默认保留不动）
"""
import argparse
import json
import sys
from pathlib import Path

import ont_common as oc


def _resolve_draft(arg: str) -> Path:
    candidates = [Path(arg), oc.DRAFTS_DIR / arg, oc.SKILL_DIR / arg]
    for p in candidates:
        if p.is_file():
            return p
    raise FileNotFoundError(f'草稿不存在: {arg}（试过: '
                            + ', '.join(str(c) for c in candidates) + '）')


def _diff_defs(draft_entities: list, db_defs: list) -> dict:
    """草稿 vs 库存定义 → {new, update, unchanged, db_only}。"""
    db_by_name = {d['name']: d for d in db_defs}
    draft_names = set()
    new, update, unchanged = [], [], []
    for e in draft_entities:
        name = e['name']
        draft_names.add(name)
        old = db_by_name.get(name)
        if old is None:
            new.append(e)
            continue
        changes = {}
        for k in ('label', 'layer', 'parent', 'comment'):
            if (e.get(k) or '') != (old.get(k) or ''):
                changes[k] = {'old': old.get(k) or '', 'new': e.get(k) or ''}
        old_members = sorted(old.get('member_tables') or [])
        new_members = sorted(e.get('member_tables') or [])
        if old_members != new_members:
            changes['member_tables'] = {
                'add': sorted(set(new_members) - set(old_members)),
                'del': sorted(set(old_members) - set(new_members))}
        (update if changes else unchanged).append(
            {'name': name, 'changes': changes} if changes else {'name': name})
    db_only = [n for n in db_by_name if n not in draft_names]
    return {'new': new, 'update': update, 'unchanged': unchanged, 'db_only': db_only}


def main():
    ap = argparse.ArgumentParser(description='实体草稿落库（默认 dry-run）')
    ap.add_argument('--draft', required=True, help='草稿 JSON 路径/文件名')
    ap.add_argument('--commit', action='store_true', help='真正落库 ontology_entity_defs')
    ap.add_argument('--rebuild', action='store_true',
                    help='落库后生成全量重建提案（需配合 --commit）')
    ap.add_argument('--prune', action='store_true',
                    help='草稿之外的库存定义置 enabled=0')
    args = ap.parse_args()

    mgr = oc.db()
    prof = oc.current_profile()
    draft_path = _resolve_draft(args.draft)
    draft = oc.load_draft(str(draft_path))
    entities = draft['entities']
    print(f'档位 {prof["name"]}（{prof["label"]}） 草稿 {draft_path}，实体 {len(entities)} 个')

    # ---- 校验（有错误即中止）
    base_tables = oc.list_base_tables(mgr)
    errors, warnings = oc.validate_draft(draft, base_tables)
    for msg in warnings:
        print(f'  [警告] {msg}')
    if errors:
        for msg in errors:
            print(f'  [错误] {msg}')
        print('校验未通过，未做任何变更。先修订草稿。')
        sys.exit(1)

    # ---- 变更单
    from core.ontology.store import OntologyStore
    store = OntologyStore(mgr)
    store.init_tables()
    db_defs = store.get_entity_defs()
    diff = _diff_defs(entities, db_defs)
    print(f'\n== 变更单: 新增 {len(diff["new"])} / 更新 {len(diff["update"])} / '
          f'不变 {len(diff["unchanged"])} / 库有草稿无 {len(diff["db_only"])}')
    for e in diff['new']:
        print(f'  [新增] {e["name"]}（{e.get("label")}，{e.get("layer")}）'
              f' {len(e.get("member_tables") or [])} 表')
    for u in diff['update']:
        print(f'  [更新] {u["name"]}: {json.dumps(u["changes"], ensure_ascii=False)}')
    for n in diff['db_only']:
        print(f'  [库有草稿无] {n}' + ('（--prune 将停用）' if args.prune else '（保留）'))

    if not args.commit:
        print('\n[dry-run] 未落库。确认无误后加 --commit（可再带 --rebuild 生成提案）。')
        return

    # ---- 落库
    for e in entities:
        store.upsert_entity_def({
            'name': e['name'], 'label': e.get('label', ''),
            'layer': e.get('layer', 'business'), 'parent': e.get('parent', ''),
            'member_tables': sorted(set(e.get('member_tables') or [])),
            'comment': e.get('comment', ''), 'enabled': 1})
    print(f'\n[commit] 已写入 {len(entities)} 条实体定义（upsert）')
    if args.prune:
        for n in diff['db_only']:
            store.delete_entity_def(n)
        if diff['db_only']:
            print(f'[prune] 已停用 {len(diff["db_only"])} 条: {diff["db_only"]}')

    # ---- 生成提案（审批在 Web 进行）
    if args.rebuild:
        from core.ontology.service import OntologyService
        svc = OntologyService()
        result = svc.rebuild_proposal()
        counts = (result.get('diff') or {}).get('counts') or {}
        print(f'\n[rebuild] 提案 #{result["proposal_id"]} 已生成（pending），'
              f'指纹 {result["fingerprint"][:12]}...')
        if counts:
            print(f'          diff 计数: {json.dumps(counts, ensure_ascii=False)}')
        print('          下一步: Web 本体管理页审批该提案后生效；'
              '审批前问数侧仍用旧版本。')
    else:
        print('\n定义已更新。生成提案：'
              '重跑本命令加 --rebuild，或在 Web 本体管理页点「手动重建」。')


if __name__ == '__main__':
    main()
