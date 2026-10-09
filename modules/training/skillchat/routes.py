# -*- coding: utf-8 -*-
"""技能对话（Skill Chat）路由：/api/skillchat/*

- GET    /skills                           技能清单（扫描 skills/*/SKILL.md）
- GET    /skills/<name>/examples           该技能示例命令（静态 JSON，可手工维护）
- POST   /skills/<name>/examples/refresh   读 SKILL.md 调 LLM 重新生成示例并写回静态文件
- POST   /sessions                         新建会话 {skill}
- GET    /sessions?skill=<name>            会话列表（按 updated_at 倒序，含 message_count）
- GET    /sessions/<sid>                   会话详情 + 全量消息
- DELETE /sessions/<sid>                   删除会话及其消息
- POST   /chat/stream                      SSE 流式对话（agent 循环：回复中的 ```bash 命令自动执行、结果回填后继续，至多 _MAX_AGENT_ROUNDS 轮）
- POST   /execute/stream                   SSE 流式执行白名单脚本命令（python tools/|skills/...，手动执行入口）
"""
import json
import os
import queue
import re
import shlex
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from flask import Blueprint, Response, jsonify, request, stream_with_context

from core.context import db_manager
from modules.training.skillchat import skills_loader

bp = Blueprint('skillchat', __name__, url_prefix='/api/skillchat')

_HISTORY_LIMIT = 10       # 拼 prompt 携带的最近 user/assistant 历史条数
_HISTORY_BUDGET = 8000    # 历史消息（含 exec 摘要行）合计字符预算，从最新往回累加
_HISTORY_FETCH = 60       # DB 预取行数（exec 摘要不占条数配额，需多取覆盖其间隔）
_EXEC_SUMMARY_LIMIT = 300  # exec 消息注入历史时的摘要行字符上限
_NEW_TITLE = '新会话'    # 会话初始标题；首条用户消息落库时用消息前 30 字替换

PROJECT_ROOT = Path(__file__).resolve().parents[3]
EXEC_TIMEOUT = int(os.environ.get('EXEC_TIMEOUT', '1800'))
_EXEC_TAIL_CHARS = 2000   # 落库时保留的 stdout 尾部字符数
_MAX_AGENT_ROUNDS = int(os.environ.get('SKILLCHAT_AGENT_ROUNDS', '8'))  # 对话自动执行的最大 LLM 轮数
_REPLY_ECHO_LIMIT = 8000  # agent 循环回填上一轮回复的字符上限
_EXEC_RESULT_LIMIT = 3000  # agent 循环单条命令结果回填字符上限
# 对话轮 LLM 调用参数：综述类任务思考+正文量大，默认 8000 会被 reasoning 吃光导致空正文
_LLM_MAX_TOKENS = int(os.environ.get('SKILLCHAT_LLM_MAX_TOKENS', '16000'))
_LLM_TIMEOUT = int(os.environ.get('SKILLCHAT_LLM_TIMEOUT', '600'))  # 秒；空闲由流式看门狗兜底


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

_EXEC_PROTOCOL = '''[执行协议]
当用户请求需要运行脚本才能完成时，你必须在回复中给出具体可执行命令，格式为 ```bash 代码块，每行一条命令。
只允许项目脚本命令：`python tools/xxx.py ...` 或 `python skills/<skill名>/scripts/xxx.py ...`（从项目根目录出发的相对路径），参数必须严格照 SKILL.md 文档中的用法书写，含空格的参数用双引号包裹（例如 `python tools/crawl_tjj_monthly.py --from 202607 --to 202608`）。代码块中只写命令本身，不要写注释、cd、export 等其它内容。
你给出的命令会被系统自动执行，执行结果会回传给你；你可根据结果继续给出新命令，直到任务完成。无需执行命令时不要输出 bash 代码块（普通说明性代码请使用其他语言标注或纯文本）。'''


def _build_continuation(reply: str, results: list) -> str:
    """agent 循环下一轮 prompt 的增量：上轮回复（截断）+ 各命令执行结果（截断）+ 继续指令。"""
    reply = (reply or '').strip()
    if len(reply) > _REPLY_ECHO_LIMIT:
        reply = reply[:_REPLY_ECHO_LIMIT] + '\n…（回复过长已截断）'
    parts = ['', '[你上一轮的回复]', reply, '', '[命令自动执行结果]']
    for r in results:
        r = (r or '').strip()
        if len(r) > _EXEC_RESULT_LIMIT:
            r = r[:_EXEC_RESULT_LIMIT] + '\n…（输出过长已截断）'
        parts.append(r)
    parts += ['', '请根据以上执行结果继续：任务未完成且仍需运行脚本时，按执行协议给出新的 '
                  '```bash 命令；任务已完成或无需再执行时，直接给出最终答复，不要再输出 bash 代码块。']
    return '\n'.join(parts)


