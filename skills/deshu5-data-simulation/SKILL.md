---
name: deshu5-data-simulation
title: deshu5 电网营销数据仿真（主数据 + 业务数据）
description: >
  基于 deshu5 真实库（fz01）的数据分布，按"区县供电公司"为根节点仿真生成
  120 张表：电网拓扑主数据（220kV站→110kV线路→110kV站→10kV线路→台区→配变）、
  PMS 配网设备台账与 D5000 电网模型、营销业务数据（抄表→计费卡→分段量费→应收→
  交费→实收→余额，2025 年至今）及其派生明细（加收/力调/台区日线损/日电量/96 点
  曲线/投资工单/发电并网），量费严格勾稽、关联零孤儿键。内置紧凑 ID 空间分配、
  写前预检、按主键清单的精确回滚台账。
  触发词：数据仿真、造数、模拟数据、仿真数据、营销数据生成、deshu5 造数。
category: productivity
tags: [deshu5, 电网营销, 数据仿真, 造数, mysql, 量费勾稽, 数据治理, PMS, D5000]
agent_created: true
---

# deshu5 电网营销数据仿真

> **库名变更（2026-09-14）**：`database01 → fz01`（仿真库）、`marketing_40 → sc01`（生产库）；
> 配套治理/本体库同名迁移（`database01_governance → fz01_governance` 等）。
> 本 skill 的默认 `--db` / profile 已同步为 `fz01` / `sc01`。
> 既有仿真台账已更名为 `manifest/fz01__模拟__20260913_110119.json`（内容未改），
> 供 `simulator.py --purge --db fz01 --tag 模拟` 回滚。
> **`sc01` 为已剔除仿真的真实数据基线（120 表 / 71,992 行），勿再往 sc01 造数。**

> **存放位置（2026-09-14 起）**：本 skill 属 **deshu5 项目级 skill**，实体文件在
> `D:/codex/deshu5/skills/deshu5-data-simulation/`（随仓库入库）。
> WorkBuddy 的项目级扫描目录是 `<项目>/.workbuddy/skills/`，故已建目录联接
> `D:/codex/deshu5/.workbuddy/skills → D:/codex/deshu5/skills` 做桥接。
> **后续 deshu5 项目 skill 一律放 `D:/codex/deshu5/skills/<skill-name>/`，不要放用户级 `~/.workbuddy/skills/`。**
> 脚本内一律用 `HERE = os.path.dirname(os.path.abspath(__file__))` 相对定位，勿写死绝对路径。

给 deshu5 的 `fz01` 库按真实数据规律"造数"：先按区县供电公司生成一整套
电网拓扑主数据，再生成挂接其上的营销业务数据（2025 年至今），量费闭环可对账。

> 设计原则：**取值不硬编码，全部按真实库分布加权抽样**。分布来自
> `references/profiles.json`（由 `profile_db.py` 从真实库反向勘探生成）。
> 因此仿真数据的统计形态（电压等级、容量档、倍率、电价、类别占比、账期跨度）
> 与真实库一致，而不是拍脑袋。

## 何时使用

- 需要为 deshu5/营销域造数，用于问数（NL2SQL）、治理规则、报表联调、性能压测
- 需要在 `fz01` 里补一批"看起来像真的"主数据 + 业务数据
- 需要可一键清除、绝不误伤存量真实数据的造数能力

## 前置条件

1. MySQL 可连（默认 `localhost:3306`，用户 `root`），密码放 `.env` 的 `MYSQL_PASSWORD`
   或传 `--password`。
2. Python 依赖 `pymysql`。本机已就绪的解释器：
   `C:/Users/11051/.workbuddy/binaries/python/envs/default/Scripts/python.exe`
3. **目标库必须已存在**（脚本只写数据、不建库）。

## 快速开始

