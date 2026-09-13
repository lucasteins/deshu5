# -*- coding: utf-8 -*-
"""全局配置（deshu5 轻量化重构版）

- MySQL 唯一底座：业务库 / 治理库 / 日志库 三库分工
- LLM：多 Provider 运行时配置见 llm_settings.json（core/llm_config.py），
  此处仅保留 env 兜底默认值
- 所有条目均可用环境变量 / .env 覆盖
"""
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    BASE_DIR = Path(__file__).parent.absolute()
    env_path = BASE_DIR / '.env'
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    pass

BASE_DIR = Path(__file__).parent.absolute()

# ==================== MySQL 底座 ====================
MYSQL_HOST = os.environ.get('MYSQL_HOST', 'localhost')
MYSQL_PORT = int(os.environ.get('MYSQL_PORT', '3306'))
MYSQL_USER = os.environ.get('MYSQL_USER', 'root')
MYSQL_PASSWORD = os.environ.get('MYSQL_PASSWORD', '')  # 从 .env 读取，勿硬编码
MYSQL_DB_BUSINESS = os.environ.get('MYSQL_DB_BUSINESS', 'marketing_40')      # 业务库
MYSQL_DB_GOVERNANCE = os.environ.get('MYSQL_DB_GOVERNANCE', 'marketing_governance')  # 治理库
MYSQL_DB_LOG = os.environ.get('MYSQL_DB_LOG', 'marketing_log')               # 日志库
MYSQL_DB_ONTOLOGY = os.environ.get('MYSQL_DB_ONTOLOGY', 'marketing_ontology')  # 本体库
MYSQL_CHARSET = 'utf8mb4'

# ==================== LLM 兜底默认值（运行时配置走 llm_settings.json）====================
KIMI_API_URL = os.environ.get('KIMI_API_URL', 'https://api.kimi.com/coding/v1')
KIMI_API_KEY = os.environ.get('KIMI_API_KEY', '')
KIMI_MODEL = os.environ.get('KIMI_MODEL', 'kimi-for-coding-highspeed')
KIMI_THINKING = os.environ.get('KIMI_THINKING', 'true').lower() in ('true', '1', 'yes')
KIMI_THINKING_BUDGET = int(os.environ.get('KIMI_THINKING_BUDGET', '6144'))

if not KIMI_API_KEY:
    import warnings
    warnings.warn('KIMI_API_KEY 未配置，请在环境变量或 .env 文件中设置。')

# ==================== Schema 元数据来源 ====================
# 表级描述 + 主外键关系来源（精简版 DDL）
DDL_TABLE_REL_FILE = os.environ.get(
    'DDL_TABLE_REL_FILE', str(BASE_DIR / 'data' / '35张营销共享层表_重构版v2.0_主外键精简版.sql'))
# 全量字段描述来源（完整版 DDL SQL）
DDL_SCHEMA_FILE = os.environ.get(
    'DDL_SCHEMA_FILE', str(BASE_DIR / 'data' / '35张营销共享层表_重构版v2.0_20260519.sql'))

# 码值库文件（治理库码值表为空时启动自动导入）
CODE_VALUES_FILE = os.environ.get(
    'CODE_VALUES_FILE', str(BASE_DIR / 'data' / 'code_values_v1.0.xls'))
CODE_VALUE_ITEMS_FILE = os.environ.get(
    'CODE_VALUE_ITEMS_FILE', str(BASE_DIR / 'data' / 'code_value_items_v1.0.xlsx'))

# ==================== 应用 ====================
FLASK_PORT = int(os.environ.get('FLASK_PORT', '5000'))
FLASK_DEBUG = os.environ.get('FLASK_DEBUG', 'false').lower() in ('true', '1', 'yes')
USE_INTENT_GENERATION = os.environ.get('USE_INTENT_GENERATION', 'true').lower() in ('true', '1', 'yes')

# ==================== SQL 生成 ====================
MAX_SQL_RETRIES = 2
RAG_TOP_K = 5
SQL_MAX_ROWS = 50

