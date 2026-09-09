# deshu5 架构说明文档

本文件夹归档 **deshu5 电力营销智能问数系统** 的架构说明文档，由 [archify](https://github.com/tt-a1i/archify)（安装于 `D:\codex\archify`）基于源码实际分析生成，并通过 showcase 级校验与浏览器实证。

归档日期：2026-09-09

## 文件清单

| 文件 | 说明 |
|---|---|
| `deshu5-architecture.html` | **主文档**：自包含交互式架构图，双击用浏览器打开即可。支持明暗主题（T）、节点搜索（/）、5 个引导视图、路径追踪（R）、导出 PNG/SVG/分享卡（E） |
| `deshu5.architecture.json` | 架构图 typed JSON 源文件（IR），修改后重新执行 deliver 命令即可再生成上图 |
| `deshu5-architecture.visual-check.*.png` | 浏览器实证截图：1440×900 / 2048×1320 两档视口 × 明暗双主题 |
| `deshu5-architecture.visual-check.html / .json` | 浏览器实证报告（三档视口无溢出、最小投影字号达标等机器证据） |

## 图内容概要

- 主链路：用户浏览器 → 前端 SPA（9 页签）→ 训练/智能问数（NL2SQL 引擎）→ core 公共底座 → MySQL 四库底座；core → LLM API（Kimi / DeepSeek）
- 本体服务：问数**默认知识源**（Schema / 概念面 / 码值枚举，`knowledge.source=ontology`），前端审批换版
- 深度分析报告：复用训练模块 SQLGenerator，planner 借本体实体词表分解子问题
- 边界：Flask 单进程（蓝图 × 7）、本机/内网受信部署（无认证鉴权）、tools 离线脚本独立进程

## 重新生成

```bash
cd /d/codex/archify/archify
node bin/archify.mjs deliver architecture "/d/codex/deshu5/架构说明文档/deshu5.architecture.json" "/d/codex/deshu5/架构说明文档/deshu5-architecture.html" --quality showcase --json
node bin/archify.mjs visual-check "/d/codex/deshu5/架构说明文档/deshu5-architecture.html" --json
```

## 校验回执

- deliver：showcase 档 9/9 项检查、composition 0 错误 0 警告
- visual-check：1440×900 / 1600×1000 / 1920×1080 三档视口均无溢出，双主题正常
- spec SHA-256 `6ce7a7cb2c10c3363d2ebbab0d91690ab9c2d346137b2a6f1adb703338825ffa`（6250 B）
- artifact SHA-256 `dce7876d7089411b062ce9b60244713572c6deafbb4eea684102b2803ca5b9a5`（817603 B）
