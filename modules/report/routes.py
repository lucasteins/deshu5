# -*- coding: utf-8 -*-
"""报告生成模块路由：/api/report/*

- POST /plan            意图 → 分解计划（模板命中 + 标准问答对命中标注），供前端预览/编辑
- POST /generate        SSE 流式：plan → 并发取数（逐题进度）→ 拼装 → 落库 → 返回报告
- GET/POST/PUT/DELETE /templates[/<id>]   报告模板 CRUD（治理库 report_templates）
- GET /runs[/<id>]      历史报告列表/详情
- GET /runs/<id>/export 下载 Markdown
"""
import json
import queue
import re
import threading
import time
import uuid
from datetime import datetime

from flask import Blueprint, Response, jsonify, request, stream_with_context

from core.context import db_manager

bp = Blueprint('report', __name__, url_prefix='/api/report')

_planner = None


def _get_planner():
    global _planner
    if _planner is None:
        from modules.report.planner import ReportPlanner
        _planner = ReportPlanner()
    return _planner


# ==================== 计划 ====================

@bp.route('/plan', methods=['POST'])
def make_plan():
    data = request.get_json() or {}
    intent_text = (data.get('intent_text') or '').strip()
    template_id = data.get('template_id')
    if not intent_text and not template_id:
        return jsonify({'success': False, 'error': '请填写意图或选择模板'}), 400
    try:
        plan = _get_planner().plan(intent_text, template_id=template_id,
                                   period_hint=(data.get('period') or '').strip(),
                                   org_hint=(data.get('org') or '').strip())
        return jsonify({'success': True, 'plan': plan})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


# ==================== 生成（SSE） ====================

def _insert_run(session_id: str, plan: dict) -> int:
    with db_manager.connect_governance() as conn:
        cursor = conn.execute('''
            INSERT INTO report_runs (session_id, intent_text, template_id, plan_json, status, created_at)
            VALUES (?, ?, ?, ?, 'running', ?)
        ''', (session_id, plan.get('intent_text', ''), plan.get('template_id'),
              json.dumps(plan, ensure_ascii=False), datetime.now().isoformat()))
        conn.commit()
        return cursor.lastrowid


def _finish_run(run_id: int, status: str, detail: list, report_md: str,
                usage: dict, duration_ms: int):
    with db_manager.connect_governance() as conn:
        conn.execute('''
            UPDATE report_runs
            SET status = ?, detail_json = ?, report_md = ?, usage_json = ?, duration_ms = ?
            WHERE id = ?
        ''', (status, json.dumps(detail, ensure_ascii=False), report_md,
              json.dumps(usage or {}, ensure_ascii=False), duration_ms, run_id))
        conn.commit()


@bp.route('/generate', methods=['POST'])
def generate():
    """SSE 事件：{kind:'plan'|'question'|'composed'} → {done:True, result:{run_id, report_md, ...}}"""
    data = request.get_json() or {}
    plan = data.get('plan')
    intent_text = (data.get('intent_text') or '').strip()
    if not plan and not intent_text:
        return jsonify({'success': False, 'error': '意图或计划不能为空'}), 400

    events = queue.Queue()

    def worker():
        start = time.time()
        run_id = None
        try:
            the_plan = plan
            if not the_plan:
                the_plan = _get_planner().plan(
                    intent_text, template_id=data.get('template_id'))
            events.put({'kind': 'plan', 'plan': the_plan})

            session_id = str(uuid.uuid4())
            run_id = _insert_run(session_id, the_plan)

            from modules.report.executor import execute_plan
            from modules.report.composer import compose

            details = execute_plan(
                the_plan, event_cb=lambda d: events.put({'kind': 'question', 'detail': d}))

            composed = compose(the_plan, details)
            events.put({'kind': 'composed', 'degraded': composed['degraded']})

            duration_ms = int((time.time() - start) * 1000)
            ok_count = sum(1 for d in details if d['status'] == 'ok')
            status = 'done' if ok_count else 'failed'
            usage = {
                'plan': the_plan.get('usage') or {},
                'compose': composed.get('usage') or {},
                'questions_ok': ok_count,
                'questions_total': len(details),
            }
            try:
                _finish_run(run_id, status, details, composed['report_md'], usage, duration_ms)
            except Exception as e:
                print(f'[WARN] report_runs 落库失败: {e}', flush=True)

            events.put({'done': True, 'result': {
                'run_id': run_id, 'report_md': composed['report_md'],
                'degraded': composed['degraded'], 'detail': details,
                'usage': usage, 'duration_ms': duration_ms,
            }})
        except Exception as e:
            import traceback
            traceback.print_exc()
            if run_id:
                try:
                    _finish_run(run_id, 'failed', [], '', {'error': str(e)},
                                int((time.time() - start) * 1000))
                except Exception:
                    pass
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


