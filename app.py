# -*- coding: utf-8 -*-
"""智能问数训练系统（deshu5 轻量化重构版）—— 应用入口

架构（五模块 + 公共底座，同一 MySQL 底座）：
    core/                公共底座：MySQL 连接层 / LLM 配置 / Schema 与 RAG 知识底座 / 本体层
    modules/settings/    ① 数据库配置 + LLM 配置
    modules/training/    ② 训练模式 + 智能问答（含 NL2SQL 生成引擎与工作流预设）
    modules/resources/   ③ 数据资源 + 统计看板
    modules/provision/   ④ 素材提资
    modules/ontology/    ⑤ 本体模型管理面（浏览/导出/漂移检测/变更审批）
    modules/report/      ⑥ 深度分析（综合问答/报告生成：意图分解 → 标准化问题取数 → 报告拼装）

启动：python app.py（或 start.bat）
"""
import os
import sys

# Windows 控制台 GBK 兼容：日志中的特殊字符不再引发 UnicodeEncodeError
try:
    sys.stdout.reconfigure(errors='replace')
    sys.stderr.reconfigure(errors='replace')
except Exception:
    pass

from flask import Flask, abort, jsonify, redirect, send_from_directory

import config

# 延迟导入的重对象单例（见 core/context.py）：
# db_manager / schema_loader / rag_retriever / get_schema_preloader()


def _startup_init():
    """启动初始化：建表（幂等）→ 码值导入 → Schema/RAG 预热。失败仅告警不阻断。"""
    try:
        from core.database import init_all_tables, import_code_values_if_empty
        init_all_tables()
    except Exception as e:
        print(f"[WARN] 表结构初始化失败: {e}")

    try:
        from core.database import import_code_values_if_empty
        import_code_values_if_empty()
    except Exception as e:
        print(f"[WARN] 码值库导入失败: {e}")

    from core.context import warmup
    warmup()


def create_app() -> Flask:
    # static_folder=None（F3.2 切换）：旧 UI static/ 已归档 archive/frontend-v1/、不再托管；
    # 前端改由下方根路由直出 frontend/dist
    app = Flask(__name__, static_folder=None)
    app.config['JSON_AS_ASCII'] = False

    # ---- 注册四个业务模块蓝图 ----
    from modules.settings.routes import bp as settings_bp, workflows_bp
    from modules.training.routes import bp as training_bp
    from modules.resources.routes import bp as resources_bp
    from modules.provision.routes import bp as provision_bp
    from modules.ontology.routes import bp as ontology_bp
    from modules.report.routes import bp as report_bp

    app.register_blueprint(settings_bp)
    app.register_blueprint(workflows_bp)
    app.register_blueprint(training_bp)
    app.register_blueprint(resources_bp)
    app.register_blueprint(provision_bp)
    app.register_blueprint(ontology_bp)
    app.register_blueprint(report_bp)

    # ---- 健康检查 ----
    @app.route('/api/health')
    def health():
        return jsonify({
            'status': 'ok',
            'version': '5.0.0',
            'modules': ['settings', 'training', 'resources', 'provision', 'ontology', 'report'],
        })

    # ---- 前端（Vue 3）托管：/（F3.2 切换，2026-09-11 皮卡丘拍板）----
    # 依据 03-迁移方案 §3.3：build base=/；dist 静态文件直出；非文件路径兜底回 index.html（SPA fallback）。
    # 旧 UI（static/）已归档 archive/frontend-v1/、不再托管；/app/** 保留 302 过渡重定向至根。
    frontend_dist = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'frontend', 'dist')

    def _dist_file(path: str):
        """dist 内文件直出；不存在或越界返回 None（realpath 归一化阻断 ../ 越权读取）"""
        target = os.path.realpath(os.path.join(frontend_dist, path))
        if target.startswith(frontend_dist + os.sep) and os.path.isfile(target):
            return send_from_directory(frontend_dist, os.path.relpath(target, frontend_dist))
        return None

    @app.route('/')
    def index():
        if not os.path.isdir(frontend_dist):
            return ('frontend/dist 不存在：请先在 frontend/ 目录执行 npm run build', 503)
        return send_from_directory(frontend_dist, 'index.html')

    @app.route('/<path:path>')
    def frontend_root(path: str):
        # /api/** 未被蓝图匹配时保持 404——SPA 兜底绝不吞 API 路径
        if path == 'api' or path.startswith('api/'):
            abort(404)
        if not os.path.isdir(frontend_dist):
            return ('frontend/dist 不存在：请先在 frontend/ 目录执行 npm run build', 503)
        hit = _dist_file(path)
        if hit is not None:
            return hit
        # 文件样路径（末段含扩展名）不回退 SPA，避免旧路径（如 /js/app.js）出「幽灵 200」
        if '.' in os.path.basename(path):
            abort(404)
        return send_from_directory(frontend_dist, 'index.html')

    @app.route('/app/', strict_slashes=False)
    @app.route('/app/<path:path>')
    def frontend_app_redirect(path: str = ''):
        # F3.2 过渡：旧 /app 入口 302 重定向到根（保目验书签；稳定后可撤）
        return redirect('/' + path, code=302)

    return app


# 全局单例 app（Flask 开发服务器与 WSGI 部署共用）
app = create_app()

if __name__ == '__main__':
    print("[startup] 正在初始化（建表/码值/Schema 预热）...")
    _startup_init()
    print(f"[startup] 启动服务: http://127.0.0.1:{config.FLASK_PORT}")
    app.run(host='0.0.0.0', port=config.FLASK_PORT, debug=config.FLASK_DEBUG)