```bash
cd D:/codex/deshu5/skills/deshu5-data-simulation/scripts
export MYSQL_PASSWORD=******

# 0) 先看清楚要写什么，不落库
python simulator.py --counties 1 --from 202501 --daily off --dry-run

# 1) 试跑 2 个区县，2025 年至今，日粒度只生成最近 3 个月
python simulator.py --counties 2 --from 202501 --daily recent

# 2) 90 秒验证质量（关联 + 勾稽）
python validate.py --tag 模拟

# 3) 全量：96 个区县全跑
python simulator.py --from 202501 --daily full

# 4) 按台账精确回滚（只删本次生成的行）
python simulator.py --purge --tag 模拟 --dry-run   # 先看清单
python simulator.py --purge --tag 模拟             # 再执行
```

### 常用参数

| 参数 | 说明 |
|---|---|
| `--counties N` | 只跑前 N 个区县（0=全部 96 个） |
| `--county-codes a b` | 只跑指定 `mgt_org_code` 的区县 |
| `--from / --to` | 账期范围 `YYYYMM`，默认 `202501` ~ 当前月 |
| `--daily` | 日粒度电量：`full` 全部月 / `recent` 最近 3 月 / `sampled` 每月抽 5 天 / `off` 不生成 |
| `--derived` | 派生长尾表：`full` 全量 / `light` 抽样（默认）/ `off` 不生成 |
| `--curve-days` | 96 点曲线每月取前 N 天（默认 2，`0`=不生成曲线） |
| `--tag` | 名称标记，**同时是回滚依据**，默认 `模拟`（不得含 `__`） |
| `--seed` | 随机种子，固定后可复现 |
| `--dry-run` | 只统计不落库 |
| `--purge` | 按本地台账删除该 tag 的仿真数据 |

## 生成内容（每个区县一套）

主数据（**严格按用户口径**）：

```
1×220kV 变电站（220kV/110kV）
  └─ 1×110kV 线路
       └─ 1×110kV 变电站（110kV/10kV）
            ├─ 2×10kV 线路
            └─ 3~5×10kV 台区（含配电变压器）
                 └─ 约 20 户用户（业务伙伴/客户/用电客户/结算账户/协议/计量点/电能表/计量点运行）
```

业务数据（2025 年至今，逐月 + 逐日）：

```
抄表读数 → 抄见量 → 计费卡(结算量/电费) → 分段量费 → 应收 → 加收明细 → 交费 → 实收 → 余额
```
另含业扩报装（申请→方案→工单→环节）、装拆移换、退补、日粒度电量（高压表/低压表分表）。

**设备域（补第 42~80 张表）**：主数据只到营销域是与真实库的最大差异——真实库的
配电设备台账在 PMS 侧、电网模型在 D5000 侧。本引擎一并补齐：

```
dim_cst_cntrl_sta.pms_cntrl_sta_id ─→ dim_equ_t_p_pd_stationpsr           (站 PSR)
dim_cst_pipeline.pms_pipeline_id   ─→ dim_ast_t_p_pd_feederlinepsr        (馈线 PSR)
                                        ├─ dim_equ_t_p_pd_linepsr         (支线)
                                        ├─ dim_equ_t_p_pd_cablepsr        (电缆段)
                                        ├─ dim_equ_t_p_pdzn_transformerpsr(配变，pwline_id 回指馈线)
                                        │    ├─ dim_equ_m_p_rel_pdtransformer_feederline  (配变↔馈线)
                                        │    ├─ dim_equ_t_p_rel_transformer_publ          (公变台账)
                                        │    └─ dim_ast_t_p_dy_stationzonepsr             (台区 PSR)
                                        └─ dim_ast_t_odps_sync_v_p_pd_polesitepsr (杆塔)
高压客户的 dim_equ_t_p_pd_optransformerpsr + dim_equ_t_p_rel_transformer_priv (专变)
D5000 电网模型：acline / aclineend / feederline_b_new / con_jlxdjbxx / tline / breaker /
                busbar / dev_pwrtransfm / transfmwd / dev_generator（10 张，id 内嵌 adcode）
```

**派生业务明细（补第 81~120 张表）**：

