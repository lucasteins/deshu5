# -*- coding: utf-8 -*-
"""训练模块路由：训练模式 + 智能问答 + 问答对库 + 错题集

- 智能问答：自然语言 → RAG 检索 → SQL 生成（生成-审查循环）→ 只读执行 → 别名翻译 → 审计
- 训练模式：智能出题 → 合理性评价 → SQL 生成 → 结果判断（判断回流问答对库/错题集）
- 问答对库 / 错题集：知识库 CRUD 与语义检索
"""
import json
import re
import time
import uuid
from datetime import datetime
from typing import List

from flask import Blueprint, Response, jsonify, request, stream_with_context

import config
from core.context import db_manager, rag_retriever

bp = Blueprint('training', __name__)

# 会话缓存（内存；生产多进程部署建议换 Redis）
session_cache = {}

# ---------- 惰性单例（避免启动时因 LLM 配置缺失报错）----------
sql_generator = None
sql_reviewer = None
sql_alias_translator = None
question_generator = None


def invalidate_generator():
    """工作流预设切换后调用：失效生成器缓存，下次请求按新预设重建。"""
    global sql_generator
    sql_generator = None


def _get_generator():
    global sql_generator
    if sql_generator is None:
        from modules.training.engine.sql_generator import SQLGenerator
        sql_generator = SQLGenerator()
    return sql_generator


def _get_reviewer():
    global sql_reviewer
    if sql_reviewer is None:
        from modules.training.engine.sql_reviewer import SQLReviewer
        sql_reviewer = SQLReviewer()
    return sql_reviewer


def _get_alias_translator():
    global sql_alias_translator
    if sql_alias_translator is None:
        from modules.training.engine.sql_alias_translator import SQLAliasTranslator
        sql_alias_translator = SQLAliasTranslator()
    return sql_alias_translator


def _get_question_generator():
    global question_generator
    if question_generator is None:
        from modules.training.engine.question_generator import QuestionGenerator
        question_generator = QuestionGenerator()
    return question_generator


# ==================== 生成-审查循环核心 ====================

