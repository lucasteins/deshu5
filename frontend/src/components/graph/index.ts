// 图谱基座出口（F2.2 首落，供 F2.3 实体图谱复用）
// - createForceGraph：零依赖 Canvas 力导向图引擎（原样复刻 resource.js 图谱模式）
// - DsForceGraph：图谱基座组件（画布 + tooltip + 工具条 + 图例 + 路径详情区）
export { createForceGraph } from './forceGraph'
export type { ForceEdge, ForceNode, ForceGraph, FindPathResult } from './forceGraph'
export { default as DsForceGraph } from './DsForceGraph.vue'
