---
name: code-value-governance
description: 码值治理唯一权威口径（治理库 sc01_governance / fz01_governance）：码值入库、生产库×码值库校核、错域/脏值/列名治理、维度映射与生成侧注入复检；material-import、deshu5-data-simulation、ads-table-standard 的码值动作均以本 skill 口径为准
type: prompt
whenToUse: 当用户要求校核/治理码值（枚举值、码表、维度值），或发现 SQL 生成中条件值取值错误、码值域与字段对不上时
---

# 码值治理工作流（全库码值体系；流程沉淀自营销 4.0 共享层 35 表治理）

> **库名变更（2026-09-14）**：`marketing_governance → sc01_governance`（生产档治理库）、
> `marketing_40 → sc01`（生产业务库）；仿真档（staging）为 `fz01_governance` / `fz01`，
> 档位切换见 `core/db_profile.py`。
>
> **归口**：本 skill 是码值治理口径的唯一权威。`deshu5-data-simulation` 的码值章节是
> **仿真侧自检**（仿真保真视角），其 C 级码值反写、治理库元数据整改等动治理库的动作，
> 裁决口径以本 skill 为准；`material-import` 的模板码值交叉校核、`ads-table-standard`
> 新报表的码值域登记，同样遵循本 skill 口径。

## 一、基础设施与数据位置

- 码值库（治理库 MySQL，生产档 `sc01_governance` / 仿真档 `fz01_governance`）：
  - `code_values`：码值域（code_name/code_cn_name/domain/data_type/description）
  - `code_value_items`：码值明细（code_name/item_code/item_name/sort_order），**编码与名称成对**
  - 初始源文件：`config.CODE_VALUES_FILE` / `config.CODE_VALUE_ITEMS_FILE`
    （默认 `<项目>/data/code_values_v1.0.xls` / `code_value_items_v1.0.xlsx`，环境变量可覆盖；
    历史源头为 deshu4 `db/database/` 下同名文件）
  - 启动时幂等导入：`core/database.py::import_code_values_if_empty`（表空才导；更新源文件后手工清表重启即重建）
- 生产库：`sc01`（仿真档业务库为 `fz01`）
- 元数据：治理库 `schema_table_docs` / `schema_column_docs`（由 `core/schema_preloader.py` 维护：
  information_schema 全量 + 治理库策展注释兜底；进程内缓存，结构变更后需重启应用生效）

## 二、校核流程（先跑校核，拿到事实再治理）

现行校核工具（都在 `skills/deshu5-data-simulation/scripts/`，报告产物落 `.workbuddy/reports/`）：

- `check_code_values.py`：码值合规检查（码表比对 + 真实数据反查分级）
- `audit_metadata.py`：治理库映射体检（悬空规则 / 孤儿映射 / 大小写失联 / form 不符等，只读）

判定口径（多轮迭代后的稳定口径，勿回退）：

1. 只查 distinct ≤ 50 的枚举型列；值去空白归一
2. **双侧比对**：实际值命中码值名称或编码均视为域内
3. **子集正常**：生产值 ⊆ 值域（含真子集）判正常，越域值才是不一致
4. **前导零归一**：'01' 与 '1' 视为同码
5. 情形A（异名映射）只按名称侧匹配且要求命中含中文（编码侧巧合面太大不采信）

> 与仿真侧分级的关系：第 4 条是治理校核对**生产实际值**的判定；`deshu5-data-simulation`
> 的 B 级（仿真值去零能对上码项、但形态与真实数据不同）是**仿真保真**判定，
> 两者视角不同、不冲突。

⚠️ **校核脚本重新生成会覆盖报告文件**。若报告中有手写裁决，先把裁决内容留档再重跑。

## 三、治理动作分类与执行顺序

对每条校核发现，按三分法裁决后执行：