def _perform_generate_sql(user_question, no_reference=False, request_mode='qa', qa_id=None,
                          generated=False, progress_cb=None, thinking_cb=None):
    """生成 SQL 的完整流程（JSON 与 SSE 两端点共用）。

    返回 (payload, http_status)；progress_cb(stage, elapsed_ms) 在阶段完成时回调，
    stage 取值：rag / schema / llm / validate / exec / audit。
    thinking_cb(evt_dict) 在关键节点回调思考事件（kind: intent/tables/columns/
    code_values/llm/repair/template/draft/review/exec/audit），仅 SSE 端点传入。
    """
    def _emit(stage, ms):
        if progress_cb:
            try:
                progress_cb(stage, ms)
            except Exception:
                pass

    def _think(evt):
        if thinking_cb:
            try:
                thinking_cb(evt)
            except Exception:
                pass

    session_id = str(uuid.uuid4())
    start_time = time.time()
    phase_times = {}

    try:
        # Step 1: RAG 检索（训练生成的题目不带参考 SQL，避免泄露）
        t_rag_start = time.time()
        if no_reference:
            retrieved_pairs = []
        else:
            retrieved_pairs = rag_retriever.retrieve(user_question)
        phase_times['rag'] = int((time.time() - t_rag_start) * 1000)
        _emit('rag', phase_times['rag'])

        # Step 2~5: 生成-审查循环
        t_gen_start = time.time()
        generator = _get_generator()
        reviewer = _get_reviewer()

        final_sql = None
        final_explanation = ''
        final_tables = []
        review_result = None
        attempts = 0
        max_attempts = config.MAX_SQL_RETRIES + 1
        phase_times['gen_attempts'] = []

        while attempts < max_attempts:
            attempts += 1
            attempt_start = time.time()

            # 生成 SQL
            review_feedback = review_result['message'] if (review_result and not review_result['passed']) else None
            gen_result = generator.generate(
                user_question=user_question,
                retrieved_pairs=retrieved_pairs,
                review_feedback=review_feedback,
                progress_cb=progress_cb,
                thinking_cb=thinking_cb
            )
            gen_elapsed = int((time.time() - attempt_start) * 1000)

            if not gen_result['success']:
                phase_times['gen'] = int((time.time() - t_gen_start) * 1000)
                phase_times['gen_attempts'].append({'attempt': attempts, 'gen': gen_elapsed, 'review': 0, 'error': gen_result.get('error')})
                phase_times['total'] = int((time.time() - start_time) * 1000)
                # 失败也写入运行日志（execution_status=failed）
                failed_sql = gen_result.get('sql', '') or ''
                try:
                    _record_generation(
                        session_id=session_id,
                        business_question=user_question,
                        generated_sql=failed_sql,
                        retrieved_pairs=json.dumps(retrieved_pairs, ensure_ascii=False),
                        review_result=json.dumps(review_result, ensure_ascii=False),
                        execution_result=json.dumps(
                            {'success': False, 'error': gen_result.get('error', '')}, ensure_ascii=False),
                        attempts=attempts,
                        duration_ms=phase_times['total']
                    )
                except Exception as log_err:
                    print(f"[WARN] 失败日志写入失败: {log_err}")
                return {
                    'success': False,
                    'error': f'LLM 生成失败: {gen_result.get("error", "未知错误")}',
                    'sql': failed_sql,
                    'raw_sql': failed_sql,
                    'raw_response': gen_result.get('raw_response', ''),
                    'prompt_preview': gen_result.get('prompt', '')[:2000] if gen_result.get('prompt') else '',
                    'timing': phase_times
                }, 500

            sql = gen_result['sql']
            final_sql = sql
            final_explanation = gen_result.get('explanation', '')
            final_tables = gen_result.get('tables_involved', [])

            # 审查
            t_review_start = time.time()
            review_result = reviewer.review(sql, final_tables)
            review_elapsed = int((time.time() - t_review_start) * 1000)
            phase_times['gen_attempts'].append({
                'attempt': attempts,
                'gen': gen_elapsed,
                'review': review_elapsed,
                'mode': gen_result.get('generation_mode', 'unknown')
            })

            if review_result['action'] in ('pass', 'warn'):
                break

            # 需要重试，继续循环
            _think({'kind': 'review', 'attempt': attempts,
                    'action': review_result.get('action', ''),
                    'message': str(review_result.get('message', ''))[:200]})

        phase_times['gen'] = int((time.time() - t_gen_start) * 1000)

        # ========== 阶段1：执行纯代码SQL（验证正确性）==========
        t_exec_start = time.time()
        t_pure_exec_start = time.time()
        pure_result = _safe_execute_sql(final_sql)
        phase_times['pure_exec'] = int((time.time() - t_pure_exec_start) * 1000)
        _emit('exec', phase_times['pure_exec'])
        _think({'kind': 'exec', 'status': 'success' if pure_result.get('success') else 'failed',
                'row_count': pure_result.get('row_count', 0),
                'ms': phase_times['pure_exec'],
                'error': str(pure_result.get('error', ''))[:300] if not pure_result.get('success') else ''})

        # 执行失败的 SQL 不能作为答案返回（编造列、错误 JOIN 等必须在源头拦截）
        if not pure_result.get('success'):
            phase_times['total'] = int((time.time() - start_time) * 1000)
            try:
                _record_generation(
                    session_id=session_id,
                    business_question=user_question,
                    generated_sql=final_sql,
                    retrieved_pairs=json.dumps(retrieved_pairs, ensure_ascii=False),
                    review_result=json.dumps(review_result, ensure_ascii=False),
                    execution_result=json.dumps(pure_result, ensure_ascii=False),
                    attempts=attempts,
                    duration_ms=phase_times['total']
                )
            except Exception as log_err:
                print(f"[WARN] 失败日志写入失败: {log_err}")
            return {
                'success': False,
                'error': f"SQL 执行失败，已拦截: {pure_result.get('error')}",
                'sql': final_sql,
                'raw_sql': final_sql,
                'review_status': review_result,
                'timing': phase_times
            }, 500

        # ========== 阶段2：别名翻译模块（两段式分离）==========
        # 纯代码SQL → 带别名展示SQL，只改SELECT子句，其他部分完全不变
        display_sql = final_sql
        alias_execution_result = None
        t_alias_start = time.time()
        if pure_result.get('success'):
            translator = _get_alias_translator()
            translated_sql = translator.translate(final_sql, final_tables, user_question)
            phase_times['alias_translate'] = int((time.time() - t_alias_start) * 1000)
            if translated_sql:
                # 展示 SQL：翻译成功即用（翻译只改 SELECT 别名，与已执行成功的纯代码 SQL 等价）
                display_sql = translated_sql
                # 执行带别名SQL，MySQL 返回的列名即为中文别名
                t_alias_exec_start = time.time()
                alias_execution_result = _safe_execute_sql(translated_sql)
                phase_times['alias_exec'] = int((time.time() - t_alias_exec_start) * 1000)

        phase_times['exec'] = int((time.time() - t_exec_start) * 1000)

        # 最终执行结果：优先使用带别名结果，回退到纯代码结果
        execution_result = alias_execution_result if (alias_execution_result and alias_execution_result.get('success')) else pure_result

        # ========== 执行后审计 ==========
        # 执行结果为空或报错时，强制检索错题集进行深度审计
        t_audit_start = time.time()
        post_execution_audit = None
        exec_error = execution_result.get('error') if not execution_result.get('success') else None
        exec_row_count = execution_result.get('row_count', 0)

        if not execution_result.get('success') or exec_row_count == 0:
            post_execution_audit = reviewer.audit_post_execution(
                sql=final_sql,  # 审计用原始纯代码SQL
                user_question=user_question,
                tables_involved=final_tables,
                execution_error=exec_error,
                row_count=exec_row_count
            )

        phase_times['audit'] = int((time.time() - t_audit_start) * 1000)
        _emit('audit', phase_times['audit'])
        _think({'kind': 'audit',
                'status': (review_result or {}).get('action', 'unknown'),
                'post_audit': bool(post_execution_audit)})
        phase_times['total'] = int((time.time() - start_time) * 1000)

        # 记录生成日志（记录纯代码SQL，不记录带别名SQL）
        log_id = _record_generation(
            session_id=session_id,
            business_question=user_question,
            generated_sql=final_sql,
            retrieved_pairs=json.dumps(retrieved_pairs, ensure_ascii=False),
            review_result=json.dumps(review_result, ensure_ascii=False),
            execution_result=json.dumps(execution_result, ensure_ascii=False),
            post_execution_audit=post_execution_audit,
            attempts=attempts,
            duration_ms=phase_times['total']
        )

        # 缓存会话
        session_cache[session_id] = {
            'log_id': log_id,
            'qa_id': qa_id,
            'question': user_question,
            'sql': display_sql,  # 缓存展示用SQL（带别名）
            'raw_sql': final_sql,  # 缓存原始纯代码SQL
            'review_result': review_result,
            'execution_result': execution_result,
            'post_execution_audit': post_execution_audit,
            'mode': request_mode,
            'generated': generated
        }

        return {
            'success': True,
            'session_id': session_id,
            'sql': display_sql,  # 展示用：带AS中文别名
            'raw_sql': final_sql,  # 原始纯代码SQL（备用）
            'explanation': final_explanation,
            'tables_involved': final_tables,
            'located_tables': gen_result.get('located_tables', []),
            'code_value_hits': gen_result.get('code_value_hits', []),
            'generation_mode': gen_result.get('generation_mode'),
            'workflow': gen_result.get('workflow', ''),
            'usage': gen_result.get('usage', {}),
            'gen_timers': gen_result.get('timers', {}),
            'retrieved_pairs': retrieved_pairs,
            'review_status': review_result,
            'post_execution_audit': post_execution_audit,
            'attempts': attempts,
            'result_preview': execution_result,
            'timing': phase_times
        }, 200

    except Exception as e:
        import traceback
        return {
            'success': False,
            'error': str(e),
            'traceback': traceback.format_exc()
        }, 500