```
dwd_cst_addl_charg         加收明细（SUM = 计费卡 t_addl_charg）
dwd_cst_special_expense    力调电费（仅高压）
dwd_cst_a_ll_dist_det_day  台区日线损（供电量 = 台区用户用电量 /(1-线损率)）
dwd_cst_es_meter_energy_day_p       表计正反向日电量（与 _xz 表同源同值）
dwd_cst_es_e_mp_comp_curve_h/_a/_v  96 点曲线（有功功率 / 电流 / 电压）
dwd_cst_invest_order / _wkorder_affilmtrl_rec / _dev_rcpt_app_dtl_info   投资工单/材料
dwd_cst_gc_app_rec / _conn_gen_power_app                       发电并网申请
```

**勾稽关系（脚本保证，`validate.py` 复核）**：

- `分段结算量之和 = 计费卡总结算量`
- `计费卡总结算费 = 目录电费 + 加收`
- `应收金额/电量 = 计费卡总结算费/量`
- `实收金额 = 交费金额`
- `SUM(加收明细.addl_amt) = 计费卡.t_addl_charg`（严格到分）
- `加收明细.addl_num × addl_prc = addl_amt`；力调（`_special_expense`）同理
- 台区日线损：`coll_pq > 0` 且 `this_read - last_read = coll_pq`
- 96 点曲线：`Σp1..p96 × 0.25h = 该表当日电量`；`tv/ta` = 电压变比 / 电流变比
- 营销 ↔ 设备域：`pms_cntrl_sta_id` / `pms_pipeline_id` 全链可 JOIN
- D5000 ↔ 营销/设备：**只有地区级可通**，走
  `D5000.region|owner|RIGHT(grid_id,6)` → `dim_org_region_map.region_adcode` → `mgt_org_code`；
  设备级无映射字段（详见 `references/01-仿真模型.md` §5.4）

## 安全模型（重要）

造数最大的风险是**删错真实数据**。本 skill 用三层防护：

1. **紧凑 ID 空间 + 写前预检**：所有仿真 ID 由 `IdPool` 分配，起点按目标库实测
   MAX 之上抬升，并扫描空闲窗口；`preflight()` 在写入前逐个计数器确认"ID 窗口与
   存量数据零重叠"，重叠就报错停写。兼容 `dwd_` 层 32 位 int 外键列。
2. **主键清单台账**：每轮生成把"实际插入的每一行主键"写入
   `scripts/manifest/{db}__{tag}__{stamp}.json`（先写 `.tmp` 再原子替换，
   每完成一个区县覆写一次，崩溃也能回滚）。**不往业务库写任何台账表。**
3. **逐主键删除**：`--purge` 读台账，按 `DELETE FROM t WHERE pk IN (...)` 删除。
   删掉的每一行都是当时确实插入过的，不会碰到存量。

> ⚠️ 历史教训：早期版本用"ID 区间 `BETWEEN min AND max`"删除，区间跨轮次累积
> （LEAST/GREATEST）后圈进了真实数据，造成误删。**不要回退到区间方案。**

## 文件布局

```
deshu5-data-simulation/
├── SKILL.md
├── references/
│   ├── profiles.json      # 真实库分布（区县清单/电压等级/容量/倍率/电价/类别/ID形态/账期）
│   ├── 01-仿真模型.md      # 拓扑、主数据字段、业务链条与勾稽口径
│   └── 02-安全与回滚.md    # ID 分配、预检、台账格式、purge 语义
└── scripts/
    ├── profile_db.py       # 重新勘探真实库 → 刷新 profiles.json
    ├── simulator.py        # 仿真引擎（生成 + purge）
    ├── validate.py         # 质量体检（规模 / 关联 / 勾稽）
    ├── check_code_values.py# 码值合规检查（码表比对 + 真实数据反查分级）
    ├── probe_decide.py     # 待决字段探针：取"真实行"权威取值，定改法（只读）
    ├── codebook_backfill.py# C 级码值反写治理库（补码项，含备份/台账/回滚）
    ├── audit_metadata.py   # 治理库元数据体检（8 项检查，只读）
    ├── fix_governance_metadata.py # 治理库元数据整改（F1 悬空规则 / F2 大小写 / F3 标注）
    ├── diag_ontology_dup.py# 勘察：本体 vs 治理「完全重复存储」三层诊断（只读）
    ├── dedup_ontology_enumerations.py # 本体码值副本下线（超集守卫 + 备份/台账/回滚）
    ├── verify_ontology_endpoints.py   # 本体端点回归（Flask test_client 打真实 HTTP，只读）
    ├── probe_meta_scope.py # 勘察：全服 schema 规模 / DDL 是否有 status 位 / 死列归属（只读）
    ├── probe_orphan_detail.py # 勘察：孤儿 code_name 在本体的码项数与映射面（只读）
    ├── triage_dead_rules.py# 勘察：悬空规则逐条归因（改指 / 删除）（只读）
    ├── real_vs_sim.py      # 真实 vs 仿真逐字段值域对照（严格排除台账主键）
    ├── export_codebook.py  # 导出码表 → references/codebook.json
    ├── snapshot_counts.py  # 全库行数快照（验证 purge 精确还原）
    ├── growth_report.py    # 对比两份快照 → 条目数增长报告 xlsx
    └── manifest/           # 主键清单台账（回滚依据，勿手动改）
```