def _exec_summary(content: str) -> str:
    """exec 消息正文（"$ cmd\\n[exit N]\\n<输出尾部>"）压缩成一行 ≤300 字符的摘要。"""
    lines = (content or '').split('\n')
    command = lines[0][2:].strip() if lines and lines[0].startswith('$ ') else ''
    exit_code = '?'
    out_start = 1
    if len(lines) > 1 and lines[1].startswith('[exit ') and lines[1].endswith(']'):
        exit_code = lines[1][6:-1]
        out_start = 2
    brief = ' '.join('\n'.join(lines[out_start:]).split())
    if len(brief) > 120:
        brief = brief[:120] + '…'
    summary = f'（此前执行过命令：{command}，退出码 {exit_code}，输出要点：{brief}）'
    return summary[:_EXEC_SUMMARY_LIMIT]


def _trim_history(rows) -> list:
    """rows 为 id 倒序的 (role, content)。最近优先累加：user/assistant 最多
    _HISTORY_LIMIT 条；role='exec' 转摘要行注入；合计 ≤ _HISTORY_BUDGET 字符，
    超预算即停。返回保序（最旧在前）的 [(role, text)]。"""
    picked = []
    ua_count = 0
    budget = _HISTORY_BUDGET
    for role, content in rows:
        if role in ('user', 'assistant'):
            if ua_count >= _HISTORY_LIMIT:
                break
            text = content or ''
        elif role == 'exec':
            text = _exec_summary(content)
        else:
            continue
        if len(text) > budget:
            break
        budget -= len(text)
        if role in ('user', 'assistant'):
            ua_count += 1
        picked.append((role, text))
    picked.reverse()
    return picked


def _build_prompt(skill_body: str, history: list, message: str) -> str:
    """[Skill 文档] + [对话历史] + [当前用户消息] 拼单 prompt（call_llm 单 prompt 通道）。"""
    lines = ['[Skill 文档]', skill_body.strip(), '', '[对话历史]']
    if history:
        for role, content in history:
            if role == 'user':
                lines.append(f'用户: {content}')
            elif role == 'assistant':
                lines.append(f'助手: {content}')
            else:  # exec 摘要行原样注入
                lines.append(content)
    else:
        lines.append('（无）')
    lines += ['', '[当前用户消息]', message, '',
              '请以该 Skill 的能力专家身份回答；与 Skill 无关的问题礼貌说明并引导回 Skill 主题。',
              '', _EXEC_PROTOCOL]
    return '\n'.join(lines)


