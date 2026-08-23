# -*- coding: utf-8 -*-
"""数据库连接档位：生产库 / 暂存库 运行时切换（前端设置页驱动，免重启）

- 生产库：marketing_40 / marketing_governance / marketing_log（config/env 默认）
- 暂存库：database01 / database01_governance / database01_governance（日志并治理库）
- 持久化：根目录 db_profile.json（gitignore）；环境变量 DB_PROFILE 优先
- 连接层（core/database.py）在每次建立连接时读取 current()，切换即时生效
"""
import json
import os
from pathlib import Path

import config

PROFILES = {
    'production': {
        'label': '生产库',
        'business': config.MYSQL_DB_BUSINESS,
        'governance': config.MYSQL_DB_GOVERNANCE,
        'log': config.MYSQL_DB_LOG,
    },
    'staging': {
        'label': '暂存库',
        'business': os.environ.get('STAGING_DB_BUSINESS', 'database01'),
        'governance': os.environ.get('STAGING_DB_GOVERNANCE', 'database01_governance'),
        'log': os.environ.get('STAGING_DB_LOG', 'database01_governance'),
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
    """当前档位：{name, label, business, governance, log}"""
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
             'business': p['business'], 'governance': p['governance'], 'log': p['log']}
            for n, p in PROFILES.items()]