## 码值合规检查

> **归口**：码值治理的权威口径（判定规则、治理动作、维度映射、生成侧注入）归 skill
> `code-value-governance` 所有；本节是**仿真侧自检**——视角是"仿真值是否失真/越域"。
> C 级反写治理库、治理库元数据整改等动治理库的动作，裁决口径以 code-value-governance 为准。
> 口径对应关系：code-value-governance 的"前导零归一视为同码"是对**生产实际值**的治理校核
> 判定；本节 B 级是对**仿真值**的保真判定（形态须与真实数据对齐，不只是对上码表）——
> 两者视角不同、不冲突。

```bash
python check_code_values.py --db fz01 --tag 模拟 \
    --manifest manifest/fz01__模拟__xxxxxxxx.json \
    --json-out code_check.json --out 码值合规检查.xlsx
```

**码表来源（两源取并集，库内无独立码表库）**：
`fz01_ontology.ontology_enumerations`（`items` 码项 + `column_refs` 字段映射）
与 `fz01_governance` 的 `code_values` / `code_value_items` / `code_value_column_form`。
注意 `code_value_column_form` 里有 **617 条 `table_name='ANY'` 通配规则**，按列名匹配所有表，
漏掉会丢掉一半覆盖。

**分级判据**（字段级，先按 `form` 选比码还是比名，再与真实数据反查）：

| 分级 | 判据 | 动作 |
|---|---|---|
| A 语义错误 | 值不在码表，真实数据也无此值 | 改仿真生成逻辑 |
| B 格式差异 | 去前导零后能对上码项，真实形态不同 | 与业务确认口径 |
| C 码表不全 | 真实数据同样存在这些值 | 治理侧补码表，**不改数据** |
| D 无对照 | 真实该字段全空（含 `\N` 哨兵） | 依码表判定 |

真实库中存有 mysqldump 遗留的**字面量 `\N`**，必须按空处理，否则会被当成有效码值。

### 目标：A / B / D 归零，只留 C

A 级是仿真自身的语义错误，B 级是形态没对齐，D 级是自造了码表外取值 —— 三类都要清零；
C 级（真实库同样如此）不是仿真缺陷，属**治理侧待补码表**，列清单交治理组即可。

### 改码值的标准作业顺序

1. **先取真实口径，再改代码。** 用 `probe_decide.py` 取"真实行"的权威取值
   （严格按台账主键 `NOT EXISTS` 排除本轮仿真行），不要凭码表想当然。
   > 反例：`bus_type` 码表有 231 个码，`0406/0323` 族共 16 个；
   > 但真实库 `gc_app_rec` **只出现过 `40601` 与 `32302`** 两个。
   > 按"同族随机抽"就会抽出 `32313`，判 A/B 级。
2. **逐字段改，一次只改一处，改完 grep 复核。** 批量并行编辑会互相覆盖
   （实测：同一批 3 个编辑只有最后一个落盘），改完必须用
   `grep "'字段': '新值'"` 逐个确认。
3. **2 个区县自测 → 复检 → 全量。** 自测务必覆盖会触发这些字段的分支
   （并网申请只在高压用户上生成，选区县要看用户构成）。

### C 级码值反写治理库

C 级归零的最后一步是**补码表登记**（改元数据，不是改数据）：

