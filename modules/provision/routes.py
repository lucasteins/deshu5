# -*- coding: utf-8 -*-
"""素材提资模块路由：上传解析 → 校验与映射预览 → 执行转换(SSE) → 人工复核

溯源（ingest_provenance）逐字段记录 direct/system/llm/manual 四类来源，
LLM 标注与人工改值进入复核队列，确认后回填目标表。
"""
import json
import os
import shutil

from flask import Blueprint, Response, jsonify, request, stream_with_context

from core.database import DatabaseManager
from modules.provision import provisioner as pv

bp = Blueprint('provision', __name__)


def _provision_parse_validate(run_id, saved_path):
    """解析+校验（upload/preview 共用）。"""
    parsed = pv.parse_workbook(saved_path)
    if not parsed:
        raise ValueError('未识别到任何 S2~S6 Sheet（00b/00c 为校验依据，无需转换）')
    validation = pv.validate(parsed)
    return parsed, validation


@bp.route('/api/provision/upload', methods=['POST'])
def provision_upload():
    """上传解析：multipart xlsx 或 JSON {path}。建 run → 解析 → 校验 → 返回 run_id+校验统计。"""
    os.makedirs(pv.UPLOAD_DIR, exist_ok=True)
    file_name = None
    try:
        if request.content_type and 'multipart/form-data' in request.content_type:
            f = request.files.get('file')
            if not f or not f.filename:
                return jsonify({'success': False, 'error': '未选择文件'}), 400
            file_name = f.filename
            run_id = pv.create_run(file_name)
            saved = os.path.join(pv.UPLOAD_DIR, f'{run_id}.xlsx')
            f.save(saved)
        else:
            data = request.get_json(silent=True) or {}
            path = (data.get('path') or '').strip()
            if not path or not os.path.isfile(path):
                return jsonify({'success': False, 'error': f'服务器路径不存在: {path}'}), 400
            file_name = os.path.basename(path)
            run_id = pv.create_run(file_name)
            saved = os.path.join(pv.UPLOAD_DIR, f'{run_id}.xlsx')
            shutil.copyfile(path, saved)
        parsed, validation = _provision_parse_validate(run_id, saved)
        stats = {k: {'ok': v['ok'], 'warn': v['warn'], 'fail': v['fail']}
                 for k, v in validation.items()}
        total = sum(v['ok'] + v['fail'] for v in validation.values())
        pv.update_run_stats(run_id, stats={'total_rows': total,
                                           'fail_rows': sum(v['fail'] for v in validation.values())})
        # 冲突项（含码值映射冲突 fail 与域级差异 warn）随校验结果一并返回
        return jsonify({'success': True, 'run_id': run_id, 'file_name': file_name,
                        'validation': stats,
                        'issues': {k: v['issues'] for k, v in validation.items()}})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/provision/<run_id>/preview')
def provision_preview(run_id):
    """校验与映射预览：按 Sheet 分组 + 字段级溯源图例（dry-run 不落库）。"""
    run = pv.get_run(run_id)
    if not run:
        return jsonify({'success': False, 'error': f'run 不存在: {run_id}'}), 404
    saved = os.path.join(pv.UPLOAD_DIR, f'{run_id}.xlsx')
    try:
        parsed, validation = _provision_parse_validate(run_id, saved)
        preview = pv.build_preview(run_id, parsed, validation)
        preview['success'] = True
        preview['run'] = run
        return jsonify(preview)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/provision/<run_id>/execute', methods=['POST'])