# ==================== 模板 CRUD ====================

def _norm_q(text: str) -> str:
    """问题规范化：去空白/尾标点，用于重复判定。"""
    return re.sub(r'[\s？?。.,，]+$', '', re.sub(r'\s+', '', text or ''))


def _sync_template_questions(conn, template_id: int, name: str, outline: list) -> dict:
    """模板问数问题 → qa_pairs diff 联动（同事务）。

    outline 中 {section_title, questions:[...]} 的每条问题可为字符串，或
    {question, standard_sql, qa_id}：
    - qa_id 已挂本模板且题干变了 → 变更：更新题干、清 standard_sql、is_usable=0，待重新生成 SQL
    - qa_id 已挂本模板且题干不变 → 保持
    - qa_id 存在但未挂本模板 / 题干规范化命中已有问答对 → 关联（带 SQL 则回填并置有效）
    - 无 qa_id 且未命中 → 新增（带 SQL 直接有效，否则 is_usable=0 待生成）
    - 原来挂本模板但本次 outline 中消失的 → 删除：is_usable=0（保留记录，移出 RAG 池）
    返回 {'inserted', 'linked', 'changed', 'deleted', 'sql_filled', 'regen_ids'}。
    """
    items = []  # (text, sql, qa_id_hint)
    for sec in (outline or []):
        for q in ((sec or {}).get('questions') or []):
            if isinstance(q, dict):
                text = str(q.get('question') or '').strip()
                sql = str(q.get('standard_sql') or '').strip()
                qa_id = q.get('qa_id')
            else:
                text, sql, qa_id = str(q).strip(), '', None
            if text:
                items.append((text, sql, qa_id))

    # 当前挂在该模板下的问答对
    linked_rows = conn.execute(
        'SELECT id, question FROM qa_pairs WHERE report_template_id = ?',
        (template_id,)).fetchall()
    linked_map = {r[0]: r[1] for r in linked_rows}
    # 全量题干规范化索引（用于无 qa_id 时去重）
    all_qa = conn.execute('SELECT id, question FROM qa_pairs').fetchall()
    norm_index = {_norm_q(r[1]): r[0] for r in all_qa if r[1]}

    inserted = linked = changed = sql_filled = 0
    regen_ids = []    # 需要重新生成 SQL 的 qa_id（新增无 SQL / 题干变更）
    seen_ids = set()  # 本次 outline 涉及的 qa_id

    for text, sql, qa_id_hint in items:
        qa_id = None
        if qa_id_hint:
            row = conn.execute('SELECT id FROM qa_pairs WHERE id = ?', (qa_id_hint,)).fetchone()
            qa_id = row[0] if row else None
        if not qa_id:
            qa_id = norm_index.get(_norm_q(text))

        if qa_id and qa_id in linked_map:
            # 已挂本模板：检查题干是否变更
            seen_ids.add(qa_id)
            if _norm_q(linked_map[qa_id]) != _norm_q(text):
                conn.execute("UPDATE qa_pairs SET question = ?, standard_sql = '', is_usable =  0 WHERE id = ?",
                             (text, qa_id))
                regen_ids.append(qa_id)
                changed += 1
            if sql:  # 提炼携带的验证 SQL 优先回填
                conn.execute("UPDATE qa_pairs SET standard_sql = ?, is_usable = 1, "
                             "generation_method = 'template-distill' WHERE id = ?", (sql, qa_id))
                sql_filled += 1
                if qa_id in regen_ids:
                    regen_ids.remove(qa_id)
        elif qa_id:
            # 存在于问答对库但未挂本模板 → 关联
            seen_ids.add(qa_id)
            if sql:
                conn.execute("UPDATE qa_pairs SET report_template_id = ?, standard_sql = ?, "
                             "is_usable = 1, generation_method = 'template-distill' WHERE id = ?",
                             (template_id, sql, qa_id))
                sql_filled += 1
            else:
                conn.execute('UPDATE qa_pairs SET report_template_id = ? WHERE id = ?',
                             (template_id, qa_id))
            linked += 1
        else:
            # 全新问题 → 新增
            cursor = conn.execute(
                "INSERT INTO qa_pairs (question, standard_sql, difficulty, source, tags, is_usable,"
                " report_template_id, ingest_time) VALUES (?, ?, '进阶题', 'report_template', ?, ?, ?, ?)",
                (text, sql, json.dumps([f'报告模板:{name}'], ensure_ascii=False),
                 1 if sql else 0,
                 template_id, datetime.now().isoformat()))
            new_id = cursor.lastrowid
            norm_index[_norm_q(text)] = new_id
            seen_ids.add(new_id)
            inserted += 1
            if sql:
                sql_filled += 1
            else:
                regen_ids.append(new_id)

    # 删除：原来挂本模板、本次 outline 未出现的 → 置为无效
    deleted = 0
    for old_id in linked_map:
        if old_id not in seen_ids:
            conn.execute('UPDATE qa_pairs SET is_usable = 0 WHERE id = ?', (old_id,))
            deleted += 1

    return {'inserted': inserted, 'linked': linked, 'changed': changed,
            'deleted': deleted, 'sql_filled': sql_filled, 'regen_ids': regen_ids}


