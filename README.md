# 智能问数训练系统 deshu5（轻量化重构版）

基于 deshu4（db/smart-query-trainer）重构。六业务模块相对独立，共享同一 MySQL 底座，
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
│   ├── knowledge_retriever.py 知识读取口（码值/模板/规则，空表回退代码常量）
│   └── ontology/           本体模型层：从底座提炼本体快照，持久化于 marketing_ontology 库
│       ├── model.py        本体内存模型（类/属性/关系/枚举/概念）+ 版本 diff
│       ├── fingerprint.py  底座结构指纹（漂移检测：表/列/主外键/关系文档 hash）
│       ├── builder.py      本体提炼（SchemaPreloader + 码值三表 + 概念映射 + 同义词）
│       ├── store.py        marketing_ontology 库读写（正式版 + 变更提案）
│       ├── service.py      问数模块统一门面（与 legacy 接口同形状，knowledge.source 切换）
│       └── export.py       OWL/RDF 导出（RDF/XML、Turtle、N-Triples、JSON-LD）
├── modules/
│   ├── settings/           ① 数据库配置 + LLM 配置
│   │   └── routes.py       /api/settings/llm*、/api/settings/db*（四库状态）、/api/workflows*
│   ├── training/           ② 训练模式 + 智能问答
│   │   ├── routes.py       出题/评价/生成(SSE)/判断/问答对库/错题集
│   │   ├── engine/         NL2SQL 生成引擎（sql_generator 等 8 文件）
│   │   └── workflow/       声明式生成工作流（7 预设 + knowledge.source 知识源开关，热切换）
│   ├── resources/          ③ 数据资源 + 统计看板
│   │   ├── routes.py       /api/resources/*（CRUD+导入）、码值库、Schema 浏览、/api/stats
│   │   ├── base.py         ResourceProvider 抽象基类
│   │   ├── registry.py     资源注册表（惰性构造）
│   │   └── providers/      9 类资源 provider（问答对/错题/码值/模板/规则…）
│   ├── provision/          ④ 素材提资
│   │   ├── routes.py       上传→预览→执行(SSE)→复核 四步工作流
│   │   └── provisioner.py  xlsx 模板解析/校验/溯源转换/LLM 标注
│   ├── ontology/           ⑤ 本体模型管理面
│   │   └── routes.py       /api/ontology/*（浏览/导出/漂移检测/提案审批）
│   └── report/             ⑥ 深度分析（综合问答/报告生成）
│       ├── planner.py      意图识别：模板匹配 → LLM 分解标准化问题 → 标准问答对命中标注
│       ├── executor.py     子问题并发取数（qa_pairs 标准SQL适配复用优先，未命中走 NL2SQL 引擎）
│       ├── composer.py     取数结果 → Markdown 月报（LLM 成文，失败降级程序拼装）
│       └── routes.py       /api/report/*（plan/generate(SSE)/templates(问数问题联动qa_pairs)/distill-template/runs/export）
├── static/                 前端（含「本体模型」页签：浏览/导出/变更审批横幅；「报告生成」页签）
├── data/                   运行时数据依赖（DDL 元数据 SQL/码值 Excel）
└── uploads/                素材提资上传目录
```

**依赖方向**（无循环）：
- `modules/* → core`（连接层、知识底座；core/sql_exec 为共用只读执行器）
- `training → resources.providers`（生成时读取知识资源）
- `report → training.engine`（复用 SQLGenerator 类层，不依赖 training.routes）
- `core/ontology → resources.providers`（提炼概念/同义词，函数级惰性 import）
- `provision / settings / resources / ontology` 互不依赖

## 本体模型层（数据底座 → 本体 → 问数）

本体从数据底座提炼（表→owl:Class、列→owl:DatatypeProperty、主外键/治理关系→owl:ObjectProperty、
码值→枚举、业务概念→skos:Concept），**持久化于 marketing_ontology 库，不临时抽取**。
仅当检测到基础表结构漂移（结构指纹比对）时生成变更提案，前端「本体模型」页签审批通过后才换版生效；
问数模块经 workflow `knowledge.source`（ontology/legacy）切换知识来源，支持 A/B 与一键回退。
导出：`GET /api/ontology/export?format=owl|ttl|nt|jsonld`。

**实体精炼层（三层两域）**：物理表按语义聚合为业务实体——主数据（MasterData：客户/计量点/供电单位…）
与业务数据（BusinessData：日电量/应收电费/业务工单…）两域，同族表合一（日电量 3 表、96点曲线 3 表、
工单 6 表…），纯关联表降级为实体间关系；ads_* 归入统计报表层（DataProduct），明细问题不召回，
省/市/县三级统计问题优先检索（workflow `knowledge.report_first`，默认开，无命中回退明细汇总）。
实体→物理表映射存 `ontology_entity_defs` 表（本体页签可编辑），编辑经重建提案审批后生效。

## MySQL 底座（四库分工）

| 库 | 用途 |
|----|------|
| marketing_40 | 业务库：35 张营销共享层表，SQL 只读执行目标 |
| marketing_governance | 治理库：问答对/错题/码值/Schema 文档/提资溯源 |
| marketing_log | 日志库：generation_logs 运行日志 |
| marketing_ontology | 本体库：本体版本/类/属性/关系/枚举/概念/变更提案 |

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
