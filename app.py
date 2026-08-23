# -*- coding: utf-8 -*-
"""智能问数训练系统（deshu5 轻量化重构版）—— 应用入口

架构（四模块 + 公共底座，同一 MySQL 底座）：
    core/                公共底座：MySQL 连接层 / LLM 配置 / Schema 与 RAG 知识底座
    modules/settings/    ① 数据库配置 + LLM 配置
    modules/training/    ② 训练模式 + 智能问答（含 NL2SQL 生成引擎与工作流预设）
    modules/resources/   ③ 数据资源 + 统计看板
    modules/provision/   ④ 素材提资

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

from flask import Flask, jsonify, send_from_directory

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
    app = Flask(__name__, static_folder='static', static_url_path='')
    app.config['JSON_AS_ASCII'] = False

    # ---- 注册四个业务模块蓝图 ----
    from modules.settings.routes import bp as settings_bp, workflows_bp
    from modules.training.routes import bp as training_bp
    from modules.resources.routes import bp as resources_bp
    from modules.provision.routes import bp as provision_bp

    app.register_blueprint(settings_bp)
    app.register_blueprint(workflows_bp)
    app.register_blueprint(training_bp)
    app.register_blueprint(resources_bp)
    app.register_blueprint(provision_bp)

    # ---- 静态页面与健康检查 ----
    @app.route('/')
    def index():
        return send_from_directory('static', 'index.html')

    @app.route('/api/health')
    def health():
        return jsonify({
            'status': 'ok',
            'version': '5.0.0',
            'modules': ['settings', 'training', 'resources', 'provision'],
        })

    return app


# 全局单例 app（Flask 开发服务器与 WSGI 部署共用）
app = create_app()

if __name__ == '__main__':
    print("[startup] 正在初始化（建表/码值/Schema 预热）...")
    _startup_init()
    print(f"[startup] 启动服务: http://127.0.0.1:{config.FLASK_PORT}")
    app.run(host='0.0.0.0', port=config.FLASK_PORT, debug=config.FLASK_DEBUG)
