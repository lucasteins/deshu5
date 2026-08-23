# 智能问数训练系统 deshu5（轻量化重构版）

基于 deshu4（db/smart-query-trainer）重构。四业务模块相对独立，共享同一 MySQL 底座，
**API 路径与前端零改动**，存量数据（marketing_40 / marketing_governance / marketing_log）直接可用。

## 架构

```
deshu5/
├── app.py                  应用入口：Flask 工厂 + 四蓝图注册 + 启动初始化
├── config.py               全局配置（MySQL/LLM/Prompt 预算，均可用 .env 覆盖）
├── core/                   公共底座（四模块共享，单向依赖：modules → core）
│   ├── database.py         MySQL 连接层（? 占位符兼容封装）+ 幂等建表
│   ├── context.py          进程级单例：db_manager / schema_loader / rag_retriever / 预热
│   ├── llm_config.py       LLM 多 Provider 运行时配置（llm_settings.json，免重启切换）
│   ├── schema_loader.py    业务库 Schema 加载（表/列/主外键/行数）
│   ├── schema_preloader.py Schema 预加载单例（information_schema + 治理库关系文档）
│   ├── schema_kb.py        Schema 知识库（表定位/列检索）
│   ├── rag_retriever.py    RAG 检索（问答对/错题/码值相似度召回）
│   └── knowledge_retriever.py 知识读取口（码值/模板/规则，空表回退代码常量）
├── modules/
│   ├── settings/           ① 数据库配置 + LLM 配置
│   │   └── routes.py       /api/settings/llm*、/api/settings/db*、/api/workflows*
│   ├── training/           ② 训练模式 + 智能问答
│   │   ├── routes.py       出题/评价/生成(SSE)/判断/问答对库/错题集
│   │   ├── engine/         NL2SQL 生成引擎（sql_generator 等 8 文件）
│   │   └── workflow/       声明式生成工作流（7 预设，热切换）
│   ├── resources/          ③ 数据资源 + 统计看板
│   │   ├── routes.py       /api/resources/*（CRUD+导入）、码值库、Schema 浏览、/api/stats
│   │   ├── base.py         ResourceProvider 抽象基类
│   │   ├── registry.py     资源注册表（惰性构造）
│   │   └── providers/      9 类资源 provider（问答对/错题/码值/模板/规则…）
│   └── provision/          ④ 素材提资
│       ├── routes.py       上传→预览→执行(SSE)→复核 四步工作流
│       └── provisioner.py  xlsx 模板解析/校验/溯源转换/LLM 标注
├── static/                 前端（复用原版；设置弹窗新增 MySQL 配置区块）
├── data/                   运行时数据依赖（DDL 元数据字典/码值 Excel）
└── uploads/                素材提资上传目录
```

**依赖方向**（无循环）：
- `modules/* → core`（连接层、知识底座）
- `training → resources.providers`（生成时读取知识资源）
- `provision / settings / resources` 互不依赖

## MySQL 底座（三库分工，与 deshu4 完全一致）

| 库 | 用途 |
|----|------|
| marketing_40 | 业务库：35 张营销共享层表，SQL 只读执行目标 |
| marketing_governance | 治理库：问答对/错题/码值/Schema 文档/提资溯源 |
| marketing_log | 日志库：generation_logs 运行日志 |

## 相比 deshu4 的变化

**删除（不再随系统分发）**：
- `exp/track-a/b/c` 三轨实验代码（与主系统重复）
- 40+ 个 `_*.py` 一次性数据治理/迁移脚本
- `benchmark/` 实验评测、`docs/`、`reports/`、`encrypted/`、`.vendor/`
- SQLite 双模式分支与双方言 DDL（统一 MySQL-only）
- `config - 副本.py`、`*.rar` 等冗余文件

**重构**：
- 2339 行单体 app.py → 应用工厂 + 4 个模块蓝图（50 路由）
- models/database.py 双方言兼容层 → 纯 MySQL 连接层（-60% 代码）
- 新增 `/api/settings/db`（三库连通状态）与 `/api/settings/db/test`（连通性测试）
- 设置弹窗新增「数据库配置（MySQL）」区块
- requirements.txt 补齐 pymysql/openpyxl/pandas/xlrd 实际依赖

**复用（未改动逻辑，仅迁移路径）**：
- 全部 NL2SQL 生成引擎、RAG、Schema 知识底座、资源 provider、素材提资转换器
- 全部前端页面与 API 契约（前端零改动）

## 启动

```bat
# 依赖安装（首次）
pip install -r requirements.txt

# 启动
start.bat        # 或 python app.py
# 访问 http://127.0.0.1:5000
```

配置项（MySQL 连接 / LLM Key / Prompt 预算等）均可通过根目录 `.env` 覆盖；
LLM Provider 运行时在「设置」页切换，无需重启。