def provision_execute(run_id):
    """执行转换（SSE 进度）：convert（直接转换入库+溯源）→ annotate_llm（批量标注）→ 汇总。
    事件：{stage,msg} 进度；{done:true,result} 完成；{error} 失败。"""
    import queue
    import threading

    run = pv.get_run(run_id)
    if not run:
        return jsonify({'success': False, 'error': f'run 不存在: {run_id}'}), 404
    events = queue.Queue()

    def worker():
        try:
            saved = os.path.join(pv.UPLOAD_DIR, f'{run_id}.xlsx')
            parsed, validation = _provision_parse_validate(run_id, saved)
            pv.update_run_stats(run_id, status='executing')
            events.put({'stage': 'convert', 'msg': '开始直接转换入库'})
            out = pv.convert_run(run_id, parsed, validation,
                                 progress_cb=lambda e: events.put(e))
            # 关键词-表映射提取（schema 文档 + 暂存非生产表 → keyword_table_map，打分消歧 1:1）
            events.put({'stage': 'keywords', 'msg': '提取关键词-表映射（打分消歧为一关键词一表）'})
            try:
                from modules.provision.keyword_extractor import extract_keywords
                kw_stats = extract_keywords(
                    progress_cb=lambda s, m: events.put({'stage': s, 'msg': m}),
                    rebuild=True)
                events.put({'stage': 'keywords',
                            'msg': f"关键词提取完成：{kw_stats['keywords']} 关键词"
                                   f"（消歧自 {kw_stats['raw_pairs']} 对，覆盖 {kw_stats['tables_covered']} 表）"
                                   f"；重建 {kw_stats['new_rows']} 行"
                                   f"（schema 表 {kw_stats['schema_doc_tables']}+列 {kw_stats['schema_doc_columns']}"
                                   f"，非生产表 {kw_stats['staging_only_tables']}）"})
            except Exception as e:
                print(f"[WARN] 关键词提取失败: {e}")
                events.put({'stage': 'keywords', 'msg': f'关键词提取失败: {e}'})
            events.put({'stage': 'annotate', 'msg': f"LLM 批量标注（{len(out['llm_tasks'])} 条任务）"})
            ann = pv.annotate_llm(run_id, out['llm_tasks'],
                                  progress_cb=lambda e: events.put(e))
            with DatabaseManager().connect_governance() as conn:
                pending = conn.execute(
                    "SELECT COUNT(*) FROM ingest_provenance WHERE run_id = ? AND review_status = 'pending'",
                    (run_id,)).fetchone()[0]
            ok = sum(out['stats']['converted'].values())
            fail = sum(v['fail'] for v in validation.values())
            pv.update_run_stats(run_id, status='done',
                                stats={'ok_rows': ok, 'review_rows': pending, 'fail_rows': fail})
            events.put({'done': True, 'result': {
                'converted': out['stats']['converted'], 'skipped': out['stats']['skipped'],
                'provenance_rows': out['stats']['provenance'],
                'conflicts': out['stats'].get('conflicts', []),
                'llm_annotated': ann['annotated'], 'llm_degraded': ann['degraded'],
                'pending_review': pending}})
        except Exception as e:
            import traceback
            traceback.print_exc()
            pv.update_run_stats(run_id, status='failed')
            events.put({'error': str(e)})

    threading.Thread(target=worker, daemon=True).start()

    def stream():
        while True:
            evt = events.get()
            yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
            if evt.get('done') or evt.get('error'):
                break

    return Response(stream_with_context(stream()), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@bp.route('/api/provision/runs')
def provision_runs():
    """历史 run 列表 + 各 run 待复核条数（复核队列下拉用；历史 pending 不再依赖当次会话）。"""
    with DatabaseManager().connect_governance() as conn:
        rows = conn.execute('''
            SELECT r.run_id, r.file_name, r.status, r.created_at,
                   SUM(CASE WHEN p.review_status = 'pending' THEN 1 ELSE 0 END) AS pending
            FROM ingest_runs r
            LEFT JOIN ingest_provenance p ON p.run_id = r.run_id
            GROUP BY r.run_id, r.file_name, r.status, r.created_at
            ORDER BY r.created_at DESC
        ''').fetchall()
    items = [{'run_id': r[0], 'file_name': r[1], 'status': r[2],
              'created_at': str(r[3]) if r[3] else None, 'pending': int(r[4] or 0)}
             for r in rows]
    return jsonify({'success': True, 'items': items})


@bp.route('/api/provision/<run_id>/review')
def provision_review(run_id):
    """人工复核队列：该 run 的 pending 溯源行。"""
    if not pv.get_run(run_id):
        return jsonify({'success': False, 'error': f'run 不存在: {run_id}'}), 404
    return jsonify({'success': True, 'items': pv.get_review_queue(run_id)})


@bp.route('/api/provision/review/batch-confirm', methods=['POST'])
def provision_review_batch_confirm():
    """批量复核确认（仅中/低优先级；高优先级服务端拒绝，必须逐条人工确认）。"""
    data = request.get_json(silent=True) or {}
    ids = data.get('ids') or []
    if not ids:
        return jsonify({'success': False, 'error': '未选择记录'}), 400
    if len(ids) > 2000:
        return jsonify({'success': False, 'error': f'单批最多 2000 条（本次 {len(ids)}）'}), 400
    try:
        result = pv.batch_confirm([int(i) for i in ids])
        return jsonify({'success': True, **result})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/provision/review/<int:prov_id>/confirm', methods=['POST'])
def provision_review_confirm(prov_id):
    """复核确认（可改 field_value）：回填目标表字段 + source_kind→manual + confirmed。"""
    data = request.get_json(silent=True) or {}
    try:
        updated = pv.confirm_provenance(prov_id, data.get('field_value'))
        return jsonify({'success': True, 'item': updated})
    except LookupError as e:
        return jsonify({'success': False, 'error': str(e)}), 404
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/provision/<run_id>/finish', methods=['POST'])
def provision_finish(run_id):
    """收尾：run → finished（report 剩余 pending 数）。"""
    if not pv.get_run(run_id):
        return jsonify({'success': False, 'error': f'run 不存在: {run_id}'}), 404
    return jsonify({'success': True, **pv.finish_run(run_id)})


@bp.route('/api/provision/keywords/extract', methods=['POST'])
def provision_keywords_extract():
    """独立重跑关键词-表映射提取（无需重新上传模板）。

    从当前治理库 schema_table_docs / schema_column_docs（PK 核心字段）
    + 暂存业务库非生产表（database01 有、marketing_40 无的表）分词提取，
    打分消歧为「一关键词一表」。

    body: {rebuild?: bool}（默认 true：先清空再写，保证 1:1；false 则追加但无法移除既有同关键词多表行）
    """
    try:
        from modules.provision.keyword_extractor import extract_keywords
        payload = request.get_json(silent=True) or {}
        rebuild = bool(payload.get('rebuild', True))
        stats = extract_keywords(rebuild=rebuild)
        return jsonify({'success': True, 'stats': stats})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500