# Prompt 预算与分段配额（字符数，Track B 收敛定型值）
PROMPT_BUDGET = int(os.environ.get('PROMPT_BUDGET', '10500'))
CODE_VALUE_CONTEXT_MAX = int(os.environ.get('CODE_VALUE_CONTEXT_MAX', '2000'))
QA_CONTEXT_MAX = int(os.environ.get('QA_CONTEXT_MAX', '1000'))
ERROR_CONTEXT_MAX = int(os.environ.get('ERROR_CONTEXT_MAX', '600'))
COLUMN_MAX_PER_TABLE = int(os.environ.get('COLUMN_MAX_PER_TABLE', '30'))
FULL_COLUMN_TOP_N = int(os.environ.get('FULL_COLUMN_TOP_N', '3'))

# ==================== LLM 调用策略 ====================
USE_REASONING_FALLBACK = os.environ.get('USE_REASONING_FALLBACK', 'false').lower() in ('true', '1', 'yes')
LLM_TIMEOUT_FAST = int(os.environ.get('LLM_TIMEOUT_FAST', '120'))
LLM_TIMEOUT_REASONING = int(os.environ.get('LLM_TIMEOUT_REASONING', '60'))
LLM_TIMEOUT_LOCATE = int(os.environ.get('LLM_TIMEOUT_LOCATE', '25'))
LLM_LOCATE_WAIT_MAX = int(os.environ.get('LLM_LOCATE_WAIT_MAX', '15'))
LLM_LOCATE_ENABLED = os.environ.get('LLM_LOCATE_ENABLED', 'false').lower() in ('true', '1', 'yes')
LLM_TIMEOUT_ALIAS = int(os.environ.get('LLM_TIMEOUT_ALIAS', '10'))
LLM_EMPTY_RETRY_MAX_MS = int(os.environ.get('LLM_EMPTY_RETRY_MAX_MS', '60000'))
LLM_STREAM_THINKING = os.environ.get('LLM_STREAM_THINKING', 'true').lower() in ('true', '1', 'yes')
LLM_REASONING_EFFORT = os.environ.get('LLM_REASONING_EFFORT', 'low')
AUX_MODEL = os.environ.get('AUX_MODEL', 'deepseek-chat')
GEN_SQL_AUDIT = os.environ.get('GEN_SQL_AUDIT', 'true').lower() in ('true', '1', 'yes')
GEN_DRAFT_EFFORT = os.environ.get('GEN_DRAFT_EFFORT', 'none')
GEN_AUDIT_EFFORT = os.environ.get('GEN_AUDIT_EFFORT', 'low')
# v2.4 回退通道（意图生成失败时的旧版 LLM 直出通道）：默认停用（2026-09-13），
# 停用后意图通道失败即报错返回，不再回退；实验对照可 env 置 true 重新开启。
GEN_FALLBACK_V24 = os.environ.get('GEN_FALLBACK_V24', 'false').lower() in ('true', '1', 'yes')
# thinking 参数能力判定：空 = 按模型名启发（v4/reasoner/flash）；'1' 强制可下发；'0' 强制不下发。
# deepseek-flash 实测默认长跑思维链且支持 thinking 参数，故启发纳入 'flash'（2026-09-12）。
LLM_THINKING_CAPABLE = os.environ.get('LLM_THINKING_CAPABLE', '')
# 审计段是否开思考：默认 False（2026-09-04 提速：思考型审计实测 4~23s/题且随平台波动，
# no-think 审计秒级返回；需要更强审计时 env 置 true）
GEN_AUDIT_THINKING = os.environ.get('GEN_AUDIT_THINKING', 'false').lower() in ('true', '1', 'yes')

# ==================== 智能出题 ====================
QGEN_BATCH_SIZE = int(os.environ.get('QGEN_BATCH_SIZE', '3'))
QGEN_DIGEST_COL_CAP = int(os.environ.get('QGEN_DIGEST_COL_CAP', '30'))
QGEN_PROVIDER = os.environ.get('QGEN_PROVIDER', 'deepseek')
QGEN_MODEL = os.environ.get('QGEN_MODEL', 'deepseek-v4-flash')

# ==================== SQL 安全（只读执行）====================
ALLOWED_SQL_PREFIXES = ('SELECT', 'WITH')
FORBIDDEN_KEYWORDS = ('DROP', 'DELETE', 'UPDATE', 'INSERT', 'ALTER', 'CREATE', 'TRUNCATE')

# ==================== 本体模型层 ====================
ONTOLOGY_BASE_IRI = os.environ.get('ONTOLOGY_BASE_IRI', 'http://deshu5.local/ontology/marketing#')
ONTOLOGY_ENABLED = os.environ.get('ONTOLOGY_ENABLED', 'true').lower() in ('true', '1', 'yes')