# ==================== 模板问题 SQL 再生成（后台） ====================

_REGEN_LOCK = threading.Lock()
_REGEN_RUNNING = set()


def _spawn_sql_regen(qa_ids: list):
    """模板保存后，后台为新增/变更的问题重新生成并验证 standard_sql。

    生成 + 只读执行验证通过 → 回填 standard_sql 且 is_usable=1（回 RAG 池）；
   而作 失败保持 is_usable=0，等训练链路人工判题回流。
    """
    ids = [i for i in dict.fromkeys(qa_ids or []) if i]
    if not ids:
        return
    with _REGEN_LOCK:
        todo = [i for i in ids if i not in _REGEN_RUNNING]
        if not todo:
            return
        _REGEN_RUNNING.update(todo)

    def _job():
        try:
            _regen_sql_for_qas(todo)
        finally:
            with _REGEN_LOCK:
                _REGEN_RUNNING.difference_update(todo)

    threading.Thread(target=_job, daemon=True).start()


def _regen_sql_for_qas(qa_ids: list):
    from core.sql_exec import safe_execute_sql
    from modules.training.engine.sql_generator import SQLGenerator

    print(f'[report] 模板问题 SQL 再生成：{len(qa_ids)} 题', flush=True)
    gen = None
    for qa_id in qa_ids:
        try:
            with db_manager.connect_governance() as conn:
                row = conn.execute('SELECT question FROM qa_pairs WHERE id = ?', (qa_id,)).fetchone()
            if not row or not row[0]:
                continue
            if gen is None:
                gen = SQLGenerator()
            result = gen.generate(user_question=row[0])
            sql = (result or {}).get('sql') or ''
            if not result.get('success') or not sql:
                print(f'[report] qa#{qa_id} SQL 生成失败: {str((result or {}).get("error"))[:120]}', flush=True)
                continue
            check = safe_execute_sql(sql)
            if not check.get('success'):
                print(f'[report] qa#{qa_id} SQL 验证失败: {str(check.get("error"))[:120]}', flush=True)
                continue
            with db_manager.connect_governance() as conn:
                conn.execute("UPDATE qa_pairs SET standard_sql = ?, is_usable = 1, "
                             "generation_method = 'template-edit' WHERE id = ?", (sql, qa_id))
                conn.commit()
            print(f'[report] qa#{qa_id} SQL 已再生成并验证（{check.get("row_count", 0)} 行试跑）', flush=True)
        except Exception as e:
            print(f'[WARN] qa#{qa_id} SQL 再生成异常: {e}', flush=True)


