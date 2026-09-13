# -*- coding: utf-8 -*-
"""SQL 查询（SQL Lab）路由：手写 SQL 只读执行 + LLM 辅助编写。

- /api/sqllab/execute：复用 core/sql_exec 只读执行器（SELECT/WITH 白名单 + 禁用词 + 自动 LIMIT）
- /api/sqllab/assist：generate 复用训练问答的完整 NL2SQL 管线；
  fix / explain / optimize 走轻量助手（assistant.py，Schema 上下文取自 SchemaPreloader）
- assist 只返回 SQL 文本，绝不自动执行——由前端插入编辑器后用户手动运行
"""
import time

from flask import Blueprint, jsonify, request

from core.sql_exec import safe_execute_sql

bp = Blueprint('sqllab', __name__)

_ASSIST_ACTIONS = ('generate', 'fix', 'explain', 'optimize')


@bp.route('/api/sqllab/execute', methods=['POST'])
def execute_sql():
    data = request.get_json() or {}
    sql = (data.get('sql') or '').strip()
    if not sql:
        return jsonify({'success': False, 'error': 'SQL为空'}), 400

    start = time.time()
    result = safe_execute_sql(sql)
    result['elapsed_ms'] = int((time.time() - start) * 1000)
    return jsonify(result), (200 if result.get('success') else 400)


@bp.route('/api/sqllab/assist', methods=['POST'])
def assist_sql():
    data = request.get_json() or {}
    action = (data.get('action') or '').strip()
    if action not in _ASSIST_ACTIONS:
        return jsonify({'success': False,
                        'error': f'不支持的 action: {action}（可选：{"/".join(_ASSIST_ACTIONS)}）'}), 400

    try:
        if action == 'generate':
            return _assist_generate(data)
        return _assist_lightweight(action, data)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


def _assist_generate(data):
    """自然语言生成 SQL：复用训练问答的完整生成-审查循环，只取生成物（不带会话语义）。"""
    question = (data.get('question') or '').strip()
    if not question:
        return jsonify({'success': False, 'error': 'question 为空'}), 400

    from modules.training.routes import _perform_generate_sql
    payload, status = _perform_generate_sql(user_question=question, request_mode='sqllab')
    if not payload.get('success'):
        return jsonify(payload), status

    return jsonify({
        'success': True,
        'action': 'generate',
        # 与智能问答取同一字段：别名翻译后的展示 SQL（AS 中文别名，执行等价已验证），
        # 保证两处「同一问题同一 SQL」；raw_sql 为纯代码版本（无别名），备查
        'sql': payload.get('sql') or payload.get('raw_sql') or '',
        'raw_sql': payload.get('raw_sql') or '',
        'explanation': payload.get('explanation', ''),
        'tables_involved': payload.get('tables_involved', []),
        'result_preview': payload.get('result_preview'),
        'timing': payload.get('timing'),
    })


def _assist_lightweight(action, data):
    """fix / explain / optimize：围绕编辑器现有 SQL 的轻量改写/解读。"""
    from modules.training.sqllab import assistant

    sql = (data.get('sql') or '').strip()
    if not sql:
        return jsonify({'success': False, 'error': 'sql 为空'}), 400

    if action == 'fix':
        error = (data.get('error') or '').strip()
        if not error:
            return jsonify({'success': False, 'error': 'error 为空'}), 400
        result = assistant.assist_fix(sql, error)
    elif action == 'explain':
        result = assistant.assist_explain(sql)
    else:
        result = assistant.assist_optimize(sql)

    return jsonify({'success': True, 'action': action, **result})