```bash
# 1) 先出清单（dry-run，默认不落库）
python codebook_backfill.py --check-json <code_check_final.json>

# 2) 确认无误后落库（自动备份 + 台账）
python codebook_backfill.py --check-json ... --commit

# 3) 精确回滚
python codebook_backfill.py --undo --manifest manifest/governance_backfill_xxx.json
```

**只 INSERT，绝不 UPDATE / DELETE**；备份优先 `mysqldump`（绝对路径
`C:\Program Files\MySQL\MySQL Server 8.0\bin\mysqldump.exe`，不在 PATH），
失败退化为 JSON 行级备份，落 `C:\Users\11051\Desktop\database\`。

`item_name` 取值优先级——**不自造描述**：

| 来源 | 判据 | 例 |
|---|---|---|
| 同表去零 | 同 `code_name` 内去零能对上已有码项 | `inst_lv '1'` ← `'01'` 顶级 |
| 同表已登记名 | 该值已是本 `code_name` 的名称 | `fuel_type '煤'`（form 标成编码） |
| 全局同名借用 | 别的 `code_name` 已登记同名条目 | `'电力网点交费'` ← `pay_chan '101'` |
| 按码借用 | 偏好表内按 `item_code` 命中 | `volt_lv '107'` ← `voltage_desc '107'` 交流220V |
| 原值登记 | 查不到权威名称 | D5000 四位码 `1003`，按值登记待治理组补语义 |

三条防污染规则（都在 `derive()` 里）：
- **跨语义列名禁用去零借用**（`NO_ZERO_BORROW={'type'}`）：`type` 在杆塔是"直线/耐张"、
  在电缆是 PMS 类型码，去零借 `'1'` 会错标语义。
- **借用前查码冲突**：借来的码若在本 `code_name` 下已被占用且名称不同，放弃借用改原值登记
  （`publ_clg_flag '01'` 已是"公变"，不能又登记成"公线"）。
- **A2 判据用补录前快照**：否则本次补录写回的索引会误触发自己。

### 治理库元数据体检与整改

码值管控本身也会"漏"：映射表里可能挂着**永远命中不到**的规则，或指向**没有码项**的
枚举。这类问题不会报错，只会让校验**静默跳过**，必须先体检再整改。

```bash
# 1) 体检（只读，8 项检查：悬空规则 / 孤儿映射 / 大小写失联 / form 不符 / 复合列名 …）
python audit_metadata.py --json-out reports/meta_audit.json

# 2) 出变更单 + 待补清单（dry-run，默认不落库）
python fix_governance_metadata.py

# 3) 落库（自动 mysqldump 备份 + 逐行台账）
python fix_governance_metadata.py --commit            # 全部
python fix_governance_metadata.py --commit --only F1  # 只落指定组

# 4) 精确回滚
python fix_governance_metadata.py --undo --manifest manifest/fix_meta_xxx.json

