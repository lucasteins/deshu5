# -*- coding: utf-8 -*-
"""技能对话（Skill Chat）路由：/api/skillchat/*

- GET    /skills                           技能清单（扫描 skills/*/SKILL.md）
- GET    /skills/<name>/examples           该技能示例命令（静态 JSON，可手工维护）
- POST   /skills/<name>/examples/refresh   读 SKILL.md 调 LLM 重新生成示例并写回静态文件
- POST   /sessions                         新建会话 {skill}
- GET    /sessions?skill=<name>            会话列表（按 updated_at 倒序，含 message_count）
- GET    /sessions/<sid>                   会话详情 + 全量消息
- DELETE /sessions/<sid>                   删除会话及其消息
- POST   /chat/stream                      SSE 流式对话（skill 正文 + 最近 20 条历史拼单 prompt）
"""
import json
import queue
import re
import threading
import uuid
from datetime import datetime

from flask import Blueprint, Response, jsonify, request, stream_with_context

from core.context import db_manager
from modules.training.skillchat import skills_loader

bp = Blueprint('skillchat', __name__, url_prefix='/api/skillchat')

_HISTORY_LIMIT = 20      # 拼 prompt 携带的最近历史条数
_NEW_TITLE = '新会话'    # 会话初始标题；首条用户消息落库时用消息前 30 字替换


# ==================== 技能与示例 ====================

@bp.route('/skills', methods=['GET'])
def list_skills():
    try:
        return jsonify({'success': True, 'items': skills_loader.list_skills()})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/skills/<name>/examples', methods=['GET'])
def get_examples(name):
    if not skills_loader.skill_exists(name):
        return jsonify({'success': False, 'error': f'未知技能: {name}'}), 404
    return jsonify({'success': True, 'examples': skills_loader.read_examples(name)})


_EXAMPLES_PROMPT = '''下面是技能「{name}」的 SKILL.md 文档正文。请站在用户视角，生成 10 条
用户会对该技能助手说的示例命令/问题：具体、可执行、覆盖该技能的主要能力，中文，
每条一句话（不超过 40 字）。只输出 JSON 字符串数组，不要输出任何其他内容。

[SKILL.md 正文]
{body}'''


def _parse_examples(text: str) -> list:
    """LLM 返回 → 示例列表：json.loads 优先，失败用正则兜底提取首个 JSON 数组。"""
    def _norm(items):
        out = []
        for x in items:
            s = str(x).strip().strip('"').strip()
            if s and s not in out:
                out.append(s)
        return out[:10]

    try:
        data = json.loads(text or '')
        if isinstance(data, list):
            return _norm(data)
    except Exception:
        pass
    m = re.search(r'\[.*\]', text or '', re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(0))
            if isinstance(data, list):
                return _norm(data)
        except Exception:
            pass
    return []


@bp.route('/skills/<name>/examples/refresh', methods=['POST'])
def refresh_examples(name):
    """LLM 重新生成示例；生成/解析失败保留旧数据并报错。"""
    if not skills_loader.skill_exists(name):
        return jsonify({'success': False, 'error': f'未知技能: {name}'}), 404
    try:
        from core.llm_config import call_chat
        prompt = _EXAMPLES_PROMPT.format(name=name, body=skills_loader.load_skill_body(name))
        resp = call_chat([{'role': 'user', 'content': prompt}], max_tokens=2000, thinking=False)
        items = _parse_examples(resp.get('content'))
    except Exception as e:
        return jsonify({'success': False, 'error': f'示例生成失败（已保留原数据）: {e}'}), 500
    if not items:
        return jsonify({'success': False, 'error': 'LLM 返回无法解析为示例数组（已保留原数据）'}), 500
    try:
        skills_loader.write_examples(name, items)
    except Exception as e:
        return jsonify({'success': False, 'error': f'示例写回失败: {e}'}), 500
    return jsonify({'success': True, 'examples': items})


# ==================== 会话 ====================

@bp.route('/sessions', methods=['POST'])
def create_session():
    data = request.get_json() or {}
    skill = (data.get('skill') or '').strip()
    if not skills_loader.skill_exists(skill):
        return jsonify({'success': False, 'error': f'未知技能: {skill}'}), 400
    sid = str(uuid.uuid4())
    now = datetime.now().isoformat()
    try:
        with db_manager.connect_governance() as conn:
            conn.execute(
                'INSERT INTO skill_chat_sessions (id, skill, title, created_at, updated_at)'
                ' VALUES (?, ?, ?, ?, ?)',
                (sid, skill, _NEW_TITLE, now, now))
            conn.commit()
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
    return jsonify({'success': True, 'session': {
        'id': sid, 'skill': skill, 'title': _NEW_TITLE,
        'created_at': now, 'updated_at': now}})


@bp.route('/sessions', methods=['GET'])
def list_sessions():
    skill = (request.args.get('skill') or '').strip()
    if not skill:
        return jsonify({'success': False, 'error': '缺少 skill 参数'}), 400
    try:
        with db_manager.connect_governance() as conn:
            rows = conn.execute('''
                SELECT s.id, s.skill, s.title, s.updated_at,
                       (SELECT COUNT(*) FROM skill_chat_messages m WHERE m.session_id = s.id)
                FROM skill_chat_sessions s
                WHERE s.skill = ?
                ORDER BY s.updated_at DESC
            ''', (skill,)).fetchall()
        return jsonify({'success': True, 'items': [
            {'id': r[0], 'skill': r[1], 'title': r[2] or _NEW_TITLE,
             'updated_at': str(r[3]) if r[3] else None, 'message_count': r[4]}
            for r in rows]})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/sessions/<sid>', methods=['GET'])
