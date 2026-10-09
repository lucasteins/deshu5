---
name: ontology-distill
title: 本体提炼（实体层精炼：体检 → LLM 提案 → 落库 → 提案审批）
description: >
  deshu5 本体模型层的提炼与修复规范：以当前档位真实 Schema（表/列/注释/行数/物理外键/
  已有概念映射）为素材，诊断提炼质量（Unmapped 堆积、悬空概念、孤立表、码值空缺），
  用 LLM 产出业务实体层草稿，人工审阅后落 ontology_entity_defs 并生成重建提案，
  经 Web 审批生效。适用 kjgl01 这类新接库（内置种子映射不命中导致全表 Unmapped）。
  触发词：本体提炼、实体提炼、Unmapped、本体重建、实体映射、ontology。
type: prompt
whenToUse: 本体实体层全落 Unmapped/提炼质量差需要重提炼时；新库接入后首次建实体层时；
  需要诊断本体五层资产（类/关系/枚举/概念/实体）健康度，或维护 ontology_entity_defs 映射定义时
---

# 本体提炼（deshu5 本体模型层）

本体 = 当前档位底座的语义层快照，存 `<档位>_ontology` 库，构建器 `core/ontology/builder.py`，
生命周期门面 `core/ontology/service.py`，管理 API `modules/ontology/routes.py`（/api/ontology/*）。

**素材来源（五路，全部只读）**：

| 层 | 来源 | 说明 |
|---|---|---|
| 类/属性 | SchemaPreloader 单例 | 业务库 information_schema ∪ 治理文档双源合并 |
| 关系 | 同上 | 物理外键图 ∪ schema_relationship_docs，冲突以物理外键为准 |
| 码值枚举 | 治理库 code_values 三表 | 唯一权威源，本体库不存副本；空 = 码值未治理 |
| 业务概念 | 治理库 keyword_table_map | 空表回退 intent_parser 常量（sc01 电力口径，对其他档位是错配） |
| 实体层 | ontology_entity_defs（库中定义优先） | 空表时用 `core/ontology/entity_map.py` 内置种子播种——**该种子是 sc01 电力营销 101 表口径** |

**生效纪律**：所有变更必须 提案（proposal）→ Web 审批 → approve 落新版；
首次引导（空库）直接构建 v1 生效。defs 编辑后不重建审批，问数侧永远看不到。

## kjgl 档位的病灶与根因（2026-10-08 确诊）

**现象**：kjgl01 库 21 张表提炼后实体层只有 1 个 `Unmapped`，装全部 21 张表。

**根因链**：
1. 内置种子 `entity_map.default_entity_map()` 全是 `dim_cst_*` / `dwd_cst_*` / `dim_grid_*`
   电力表，kjgl 一张不命中；
2. 兜底二（治理域标签聚合）依赖 `schema_table_docs.domain_l1/l2`，而 kjgl01_governance
   该表无这两列（`kjgl_sync.refresh_schema_docs` 不写域标签），兜底失效；
3. 于是 21 张表全部落 Unmapped。概念面（49 词）已由 `tools/seed_kjgl01_keywords.py`
   播种故正常；码值面空（治理库 code_values 未建）；关系面 27 条物理外键正常。

**修复结果**：本技能脚本已产出 15 实体草稿并落 defs（提案 #3 待审批）。
复跑/换档位时按下面工作流即可，不要手改库。

## 标准工作流（三步 + 审批）

```bash
# 1) 体检（只读）：定位病灶层级，输出 Unmapped 清单/悬空概念/孤立表/码值空缺
python skills/ontology-distill/scripts/diagnose_ontology.py

# 2) LLM 提案（不落库）：真实 Schema 上下文 → 实体层草稿 JSON
python skills/ontology-distill/scripts/propose_entities.py --note "业务背景一句话"

# 3) 人工审阅 drafts/entity_defs_<档位>_<时间戳>.json（重点改 name/label/layer/成员表归属），
#    然后落库 + 生成重建提案（单行命令，不要换行续行）
python skills/ontology-distill/scripts/apply_entity_defs.py --draft entity_defs_kjgl_xxxxxxxx_xxxxxx.json --commit --prune --rebuild

# 4) Web 本体管理页审批提案 → 生效（问数侧 invalidate 自动联动）
```

### 脚本参数

| 脚本 | 参数 | 说明 |
|---|---|---|
| diagnose_ontology.py | `--json-out <路径>` | 另存机器可读体检报告（默认只打印） |
| propose_entities.py | `--note "..."` | 业务背景注入 prompt（系统定位、口径），显著提升提案质量 |
| | `--max-col N` | 每表注入 prompt 的最大列数（默认 25，主键/有注释列优先） |
| | `--out <路径>` | 自定义草稿输出路径（默认 drafts/entity_defs_<档位>_<时间戳>.json） |
| apply_entity_defs.py | `--draft <文件>` | 必填；支持绝对路径 / scripts 相对路径 / drafts 下文件名 |
| | `--commit` | 真正落库（默认 dry-run 只出变更单：新增/更新/不变/库有草稿无） |
| | `--prune` | 草稿之外的库存定义置 enabled=0（清掉历史 Unmapped 全包定义用） |
| | `--rebuild` | 落库后生成全量重建提案（pending），打印提案 id |

退出码约定：diagnose 有"阻断"级发现返回 1；propose 草稿校验有错返回 1；
apply 校验不通过返回 1 且不做任何变更。

**技能对话执行约束**（命令白名单 `_CMD_RE`/`_ARG_RE`）：参数只允许字母数字与
`-_ = . , : /`，**不能带空格和引号**——`--note` 在对话里用短词连写
（如 `--note 科技项目管理系统_含规划清单`），或省略 --note 让 LLM 纯按 Schema 提炼。

## 实体层设计口径（审阅草稿的验收标准）

- **name** 英文 PascalCase 标识符（Project/Achievement/Organization），**label** 中文名；
  layer 三选一：master 主数据（机构/人员/字典）/ business 业务对象及明细 / report 统计报表。
- **每张底座表恰好归属一个实体**；关联表（项目-人员、成果-署名这类）就近挂主导业务实体，
  在该实体 comment 中注明其关系表身份（defs 机制不支持"降级为实体间关系"，
  那是 `entity_map.RELATION_TABLE_MAP` 的 sc01 专用硬编码）。
- 实体数量按语义自然聚合：21 张表提炼出 10~18 个实体为宜；一表一实体（细碎）
  和全部塞一个实体（Unmapped 翻版）都判不合格。
- comment 80~200 字，只基于真实表/列概括，禁止编造；写清涵盖表的业务范围与典型问数场景。
- apply 校验红线：name 非法/重复、layer 越界、member_tables 含底座不存在的表 → 直接拒绝。

## 其他四层资产的治理入口（本体之外的配套动作）

| 层 | 治理入口 | 现状（kjgl） |
|---|---|---|
| 概念面 | 治理库 keyword_table_map（resources API 维护，先例 `tools/seed_kjgl01_keywords.py`） | 49 词已播种 |
| 码值面 | 走 `code-value-governance` 技能补 code_values 三表；补后本体即时生效（免重建） | 空，待治理 |
| 同义词组 | sql_knowledge 同族消歧规则（business_rule provider） | 空 |
| 关系面 | `python tools/seed_kjgl01_schema_docs.py` 刷新治理关系文档；物理外键随 kjgl 同步自动重建 | 27 条正常 |

## 纪律与已知坑

- **绝不绕过提案机制直写 ontology_entities 等版本表**；entity_defs 是唯一可直写的
  "规则表"（它只是构建素材，不直接改变生效版本）。
- **脚本绝不自动 approve**：提案审批只能在 Web 本体管理页人工进行
  （approve 会联动 invalidate 问数缓存；脚本进程内的 Service 实例与 Web 进程无关）。
- 运行中 Web 进程的本体是缓存快照：defs 落库不影响在跑服务，重建→审批后才生效，
  这与 `seed_kjgl01_keywords.py` 必须走 HTTP 写 keyword_table_map 的原因不同
  （keyword provider 有运行时缓存，defs 只在 build 时读取，无此问题）。
- 不要把"全表 Unmapped"的定义留在 defs 里占位——重建时它会被当成正经实体定义照单全收；
  用 `--prune` 清掉。
- 底座结构变化（kjgl 源库加表重同步）后：先 `python tools/seed_kjgl01_schema_docs.py`
  刷新文档，再跑 diagnose 看漂移，新表进 Unmapped 就重跑 propose/apply 把新表归入实体。
- LLM 提案不是终点：草稿必须人工审阅（重点核对成员表归属与 comment 是否贴真实业务），
  审完再 --commit。 drafts/ 与 reports/ 是运行时产物，不入库。