# 5) 回归：整改后必须重跑码值合规，确认 1002/1002 不被打破
python check_code_values.py --manifest <模拟台账.json> --json-out reports/code_check_after_meta_fix.json
```

三组整改动作：

| 组 | 问题 | 动作 |
|---|---|---|
| F1 | **悬空规则**：`ANY` 规则的列名全服不存在 | 删除（带"无损失"守卫） |
| F2 | **码名大小写失联**：`code_value_items.code_name` 与 `code_values` 仅大小写不同 | UPDATE 对齐 |
| F3 | **标注/唯一性**：form 与真实取值形态不符；一列映多 code_name | 改 form / 删冗余 |

F1 的根因与守卫（**关键**）：映射表按「码列 + 名称列」**成对登记**，但真实模型
每对只留其一 —— 悬空规则**全是"错的那一半"**，其对偶真实列已被另一条规则覆盖。
守卫判据 = **对偶真实列是否已被映射表中任一条规则覆盖**；只有 `redundant=True`
才自动删，否则只报告。对偶列推导要覆盖 `_code` / `_name` / `_desc` / 无后缀
四种形态互换（`city_desc` 的真身是 `city_name`，不是 `city`）。

> **守卫必须限定"目标业务库"，不能只看"全服存在"。** 踩过的坑：
> `--profile prod` 首轮把 62 条全判为可删，提交前复核发现
> `ANY.pay_mode`(id 1529) / `ANY.pay_mode_code`(id 1601) 的对偶列
> `pay_mode_desc` **只存在于 staging `fz01`**，`sc01` 里没有——
> 照删会让 staging 侧这两列**凭空失去校验覆盖**。修正后守卫判据是
> `对偶列 ∈ 目标业务库列 且 已被覆盖`，生产正确拦下这 2 条（删 60）。
> 即：**`audit_metadata.py` 的 S1 口径是"目标业务库无此列"，不是"全服无此列"**。
`--undo` 能精确还原，是因为台账里存了**被删整行原文**与**被改字段原值**：

```json
{"ops": {"f1_delete": [{"id": 2189, "table_name": "ANY", "column_name": "actl_mr_mode_desc",
                        "code_name": "actl_mr_mode", "form": "混合", "updated_at": "..."}],
         "f2_update": [{"from": "bill_Mode", "to": "bill_mode", "ids": [4263, 4264]}],
         "f3a_update": [{"id": 2311, "old_form": "编码", "new_form": "名称"}]}}
```

### 体检报告的三条定性纪律（踩过坑）

上一版报告凭"补录过程中的顺带观察"下了三条结论，**全部被全库体检验证推翻**：

| 误判 | 真相 |
|---|---|
| 「复合 `code_name` `'a;b'` 展开后按整串查表，永远命中不到」 | **有意设计**。`;` 语法 = 一行覆盖"码列 + 名称列"，`check_code_values.py:98` 已正确拆分 |
| 「`type` / `plant_type` / `running_state` 跨语义共用会互相放宽」 | S7 检出逻辑**本身有缺陷**：`X` + `X_desc` 是正常码名配对，不是跨语义 |
| 「`fuel_type`、`plant_type` 的 form 标错」 | 全量抽检 247 项 form 只命中 `volt_cls01`；这两处不成立 |

纪律：
1. **先全库体检，再逐条归因，最后整改** —— 不要在做事过程中顺手定性。
2. **每个"检出项"都要再问一次"它到底会不会导致错误的校验结果"**。S5（一列映两码表）
   两侧命中率都 100%，是**冗余登记**而非冲突；S8 复合列名是**特性**而非缺陷。
   把这类写进"判定为非问题（留档说明）"，比装作没看见更负责。
3. **能自动修的才修，不能修的出清单**。67 个空壳枚举缺权威码项，
   绝不臆造 —— 输出《待补码表清单》交治理组。

### staging / 生产的处置边界

体检与整改**默认只动 staging**（`fz01_governance`）。生产
`sc01_governance` 存在**同源的 62 条悬空规则**（staging 由生产同步而来）。

脚本已参数化，两套档案一键切换：

```bash
python audit_metadata.py --gov-db sc01_governance --ont-db sc01_ontology \
       --biz-db sc01 --json-out reports/meta_audit_prod_after.json

