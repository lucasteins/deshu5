---
name: material-import
description: 素材提资离线清洗工作流：把源端业务系统的原始数据（Excel 模板等）按治理库（sc01_governance）标准格式离线清洗成标准数据，校验复核后才导入生产库；含码值交叉校核与 upsert 冲突保护纪律
type: prompt
whenToUse: 当用户提供素材提资模板/原始业务数据需要入库，或要求把原始数据清洗为标准数据资源时；凡涉及"导入、提资、清洗、转换"治理库数据的场景
---

# 素材提资离线清洗工作流（sc01_governance）

> **库名变更（2026-09-14）**：`marketing_governance → sc01_governance`（生产档治理库）、
> `marketing_40 → sc01`（生产业务库）、`marketing_log → sc01_log`；仿真档（staging）为
> `fz01_governance` / `fz01`，档位见 `core/db_profile.py`。
> 模块代码已收敛至 `modules/provision/`（旧 `db/smart-query-trainer/` 路径作废）。

## 一、铁律

1. **不直接写生产库**：原始数据先在离线环境清洗成治理库标准格式的数据文件，经校验+复核后才允许导入生产治理库（`sc01_governance` MySQL）。
2. **凡写库先备份**：mysqldump 导全库到 `db/database/sc01_governance_bak_<动作>_<yyyymmdd>.sql`（mysqldump 在 `C:\Program Files\MySQL\MySQL Server 8.0\bin\`；历史备份保留旧名 `marketing_governance_bak_*`，勿改）。
3. **已存在行默认不覆盖**：upsert 遇差异一律进人工复核队列，确认后才覆盖（2026-08-20 模板错误值覆盖正确码值的事故教训：cust_cls:03 高压/低压居民）。
4. **删除按精确键不用模糊匹配**：回滚/清理用主键或自然键等值条件，禁用 LIKE 批量删除（同日 sql_knowledge 误删 elec_caliber 教训）。

## 二、模板结构（素材提资模板 9 Sheet → 目标表）

**字段级维护要求（转换规则）见本 skill 目录《素材提资字段映射规范.md》——它是素材提资转换的唯一权威依据**：每个目标字段的来源列、转换规则、溯源类别、校验与冲突处理都在那里；LLM 离线转换时先读该规范再动手，规则要改先改规范、provisioner 跟随。

| Sheet | 目标表 | 关键列 |
|---|---|---|
| S2-管理元数据 | schema_table_docs | 表名/中文名/所属域/二级业务分类/层级/行数/时间范围/更新频率/来源系统 |
| S3-码值维度 | code_values + code_value_column_form | code_name/中文名/所属域/数据类型/描述/编码列名/描述列名 |
| S3-码值明细 | code_value_items | code_name/item_code/item_name/排序 |
| S4-表关联关系 | schema_relationship_docs | 源表/源列/目标表/目标列/来源 |
| S5-问答对素材 | qa_pairs | 问题/标准SQL/维护人/是否可用/来源 |
| S6-业务规则 | sql_knowledge | 规则描述/正例SQL/反例SQL |
| 00b-枚举字典 / 00c-二级业务分类 | 校验依据 | 层级/域/分类枚举 |

业务分类权威源：`business_domains`（SG-CIM4.5，域中文名↔编码映射见本 skill 目录《业务分类框架_SGCIM4.5.md》）。

## 三、离线清洗流程（原始 → 标准数据文件）

1. **解析**：openpyxl 只读模式读 Sheet；表头剥离"必填/选填/✅"标记；行号→单元格引用（S5!C3）全程携带。
2. **规范校验**（不过即 fail，列差异清单）：
   - 枚举：层级∈{DIM,DWD,DWS,ADS}、所属域∈10 域、二级分类∈business_domains
   - 必填非空；码值明细 code_name ⊆ 码值维度
   - S4 表/列在业务库 `sc01` `information_schema` 存在；S5 SQL 试执行（probe LIMIT 1）
3. **码值交叉校核**（防事故关键点）：
   - 模板 code_name 库内已存在 → 比对中文名/类型/描述，差异列 warn
   - 模板明细 code_name+item_code 已存在但 item_name 不同 → **fail 级映射冲突**，该行禁止直接转换，强制人工复核
   - 新域/新明细 → 正常放行

   > 字段级转换规则（来源列→目标字段→转换规则→溯源）全部维护在本 skill 目录《素材提资字段映射规范.md》，清洗时以它为准。
4. **标准数据产出**（离线文件，不触库）：按目标表格式导出清洗结果（建议 `db/dataset/` 下标准 Excel 或 INSERT SQL 文件），逐字段带溯源标注：
   - direct（模板直接转换，source_ref=单元格）
   - system（系统推导：域编码映射、objects_involved 提取、answer 执行回填、doc_text 拼接）
   - llm（LLM 标注：难度/tags/explanation/触发词，默认 pending 待复核）
   - manual（人工标注/复核产物）
5. **复核**：fail/warn 项与 LLM 项逐条人工确认或改值。

## 四、导入生产库

两条路径（都经过 upsert 冲突保护，不会静默覆盖）：

- **页面路径**（推荐）：智能问数训练系统 →「素材提资」页 → 上传 → 校验预览 → 执行 → 人工复核队列 confirm
- **脚本路径**：`modules/provision/provisioner.py`（parse_workbook/validate/convert_run/annotate_llm），路由 `modules/provision/routes.py`（`/api/provision/*`，注册于 app.py）

导入后核对六表行数与 provenance 记录完整性；同模板重跑必须幂等（零重复行）。

## 五、基础设施速查

- 治理库：`sc01_governance`（生产档；仿真档 `fz01_governance`）；业务库 `sc01`（probe 执行用）；日志库 `sc01_log`
- Python：`C:\Users\11051\AppData\Local\Programs\Python\Python312\python.exe`（裸 python 是商店占位不可用）
- 冲突保护实现：`modules/provision/provisioner.py::_upsert_guard`（存在+差异→pending 复核，confirm 才覆盖）
- **码值治理口径归口 skill `code-value-governance`（唯一权威）**：本 skill 的码值交叉校核遵循其判定口径
