// 图表域出口（F1.2，设计稿 §4.4.7）
//   ECharts 基座：echarts（按需注册）/ useECharts / useChartTokens / option 构建器
//   组件：DsChart（通用宿主）/ DsKpiCard（KPI 卡 + sparkline）/ DsSparkline / DsHeatGrid
// 图表内状态（骨架 / 空 / 错）由 DsAsyncSection 统一接管；此处只处理图表内空态。
import './charts.css'

export * from './echarts'
export * from './chartTokens'
export * from './useECharts'
export * from './options'

export { default as DsChart } from './DsChart.vue'
export { default as DsKpiCard } from './DsKpiCard.vue'
export { default as DsSparkline } from './DsSparkline.vue'
export { default as DsHeatGrid } from './DsHeatGrid.vue'