@bp.route('/templates/<int:tid>', methods=['GET'])
def get_template(tid):
    """模板详情：outline 问题按 qa_id 实时带出 SQL 状态（供表格编辑器展示/编辑）。"""
    try:
        with db_manager.connect_governance() as conn:
            row = conn.execute(
                'SELECT id, name, trigger_words, outline, enabled, remark FROM report_templates WHERE id = ?',
                (tid,)).fetchone()
            if not row:
                return jsonify({'success': False, 'error': '模板不存在'}), 404
            qa_rows = conn.execute(
                'SELECT id, question, standard_sql, is_usable FROM qa_pairs WHERE report_template_id = ?',
                (tid,)).fetchall()
        qa_by_id = {r[0]: {'question': r[1] or '', 'standard_sql': r[2] or '', 'is_usable': bool(r[3])}
                    for r in qa_rows}
        outline = json.loads(row[3]) if row[3] else []
        # 问题对象化：{question, qa_id, has_sql, is_usable}
        for sec in outline:
            qs = []
            for q in (sec.get('questions') or []):
                if isinstance(q, dict):
                    qa_id = q.get('qa_id')
                    text = str(q.get('question') or '').strip()
                else:
                    qa_id, text = None, str(q).strip()
                if not text:
                    continue
                info = qa_by_id.get(qa_id) or {}
                qs.append({'question': text, 'qa_id': qa_id,
                           'has_sql': bool(info.get('standard_sql')),
                           'is_usable': bool(info.get('is_usable'))})
            sec['questions'] = qs
        return jsonify({'success': True, 'item': {
            'id': row[0], 'name': row[1],
            'trigger_words': json.loads(row[2]) if row[2] else [],
            'outline': outline, 'enabled': bool(row[4]), 'remark': row[5] or '',
        }})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/templates', methods=['GET'])
def list_templates():
    try:
        return jsonify({'success': True, 'items': _get_planner().load_templates()})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/templates', methods=['POST'])
