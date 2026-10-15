---
name: research-planning
title: 科研综述与需求谋划
description: >
  面向公司科技管理场景的科研文书技能：三路文献检索（arXiv 开源预印本、IEEE Xplore、Nature，
  元数据免认证脚本直采；全文经浙大 CARSI 机构通道）、前沿综述撰写、下阶段科技需求清单编制、
  研究方向谋划。触发词：综述、文献调研、前沿调研、需求清单、研究方向谋划、立项谋划、
  arxiv、IEEE、Nature、论文检索、科研规划。
whenToUse: 需要检索最新科研文献、编写技术综述、编制下阶段科技需求清单或谋划研究方向时使用
---

# 科研综述与需求谋划

本技能把"文献检索 → 证据汇总 → 正式文书生成"做成可复用流水线，方法论参照
`skills/academic-research-skills/`（GitHub 高星科研技能集，作为方法论参考库保留在本目录，
重点看其 deep-research/ 与 academic-pipeline/ 的分阶段编排与引用校验要求）。

## 数据源与脚本（三路检索，元数据免认证）

三个检索脚本参数规则相同：`q=` 为检索式（**空格一律用 `_` 代替**，arxiv 查询式中的
`AND` 保留），`topic=` 为主题名（中文连写，用于归并结果），`max=` 条数。
结果增量合并入 `skills/research-planning/results/<topic>.json`，下次生成文档时自动引用。

```bash
python skills/research-planning/scripts/search_arxiv.py q=all:ontology_AND_all:retrieval-augmented_generation topic=本体RAG max=8
python skills/research-planning/scripts/search_ieee.py q=carbon-aware_data_center_scheduling topic=算电碳协同 max=10
python skills/research-planning/scripts/search_nature.py q=AI_data_centre_carbon topic=算电碳协同 max=10
```

- arXiv：官方 API，按提交日期倒序，偶发 502 会自动退避重试。
- IEEE Xplore：脚本自动先取 Cookie 再调 rest/search，取标题/作者/出处/年/被引/摘要/DOI。
- Nature：nature.com 站内检索按日期倒序，覆盖 Nature 系全部期刊。
- **全文获取**：IEEE/Nature 全文须走浙大 CARSI 机构通道（libdb.zju.edu.cn → CARSI → 浙江大学
  统一认证 → 资源详情页"访问资源"），此链路需浏览器人工操作；写综述时引用元数据即可，
  确需全文时提示用户走 CARSI 通道。

## 文档生成（正式格式，强制）

三类文书的格式规范在 references/ 下，生成前必须先读：

```bash
python skills/research-planning/scripts/show_template.py survey
python skills/research-planning/scripts/show_template.py requirements
python skills/research-planning/scripts/show_template.py planning
python skills/research-planning/scripts/show_template.py review
python skills/research-planning/scripts/show_template.py common
```

生成器按模板分节撰写并自动装配文档头、重编引用、生成参考文献，落盘 `kjgl研究谋划/output/`：

```bash
python skills/research-planning/scripts/compose_doc.py type=survey title=能源算电碳协同前沿综述
python skills/research-planning/scripts/compose_doc.py type=requirements title=下阶段科技需求清单
python skills/research-planning/scripts/compose_doc.py type=planning title=研究方向谋划报告
python skills/research-planning/scripts/compose_doc.py type=review title=《某需求》需求先进性审查报告 target=kjgl研究谋划/inbox/xxx.md
```

`type=review`（先进性审查报告）额外要求：先经 `python tools/save_text.py <文件名.md> <需求原文>`
把审查对象暂存到 `kjgl研究谋划/inbox/`，再用 `target=` 指向该文件；title 用
`《需求名》需求先进性审查报告`。审查前应先完成相关主题的三路检索。

`topics=主题1,主题2` 可限定只用指定主题的检索结果；缺省用全部。kjgl 业务底数自动读取
`kjgl研究谋划/kjgl_base.json`（项目库+规划需求清单）。

### 格式硬约束（回答用户时也必须遵守的要点）

1. **文档头**：文档编号（KJGL-ZS/XQ/MH/SC-年份-序号）、密级、编制单位、日期、版本。
2. **章节编号**：一、（一）、1.、（1）四级；一级章用 `##`，禁止 `###`。
3. **图表**：表 n / 图 n 连续编号，表题在表上方。
4. **引用**：一律 `[n]` 编号，对应文末《参考文献》；**每条引用必须来自检索结果库**，
   禁止凭记忆编造文献。参考文献格式：`[n] 作者. 标题[类型]. 出处, 年. 标识.`。
5. **语体**：书面公文语体；指标须可量化；结论须有依据。
6. **版本记录**：文档末尾固定附版本记录表。

## 标准工作流

1. **明确主题**：与用户确认综述/谋划覆盖的主题清单（默认对齐 kjgl 九大规划方向）。
2. **逐主题三路检索**：每个主题至少跑 search_arxiv，条件允许加跑 search_ieee / search_nature；
   已有结果的主题会增量合并，可跳过。
3. **读格式模板**：show_template.py 读对应类型模板，向用户确认要点（篇幅、主题、侧重）。
4. **生成文档**：compose_doc.py 一键生成正式文书，告知用户落盘路径。
5. **交付说明**：向用户说明引用了多少条文献、覆盖哪些主题、哪些判断需要专家复核。

## 边界与纪律

- 本技能产出为**谋划建议稿**，不直接写 kjgl 库；入库须走 Web 端规划需求录入流程。
- 检索脚本若返回风控错误（IEEE 偶发 418），提示稍后重试或改用浏览器 CARSI 通道。
- 回答用户时若未运行检索脚本，不得给出带具体文献出处的论断。