def _safe_execute_sql(sql: str) -> dict:
    """安全执行 SQL（只读，限制行数）"""
    if not sql:
        return {'success': False, 'error': 'SQL为空'}

    sql = _strip_sql_comments(sql)

    # 清理 SQL：只保留第一条有效语句（以第一个分号分割）
    sql = sql.strip()
    first_semicolon = sql.find(';')
    if first_semicolon > 0:
        after_semicolon = sql[first_semicolon + 1:].strip()
        if after_semicolon:
            sql = sql[:first_semicolon + 1]

    # 安全检查
    sql_upper = sql.strip().upper()
    allowed_prefixes = config.ALLOWED_SQL_PREFIXES
    if not any(sql_upper.startswith(p) for p in allowed_prefixes):
        prefix_list = ' / '.join(allowed_prefixes)
        return {'success': False, 'error': f'只允许执行 {prefix_list} 语句'}

    for kw in config.FORBIDDEN_KEYWORDS:
        if kw in sql_upper:
            return {'success': False, 'error': f'包含禁止的关键词: {kw}'}

    try:
        with db_manager.connect_business() as conn:
            sql_for_limit = sql.rstrip(';').strip()
            limited_sql = _add_limit_if_needed(sql_for_limit)
            cursor = conn.execute(limited_sql)

            headers = [desc[0] for desc in cursor.description] if cursor.description else []
            rows = cursor.fetchall()

            rows_serializable = [
                [str(cell) if cell is not None else None for cell in row]
                for row in rows
            ]

            return {
                'success': True,
                'headers': headers,
                'rows': rows_serializable,
                'row_count': len(rows_serializable)
            }
    except Exception as e:
        return {'success': False, 'error': str(e)}


def _extract_fields_from_sql(sql: str) -> List[str]:
    """从 SQL 中提取字段名"""
    if not sql:
        return []
    fields = set()
    # SELECT 字段
    select_match = re.search(r'SELECT\s+(.+?)\s+FROM', sql, re.IGNORECASE | re.DOTALL)
    if select_match:
        for match in re.finditer(r'(?:[\w_]+\.)?([\w_]+)', select_match.group(1)):
            name = match.group(1)
            if name.upper() not in {'SELECT', 'FROM', 'WHERE', 'AND', 'OR', 'AS', 'BY', 'GROUP', 'ORDER', 'LIMIT', 'SUM', 'COUNT', 'AVG', 'MAX', 'MIN', 'DISTINCT'}:
                fields.add(name)
    # WHERE 字段
    where_match = re.search(r'WHERE\s+(.+?)(?:\s+GROUP|\s+ORDER|\s+LIMIT|$)', sql, re.IGNORECASE | re.DOTALL)
    if where_match:
        for match in re.finditer(r'(?:[\w_]+\.)?([\w_]+)\s*(?:=|!=|<>|>|<|>=|<=|LIKE|IN)', where_match.group(1), re.IGNORECASE):
            fields.add(match.group(1))
    return sorted(fields)


def _strip_sql_comments(sql: str) -> str:
    """去掉 SQL 中的注释行和多行注释"""
    sql = re.sub(r'/\*.*?\*/', '', sql, flags=re.DOTALL)
    lines = []
    for line in sql.split('\n'):
        stripped = line.strip()
        if not stripped.startswith('--') and not stripped.startswith('//'):
            lines.append(line)
    return '\n'.join(lines)


def _add_limit_if_needed(sql: str) -> str:
    """如果 SQL 没有 LIMIT，自动添加"""
    sql_stripped = sql.strip()
    if not re.search(r'\bLIMIT\s+\d+\s*$', sql_stripped, re.IGNORECASE):
        sql_stripped += f' LIMIT {config.SQL_MAX_ROWS}'
    return sql_stripped


