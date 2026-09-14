---
name: report-management
description: deshu5 报表（报告模板 + 运行历史）标准化存储与管理规范：report_templates outline/触发词/问数问题三形态、qa_pairs diff 联动与 SQL 再生成、report_runs 落库结构与 Markdown 正文规范
type: prompt
whenToUse: 新增/修改/删除报告模板、从历史报告提炼模板、直接维护报表库数据，或需要核对 report_templates / report_runs / qa_pairs 联动存储格式时
---

# 报表管理规范（deshu5 报告生成模块）

所有报表资产存治理库（生产档 `sc01_governance` / 仿真档 `fz01_governance`，随 `core/db_profile.py` 当前档位；旧名 `marketing_governance` 已于 2026-09-14 迁移），建表入口 `core/database.py::init_report_tables`（幂等）。
模块代码在 `modules/report/`（planner 规划 / executor 取数 / composer 成文 / routes 路由）。

## 一、存储分工：两表 + 一联动列

- `report_templates`：可复用报告模板（管理的核心资产）
- `report_runs`：每次生成落库一份运行历史（计划 + 取数明细 + 正文）
- `qa_pairs.report_template_id`：模板问数问题 ↔ 标准问答对的联动列（模板问题同时是 RAG 池中的问答对）

## 二、报告模板存储规范（report_templates）

| 字段 | 口径 |
|------|------|
| name | 泛化名称，去掉具体单位名与期间（"设备管理月报""工业用电情况月报"），不写"2026年3月杭州公司…" |
| trigger_words | JSON 数组 3~6 个，必含模板名本身 + 同义说法（如 `["工业用电","用电月报","用电分析","工业用电情况","用电量"]`），意图含其中一词即命中 |
| outline | JSON 数组 `[{section_title, hint, questions}]`；hint 一句话说明本章取数方向（问什么指标） |
| enabled | 1 启用 / 0 停用（停用后不参与 trigger_words 命中） |
| remark | 溯源留痕，提炼产物写 `提炼自 run#N`，手工维护写维护原因 |
| created_at / updated_at | ISO 时间串，更新时刷 updated_at |

**outline 章节组织**（以库内 4 个在用模板为准）：
- 4~6 章，第 1 章多为"概述/总体情况"，后续章节按业务维度展开（结构/趋势/重点客户/风险…）
- 每章 1~3 道问数问题，全模板问题总量控制在 4~10 道（planner 硬顶 `REPORT_MAX_QUESTIONS=10`）

**questions 元素三种形态**（混用允许，对象形态优先）：
1. `"问题题干"` —— 纯字符串，无 SQL、无挂链（待生成）
2. `{"question": "...", "qa_id": 205}` —— 已挂 qa_pairs
3. `{"question": "...", "standard_sql": "...", "qa_id": 205}` —— 带已验证 SQL（提炼回填产物，qa_id 可为 null 待保存时入库）

**问题题干撰写纪律**：
- 用"本月/上一自然年度/统计期内"等相对期间，禁止写死单位名和具体月份（生成时由 planner 实例化填入）
- 每题完整自含一句话、只问一个主题、能用一条 SELECT 回答（统计/排名/明细清单均可）

## 三、qa_pairs 联动规范（_sync_template_questions diff 语义）

模板保存（POST/PUT `/api/report/templates`）时同事务对 outline 问题做 diff 联动，**手工直改 report_templates 而不同步 qa_pairs 是最常见错误**：

- **新增**：题干规范化（去空白/尾标点）全库无命中 → INSERT qa_pairs：`source='report_template'`、`difficulty='进阶题'`、`tags=["报告模板:<模板名>"]`；带 SQL 直接 `is_usable=1`，否则 `is_usable=0` 待生成
- **关联**：题干命中已有问答对 → 挂 `report_template_id`（带 SQL 则回填并置有效）
- **变更**：qa_id 已挂本模板但题干变了 → 更新题干、**清 standard_sql、is_usable=0**（防口径漂移，强制重新验证）
- **删除**：原挂本模板、本次 outline 消失 → `is_usable=0` 软删（**保留记录**，仅移出 RAG 池，勿 DELETE）
- 保存后后台线程为无 SQL / 题干变更的题重新生成 SQL 并只读试跑，验证通过才回填 `is_usable=1`；失败保持无效，等训练链路人工判题回流

`generation_method` 取值：`template-distill`（提炼/保存时携带 SQL 回填）、`template-edit`（保存后后台再生成验证通过）、`template-autogen`（历史遗留，现代码不再产生）。