python fix_governance_metadata.py --profile prod --commit   # staging 为默认
python fix_governance_metadata.py --profile prod --undo --manifest manifest/fix_meta_prod_xxx.json
```

**动生产的前提**：① 用户明确授权；② 先备份（`prod_meta_<stamp>.sql`）；
③ 先 dry-run 出变更单复核差异；④ 落库后重跑体检 + 码值合规回归。
2026-09-13 经用户指令授权执行过一轮：映射 756 → **696**（删 60），S1 62 → **24**。

**生产残余 24 条的成因分类（重要，勿再误删）**：

| 类型 | 条数 | 特征 | 处置 |
|---|---:|---|---|
| A | 22 | 引用列**只存在于 staging `fz01`**，生产模型无此列 | 可删（但属新问题类，须单独确认） |
| B | 2 | 引用列**任何业务库都不存在**（`pay_mode` / `pay_mode_code`） | 可删（须单独确认） |

> 这 24 条**都不满足 F1 守卫**（A 类对偶列不在 `sc01`；B 类无对偶列），
> 所以脚本不会自动删——**这是设计如此，不是漏删**，不要为了"清干净"而放宽守卫。

### 本体库不存码值副本（治理库是唯一权威）

**码值只有一份存储：治理库 `code_values` / `code_value_items` / `code_value_column_form`。**
本体库 `ontology_enumerations` 曾是构建时的快照副本，2026-09-13 已全量下线。

踩过的坑：本体构建（`_build_enumerations`）本来就直读治理库取码值，
`save_active()` 又把它落进 `ontology_enumerations` → **同一份码值存两份**。
后果是治理库补码（如 C 级反写 36 条）后本体副本滞后，两份不一致且**无同步机制**。

排查（只读）：`python diag_ontology_dup.py`（产出 D1 版本冗余 / D2 跨库重复 / D3 大字段）

执行下线：

```bash
python dedup_ontology_enumerations.py            # dry-run：出变更单
python dedup_ontology_enumerations.py --commit   # 备份 + 删除 + 运行时校验
python dedup_ontology_enumerations.py --undo --manifest manifest/ontology_dedup_xxx.json
python verify_ontology_endpoints.py              # 端点回归（Flask test_client 打真实 HTTP）
```

**决策判据（超集守卫）**：只有当治理库是本体码值的**严格超集**时才可删——
`仅本体有 code_name = 0` 且 `仅本体有码项 = 0` 且 `中文名不一致 = 0`。
不满足则脚本**默认拒绝执行**（须 `--force`）。实测 420/420 中文名一致、0 条仅本体 → 零损失。

配套代码改动（3 处，切断双写）：

| 文件 | 改动 |
|---|---|
| `core/ontology/builder.py` | 抽出 `fill_enumerations_from_governance(db, ont)`，构建与装载共用同一实现 |
| `core/ontology/store.py::load_active()` | 删掉 `SELECT FROM ontology_enumerations`，改调上述函数直读治理库 |
| `core/ontology/store.py::save_active()` | 删掉 `INSERT INTO ontology_enumerations`，不再写副本 |

**关键**：`ontology_enumerations` 表**保留为空表不 DROP**（避免破坏 `init_tables()` 幂等性）。
改动后治理库补码**即时生效**，无需重建本体。

**改这类装载路径后的必做验证**（`store.py` 属"生成/装载逻辑"，按铁律必须自测）：
1. **端点回归**：`verify_ontology_endpoints.py` —— 5 个 `/api/ontology/*` 端点全绿
   （`/enumerations` 条数须等于治理库 `code_values` 行数；`/classes/<t>` 的落列码值非空）。
   必须用 Flask `test_client` 打**真实 HTTP**，不要只直接调 Python 函数——
   路由层的数据形状（`items` / `item_count` / `column_refs`）才验得到。
2. **隔离自测**：把本体库指向临时库（`STAGING_DB_ONTOLOGY=tmp_xxx`）跑
   「建表 → `build()` → `save_active()` → `load_active()`」，
   断言 `save_active()` 落库 `ontology_enumerations` **0 行**、`load_active()` 仍能取到码值，
   跑完 DROP 临时库。**不要拿 `fz01_ontology` 直接试。**

**未处理（只报告，别自作主张）**：本体旧版本快照（约 2.3 万行 / 5 版）是**版本化设计**
而非重复存储；`ontology_proposals.snapshot` 内嵌码值域（9.19 MB）是审批机制载体。
两者要清理都属于"改设计"，须单独确认。

### 三个高频坑

- **`int` 列会把前导零吃掉。** `arch_status` / `dev_use_stat` / `bus_type` / `bus_categ`
  在库里都是 `int`。写 `'02'` 实际落库是 `2`；真实行若存 `1`，写 `'01'` 才算对齐。
  改前先查 `information_schema.columns.column_type`，再决定写字符串还是数字。
- **`bus_categ` 是 `bus_type` 的前 4 位**，且 `bus_categ_desc` 用码表 `bus_categ` 名称
  （`0406→分布式电源改类`、`0323→电网侧实施`），**不是** `bus_type` 的名称。
- **真实全空的列（D 级无对照）按治理码表填。** 如 `acline_b.linetype` 真实 24 行全为
  `NULL`，此时以码表 `linetype`（1 主线…5 馈线）为准；**不要**顺手套用同表的 D5000
  四位码（`line_type` 才是 `1001` 口径），同族各列口径并不统一。

## 注意事项

- **`--tag` 必须唯一到"一批数据"**。同一 tag 多轮生成，purge 会按全部台账合并删除；
  想分批回滚就用不同 tag。
- 校验 `validate.py` **以 tag 圈定仿真范围**（经计量点名称 → `inst_id` 回溯业务数据）。
  不要用 `MAX(账期)` 直接取，否则会体检到真实数据 —— 这是踩过的坑。
- **圈定范围要看清楚参照列**。真实/样例库里有几组"占位数据"（如
  `dwd_cst_a_ll_dist_det_day` 里 `pro_mgt_org_code='33101'` 的 803 行、
  `dwd_cst_es_e_mp_comp_curve_h` 里 `cust_no='000022'` 的 216 行，`rec_id` 在 5.5 亿
  区间）。它们会和仿真数据落在同一张表，若按 `cust_no` 之类**可能重号的弱键**圈定，
  会把占位数据算进来，得出"我方数据有缺陷"的假警报。优先用
  `meter_asset_no` / `inst_id` / PSR id 这类强关联键回溯。
- 引擎按 `ID_COLS` 自动登记每张写入表的主键；若有表未登记，报告里会出现
  `[回滚缺口]` 告警，必须补齐后再跑，否则 purge 会漏删。
- 账期推进一律用 `ym_add()`（绝对月序运算），不要对 `YYYYMM` 直接做 divmod。
- **`flow` 列表是混合类型**：业扩申请条目有 `app_id`，设备领用单条目有
  `rcpt_app_form_id`。凡按客户取 `flow[0]` 的地方都必须先按 `app_id is not None`
  过滤，否则首元素是领用单时会 `KeyError` —— 该 bug 只在长账期/特定种子下暴露，
  小样本 smoke 测不出来。
- **金额字段绝不能 `round(数量 × 单价, 2)` 反算**。单价只保留有限小数位，高压客户
  月电量上万时反算误差可达 0.05 元，直接打破 `SUM(明细) = 计费卡加收` 的对账。
  正确做法：先定金额、再反推单价，并在单价精度不足时提高小数位兜住。
- **真跑全量前先 `--counties 2` 小批验证**，且小批**必须覆盖长账期**
  （`--from 202501 --to 当前月 --daily full`），否则长账期才触发的分支测不出来。
  跑完务必 `validate.py` 复核。
- 改完生成/回滚逻辑，务必按 `references/02-安全与回滚.md` 第五节做
  **快照→小批生成→purge→快照** 自测，要求差异表数为 0。
- `validate.py` 在库内**没有二级索引**的前提下工作，靠"物化到带索引临时表"把
  全量体检压到 20 秒。**不要退回到相关子查询写法**（80 万行会跑 9 分钟以上），
  也不要为了体检去给业务表加索引。详见 `references/02-安全与回滚.md`。
- **写码值字段前先查口径，不要自造描述**。本体枚举的 `column_refs` 已登记
  "哪个表哪个列该用哪张码表、存码还是存名"，照它填。跑完务必用
  `check_code_values.py` 过一遍 —— 已实测出 53 个字段语义写错，典型三类：
  ① 同名字段口径不同（`county_code` 存**机构码**、`bus_county_code` 存 **adcode**）；
  ② 业务类型取错码表（`bus_type` 是 6 位 `010101`，不是 3 位 `101`）；
  ③ 描述自造（`已归档` 应为 `归档`、`在运` 应为 `运行`）。
- **前导零形态要跟真实数据对齐**。同库内 `read_type` 真实同时存在 `01` 与 `1`，
  `inst_lv` 真实只用 `1` 而码表登记 `01` —— 不能凭码表想当然，先看真实分布。
- **`EXISTS` 半连接而非 `JOIN`**：按台账主键圈定时，若主键非唯一（如日电量表按表号），
  `JOIN` 会放大行数。临时表 collation 要与主键列一致，否则报 1267。