def _record_generation(**kwargs) -> int:
    """记录生成日志，返回 log_id"""
    review_passed = None
    review_issues = ''
    try:
        review_obj = json.loads(kwargs.get('review_result', '{}'))
        review_passed = 1 if review_obj.get('passed') else 0
        review_issues = json.dumps(review_obj.get('issues', []), ensure_ascii=False)
    except Exception:
        pass

    execution_status = 'unknown'
    row_count = 0
    try:
        exec_obj = json.loads(kwargs.get('execution_result', '{}'))
        execution_status = 'success' if exec_obj.get('success') else 'failed'
        row_count = exec_obj.get('row_count', 0)
    except Exception:
        pass

    rag_count = 0
    try:
        pairs_obj = json.loads(kwargs.get('retrieved_pairs', '[]'))
        rag_count = len(pairs_obj)
    except Exception:
        pass

    post_execution_audit = ''
    try:
        audit_obj = kwargs.get('post_execution_audit')
        if audit_obj:
            post_execution_audit = json.dumps(audit_obj, ensure_ascii=False)
    except Exception:
        pass

    with db_manager.connect_log() as conn:
        cursor = conn.execute('''
            INSERT INTO generation_logs (
                session_id, user_question, generated_sql,
                rag_pairs_count, review_passed, review_issues,
                execution_status, row_count, post_execution_audit,
                attempts, latency_ms, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            kwargs['session_id'],
            kwargs['business_question'],
            kwargs['generated_sql'],
            rag_count,
            review_passed,
            review_issues,
            execution_status,
            row_count,
            post_execution_audit,
            kwargs['attempts'],
            kwargs['duration_ms'],
            datetime.now().isoformat()
        ))
        conn.commit()
        return cursor.lastrowid


# ==================== 智能问答 API ====================

@bp.route('/api/generate-sql', methods=['POST'])
def generate_sql():
    """智能问答：生成 SQL（JSON 版，流程见 _perform_generate_sql）"""
    data = request.get_json() or {}
    user_question = data.get('question', '').strip()
    if not user_question:
        return jsonify({'success': False, 'error': '问题不能为空'}), 400
    payload, status = _perform_generate_sql(
        user_question=user_question,
        no_reference=bool(data.get('no_reference', False)),
        request_mode=data.get('mode', 'qa'),
        qa_id=data.get('qa_id'),
        generated=bool(data.get('generated', False)))
    return jsonify(payload), status


@bp.route('/api/generate-sql-stream', methods=['POST'])
def generate_sql_stream():
    """SSE 流式生成：实时推送阶段进度事件（{stage,ms}）与思考事件（{thinking:{kind,...}}），最后推完整结果。"""
    import queue
    import threading

    data = request.get_json() or {}
    user_question = data.get('question', '').strip()
    if not user_question:
        return jsonify({'success': False, 'error': '问题不能为空'}), 400

    events = queue.Queue()

    def on_progress(stage, ms):
        events.put({'stage': stage, 'ms': ms})

    def on_thinking(evt):
        events.put({'thinking': evt})

    def worker():
        try:
            payload, status = _perform_generate_sql(
                user_question=user_question,
                no_reference=bool(data.get('no_reference', False)),
                request_mode=data.get('mode', 'qa'),
                qa_id=data.get('qa_id'),
                generated=bool(data.get('generated', False)),
                progress_cb=on_progress,
                thinking_cb=on_thinking)
            events.put({'done': True, 'status': status, 'result': payload})
        except Exception as e:
            import traceback
            traceback.print_exc()
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


# ==================== 训练模式 API ====================

# 训练题目中可用作 RAG/去重池的数据过滤条件
_USABLE_QA_FILTER = """
    (is_usable IS NULL OR is_usable = 1)
""".strip()


@bp.route('/api/next-question', methods=['GET'])
def next_question():
    """
    训练模式：出题模块与 SQL 生成完全解耦

    - 只返回自然语言业务题，不生成、不执行 SQL。
    - source=generated 强制使用智能生成题；source=qa 强制使用历史题库；默认 auto。
    - 新生成题目默认 is_usable=0，需经 /api/evaluate-question 评价为"合理"后才进入 RAG/去重池。
    """
    session_id = str(uuid.uuid4())
    start_time = time.time()
    phase_times = {}
    source = request.args.get('source', 'auto')  # auto | generated | qa
    difficulty = request.args.get('difficulty')  # 基础题 | 进阶题 | 挑战题 | None

    qa_id = None
    question = None
    question_source = source
    generated = False

    try:
        # ---------- 1. 尝试智能出题 ----------
        if source in ('auto', 'generated'):
            try:
                t_qgen_start = time.time()
                qgen = _get_question_generator()
                candidate = qgen.generate(difficulty=difficulty)
                if candidate:
                    # 保存为待评价题目（is_usable=0）
                    qa_id = qgen.save_to_qa_pairs(candidate)
                    question = candidate['question']
                    difficulty = candidate['difficulty']
                    generated = True
                    question_source = 'generated'
                phase_times['qgen'] = int((time.time() - t_qgen_start) * 1000)
            except Exception as e:
                print(f"[WARN] 智能出题失败: {e}")
                phase_times['qgen'] = -1

            if source == 'generated' and not generated:
                return jsonify({'success': False, 'error': '未能生成新题目，请稍后重试'}), 503

        # ---------- 2. 回退到历史题库 ----------
        if not generated:
            with db_manager.connect_governance() as conn:
                cursor = conn.execute(f'''
                    SELECT id, question, difficulty
                    FROM qa_pairs
                    WHERE standard_sql IS NOT NULL AND standard_sql != ''
                      AND {_USABLE_QA_FILTER}
                    ORDER BY RAND() LIMIT 1
                ''')
                row = cursor.fetchone()

                if not row:
                    return jsonify({'success': False, 'error': '暂无可用题目'}), 404

                qa_id, question, difficulty = row
                question_source = 'qa'

        phase_times['total'] = int((time.time() - start_time) * 1000)

        # 缓存会话（此时还没有 SQL）
        session_cache[session_id] = {
            'qa_id': qa_id,
            'question': question,
            'sql': None,
            'raw_sql': None,
            'standard_sql': None,
            'execution_result': None,
            'mode': 'training',
            'generated': generated,
            'question_source': question_source,
            'question_rated': False
        }

        return jsonify({
            'success': True,
            'session_id': session_id,
            'qa_id': qa_id,
            'question': question,
            'difficulty': difficulty or '进阶题',
            'source': question_source,
            'generated': generated,
            'timing': phase_times
        })

    except Exception as e:
        import traceback
        return jsonify({'success': False, 'error': str(e), 'traceback': traceback.format_exc()}), 500


@bp.route('/api/evaluate-question', methods=['POST'])
def evaluate_question():
    """
    评价生成题目的合理性。

    请求体：{session_id, rating, feedback?}
    rating 取值：合理 | 不合理
    - 合理：is_usable 保持 0；待 SQL 被判断为"正确"后再置为 1。
    - 不合理：新生成的题目直接从 qa_pairs 删除；历史题库题目标记 is_usable=0，不再进入 RAG/去重池。
    """
    data = request.get_json() or {}
    session_id = data.get('session_id', '')
    rating = data.get('rating', '')

    if not session_id or session_id not in session_cache:
        return jsonify({'success': False, 'error': '会话不存在'}), 400

    if rating not in ('合理', '不合理'):
        return jsonify({'success': False, 'error': '评价类型无效'}), 400

    session = session_cache[session_id]
    qa_id = session.get('qa_id')
    generated = session.get('generated', False)

    is_usable = 0
    deleted = False

    try:
        with db_manager.connect_governance() as conn:
            if qa_id:
                if rating == '不合理' and generated:
                    # 新生成题目被评价为不合理：直接从问答对库删除
                    conn.execute('DELETE FROM qa_pairs WHERE id = ?', (qa_id,))
                    deleted = True
                else:
                    conn.execute('''
                        UPDATE qa_pairs
                        SET is_usable = ?
                        WHERE id = ?
                    ''', (is_usable, qa_id))
            conn.commit()

        session['question_rated'] = True
        session['question_rating'] = rating

        return jsonify({
            'success': True,
            'message': '题目已从问答对库删除' if deleted else '问题评价已记录',
            'rating': rating,
            'is_usable': bool(is_usable),
            'deleted': deleted
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/judge', methods=['POST'])
def submit_judgment():
    """
    提交用户判断（正确/错误/跳过）+ 细粒度标注

    细粒度标注字段：表选择/字段选择/JOIN 路径/WHERE 条件/聚合是否正确、
    人工修正 SQL、标注备注。
    """
    data = request.get_json() or {}
    session_id = data.get('session_id', '')
    judgment = data.get('judgment', '')
    feedback = data.get('feedback', '')

    annotation = {
        'table_choice_correct': data.get('table_choice_correct'),
        'field_choice_correct': data.get('field_choice_correct'),
        'join_path_correct': data.get('join_path_correct'),
        'where_condition_correct': data.get('where_condition_correct'),
        'aggregation_correct': data.get('aggregation_correct'),
        'human_corrected_sql': data.get('human_corrected_sql', ''),
        'annotation_remark': data.get('annotation_remark', '')
    }

    if not session_id or session_id not in session_cache:
        return jsonify({'success': False, 'error': '会话不存在'}), 400

    if judgment not in ('正确', '错误', '跳过'):
        return jsonify({'success': False, 'error': '判断类型无效'}), 400

    session = session_cache[session_id]

    try:
        # 更新生成日志（包含细粒度标注）
        with db_manager.connect_log() as conn:
            conn.execute('''
                UPDATE generation_logs 
                SET user_judgment = ?, user_feedback = ?,
                    table_choice_correct = ?, field_choice_correct = ?,
                    join_path_correct = ?, where_condition_correct = ?,
                    aggregation_correct = ?, human_corrected_sql = ?,
                    annotation_remark = ?
                WHERE session_id = ?
            ''', (
                judgment, feedback,
                annotation['table_choice_correct'],
                annotation['field_choice_correct'],
                annotation['join_path_correct'],
                annotation['where_condition_correct'],
                annotation['aggregation_correct'],
                annotation['human_corrected_sql'],
                annotation['annotation_remark'],
                session_id
            ))
            conn.commit()

        # 训练模式下，对"正确"的生成题，把生成的 SQL 回流为 qa_pairs 的标准答案
        if judgment == '正确' and session.get('generated') and session.get('qa_id'):
            try:
                with db_manager.connect_governance() as conn:
                    generated_sql = session.get('raw_sql') or session.get('sql')
                    conn.execute('''
                        UPDATE qa_pairs
                        SET standard_sql = ?, is_usable = 1
                        WHERE id = ?
                    ''', (generated_sql, session['qa_id']))
                    conn.commit()
            except Exception as e:
                print(f"[WARN] 回流生成题标准 SQL 失败: {e}")

        # 如果是错误判断，保留 session_cache 以便后续提交详细反馈
        # error_record 的创建交由 /api/error-record 处理
        if judgment != '错误':
            del session_cache[session_id]

        return jsonify({'success': True, 'message': '判断已记录'})

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


def _create_error_record(session: dict, feedback: str = '') -> int:
    """创建错题记录"""
    with db_manager.connect_governance() as conn:
        cursor = conn.execute('''
            INSERT INTO error_records (
                business_question, generated_sql, correct_sql,
                error_type, error_detail, sql_pattern,
                tables_involved, fields_involved,
                is_resolved, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            session.get('question', ''),
            session.get('sql', ''),
            session.get('standard_sql', ''),
            '待分类',
            feedback or '用户在训练模式中标记为错误',
            '',
            json.dumps(session.get('tables_involved', []), ensure_ascii=False),
            '',
            0,
            datetime.now().isoformat()
        ))
        conn.commit()
        return cursor.lastrowid


