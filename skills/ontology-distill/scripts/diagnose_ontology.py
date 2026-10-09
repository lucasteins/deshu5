# -*- coding: utf-8 -*-
"""当前档位本体提炼质量体检（只读，不落库不改数）。

检查项：
  D1 生效版本总览（版本 / 构建时间 / 指纹 / 五层资产计数）
  D2 实体层覆盖：Unmapped 成员占比、layer 分布（提炼质量的核心指标）
  D3 实体定义表 ontology_entity_defs 状态（空 = 重建也只会重现烂结果）
  D4 概念悬空：concept → maps_to 指向底座不存在的表
  D5 关系覆盖：来源分布、无任何关系的孤立表
  D6 码值枚举：治理库 code_values 三表行数（0 = 码值层未治理）
  D7 同义词组：生效版本 synonym_groups

输出体检结论与建议动作；--json-out 另存机器可读报告。

用法：
    python skills/ontology-distill/scripts/diagnose_ontology.py [--json-out reports/xxx.json]
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import ont_common as oc


def _table_count(conn, table):
    try:
        return conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
    except Exception:
        return None


def diagnose() -> dict:
    mgr = oc.db()
    prof = oc.current_profile()
    report = {'profile': prof['name'], 'business_db': prof['business'],
              'ontology_db': prof['ontology'], 'checked_at': datetime.now().isoformat(),
              'findings': []}

    base_tables = oc.list_base_tables(mgr)
    base_set = {t['name'] for t in base_tables}
    report['base_table_count'] = len(base_tables)

    # ---- D1 生效版本总览
    with mgr.connect_ontology() as conn:
        meta_row = conn.execute(
            'SELECT version, base_fingerprint, built_at, summary '
            'FROM ontology_meta ORDER BY version DESC LIMIT 1').fetchone()
    if not meta_row:
        report['findings'].append({'id': 'D1', 'level': 'error',
                                   'msg': '本体库无生效版本（首次引导未完成）'})
        report['meta'] = None
        return report
    meta = {'version': meta_row[0], 'base_fingerprint': meta_row[1] or '',
            'built_at': meta_row[2] or '', 'summary': json.loads(meta_row[3] or '{}')}
    report['meta'] = meta
    s = meta['summary']

    # ---- D2 实体层覆盖（读生效版本快照）
    with mgr.connect_ontology() as conn:
        ent_rows = conn.execute(
            'SELECT name, label, layer, member_tables FROM ontology_entities '
            'WHERE version = ?', (meta['version'],)).fetchall()
        concept_rows = conn.execute(
            'SELECT concept, maps_to FROM ontology_concepts WHERE version = ?',
            (meta['version'],)).fetchall()
        rel_rows = conn.execute(
            'SELECT from_class, to_class, source FROM ontology_relations WHERE version = ?',
            (meta['version'],)).fetchall()
        defs_rows = conn.execute(
            'SELECT name, enabled FROM ontology_entity_defs').fetchall()

    entities = [{'name': r[0], 'label': r[1] or '', 'layer': r[2] or '',
                 'member_tables': json.loads(r[3] or '[]')} for r in ent_rows]
    table_to_entities = {}
    for e in entities:
        for t in e['member_tables']:
            table_to_entities.setdefault(t, []).append(e['name'])
    unmapped = next((e for e in entities if e['name'] == 'Unmapped'), None)
    unmapped_tables = sorted(unmapped['member_tables']) if unmapped else []
    layer_dist = {}
    for e in entities:
        layer_dist[e['layer']] = layer_dist.get(e['layer'], 0) + 1
    report['entities'] = {
        'total': len(entities), 'by_layer': layer_dist,
        'unmapped_count': len(unmapped_tables), 'unmapped_tables': unmapped_tables,
        'items': entities}
    if unmapped_tables:
        pct = round(len(unmapped_tables) * 100 / max(len(base_tables), 1))
        lvl = 'error' if pct >= 50 else 'warn'
        report['findings'].append({
            'id': 'D2', 'level': lvl,
            'msg': f'Unmapped 实体装了 {len(unmapped_tables)}/{len(base_tables)} 张表'
                   f'（{pct}%），实体层提炼基本失效'})
    elif not entities:
        report['findings'].append({'id': 'D2', 'level': 'error',
                                   'msg': '生效版本没有任何实体'})

    # ---- D3 实体定义表
    defs_on = [r[0] for r in defs_rows if r[1]]
    report['entity_defs'] = {'total': len(defs_rows), 'enabled': len(defs_on),
                             'names': sorted(defs_on)}
    if not defs_rows:
        report['findings'].append({
            'id': 'D3', 'level': 'error',
            'msg': 'ontology_entity_defs 为空：内置种子映射（sc01 电力口径）对本档位不命中，'
                   '重建提案仍会全部落 Unmapped。先用 propose_entities.py 出草稿'})

    # ---- D4 概念悬空
    concepts = [{'concept': r[0], 'maps_to': json.loads(r[1] or '[]')} for r in concept_rows]
    dangling = [{'concept': c['concept'],
                 'missing': [t for t in c['maps_to'] if t not in base_set]}
                for c in concepts if any(t not in base_set for t in c['maps_to'])]
    report['concepts'] = {'total': len(concepts), 'dangling': dangling}
    if dangling:
        report['findings'].append({
            'id': 'D4', 'level': 'warn',
            'msg': f'{len(dangling)} 个概念映射到底座不存在的表（悬空映射），'
                   f'如: {dangling[0]["concept"]} -> {dangling[0]["missing"]}'})

    # ---- D5 关系覆盖
    src_dist = {}
    linked = set()
    for fc, tc, src in rel_rows:
        src_dist[src or 'unknown'] = src_dist.get(src or 'unknown', 0) + 1
        linked.update((fc, tc))
    isolated = sorted(base_set - linked)
    report['relations'] = {'total': len(rel_rows), 'by_source': src_dist,
                           'isolated_tables': isolated}
    if isolated:
        report['findings'].append({
            'id': 'D5', 'level': 'warn',
            'msg': f'{len(isolated)} 张表无任何关系边（孤立表）: {isolated[:6]}'
                   + ('...' if len(isolated) > 6 else '')})

    # ---- D6 码值枚举（治理库权威源）
    with mgr.connect_governance() as conn:
        cv = {t: _table_count(conn, t)
              for t in ('code_values', 'code_value_items', 'code_value_column_form')}
    report['code_values'] = cv
    if not cv.get('code_values'):
        report['findings'].append({
            'id': 'D6', 'level': 'warn',
            'msg': '治理库 code_values 为空：本体码值层为空。'
                   '需要码值治理请走 code-value-governance 技能链路'})

    # ---- D7 同义词组
    syn = s.get('synonym_groups') or {}
    report['synonym_groups'] = len(syn)
    if not syn:
        report['findings'].append({'id': 'D7', 'level': 'info',
                                   'msg': '同义词组为空（sql_knowledge 未登记同族消歧规则）'})

    # ---- 每表归属明细
    report['table_owners'] = [
        {'table': t['name'], 'comment': t['comment'], 'rows': t['rows'],
         'entities': table_to_entities.get(t['name'], [])} for t in base_tables]
    return report


def print_report(r: dict):
    prof = oc.current_profile()
    print(f'== 本体体检：档位 {r["profile"]}（{prof["label"]}）'
          f' 业务库 {r["business_db"]} / 本体库 {r["ontology_db"]}')
    print(f'   底座表数: {r["base_table_count"]}    体检时间: {r["checked_at"]}')
    m = r.get('meta')
    if m:
        s = m['summary']
        print(f'\n[D1] 生效版本 v{m["version"]}（{m["built_at"]}）'
              f' 指纹 {m["base_fingerprint"][:12]}...')
        print(f'     类 {s.get("classes")} / 属性 {s.get("properties")} / '
              f'关系 {s.get("relations")} / 枚举 {s.get("enumerations")} / '
              f'概念 {s.get("concepts")} / 实体 {s.get("entities")}')
    e = r.get('entities')
    if e:
        print(f'\n[D2] 实体层: 共 {e["total"]} 个实体，layer 分布 {e["by_layer"]}')
        if e['unmapped_count']:
            print(f'     Unmapped 成员 {e["unmapped_count"]} 张:')
            for t in e['unmapped_tables']:
                print(f'       - {t}')
        else:
            print('     无 Unmapped 残留 OK')
        others = [x for x in e['items'] if x['name'] != 'Unmapped']
        for x in others:
            print(f'     [{x["layer"]:<8}] {x["name"]}（{x["label"]}）'
                  f' {len(x["member_tables"])} 表')
    d = r.get('entity_defs')
    if d is not None:
        print(f'\n[D3] 实体定义表: {d["enabled"]}/{d["total"]} 启用'
              + (f' -> {d["names"]}' if d["names"] else ''))
    c = r.get('concepts')
    if c:
        print(f'\n[D4] 概念: {c["total"]} 个，悬空 {len(c["dangling"])} 个')
        for x in c['dangling'][:5]:
            print(f'       - {x["concept"]} -> 缺失表 {x["missing"]}')
    rel = r.get('relations')
    if rel:
        print(f'\n[D5] 关系: {rel["total"]} 条，来源 {rel["by_source"]}')
        if rel['isolated_tables']:
            print(f'     孤立表 {len(rel["isolated_tables"])} 张: {rel["isolated_tables"]}')
    cv = r.get('code_values')
    if cv:
        print(f'\n[D6] 码值（治理库）: {cv}')
    print(f'\n[D7] 同义词组: {r.get("synonym_groups")} 组')

    errs = [f for f in r['findings'] if f['level'] == 'error']
    warns = [f for f in r['findings'] if f['level'] == 'warn']
    infos = [f for f in r['findings'] if f['level'] == 'info']
    print(f'\n== 发现: {len(errs)} 阻断 / {len(warns)} 警告 / {len(infos)} 提示')
    for f in r['findings']:
        mark = {'error': '[阻断]', 'warn': '[警告]', 'info': '[提示]'}[f['level']]
        print(f'  {mark} {f["id"]} {f["msg"]}')
    if errs or warns:
        print('\n== 建议动作')
        if any(f['id'] in ('D2', 'D3') and f['level'] == 'error' for f in r['findings']):
            print('  1) python skills/ontology-distill/scripts/propose_entities.py'
              '  # LLM 出实体层草稿')
            print('  2) 人工审阅 drafts/ 下草稿 JSON（改 name/label/分层/成员表）')
            print('  3) python skills/ontology-distill/scripts/apply_entity_defs.py'
              ' --draft <草稿> --commit --prune --rebuild  # 落定义+清旧定义+生成提案')
            print('  4) Web 本体管理页审批提案生效')
        if any(f['id'] == 'D6' for f in r['findings']):
            print('  - 码值层：走 code-value-governance 技能补登记码值域')


def main():
    ap = argparse.ArgumentParser(description='当前档位本体提炼质量体检（只读）')
    ap.add_argument('--json-out', default='', help='机器可读报告输出路径（相对 scripts/ 或绝对）')
    args = ap.parse_args()

    report = diagnose()
    print_report(report)
    if args.json_out:
        out = Path(args.json_out)
        if not out.is_absolute():
            out = oc.HERE / out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'\n报告已写入 {out}')
    has_error = any(f['level'] == 'error' for f in report['findings'])
    sys.exit(1 if has_error else 0)


if __name__ == '__main__':
    main()
