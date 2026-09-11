/**
 * ECharts 按需注册（03-迁移方案 §二：ECharts 5；设计稿 §4.4.7 图表规范）
 *
 * 只注册看板用到的图表与组件（折线 / 横向条形 / 环形 + 网格 / 提示 / 图例 / 标记点），
 * 避免全量打包（F0.1 曾因全量 echarts 触发 chunk 体积告警）。图谱沿用原 Canvas 实现，不走 ECharts。
 *
 * 类型说明：option / series 类型全部来自 `echarts/core|charts|components` 子路径，
 * 经 `ComposeOption` 组合——与根包 `echarts` 的声明是两套，混用会触发 TS 冲突。
 */
import * as echarts from 'echarts/core'
import { BarChart, LineChart, PieChart } from 'echarts/charts'
import {
  GridComponent,
  LegendComponent,
  MarkPointComponent,
  TitleComponent,
  TooltipComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { ComposeOption } from 'echarts/core'
import type { BarSeriesOption, LineSeriesOption, PieSeriesOption } from 'echarts/charts'
import type {
  GridComponentOption,
  LegendComponentOption,
  MarkPointComponentOption,
  TitleComponentOption,
  TooltipComponentOption,
} from 'echarts/components'

echarts.use([
  LineChart,
  BarChart,
  PieChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  MarkPointComponent,
  TitleComponent,
  CanvasRenderer,
])

/** 本工程实际用到的 option 组合（按需注册的对应类型） */
export type EChartsOption = ComposeOption<
  | LineSeriesOption
  | BarSeriesOption
  | PieSeriesOption
  | GridComponentOption
  | TooltipComponentOption
  | LegendComponentOption
  | MarkPointComponentOption
  | TitleComponentOption
>

/** ECharts 实例类型（echarts.init 的返回值；core 导出的具名类型，避免内部类型外泄） */
export type { EChartsType as EChartsInstance } from 'echarts/core'

export { echarts }