@bp.route('/api/save-qa', methods=['POST'])
def save_qa():
    """保存问答对到 qa_pairs"""
    data = request.get_json() or {}
    question = data.get('question', '').strip()
    sql = data.get('sql', '').strip()
    difficulty = data.get('difficulty', '进阶题')

    if not question or not sql:
        return jsonify({'success': False, 'error': '问题和SQL不能为空'}), 400

    try:
        with db_manager.connect_governance() as conn:
            conn.execute('''
                INSERT INTO qa_pairs (question, standard_sql, difficulty, source, ingest_time)
                VALUES (?, ?, ?, ?, ?)
            ''', (question, sql, difficulty, 'user_saved', datetime.now().isoformat()))
            conn.commit()

        return jsonify({'success': True, 'message': '问答对已保存'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/error-record', methods=['POST'])
def submit_error_record():
    """提交错误记录（从弹窗），支持细粒度错误维度"""
    data = request.get_json() or {}
    session_id = data.get('session_id', '')
    error_type = data.get('error_type', '其他')
    correct_sql = data.get('correct_sql', '')
    error_detail = data.get('error_detail', '')

    error_dimensions = {
        'table_choice_error': data.get('table_choice_error', False),
        'field_choice_error': data.get('field_choice_error', False),
        'join_path_error': data.get('join_path_error', False),
        'where_condition_error': data.get('where_condition_error', False),
        'aggregation_error': data.get('aggregation_error', False)
    }

    session = session_cache.get(session_id, {})

    try:
        with db_manager.connect_governance() as conn:
            # 检查是否已有相同模式的记录（frequency 累加）
            sql_pattern = session.get('sql', '')[:200]  # 取前200字符作为模式
            cursor = conn.execute(
                'SELECT id, frequency FROM error_records WHERE sql_pattern = ?',
                (sql_pattern,)
            )
            existing = cursor.fetchone()

            if existing:
                # 更新频率 + 补充 correct_sql / error_detail（如果之前为空）
                conn.execute(
                    '''UPDATE error_records 
                       SET frequency = frequency + 1, 
                           last_occurred = ?,
                           correct_sql = CASE WHEN correct_sql IS NULL OR correct_sql = '' THEN ? ELSE correct_sql END,
                           error_detail = CASE WHEN error_detail IS NULL OR error_detail = '' THEN ? ELSE error_detail END
                       WHERE id = ?''',
                    (datetime.now().isoformat(), correct_sql, error_detail, existing[0])
                )
                conn.commit()
                return jsonify({'success': True, 'message': '错误记录频率已更新，修正建议已补充'})

            # 从生成 SQL 中提取字段
            generated_sql = session.get('sql', '')
            fields_involved = _extract_fields_from_sql(generated_sql)

            # 组装错误维度信息
            dimensions_str = json.dumps(error_dimensions, ensure_ascii=False)

            cursor = conn.execute('''
                INSERT INTO error_records (
                    business_question, generated_sql, correct_sql,
                    error_type, error_detail, sql_pattern,
                    tables_involved, fields_involved,
                    is_resolved, frequency, created_at, last_occurred
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                session.get('question', ''),
                generated_sql,
                correct_sql,
                error_type,
                error_detail + '\n错误维度：' + dimensions_str if any(error_dimensions.values()) else error_detail,
                sql_pattern,
                json.dumps(session.get('tables_involved', []), ensure_ascii=False),
                json.dumps(fields_involved, ensure_ascii=False),
                0,
                1,
                datetime.now().isoformat(),
                datetime.now().isoformat()
            ))
            conn.commit()

        # 清理会话缓存（训练模式/智能问答模式在提交详细反馈后清理）
        if session_id in session_cache:
            del session_cache[session_id]

        return jsonify({'success': True, 'message': '错误记录已保存'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ==================== 问答对库管理 API ====================

@bp.route('/api/qa-pairs')
def get_qa_pairs():
    """获取问答对列表"""
    try:
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 20, type=int)
        offset = (page - 1) * per_page

        with db_manager.connect_governance() as conn:
            cursor = conn.execute(
                '''SELECT id, question, standard_sql, difficulty, source, ingest_time,
                          is_usable
                   FROM qa_pairs ORDER BY id DESC LIMIT ? OFFSET ?''',
                (per_page, offset)
            )
            rows = cursor.fetchall()

            cursor = conn.execute('SELECT COUNT(*) FROM qa_pairs')
            total = cursor.fetchone()[0]

        items = []
        for row in rows:
            items.append({
                'id': row[0],
                'question': row[1],
                'standard_sql': row[2],
                'difficulty': row[3],
                'source': row[4],
                'ingest_time': row[5],
                'is_usable': row[6],
                # 评价/元数据列已下线，键保留默认值兼容前端
                'question_rating': None,
                'tables_involved': []
            })

        return jsonify({'success': True, 'total': total, 'page': page, 'per_page': per_page, 'items': items})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/qa-detail')
def get_qa_detail():
    """获取单条问答对详情"""
    try:
        qa_id = request.args.get('id', type=int)
        if not qa_id:
            return jsonify({'success': False, 'error': '缺少id参数'}), 400

        with db_manager.connect_governance() as conn:
            cursor = conn.execute(
                'SELECT id, question, standard_sql, difficulty, source, ingest_time FROM qa_pairs WHERE id = ?',
                (qa_id,)
            )
            row = cursor.fetchone()

        if not row:
            return jsonify({'success': False, 'error': '问答对不存在'}), 404

        return jsonify({
            'success': True,
            'item': {
                'id': row[0],
                'question': row[1] or '',
                'standard_sql': row[2] or '',
                'difficulty': row[3] or '进阶题',
                'source': row[4] or 'user_saved',
                'ingest_time': row[5]
            }
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/qa-search', methods=['POST'])
def search_qa_pairs():
    """语义搜索问答对：按 RAG 相似度排序

    输入：{ 'query': '杭州供电单位查询', 'limit': 20 }
    输出：按相似度得分从高到低排序的问答对列表
    """
    data = request.get_json() or {}
    query = data.get('query', '').strip()
    limit = data.get('limit', 20)

    if not query:
        return jsonify({'success': False, 'error': '搜索关键词不能为空'}), 400

    try:
        # 复用 RAGRetriever 的检索方法（它本身就是对 qa_pairs 做相似度检索）
        retrieved_pairs = rag_retriever.retrieve(query)

        results = []
        for pair in retrieved_pairs[:limit]:
            qa_id = pair.get('id')
            if qa_id:
                with db_manager.connect_governance() as conn:
                    cursor = conn.execute(
                        'SELECT id, question, standard_sql, difficulty, source, ingest_time FROM qa_pairs WHERE id = ?',
                        (qa_id,)
                    )
                    row = cursor.fetchone()
                    if row:
                        results.append({
                            'id': row[0],
                            'question': row[1] or '',
                            'standard_sql': row[2] or '',
                            'difficulty': row[3] or '进阶题',
                            'source': row[4] or 'rag',
                            'ingest_time': row[5],
                            'similarity_score': pair.get('similarity_score', 0),
                            'combined_score': pair.get('combined_score', 0)
                        })
            else:
                # 没有 id 的 RAG 结果（外部 QA 对），直接返回
                results.append({
                    'id': None,
                    'question': pair.get('question', ''),
                    'standard_sql': pair.get('standard_sql', ''),
                    'difficulty': pair.get('difficulty', '进阶题'),
                    'source': pair.get('source', 'rag'),
                    'ingest_time': pair.get('ingest_time', ''),
                    'similarity_score': pair.get('similarity_score', 0),
                    'combined_score': pair.get('combined_score', 0)
                })

        return jsonify({
            'success': True,
            'query': query,
            'count': len(results),
            'items': results
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/qa-update', methods=['POST'])
def update_qa_pair():
    """更新问答对。输入：{ 'id': 1, 'question': '...', 'standard_sql': '...', 'difficulty': '进阶题' }"""
    data = request.get_json() or {}
    qa_id = data.get('id')

    if not qa_id:
        return jsonify({'success': False, 'error': '缺少id参数'}), 400

    try:
        update_fields = []
        params = []

        if 'question' in data:
            update_fields.append('question = ?')
            params.append(data['question'])
        if 'standard_sql' in data:
            update_fields.append('standard_sql = ?')
            params.append(data['standard_sql'])
        if 'difficulty' in data:
            update_fields.append('difficulty = ?')
            params.append(data['difficulty'])

        if not update_fields:
            return jsonify({'success': False, 'error': '没有提供要更新的字段'}), 400

        params.append(qa_id)

        with db_manager.connect_governance() as conn:
            conn.execute(
                f'UPDATE qa_pairs SET {", ".join(update_fields)} WHERE id = ?',
                tuple(params)
            )
            conn.commit()

        return jsonify({'success': True, 'message': '问答对已更新'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/qa-delete', methods=['POST'])
def delete_qa_pair():
    """删除问答对。输入：{ 'id': 1 }"""
    data = request.get_json() or {}
    qa_id = data.get('id')

    if not qa_id:
        return jsonify({'success': False, 'error': '缺少id参数'}), 400

    try:
        with db_manager.connect_governance() as conn:
            cursor = conn.execute(
                'DELETE FROM qa_pairs WHERE id = ?',
                (qa_id,)
            )
            conn.commit()

            if cursor.rowcount == 0:
                return jsonify({'success': False, 'error': '问答对不存在'}), 404

        return jsonify({'success': True, 'message': '问答对已删除'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ==================== 错题集管理 API ====================

@bp.route('/api/error-list')
def get_error_list():
    """获取错题列表"""
    try:
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 20, type=int)
        error_type = request.args.get('error_type', None)
        resolved = request.args.get('resolved', None)
        offset = (page - 1) * per_page

        conditions, params = [], []
        if error_type:
            conditions.append('error_type = ?')
            params.append(error_type)
        if resolved in ('0', '1'):
            conditions.append('is_resolved = ?')
            params.append(int(resolved))
        where = ('WHERE ' + ' AND '.join(conditions)) if conditions else ''

        with db_manager.connect_governance() as conn:
            cursor = conn.execute(
                f'''SELECT id, business_question, generated_sql, correct_sql, error_type,
                           error_detail, is_resolved, frequency, created_at
                    FROM error_records {where} ORDER BY id DESC LIMIT ? OFFSET ?''',
                (*params, per_page, offset)
            )
            cursor_total = conn.execute(
                f'SELECT COUNT(*) FROM error_records {where}', params
            )

            rows = cursor.fetchall()
            total = cursor_total.fetchone()[0]

        items = []
        for row in rows:
            items.append({
                'id': row[0],
                'business_question': row[1],
                'generated_sql': row[2],
                'correct_sql': row[3],
                'error_type': row[4],
                'error_detail': row[5],
                'is_resolved': bool(row[6]),
                'frequency': row[7] or 1,
                'created_at': row[8]
            })

        return jsonify({'success': True, 'total': total, 'page': page, 'per_page': per_page, 'items': items})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/error-detail')
def get_error_detail():
    """获取单条错题详情（编辑用）"""
    try:
        error_id = request.args.get('id', type=int)
        if not error_id:
            return jsonify({'success': False, 'error': '缺少id参数'}), 400

        with db_manager.connect_governance() as conn:
            cursor = conn.execute(
                '''SELECT id, business_question, generated_sql, correct_sql, error_type, error_detail,
                          is_resolved, frequency, created_at, resolution_note, tables_involved
                   FROM error_records WHERE id = ?''',
                (error_id,)
            )
            row = cursor.fetchone()

        if not row:
            return jsonify({'success': False, 'error': '错题不存在'}), 404

        return jsonify({
            'success': True,
            'item': {
                'id': row[0],
                'business_question': row[1] or '',
                'generated_sql': row[2] or '',
                'correct_sql': row[3] or '',
                'error_type': row[4] or '其他',
                'error_detail': row[5] or '',
                'is_resolved': bool(row[6]),
                'frequency': row[7] or 1,
                'created_at': row[8],
                'resolution_note': row[9] or '',
                'tables_involved': row[10] or ''
            }
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/error-search', methods=['POST'])
def search_errors():
    """语义搜索错题：按 RAG 相似度排序

    输入：{ 'query': '杭州供电单位查询' }
    输出：按相似度得分从高到低排序的错题列表
    """
    data = request.get_json() or {}
    query = data.get('query', '').strip()

    if not query:
        return jsonify({'success': False, 'error': '搜索关键词不能为空'}), 400

    try:
        with db_manager.connect_governance() as conn:
            cursor = conn.execute(
                '''SELECT id, business_question, generated_sql, correct_sql, error_type, error_detail,
                          is_resolved, frequency, created_at, resolution_note, tables_involved
                   FROM error_records'''
            )
            all_records = cursor.fetchall()

        if not all_records:
            return jsonify({'success': True, 'items': []})

        records = []
        for row in all_records:
            records.append({
                'id': row[0],
                'business_question': row[1] or '',
                'generated_sql': row[2] or '',
                'correct_sql': row[3] or '',
                'error_type': row[4] or '其他',
                'error_detail': row[5] or '',
                'is_resolved': bool(row[6]),
                'frequency': row[7] or 1,
                'created_at': row[8],
                'resolution_note': row[9] or '',
                'tables_involved': row[10] or ''
            })

        user_keywords = rag_retriever.extract_keywords(query)

        scored_items = []
        for record in records:
            question = record['business_question']
            sim_score = rag_retriever._similarity_score(query, question)
            kw_score = rag_retriever._keyword_match_score(user_keywords, question)
            score = sim_score * 0.6 + kw_score * 0.4

            if score > 0.05:  # 过滤掉几乎不相关的
                record['similarity_score'] = round(score, 3)
                record['match_type'] = '语义匹配'
                scored_items.append(record)

        scored_items.sort(key=lambda x: x['similarity_score'], reverse=True)

        return jsonify({
            'success': True,
            'query': query,
            'count': len(scored_items),
            'items': scored_items
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/error-update', methods=['POST'])
def update_error_record():
    """更新错题记录。输入：{ 'id': 1, 'correct_sql': '...', 'error_type': '...', 'error_detail': '...' }"""
    data = request.get_json() or {}
    error_id = data.get('id')

    if not error_id:
        return jsonify({'success': False, 'error': '缺少id参数'}), 400

    try:
        update_fields = []
        params = []

        if 'business_question' in data:
            update_fields.append('business_question = ?')
            params.append(data['business_question'])
        if 'generated_sql' in data:
            update_fields.append('generated_sql = ?')
            params.append(data['generated_sql'])
            # 同步更新 sql_pattern
            update_fields.append('sql_pattern = ?')
            params.append(data['generated_sql'][:200])
        if 'correct_sql' in data:
            update_fields.append('correct_sql = ?')
            params.append(data['correct_sql'])
        if 'error_type' in data:
            update_fields.append('error_type = ?')
            params.append(data['error_type'])
        if 'error_analysis' in data or 'sql_tips' in data:
            parts = []
            if data.get('error_analysis'):
                parts.append('错误分析：' + data['error_analysis'])
            if data.get('sql_tips'):
                parts.append('SQL技巧：' + data['sql_tips'])
            if parts:
                update_fields.append('error_detail = ?')
                params.append('\n\n'.join(parts))
        if 'error_detail' in data:
            update_fields.append('error_detail = ?')
            params.append(data['error_detail'])
        if 'user_feedback' in data:
            update_fields.append('user_feedback = ?')
            params.append(data['user_feedback'])

        if not update_fields:
            return jsonify({'success': False, 'error': '没有提供要更新的字段'}), 400

        # 自动更新 last_occurred
        update_fields.append('last_occurred = ?')
        params.append(datetime.now().isoformat())

        params.append(error_id)

        with db_manager.connect_governance() as conn:
            conn.execute(
                f'UPDATE error_records SET {", ".join(update_fields)} WHERE id = ?',
                tuple(params)
            )
            conn.commit()

        return jsonify({'success': True, 'message': '错题已更新'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/error-delete', methods=['POST'])
def delete_error_record():
    """删除错题记录。输入：{ 'id': 1 }"""
    data = request.get_json() or {}
    error_id = data.get('id')

    if not error_id:
        return jsonify({'success': False, 'error': '缺少id参数'}), 400

    try:
        with db_manager.connect_governance() as conn:
            cursor = conn.execute(
                'DELETE FROM error_records WHERE id = ?',
                (error_id,)
            )
            conn.commit()

            if cursor.rowcount == 0:
                return jsonify({'success': False, 'error': '错题不存在'}), 404

        return jsonify({'success': True, 'message': '错题已删除'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/error-resolve', methods=['POST'])
def resolve_error_record():
    """标记错题为已修复 / 取消已修复

    输入：{ 'id': 1, 'is_resolved': true/false, 'resolution_note': '已修正SQL条件' }
    """
    data = request.get_json() or {}
    error_id = data.get('id')
    is_resolved = data.get('is_resolved')

    if not error_id:
        return jsonify({'success': False, 'error': '缺少id参数'}), 400

    if is_resolved is None:
        is_resolved = True

    try:
        resolution_note = data.get('resolution_note', '')

        with db_manager.connect_governance() as conn:
            conn.execute(
                'UPDATE error_records SET is_resolved = ?, resolution_note = ?, last_occurred = ? WHERE id = ?',
                (1 if is_resolved else 0, resolution_note, datetime.now().isoformat(), error_id)
            )
            conn.commit()

        return jsonify({'success': True, 'message': '已标记为已修复' if is_resolved else '已取消已修复'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
