# -*- coding: utf-8 -*-
"""数据库连接档位：生产库 / 仿真库 运行时切换（前端设置页驱动，免重启）

命名约定（2026-09-14 重命名）：
- 生产档位 sc01 ：sc01 / sc01_governance / sc01_log / sc01_ontology（config/env 默认）
  真实数据基线 = fz01 剔除仿真后的 71,992 行（120 表）
- 仿真档位 fz01：fz01 / fz01_governance / fz01_governance（日志并治理库）/ fz01_ontology
  含全部仿真数据（2,425,404 行），供回滚与对照
- 档位 key（production/staging）保持不变，避免破坏 db_profile.json 与前端切换接口
- 持久化：根目录 db_profile.json（gitignore）；环境变量 DB_PROFILE 优先
- 连接层（core/database.py）在每次建立连接时读取 current()，切换即时生效
"""
import json
import os
from pathlib import Path

import config

PROFILES = {
    'production': {
        'label': '生产库 sc01',
        'business': config.MYSQL_DB_BUSINESS,
        'governance': config.MYSQL_DB_GOVERNANCE,
        'log': config.MYSQL_DB_LOG,
        'ontology': config.MYSQL_DB_ONTOLOGY,
    },
    'staging': {
        'label': '仿真库 fz01',
        'business': os.environ.get('STAGING_DB_BUSINESS', 'fz01'),
        'governance': os.environ.get('STAGING_DB_GOVERNANCE', 'fz01_governance'),
        'log': os.environ.get('STAGING_DB_LOG', 'fz01_governance'),
        'ontology': os.environ.get('STAGING_DB_ONTOLOGY', 'fz01_ontology'),
    },
}

_STATE_PATH = Path(__file__).resolve().parent.parent / 'db_profile.json'


def current_name() -> str:
    """当前生效档位名：环境变量 DB_PROFILE > db_profile.json > production。"""
    env = os.environ.get('DB_PROFILE', '')
    if env in PROFILES:
        return env
    try:
        with open(_STATE_PATH, encoding='utf-8') as f:
            name = (json.load(f).get('current') or '').strip()
            if name in PROFILES:
                return name
    except Exception:
        pass
    return 'production'


def current() -> dict:
    """当前档位：{name, label, business, governance, log, ontology}"""
    name = current_name()
    return dict(PROFILES[name], name=name)


def switch(name: str) -> dict:
    """切换档位并持久化。未知档位抛 ValueError。返回切换后的档位。"""
    name = (name or '').strip()
    if name not in PROFILES:
        raise ValueError(f'未知数据库档位: {name}（可选: {", ".join(PROFILES)}）')
    _STATE_PATH.write_text(
        json.dumps({'current': name}, ensure_ascii=False, indent=2), encoding='utf-8')
    return current()


def list_profiles() -> list:
    """全部档位摘要（供前端展示）。"""
    return [{'name': n, 'label': p['label'],
             'business': p['business'], 'governance': p['governance'], 'log': p['log'],
             'ontology': p['ontology']}
            for n, p in PROFILES.items()]
