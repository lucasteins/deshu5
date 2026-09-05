# -*- coding: utf-8 -*-
"""设置模块路由：LLM 多 Provider 配置 + MySQL 数据库配置 + 生成工作流预设"""
import time

import requests
from flask import Blueprint, jsonify, request

import config
from core import llm_config

bp = Blueprint('settings', __name__, url_prefix='/api/settings')
workflows_bp = Blueprint('settings_workflows', __name__)  # /api/workflows（无前缀，供设置页切换生成工作流）


# ==================== LLM 配置 ====================

@bp.route('/llm', methods=['GET'])
def get_llm_settings():
    """当前 LLM 配置（api_key 脱敏）+ 可用 provider 列表"""
    try:
        data = llm_config.all_settings()
        data['presets'] = {k: {'api_url': v['api_url'], 'model': v['model']}
                           for k, v in llm_config.PROVIDER_PRESETS.items()}
        data['success'] = True
        return jsonify(data)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/llm', methods=['POST'])
def save_llm_settings():
    """保存 LLM 配置。body: {provider, api_url?, model?, api_key?, temperature?, set_active?}
    api_key 留空或传脱敏值时保留原值。"""
    try:
        payload = request.get_json(force=True) or {}
        data = llm_config.save(payload)
        data['success'] = True
        return jsonify(data)
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/llm/test', methods=['POST'])
def test_llm_settings():
    """连通性测试：用表单给定配置（不落库）或当前生效配置发一条最小请求"""
    try:
        payload = request.get_json(silent=True) or {}
        cfg = llm_config.preview_config(payload) if payload.get('provider') else llm_config.current()
        url, headers, req_payload = llm_config.build_request(
            [{'role': 'user', 'content': '1+1=?'}], max_tokens=2000, thinking=True, cfg=cfg)
        t0 = time.perf_counter()
        resp = requests.post(url, headers=headers, json=req_payload, timeout=60)
        elapsed = int((time.perf_counter() - t0) * 1000)
        resp.raise_for_status()
        data = resp.json()
        msg = data['choices'][0]['message']
        return jsonify({
            'success': True,
            'ok': True,
            'elapsed_ms': elapsed,
            'model': data.get('model'),
            'reasoning': bool(msg.get('reasoning_content')),
            'content': (msg.get('content') or '')[:100],
        })
    except Exception as e:
        return jsonify({'success': True, 'ok': False, 'error': str(e)})


# ==================== 数据库配置（MySQL 底座）====================

def _mask(s: str, keep: int = 2) -> str:
    if not s:
        return ''
    return s[:keep] + '****' + s[-keep:] if len(s) > keep * 2 else '****'


def _ping_profile(profile: dict) -> dict:
    """探测某档位四库连通性，返回 {key: True|错误信息}。"""
    import pymysql

    result = {}
    for key in ('business', 'governance', 'log', 'ontology'):
        try:
            conn = pymysql.connect(host=config.MYSQL_HOST, port=config.MYSQL_PORT,
                                   user=config.MYSQL_USER, password=config.MYSQL_PASSWORD,
                                   database=profile[key], charset=config.MYSQL_CHARSET,
                                   connect_timeout=5)
            conn.close()
            result[key] = True
        except Exception as e:
            result[key] = str(e)
    return result


@bp.route('/db', methods=['GET'])
def get_db_settings():
    """当前数据库档位 + 全部档位（生产/暂存）四库连通状态"""
    from core import db_profile

    profiles = []
    for p in db_profile.list_profiles():
        ping = _ping_profile(p)
        profiles.append({
            'name': p['name'], 'label': p['label'],
            'business': p['business'], 'governance': p['governance'], 'log': p['log'],
            'ontology': p['ontology'],
            'connected': all(ping[k] is True for k in ('business', 'governance', 'log', 'ontology')),
            'ping': {k: (True if v is True else str(v)) for k, v in ping.items()},
        })
    cur = db_profile.current()
    return jsonify({
        'success': True,
        'host': config.MYSQL_HOST,
        'port': config.MYSQL_PORT,
        'user': config.MYSQL_USER,
        'password_masked': _mask(config.MYSQL_PASSWORD),
        'current_profile': cur['name'],
        'current_label': cur['label'],
        'profiles': profiles,
        'databases': [
            {'key': 'business', 'name': cur['business'], 'desc': '业务库（SQL 只读执行目标）'},
            {'key': 'governance', 'name': cur['governance'], 'desc': '治理库（知识资源/提资溯源）'},
            {'key': 'log', 'name': cur['log'], 'desc': '日志库（运行日志）'},
            {'key': 'ontology', 'name': cur['ontology'], 'desc': '本体库（本体模型层存储）'},
        ],
    })


