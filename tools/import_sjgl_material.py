# -*- coding: utf-8 -*-
"""sjgl 素材提资导入 CLI（脚本路径，等价页面「素材提资」上传→校验→执行流程）

目标库跟随 db_profile 档位，本工具固定以 DB_PROFILE=sjgl 运行：
    DB_PROFILE=sjgl python tools/import_sjgl_material.py [模板xlsx路径] [--no-llm]

流程：create_run → parse_workbook → validate → convert_run（含自动建表，本场景表已预建）
→ annotate_llm（LLM 未配置/失败自动降级 manual/pending）→ update_run_stats(done)。
复核队列留在 ingest_provenance（pending），页面「素材提资」可继续人工复核。
"""
import os
import shutil
import sys

os.environ['DB_PROFILE'] = 'sjgl'  # 固定落 sjgl 档位，防误写 sc01/fz01/kjgl01
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import db_profile  # noqa: E402
from modules.provision import provisioner as pv  # noqa: E402

DEFAULT_XLSX = (r'D:\codex\运维运营指标梳理结果汇总'
                r'\汇总-运维运营指标相关表-素材提资模板-完整填写-表头去除填写提示.xlsx')


def main():
    path = next((a for a in sys.argv[1:] if not a.startswith('--')), DEFAULT_XLSX)
    no_llm = '--no-llm' in sys.argv
    prof = db_profile.current()
    print(f'[档位] {prof["name"]} → 业务库 {prof["business"]} / 治理库 {prof["governance"]}')
    assert prof['business'] == 'sjgl', '档位校验失败，拒绝继续'

    run_id = pv.create_run(os.path.basename(path))
    saved = os.path.join(pv.UPLOAD_DIR, f'{run_id}.xlsx')
    os.makedirs(pv.UPLOAD_DIR, exist_ok=True)
    shutil.copyfile(path, saved)
    print(f'[run] {run_id} <- {os.path.basename(path)}')

    parsed = pv.parse_workbook(saved)
    validation = pv.validate(parsed)
    for key, v in validation.items():
        print(f'[校验] {key}: ok={v["ok"]} warn={v["warn"]} fail={v["fail"]}')
        for i in v['issues']:
            if i['level'] == 'fail':
                print(f'    FAIL 行{i["row"]}: {i["msg"][:120]}')
    pv.update_run_stats(run_id, status='executing',
                        stats={'total_rows': sum(v['ok'] + v['fail'] for v in validation.values()),
                               'fail_rows': sum(v['fail'] for v in validation.values())})

    out = pv.convert_run(run_id, parsed, validation,
                         progress_cb=lambda e: print(f'[{e["stage"]}] {e["msg"]}'))
    print('[converted]', out['stats']['converted'])
    print('[skipped]', out['stats']['skipped'])
    print('[conflicts]', len(out['stats'].get('conflicts', [])))
    print('[provenance]', out['stats']['provenance'])

    if no_llm:
        ann = {'annotated': 0, 'degraded': 0}
        print('[annotate] --no-llm，跳过 LLM 标注')
    else:
        ann = pv.annotate_llm(run_id, out['llm_tasks'],
                              progress_cb=lambda e: print(f'[{e["stage"]}] {e["msg"]}'))
    print('[annotate]', ann)

    from core.database import DatabaseManager
    with DatabaseManager().connect_governance() as conn:
        pending = conn.execute(
            "SELECT COUNT(*) FROM ingest_provenance WHERE run_id = ? AND review_status = 'pending'",
            (run_id,)).fetchone()[0]
    ok = sum(out['stats']['converted'].values())
    fail = sum(v['fail'] for v in validation.values())
    pv.update_run_stats(run_id, status='done',
                        stats={'ok_rows': ok, 'review_rows': pending, 'fail_rows': fail})
    print(f'[done] run={run_id} ok_rows={ok} fail_rows={fail} pending_review={pending}')


if __name__ == '__main__':
    main()
