# -*- coding: utf-8 -*-
"""一次性种子脚本：经 deshu5 resources API 为当前档位治理库灌入 kjgl 领域关键词映射。

直接连库写 keyword_table_map 会让运行中进程的 provider 缓存过期不刷新，
因此走 POST /api/resources/keyword_table_map/items（每次写入自动 invalidate 缓存）。
运行前提：deshu5 已启动且当前档位 = kjgl（治理库 = kjgl01_governance）。
"""
import json
import sys
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else 'http://127.0.0.1:5001'

MAPPING = {
    '项目': ['projects'],
    '科技项目': ['projects'],
    '项目名称': ['projects'],
    '项目编号': ['projects'],
    '立项': ['projects'],
    '负责人': ['projects'],
    '文档': ['documents'],
    '文件': ['documents'],
    '资料': ['documents'],
    '报告': ['documents'],
    '扫描': ['documents', 'import_batches'],
    '文档类型': ['doc_types'],
    '成果': ['achievements'],
    '论文': ['achievements'],
    '专利': ['achievements'],
    '软著': ['achievements'],
    '软件著作权': ['achievements'],
    '标准': ['achievements'],
    '署名': ['achievement_persons'],
    '作者': ['achievement_persons'],
    '发明人': ['achievement_persons'],
    '完成人': ['achievement_persons'],
    '佐证': ['document_achievements'],
    '人员': ['persons'],
    '专家': ['persons'],
    '成员': ['project_persons'],
    '机构': ['organizations'],
    '单位': ['organizations'],
    '部门': ['organizations'],
    '用户': ['users'],
    '账号': ['users'],
    '导入': ['import_batches'],
    '批次': ['import_batches'],
    '流水线': ['pipeline_tasks'],
    '研究内容': ['project_research_contents'],
    '子课题': ['project_research_contents'],
    '课题': ['project_research_contents'],
    '指标': ['project_indicators'],
    '考核': ['project_indicators'],
    '经费': ['project_funding_items', 'document_funding_items'],
    '预算': ['project_funding_items', 'projects'],
    '科目': ['project_funding_items', 'document_funding_items'],
    '决算': ['project_funding_items', 'document_funding_items'],
    '金额': ['project_funding_items', 'document_funding_items'],
    '溯源': ['document_field_sources'],
    '成果转化': ['planning_transform_items'],
    '奖励': ['planning_award_items'],
    '获奖': ['planning_award_items'],
    '需求': ['planning_requirement_items'],
}

ok = fail = 0
for kw, tables in MAPPING.items():
    for t in tables:
        body = json.dumps({'keyword': kw, 'table_name': t, 'enabled': 1}).encode('utf-8')
        req = urllib.request.Request(
            f'{BASE}/api/resources/keyword_table_map/items', data=body,
            headers={'Content-Type': 'application/json'}, method='POST')
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                d = json.loads(resp.read().decode('utf-8'))
            if d.get('success'):
                ok += 1
            else:
                fail += 1
                print(f'[FAIL] {kw}->{t}: {d}', flush=True)
        except Exception as e:
            fail += 1
            print(f'[ERR ] {kw}->{t}: {e}', flush=True)
print(f'完成: 成功 {ok} 条, 失败 {fail} 条')
sys.exit(0 if fail == 0 else 1)