@bp.route('/chat/stream', methods=['POST'])
def chat_stream():
    """SSE 帧：{assistant_start} 每轮开始 / {thinking:chunk} / {content:chunk} 增量 /
    {exec_start} / {exec_out:line} / {exec_done} 自动执行 / {done,result} / {error}。"""
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
                (session_id, _HISTORY_FETCH)).fetchall()
        history = _trim_history(rows)
        prompt = _build_prompt(skills_loader.load_skill_body(skill), history, message)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

    events = queue.Queue()

    def worker():
        try:
            from core.llm_transport import call_llm

            def persist_message(role, content, usage=None):
                now = datetime.now().isoformat()
                with db_manager.connect_governance() as conn:
                    conn.execute(
                        'INSERT INTO skill_chat_messages (session_id, role, content, usage_json, created_at)'
                        ' VALUES (?, ?, ?, ?, ?)',
                        (session_id, role, content,
                         json.dumps(usage, ensure_ascii=False) if usage else None, now))
                    conn.execute('UPDATE skill_chat_sessions SET updated_at = ? WHERE id = ?',
                                 (now, session_id))
                    conn.commit()

            now = datetime.now().isoformat()
            with db_manager.connect_governance() as conn:
                conn.execute(
                    'INSERT INTO skill_chat_messages (session_id, role, content, usage_json, created_at)'
                    ' VALUES (?, ?, ?, NULL, ?)',
                    (session_id, 'user', message, now))
                # 首条消息时把初始标题换成消息前 30 字
                if srow[2] == _NEW_TITLE:
                    conn.execute('UPDATE skill_chat_sessions SET title = ? WHERE id = ?',
                                 (message[:30], session_id))
                conn.execute('UPDATE skill_chat_sessions SET updated_at = ? WHERE id = ?',
                             (now, session_id))
                conn.commit()

            # agent 循环：LLM 回复 → 提取 ```bash 命令 → 自动执行 → 结果回填 → LLM 继续
            continuation = ''
            content = ''
            usage = {}
            for round_no in range(_MAX_AGENT_ROUNDS):
                events.put({'assistant_start': True})
                resp = call_llm(
                    prompt + continuation, thinking=True, stream_cb=lambda phase, chunk: events.put(
                        {'thinking': chunk} if phase == 'reasoning' else {'content': chunk}),
                    max_tokens=_LLM_MAX_TOKENS, timeout=_LLM_TIMEOUT)
                content = resp.get('content') or ''
                usage = resp.get('usage') or {}
                persist_message('assistant', content, usage)
                commands = extract_exec_commands(content)
                if not commands or round_no >= _MAX_AGENT_ROUNDS - 1:
                    break
                results = []
                for cmd in commands:
                    lock = _acquire_exec_lock(session_id)
                    if lock is None:
                        results.append(f'$ {cmd}\n[exit -1]\n该会话有命令正在执行，本条跳过自动执行')
                        continue
                    try:
                        events.put({'exec_start': cmd, 'auto': True})
                        exit_code, exec_content, message_id, _error = _run_exec_command(
                            session_id, cmd, events)
                        events.put({'exec_done': True, 'exit_code': exit_code,
                                    'message_id': message_id, 'auto': True})
                    finally:
                        lock.release()
                    results.append(exec_content)
                # 只保留最近一轮的回复+结果回填（累加会让 prompt 随轮数无界膨胀、LLM 越调越慢）
                continuation = _build_continuation(content, results)
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


# ==================== 命令执行（SSE） ====================

_SCRIPT_RE = re.compile(r'^(tools|skills)/[A-Za-z0-9_\-./]+\.py$')
_BASH_BLOCK_RE = re.compile(r'```(?:bash|sh|shell)[^\S\n]*\n([\s\S]*?)```')

_EXEC_LOCKS = {}              # session_id -> threading.Lock，执行期间持有
_EXEC_LOCKS_GUARD = threading.Lock()


def parse_exec_command(command: str):
    """白名单校验：通过返回 (script_relpath, args)，否则 None。

    shlex 解析（支持双/单引号包裹的含空格参数）；脚本限定 tools/ 或 skills/ 下的
    .py 相对路径，一律拒绝 .. 路径穿越，且解析后的绝对路径必须仍在项目根内。
    执行走 argv 列表不经 shell，参数内容无注入面，不限制参数字符集。
    """
    cmd = (command or '').strip()
    if not cmd:
        return None
    # Windows 反斜杠先归一化为正斜杠（posix shlex 会把 \ 当转义符吞掉）；
    # 项目脚本参数均为相对路径/简单值，Windows API 本身也接受 / 分隔
    cmd = cmd.replace('\\', '/')
    try:
        parts = shlex.split(cmd, posix=True)
    except ValueError:
        parts = cmd.split()
    if len(parts) < 2 or parts[0] != 'python':
        return None
    script = parts[1]
    if not _SCRIPT_RE.match(script) or '..' in script:
        return None
    try:
        (PROJECT_ROOT / script).resolve().relative_to(PROJECT_ROOT)
    except ValueError:
        return None
    return script, parts[2:]


def extract_exec_commands(content: str) -> list:
    """从回复的 ```bash/sh/shell 代码块中逐行提取过白名单的命令（跳过空行与 # 注释行）。"""
    cmds = []
    for block in _BASH_BLOCK_RE.findall(content or ''):
        for line in block.split('\n'):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if parse_exec_command(line):
                cmds.append(line)
    return cmds


def _acquire_exec_lock(session_id: str):
    """会话级互斥：拿到返回 lock（执行结束负责 release），已有命令在执行返回 None。"""
    with _EXEC_LOCKS_GUARD:
        lock = _EXEC_LOCKS.setdefault(session_id, threading.Lock())
    return lock if lock.acquire(blocking=False) else None