@bp.route('/db/profile', methods=['POST'])
def switch_db_profile():
    """切换数据库档位（生产/暂存）。body: {profile: 'production'|'staging'}
    切换后失效全部缓存并重载（SchemaPreloader/RAG/SchemaLoader/生成器），免重启。"""
    from core import context
    from core import db_profile

    payload = request.get_json(silent=True) or {}
    name = (payload.get('profile') or '').strip()
    try:
        cur = context.switch_database(name)
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': f'切换失败: {e}'}), 500
    # 返回切换后的完整状态
    ping = _ping_profile(cur)
    return jsonify({
        'success': True,
        'current_profile': cur['name'],
        'current_label': cur['label'],
        'databases': [
            {'key': 'business', 'name': cur['business'], 'connected': ping['business'] is True,
             'error': '' if ping['business'] is True else str(ping['business'])},
            {'key': 'governance', 'name': cur['governance'], 'connected': ping['governance'] is True,
             'error': '' if ping['governance'] is True else str(ping['governance'])},
            {'key': 'log', 'name': cur['log'], 'connected': ping['log'] is True,
             'error': '' if ping['log'] is True else str(ping['log'])},
            {'key': 'ontology', 'name': cur['ontology'], 'connected': ping['ontology'] is True,
             'error': '' if ping['ontology'] is True else str(ping['ontology'])},
        ],
    })


@bp.route('/db/test', methods=['POST'])
def test_db_settings():
    """按表单给定配置测试连通性（不落库）。
    body: {host?, port?, user?, password?（空=沿用当前配置）}"""
    try:
        import pymysql
        payload = request.get_json(silent=True) or {}
        host = (payload.get('host') or config.MYSQL_HOST).strip()
        port = int(payload.get('port') or config.MYSQL_PORT)
        user = (payload.get('user') or config.MYSQL_USER).strip()
        password = payload.get('password') or config.MYSQL_PASSWORD
        t0 = time.perf_counter()
        conn = pymysql.connect(host=host, port=port, user=user,
                               password=password, charset='utf8mb4', connect_timeout=5)
        try:
            with conn.cursor() as cur:
                cur.execute('SELECT VERSION()')
                version = cur.fetchone()[0]
        finally:
            conn.close()
        elapsed = int((time.perf_counter() - t0) * 1000)
        return jsonify({'success': True, 'ok': True, 'elapsed_ms': elapsed,
                        'server_version': str(version)})
    except Exception as e:
        return jsonify({'success': True, 'ok': False, 'error': str(e)})


# ==================== 生成工作流预设 ====================

@workflows_bp.route('/api/workflows', methods=['GET'])
def get_workflows():
    """列出全部工作流预设及当前生效预设"""
    try:
        from modules.training.workflow import engine as wf_engine
        return jsonify({
            'success': True,
            'current': wf_engine.get_current_name(),
            'presets': wf_engine.list_presets()
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@workflows_bp.route('/api/workflows', methods=['POST'])
def switch_workflow():
    """切换当前工作流预设（热生效：下次生成重建 SQLGenerator，无需重启）"""
    from modules.training.routes import invalidate_generator
    try:
        from modules.training.workflow import engine as wf_engine
        payload = request.get_json(silent=True) or {}
        name = (payload.get('name') or '').strip()
        if not name:
            return jsonify({'success': False, 'error': '缺少 name 参数'}), 400
        wf = wf_engine.set_current(name)
        invalidate_generator()  # 失效缓存的生成器，下次请求按新预设重建
        return jsonify({'success': True, 'current': name, 'config': wf})
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 404
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