1. **登记新值**：INSERT 进 `code_value_items`（注意有的域内容存 item_code 侧，如 prc_ind_ustry_cls、prc_type、rpt_ec_categ——新增项双侧都写）；批量反写走 `codebook_backfill.py`（**只 INSERT，绝不 UPDATE / DELETE**，自动备份 + 台账，支持 `--undo` 精确回滚）
2. **清洗脏值**：UPDATE 生产库（如 `valid_flag_desc '是'→'有效'`）；语义存疑的映射要留痕（如 rs_reason_cls 00→01）
3. **结构治理**（列更名/删列）：必须三处同步，缺一不可——
   - 业务库（`sc01` / `fz01` 双档，ALTER TABLE）
   - 治理库 `schema_table_docs` / `schema_column_docs`（注释与 data_type 同步，须与物理类型一致）
   - 同步后必须重启应用（SchemaPreloader 进程内缓存）
4. **新建码值域**：flag 类约定 01否/02是、01无效/02有效；新域后列名即与域名直接匹配，无需 supplement
5. **映射体检整改**：悬空规则 / 码名大小写失联 / form 标注走 `fix_governance_metadata.py`
   （dry-run 出变更单 → `--commit` 落库，带守卫与 `--undo`）；**默认只动 staging（fz01_governance），
   动生产须用户明确授权 + 先备份 + dry-run 复核 + 回归**（守卫细则见 deshu5-data-simulation 对应章节）

## 四、维度↔表字段映射（生成侧注入的关键）

- 自动映射：`core/rag_retriever.py::_load_code_value_index` 用"列名 ∩ 码值域名"构建
- **补充映射 supplement**（域名与存储列名不一致时人工核实后添加，现有生效清单）：
  - `prc_ind_cls → 3张dwd表.prc_ind_cls_desc`（行业分类_电价名称列）
  - `cust_ind_cls_desc → dim_cst_cust/dim_cst_elec_cons_cust.cust_ind_cls_desc_1`（门类行业列，勿与明细行业列 cust_ind_cls_desc 混淆，二者粒度不同）
  - `dereg_attr_cls → dim_cst_elec_cons_cust.dereg_attr_cls_desc`
  - `county_code → dim_cst_mgt_org.county_name`、`station_code → dim_cst_mgt_org.station_name`
  - `line_publ_clg_flag → dim_cst_pipeline.line_publ_clg_flag_desc`
- 历史教训：`settle_acct.pay_mode_desc` 实际承载 pay_chan_desc 域（e户通代扣等），已通过列更名 `pay_chan_desc` 根治；`charg_acct.chan_no` 存的是渠道编码（WECHAT/ALIPAY）不是中文渠道名，勿用于中文值过滤
- 核实方法：先查生产实际值 `SELECT DISTINCT col`，再与候选域明细比对，确认后才写映射（禁止凭名字猜）

## 五、生成侧注入与复检

- 每题注入：`core/rag_retriever.py::RAGRetriever.retrieve_code_values(question, tables)` → 提示词【维度码值】段（"WHERE 条件值必须从这里取"）；通道一=问题子串命中值，通道二=定位表的码值列带值域
- **存储形态标注**（编码/描述混用治理）：
  - `code_value_column_form` 表记录每列实际存储形态（名称/编码/混合；含 `table_name='ANY'` 通配规则，按列名匹配所有表），由校核流程双侧判定维护，数据变更后须重跑校核刷新
  - 存编码列在提示中标注【该字段存编码】并给 名称=编码 对照；规则 19 强制模型服从形态
  - `modules/training/engine/sql_generator.py::_translate_code_value_literals` 后处理兜底：把存编码列条件中的中文描述字面量机械翻译成编码（支持 `=` 与 `IN`），只对值域内精确名称生效；域外描述靠提示与规则引导模型选最近项
- 治理完成后复检三件套：
  1. 重跑校核（`check_code_values.py` / `audit_metadata.py`，越域情形应趋近 0）
  2. 端到端生成 2-3 道涉及码值条件的题，确认条件值取自码值提示、编码列用编码
  3. 检查 qa_pairs 是否引用过变更列（`standard_sql LIKE '%列名%'`），有则同步修正
- 完整治理案例与体检报告见 `.workbuddy/reports/`（码值合规检查、反写治理库、元数据整改等报告）