def _kill_process_tree(proc):
    """Windows 用 taskkill /T 杀整棵进程树，其他平台 kill 自身。"""
    try:
        if os.name == 'nt':
            subprocess.run(['taskkill', '/F', '/T', '/PID', str(proc.pid)],
                           capture_output=True, timeout=15)
        else:
            proc.kill()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _run_exec_command(session_id: str, command: str, events: 'queue.Queue'):
    """执行白名单命令：stdout 逐行推 {'exec_out': line}，结果落库 role='exec' 消息。

    返回 (exit_code, exec_content, message_id, error)；error 非空表示超时等异常终态
    （超时也会落库，exit_code 为终止后的进程退出码）。调用方负责会话执行锁的获取与释放。
    """
    parsed = parse_exec_command(command)
    if not parsed:
        return -1, f'$ {command}\n[exit -1]\n命令不在允许范围', None, '命令不在允许范围'
    script, args = parsed
    proc = None
    try:
        env = os.environ.copy()
        env['PYTHONIOENCODING'] = 'utf-8'
        proc = subprocess.Popen(
            [sys.executable, script, *args],
            cwd=str(PROJECT_ROOT), stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True,
            encoding='utf-8', errors='replace', env=env)
        lines_q = queue.Queue()

        def reader():
            try:
                for line in proc.stdout:
                    lines_q.put(line)
            finally:
                lines_q.put(None)  # EOF 哨兵

        threading.Thread(target=reader, daemon=True).start()

        tail_buf = []
        tail_len = 0
        deadline = time.time() + EXEC_TIMEOUT
        timeout_error = None
        while True:
            try:
                line = lines_q.get(timeout=0.1)
            except queue.Empty:
                line = ''
            if line is None:
                break
            if line:
                events.put({'exec_out': line})
                tail_buf.append(line)
                tail_len += len(line)
                while tail_len > _EXEC_TAIL_CHARS * 2 and tail_buf:
                    tail_len -= len(tail_buf.pop(0))
            if time.time() > deadline:
                _kill_process_tree(proc)
                timeout_error = f'命令执行超时（{EXEC_TIMEOUT} 秒），已终止'
                break
        exit_code = proc.wait()
        tail = ''.join(tail_buf)[-_EXEC_TAIL_CHARS:]
        if timeout_error:
            tail = f'{tail}\n{timeout_error}'.strip()
        content = f"$ {command}\n[exit {exit_code}]\n{tail}"
        now = datetime.now().isoformat()
        with db_manager.connect_governance() as conn:
            cur = conn.execute(
                'INSERT INTO skill_chat_messages (session_id, role, content, usage_json, created_at)'
                ' VALUES (?, ?, ?, NULL, ?)',
                (session_id, 'exec', content, now))
            message_id = cur.lastrowid
            conn.execute('UPDATE skill_chat_sessions SET updated_at = ? WHERE id = ?',
                         (now, session_id))
            conn.commit()
        return exit_code, content, message_id, timeout_error
    finally:
        if proc is not None:
            try:
                if proc.stdout:
                    proc.stdout.close()
            except Exception:
                pass
            try:
                if proc.poll() is None:
                    proc.kill()
            except Exception:
                pass


@bp.route('/execute/stream', methods=['POST'])
def execute_stream():
    """SSE 帧：{exec_out:line} → {exec_done,exit_code,message_id} / {error}。"""
    data = request.get_json() or {}
    session_id = (data.get('session_id') or '').strip()
    command = (data.get('command') or '').strip()
    if not session_id:
        return jsonify({'success': False, 'error': '缺少 session_id'}), 400
    try:
        with db_manager.connect_governance() as conn:
            srow = conn.execute(
                'SELECT id FROM skill_chat_sessions WHERE id = ?', (session_id,)).fetchone()
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
    if not srow:
        return jsonify({'success': False, 'error': '会话不存在'}), 404
    parsed = parse_exec_command(command)
    if not parsed:
        return jsonify({'success': False, 'error': '命令不在允许范围'}), 400
    lock = _acquire_exec_lock(session_id)
    if lock is None:
        return jsonify({'success': False, 'error': '该会话有命令正在执行'}), 409

    events = queue.Queue()

    def worker():
        try:
            exit_code, _content, message_id, error = _run_exec_command(
                session_id, command, events)
            if error:
                events.put({'error': error})
            else:
                events.put({'exec_done': True, 'exit_code': exit_code, 'message_id': message_id})
        except Exception as e:
            import traceback
            traceback.print_exc()
            events.put({'error': str(e)})
        finally:
            lock.release()

    threading.Thread(target=worker, daemon=True).start()

    def stream():
        while True:
            evt = events.get()
            yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
            if evt.get('exec_done') or evt.get('error'):
                break

    return Response(stream_with_context(stream()), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})