def get_session(sid):
    try:
        with db_manager.connect_governance() as conn:
            srow = conn.execute(
                'SELECT id, skill, title, created_at, updated_at'
                ' FROM skill_chat_sessions WHERE id = ?', (sid,)).fetchone()
            if not srow:
                return jsonify({'success': False, 'error': '会话不存在'}), 404
            mrows = conn.execute(
                'SELECT id, role, content, usage_json, created_at'
                ' FROM skill_chat_messages WHERE session_id = ? ORDER BY id ASC',
                (sid,)).fetchall()
        messages = []
        for r in mrows:
            try:
                usage = json.loads(r[3]) if r[3] else None
            except Exception:
                usage = None
            messages.append({'id': r[0], 'role': r[1], 'content': r[2] or '',
                             'usage': usage, 'created_at': str(r[4]) if r[4] else None})
        return jsonify({'success': True,
                        'session': {'id': srow[0], 'skill': srow[1], 'title': srow[2] or '',
                                    'created_at': str(srow[3]) if srow[3] else None,
                                    'updated_at': str(srow[4]) if srow[4] else None},
                        'messages': messages})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/sessions/<sid>', methods=['DELETE'])
def delete_session(sid):
    try:
        with db_manager.connect_governance() as conn:
            conn.execute('DELETE FROM skill_chat_messages WHERE session_id = ?', (sid,))
            conn.execute('DELETE FROM skill_chat_sessions WHERE id = ?', (sid,))
            conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ==================== 对话（SSE） ====================

def _build_prompt(skill_body: str, history: list, message: str) -> str:
    """[Skill 文档] + [对话历史] + [当前用户消息] 拼单 prompt（call_llm 单 prompt 通道）。"""
    lines = ['[Skill 文档]', skill_body.strip(), '', '[对话历史]']
    if history:
        for role, content in history:
            lines.append(f"{'用户' if role == 'user' else '助手'}: {content}")
    else:
        lines.append('（无）')
    lines += ['', '[当前用户消息]', message, '',
              '请以该 Skill 的能力专家身份回答；与 Skill 无关的问题礼貌说明并引导回 Skill 主题。']
    return '\n'.join(lines)


@bp.route('/chat/stream', methods=['POST'])
def chat_stream():
    """SSE 帧：{thinking:chunk} / {content:chunk} → {done,result:{content,usage}} / {error}。"""
    data = request.get_json() or {}
    skill = (data.get('skill') or '').strip()
    session_id = (data.get('session_id') or '').strip()
    message = (data.get('message') or '').strip()
    if not skill or not skills_loader.skill_exists(skill):
        return jsonify({'success': False, 'error': f'未知技能: {skill}'}), 400
    if not session_id:
        return jsonify({'success': False, 'error': '缺少 session_id'}), 400
    if not message:
        return jsonify({'success': False, 'error': '消息不能为空'}), 400

    try:
        with db_manager.connect_governance() as conn:
            srow = conn.execute(
                'SELECT id, skill, title FROM skill_chat_sessions WHERE id = ?',
                (session_id,)).fetchone()
            if not srow:
                return jsonify({'success': False, 'error': '会话不存在'}), 404
            if srow[1] != skill:
                return jsonify({'success': False, 'error': '会话与技能不匹配'}), 400
            rows = conn.execute(
                'SELECT role, content FROM skill_chat_messages'
                ' WHERE session_id = ? ORDER BY id DESC LIMIT ?',
                (session_id, _HISTORY_LIMIT)).fetchall()
        history = [(r[0], r[1] or '') for r in reversed(rows)]
        prompt = _build_prompt(skills_loader.load_skill_body(skill), history, message)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

    events = queue.Queue()

    def worker():
        try:
            from core.llm_transport import call_llm
            resp = call_llm(
                prompt, thinking=True,
                stream_cb=lambda phase, chunk: events.put(
                    {'thinking': chunk} if phase == 'reasoning' else {'content': chunk}))
            content = resp.get('content') or ''
            usage = resp.get('usage') or {}
            now = datetime.now().isoformat()
            with db_manager.connect_governance() as conn:
                conn.execute(
                    'INSERT INTO skill_chat_messages (session_id, role, content, usage_json, created_at)'
                    ' VALUES (?, ?, ?, NULL, ?)',
                    (session_id, 'user', message, now))
                conn.execute(
                    'INSERT INTO skill_chat_messages (session_id, role, content, usage_json, created_at)'
                    ' VALUES (?, ?, ?, ?, ?)',
                    (session_id, 'assistant', content,
                     json.dumps(usage, ensure_ascii=False), now))
                # 首条消息时把初始标题换成消息前 30 字
                if srow[2] == _NEW_TITLE:
                    conn.execute('UPDATE skill_chat_sessions SET title = ? WHERE id = ?',
                                 (message[:30], session_id))
                conn.execute('UPDATE skill_chat_sessions SET updated_at = ? WHERE id = ?',
                             (now, session_id))
                conn.commit()
            events.put({'done': True, 'result': {'content': content, 'usage': usage}})
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
