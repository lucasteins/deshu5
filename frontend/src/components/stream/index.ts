// 流式叙事基座（设计稿 §4.4.4 / §4.5.1；F2.1 首落，F2.4 提资·报告复用）
//
// 组件：
//   DsStepRail      流程轨（阶段条状态机）
//   DsThinkingPanel 思考窗口（分类渲染 + 逐字流）
//   DsSqlViewer     SQL 查看器（token 级着色 + 打字机）
//   DsClosingBar    收束条（总耗时 + 摘要 + 动作）
// 工具：
//   sqlHighlight.ts SQL 轻量着色 + 美化（tokenizeSql / highlightSql / formatSql）
//   thinking.ts     QaThinking → 思考条目映射（thinkingToEntry）
//   useAutoScroll.ts 滚动策略（不无条件滚底）
export { default as DsStepRail } from './DsStepRail.vue'
export type { StreamStep, StreamStepStatus } from './DsStepRail.vue'
export { default as DsThinkingPanel } from './DsThinkingPanel.vue'
export { default as DsSqlViewer } from './DsSqlViewer.vue'
export { default as DsClosingBar } from './DsClosingBar.vue'
export { tokenizeSql, highlightSql, formatSql, escapeHtml } from './sqlHighlight'
export type { SqlToken, SqlTokenType } from './sqlHighlight'
export { thinkingToEntry } from './thinking'
export type { ThinkingEntry, ThinkingStream, ThinkingTone } from './thinking'
export { useAutoScroll } from './useAutoScroll'
export type { UseAutoScrollReturn } from './useAutoScroll'