def create_template():
    data = request.get_json() or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'success': False, 'error': '模板名称不能为空'}), 400
    try:
        outline = data.get('outline') or []
        with db_manager.connect_governance() as conn:
            cursor = conn.execute('''
                INSERT INTO report_templates (name, trigger_words, outline, enabled, remark, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (name,
                  json.dumps(data.get('trigger_words') or [], ensure_ascii=False),
                  json.dumps(outline, ensure_ascii=False),
                  1 if data.get('enabled', True) else 0,
                  data.get('remark') or '',
                  datetime.now().isoformat(), datetime.now().isoformat()))
            tid = cursor.lastrowid
            sync = _sync_template_questions(conn, tid, name, outline)
            conn.commit()
            if sync.get('regen_ids'):
                _spawn_sql_regen(sync['regen_ids'])
            sync.pop('regen_ids', None)
            return jsonify({'success': True, 'id': tid, 'sync': sync})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/templates/<int:tid>', methods=['PUT'])
def update_template(tid):
    data = request.get_json() or {}
    try:
        outline = data.get('outline') or []
        name = (data.get('name') or '').strip()
        with db_manager.connect_governance() as conn:
            conn.execute('''
                UPDATE report_templates
                SET name = ?, trigger_words = ?, outline = ?, enabled = ?, remark = ?, updated_at = ?
                WHERE id = ?
            ''', (name,
                  json.dumps(data.get('trigger_words') or [], ensure_ascii=False),
                  json.dumps(outline, ensure_ascii=False),
                  1 if data.get('enabled', True) else 0,
                  data.get('remark') or '',
                  datetime.now().isoformat(), tid))
            sync = _sync_template_questions(conn, tid, name, outline)
            conn.commit()
        if sync.get('regen_ids'):
            _spawn_sql_regen(sync['regen_ids'])
        sync.pop('regen_ids', None)
        return jsonify({'success': True, 'sync': sync})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/templates/<int:tid>', methods=['DELETE'])
def delete_template(tid):
    try:
        with db_manager.connect_governance() as conn:
            conn.execute('DELETE FROM report_templates WHERE id = ?', (tid,))
            conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ==================== 模板提炼 ====================

_DISTILL_SYSTEM = '你是报告模板提炼专家，擅长把一次性报告泛化为可复用的报告模板。'

_DISTILL_PROMPT = '''根据下面这份报告的生成意图、取数计划与报告正文，提炼出一份可复用的报告模板。

【原始意图】{intent}
【取数计划（分章节的标准化问题）】
{plan_block}
【报告正文（节选）】
{report_head}

提炼要求：
1. name：模板名称，泛化处理（去掉具体单位名和期间），如"设备管理月报"；
2. trigger_words：3~6 个触发词，用户意图中含这些词时应命中本模板（含 name 本身与同义说法）；
3. outline：章节大纲，每章 {{section_title, hint, questions}}；hint 说明该章的取数方向（问什么指标）；questions 为该章的 1~3 道标准问数问题，每道为 {{"question": "...", "src": "q题号"}}——question 从取数计划对应问题的原文改写（仅把具体单位名/期间替换为"本月"等相对表述），src 填该问题在取数计划中的原题号（如 q3），用于回挂已验证 SQL，务必对应准确；
4. 只输出严格 JSON：{{"name": "...", "trigger_words": [...], "outline": [{{"section_title": "...", "hint": "...", "questions": [{{"question": "...", "src": "q1"}}]}}]}}'''


@bp.route('/distill-template', methods=['POST'])
def distill_template():
    """从历史报告提炼可复用模板（只提炼不落库，前端确认编辑后走 templates 接口保存）。"""
    data = request.get_json() or {}
    run_id = data.get('run_id')
    if not run_id:
        return jsonify({'success': False, 'error': '缺少 run_id'}), 400
    try:
        with db_manager.connect_governance() as conn:
            row = conn.execute(
                'SELECT intent_text, plan_json, report_md, detail_json FROM report_runs WHERE id = ?',
                (run_id,)).fetchone()
        if not row:
            return jsonify({'success': False, 'error': '报告不存在'}), 404

        intent_text = row[0] or ''
        plan = json.loads(row[1]) if row[1] else {}
        report_head = (row[2] or '')[:3000]
        # 运行明细：question -> {sql, qa_id}，用于把已验证 SQL 挂回模板问题
        details = json.loads(row[3]) if len(row) > 3 and row[3] else []
        run_qas = [
            {'qid': d.get('qid'),
             'question': str(d.get('question') or '').strip(),
             'standard_sql': str(d.get('sql') or ''),
             'qa_id': d.get('qa_id')}
            for d in details
            if str(d.get('question') or '').strip()
               and d.get('status') == 'ok' and d.get('sql')
        ]
        run_by_qid = {r['qid']: r for r in run_qas if r.get('qid')}

        def _match_run_qa(text: str, src: str = None):
            """回填已验证 SQL：优先 src 题号精确回挂（LLM 提炼时标注）；
            src 有标注但该题无成功 SQL → 不挂（宁缺勿错，避免口径错挂）；
            无 src 时兜底题干规范化精确匹配 + 模糊匹配（阈值 0.6）。"""
            if src:
                return run_by_qid.get(src)
            from core.context import rag_retriever
            norm = _norm_q(text)
            for r in run_qas:
                if _norm_q(r['question']) == norm:
                    return r
            best, best_score = None, 0.6
            for r in run_qas:
                score = max(rag_retriever._similarity_score(text, r['question']),
                            rag_retriever._partial_similarity_score(text, r['question']))
                if score > best_score:
                    best, best_score = r, score
            return best

        plan_block = '\n'.join(
            f"■ {sec.get('section_title', '')}\n" + '\n'.join(
                f"  - {q.get('qid', '?')}: {q.get('question', '')}" for q in sec.get('questions', []))
            for sec in (plan.get('sections') or [])) or '（无计划信息）'

        from core.llm_config import call_chat
        from modules.report.planner import _extract_json
        resp = call_chat(
            [{'role': 'system', 'content': _DISTILL_SYSTEM},
             {'role': 'user', 'content': _DISTILL_PROMPT.format(
                 intent=intent_text, plan_block=plan_block, report_head=report_head)}],
            max_tokens=2000, thinking=False, effort='low')
        template = _extract_json(resp['content'])
        # 规整结构，缺省兜底；命中运行明细的问题回填 standard_sql/qa_id（供保存时直接入问答对库）
        outline = []
        for s in (template.get('outline') or []):
            if not isinstance(s, dict):
                continue
            questions = []
            for q in (s.get('questions') or []):
                text = str(q.get('question') if isinstance(q, dict) else q or '').strip()
                if not text:
                    continue
                src = str(q.get('src') or '').strip() if isinstance(q, dict) else ''
                hit = _match_run_qa(text, src or None)
                if hit:
                    questions.append({'question': text, 'standard_sql': hit['standard_sql'],
                                      'qa_id': hit.get('qa_id')})
                else:
                    questions.append(text)
            outline.append({'section_title': str(s.get('section_title') or '').strip(),
                            'hint': str(s.get('hint') or '').strip(),
                            'questions': questions})
        outline = [s for s in outline if s['section_title']]
        if not outline:  # LLM 未给出大纲时退化为计划章节，问题按 qid 直接回挂 SQL
            outline = [{'section_title': sec.get('section_title', ''), 'hint': '',
                        'questions': [
                            {'question': str(q.get('question') or '').strip(),
                             'standard_sql': (run_by_qid.get(q.get('qid')) or {}).get('standard_sql', ''),
                             'qa_id': (run_by_qid.get(q.get('qid')) or {}).get('qa_id')}
                            for q in (sec.get('questions') or [])
                            if str(q.get('question') or '').strip()]}
                       for sec in (plan.get('sections') or [])]
        return jsonify({'success': True, 'template': {
            'name': str(template.get('name') or '').strip() or (plan.get('report_title') or '新模板'),
            'trigger_words': [str(w).strip() for w in (template.get('trigger_words') or []) if str(w).strip()],
            'outline': outline,
            'remark': f'提炼自 run#{run_id}',
        }})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


# ==================== 历史 ====================

def _parse_row(r, with_body: bool):
    plan = None
    try:
        plan = json.loads(r[4]) if r[4] else None
    except Exception:
        pass
    item = {
        'id': r[0],
        'session_id': r[1],
        'intent_text': r[2] or '',
        'template_id': r[3],
        'report_title': (plan or {}).get('report_title') or (r[2] or '')[:30],
        'status': r[7],
        'duration_ms': r[9],
        'created_at': str(r[10]) if r[10] else None,
    }
    if with_body:
        item.update({
            'plan': plan,
            'detail': json.loads(r[5]) if r[5] else [],
            'report_md': r[6] or '',
            'usage': json.loads(r[8]) if r[8] else {},
        })
    return item


_RUN_COLS = 'id, session_id, intent_text, template_id, plan_json, detail_json, report_md, status, usage_json, duration_ms, created_at'


@bp.route('/runs', methods=['GET'])
def list_runs():
    page = request.args.get('page', 1, type=int)
    per_page = min(request.args.get('per_page', 20, type=int), 100)
    try:
        with db_manager.connect_governance() as conn:
            total = conn.execute('SELECT COUNT(*) FROM report_runs').fetchone()[0]
            rows = conn.execute(
                f'SELECT {_RUN_COLS} FROM report_runs ORDER BY id DESC LIMIT ? OFFSET ?',
                (per_page, (page - 1) * per_page)).fetchall()
        return jsonify({'success': True, 'total': total, 'page': page,
                        'items': [_parse_row(r, with_body=False) for r in rows]})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/runs/<int:rid>', methods=['GET'])
def get_run(rid):
    try:
        with db_manager.connect_governance() as conn:
            row = conn.execute(
                f'SELECT {_RUN_COLS} FROM report_runs WHERE id = ?', (rid,)).fetchone()
        if not row:
            return jsonify({'success': False, 'error': '报告不存在'}), 404
        return jsonify({'success': True, 'item': _parse_row(row, with_body=True)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/runs/<int:rid>', methods=['DELETE'])
def delete_run(rid):
    try:
        with db_manager.connect_governance() as conn:
            conn.execute('DELETE FROM report_runs WHERE id = ?', (rid,))
            conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/runs/<int:rid>/export', methods=['GET'])
def export_run(rid):
    try:
        with db_manager.connect_governance() as conn:
            row = conn.execute(
                'SELECT report_md, intent_text FROM report_runs WHERE id = ?', (rid,)).fetchone()
        if not row:
            return jsonify({'success': False, 'error': '报告不存在'}), 404
        filename = f'report_{rid}.md'
        return Response(row[0] or '', mimetype='text/markdown; charset=utf-8', headers={
            'Content-Disposition': f'attachment; filename="{filename}"',
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