## 四、运行历史存储规范（report_runs）

每次 `/api/report/generate` 落一行，先 INSERT（status='running'）完成后 UPDATE：

- `status`：`running` / `done` / `failed`（done 的判定：至少一道题取数 ok）
- `plan_json`（MEDIUMTEXT）固定键：`intent_text, org_scope[], period, question_count, report_title, sections[], template_id, template_name, usage`
  - `sections[].questions[]`：`{qid, question, source}`，qid 全报告连续编号（q1…qN）
  - `source`：`generated`（NL2SQL）/ `qa_pair`（命中标准问答对，附 `qa_id, standard_sql, match_score`；模板直挂附 `from_template: true`）
- `detail_json`（MEDIUMTEXT）每题一项，固定 12 键：`qid, section_title, question, source, qa_id, sql, headers, rows, row_count, status(ok/failed), error, ms`
  - `sql` 必留痕（即使执行失败）；rows 为只读执行结果（自动补 LIMIT，上限 `SQL_MAX_ROWS=50`）
- `usage_json`：`{plan, compose, questions_ok, questions_total}`
- `duration_ms`、`session_id`（uuid）、`created_at`

**report_md 正文规范**（composer 产出，正式公文文风）：
- 结构：`# 报告标题` → `## 概述`（总体结论先行）→ `---` 分隔 → `## 一、章节名`（汉字序号）逐章展开
- 每章：先一句话概括 → Markdown 表格（数字逐字来自取数结果，**严禁编造**）→ 必要时分要点小结
- 取数失败/为空章节：保留章节标题，写明"本期无数据/数据暂缺"，严禁虚构指标
- LLM 成文失败时降级程序拼装（`degraded` 标记），结构相同但无概述与小结

## 五、新增报表标准流程

**首选（提炼链路，SQL 自动回挂）**：
1. 先以一次性意图跑通一份报告（`/api/report/generate`，落 report_runs）
2. `POST /api/report/distill-template {run_id}` 提炼模板（自动按 `src: q题号` 回挂已验证 SQL；仅提炼不落库）
3. 前端/人工确认编辑后 `POST /api/report/templates` 保存 → 自动 diff 联动 qa_pairs + 后台 SQL 再生成
4. remark 写 `提炼自 run#N`

**手工新增**：按第二节结构构造 outline，走 `POST /api/report/templates`（不要绕过接口直接 INSERT，否则丢失 qa_pairs 联动与 SQL 再生成）。

**复检清单**：
1. `GET /api/report/templates/<id>`：outline 问题对象化后 `has_sql/is_usable` 状态符合预期
2. 用触发词意图 `POST /api/report/plan`，确认命中本模板且问题实例化正确（单位/期间已填入）
3. 完整 generate 一次，report_runs 新行 status=done、detail 无 failed
4. 无 SQL 的题确认已进入后台再生成（或转训练链路判题），不长期滞留 `is_usable=0`

## 六、纪律与已知坑

- 模板问题变更 = 清 SQL 重验：绝不直接手改 qa_pairs.standard_sql 而不动 is_usable 状态之外的字段联动；口径调整经训练链路验证后回流
- 执行期按 qa_id **实时重取** standard_sql（executor `_fresh_standard_sql`）：改问答对 SQL 即对所有新报告生效，模板 outline 里的 SQL 副本只是提炼快照
- DELETE 模板只删 report_templates 行，**不级联清理** qa_pairs.report_template_id 与历史 report_runs.template_id（库内已存在模板 1/2 删除后的悬挂引用）；删除前先评估是否改为 enabled=0
- 触发词过宽会抢命中（命中词数最多者胜）：新模板上线后用代表性意图回归老模板的命中情况
- report_runs 只增不改（除运行中状态翻转）；清理历史走 `DELETE /api/report/runs/<id>`，导出走 `GET /api/report/runs/<id>/export`（Markdown 下载）
- **SQL 再生成只验语法不验数据**（safe_execute_sql 0 行也算成功）——模板/问答对的 SQL 上线前必须人工或脚本验证"返回行数 > 0"，否则就是"能跑但全空"（2026-09 模板7 事故根因）；窄表条件值（report_name/dim_name/indicator_name）必须与数据逐字一致，用唯一前缀补全兜底（`_complete_name_literals`）
- 模板 outline 里 questions 的 qa_id 可能为 null（提炼后未回挂）：按题干规范化文本反查 qa_pairs 回挂，勿按 qa_id 空值跳过联动
