/**
 * ECharts option 构建器（设计稿 §4.4.7 图表规范）
 *
 * 通用约定（全站统一，避免各页各写一份）：
 * - 只保留**水平**网格线：1px、dasharray 2 4、--line-subtle；无垂直网格
 * - y 轴不画轴线，刻度 11px --text-3 右对齐；x 轴 1px 实线 --line
 * - 线宽 ≥2px（演示模式 3px，取 --chart-stroke）；文字最小 11px（演示 12px）
 * - tooltip：跟随指针、偏移 12、max-width 280、300ms 延迟出现、移动即时更新
 * - 数据标签默认关闭（横向条形除外——排名必须可读）
 * - 禁用：3D / gauge / 双 Y 轴 / >3 类饼图 / 无排序条形
 */
import type { EChartsOption } from './echarts'
import type { BarSeriesOption, LineSeriesOption, PieSeriesOption } from 'echarts/charts'
import { echarts } from './echarts'
import type { ChartTokens } from './chartTokens'

/* ---------- 工具 ---------- */

/** #rgb / #rrggbb → rgba(...)；非 hex（如暗模 rgba 值）原样返回 */
export function withAlpha(color: string, alpha: number): string {
  const hex = color.trim()
  const m3 = /^#([0-9a-f]{3})$/i.exec(hex)
  const m6 = /^#([0-9a-f]{6})$/i.exec(hex)
  let r: number
  let g: number
  let b: number
  if (m6) {
    r = parseInt(m6[1].slice(0, 2), 16)
    g = parseInt(m6[1].slice(2, 4), 16)
    b = parseInt(m6[1].slice(4, 6), 16)
  } else if (m3) {
    r = parseInt(m3[1][0] + m3[1][0], 16)
    g = parseInt(m3[1][1] + m3[1][1], 16)
    b = parseInt(m3[1][2] + m3[1][2], 16)
  } else {
    return hex
  }
  return `rgba(${r}, ${g}, ${b}, ${alpha})`
}

function chartFont(t: ChartTokens): number {
  return Math.max(11, Math.round(11 * t.fsScale))
}

/**
 * tooltip 公共外观（§4.4.7 Tooltip）
 *
 * 关于「300ms 延迟出现」：设计稿要求延迟出现以避免快速划过抖动，同时「移动时 0ms 更新」。
 * 但 ECharts 的 `showDelay` 由 `_showOrMove` 统一承担，**位置更新同样被延迟**——设 300ms 会让
 * tooltip 滞后跟随，与「移动时即时更新」冲突。故取 0，抖动抑制交给 ECharts 自身（transitionDuration）。
 * 此为有据偏差，已记入 02-交接日志。
 */
function tooltipBase(t: ChartTokens, fs: number) {
  return {
    showDelay: 0,
    borderWidth: 1,
    borderColor: t.line,
    backgroundColor: t.surface1,
    padding: [8, 10] as [number, number],
    extraCssText: 'border-radius:8px;box-shadow:0 8px 24px rgba(16,24,40,.10);max-width:280px;',
    textStyle: { color: t.text1, fontSize: fs },
  }
}

/** 自定义 tooltip HTML（跨系列共享：一条竖线 + 所有系列值） */
interface TipItem {
  seriesName: string
  color: string
  value: number | string
  name?: string
  axisValueLabel?: string
  percent?: number
  marker?: string
}

function tipHtml(
  head: string,
  items: TipItem[],
  opts: { showPercent?: boolean; unit?: string } = {},
): string {
  const rows = items
    .map((it) => {
      const value =
        typeof it.value === 'number' ? it.value.toLocaleString('zh-CN') : String(it.value)
      const pct =
        opts.showPercent && typeof it.percent === 'number'
          ? `<span class="d5tip__p">${it.percent.toFixed(1)}%</span>`
          : ''
      return (
        `<div class="d5tip__row">` +
        `<span class="d5tip__sw" style="background:${it.color}"></span>` +
        `<span class="d5tip__n">${it.seriesName}</span>` +
        `<span class="d5tip__v">${value}${opts.unit ?? ''}</span>${pct}` +
        `</div>`
      )
    })
    .join('')
  return `<div class="d5tip">${head ? `<div class="d5tip__t">${head}</div>` : ''}${rows}</div>`
}

/* ---------- 折线（时间趋势，1–3 条同轴） ---------- */

export interface LineSeriesSpec {
  name: string
  data: number[]
  color: string
  /** 面积渐变（仅首系列） */
  area?: boolean
  /** 峰值直接标注（§4.5.3：峰值点带标注 + 主色小圆点） */
  markPeak?: boolean
  /** 峰值标注后缀（如「次」） */
  peakSuffix?: string
}

export function buildLineOption(
  t: ChartTokens,
  spec: { categories: string[]; series: LineSeriesSpec[]; unit?: string },
): EChartsOption {
  const fs = chartFont(t)
  const series: LineSeriesOption[] = spec.series.map((s) => ({
    name: s.name,
    type: 'line',
    smooth: false,
    symbol: 'circle',
    symbolSize: 6,
    showSymbol: spec.categories.length <= 16,
    lineStyle: { width: t.strokeWidth, color: s.color },
    itemStyle: { color: s.color, borderColor: t.surface1, borderWidth: 2 },
    areaStyle: s.area
      ? {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: withAlpha(s.color, 0.16) },
            { offset: 1, color: withAlpha(s.color, 0) },
          ]),
        }
      : undefined,
    emphasis: { focus: 'series' },
    markPoint: s.markPeak
      ? {
          symbol: 'circle',
          symbolSize: 7,
          itemStyle: { color: s.color },
          label: {
            show: true,
            position: 'top',
            distance: 8,
            formatter: `{c}${s.peakSuffix ?? ''}`,
            color: s.color,
            fontSize: fs,
            fontFamily: t.fontMono,
            fontWeight: 500,
            backgroundColor: withAlpha(s.color, 0.12),
            padding: [3, 7],
            borderRadius: 4,
          },
          data: [{ type: 'max', name: '峰值' }],
        }
      : undefined,
    data: s.data,
  }))

  return {
    animationDuration: 400,
    animationEasing: 'cubicOut',
    grid: { left: 4, right: 16, top: 36, bottom: 2, containLabel: true },
    tooltip: {
      ...tooltipBase(t, fs),
      trigger: 'axis',
      axisPointer: {
        type: 'line',
        lineStyle: { color: t.lineStrong, width: 1, type: [3, 3] },
      },
      formatter: (params: unknown) => {
        const list = (Array.isArray(params) ? params : [params]) as TipItem[]
        const head = list[0]?.axisValueLabel ?? list[0]?.name ?? ''
        return tipHtml(head, list, { unit: spec.unit ?? '' })
      },
    },
    xAxis: {
      type: 'category',
      boundaryGap: false,
      data: spec.categories,
      axisLine: { lineStyle: { color: t.line, width: 1 } },
      axisTick: { show: false },
      axisLabel: { color: t.text3, fontSize: fs, fontFamily: t.fontMono, margin: 10 },
    },
    yAxis: {
      type: 'value',
      axisLine: { show: false },
      axisTick: { show: false },
      splitLine: { lineStyle: { color: t.lineSubtle, width: 1, type: [2, 4] } },
      axisLabel: {
        color: t.text3,
        fontSize: fs,
        fontFamily: t.fontMono,
        align: 'right',
        margin: 8,
      },
    },
    series,
  }
}

/* ---------- 环形（占比 ≤5 类，内径 62% + 中心总数） ---------- */

export interface DonutSlice {
  name: string
  value: number
  color: string
  /** 图例隐藏后置灰（保留占比口径，不重算） */
  dimmed?: boolean
}

export function buildDonutOption(
  t: ChartTokens,
  spec: { slices: DonutSlice[]; unit?: string },
): EChartsOption {
  const fs = chartFont(t)
  const slices: PieSeriesOption[] = [
    {
      type: 'pie',
      radius: ['62%', '84%'],
      center: ['50%', '50%'],
      avoidLabelOverlap: false,
      label: { show: false },
      labelLine: { show: false },
      itemStyle: { borderColor: t.surface1, borderWidth: 2, borderRadius: 2 },
      emphasis: {
        scale: true,
        scaleSize: 4,
        itemStyle: { shadowBlur: 10, shadowColor: 'rgba(16,24,40,0.18)' },
      },
      data: spec.slices.map((s) => ({
        name: s.name,
        value: s.value,
        itemStyle: { color: s.color, opacity: s.dimmed ? 0.25 : 1 },
      })),
    },
  ]

  return {
    animationDuration: 400,
    animationEasing: 'cubicOut',
    tooltip: {
      ...tooltipBase(t, fs),
      trigger: 'item',
      formatter: (params: unknown) => {
        const p = params as TipItem
        return tipHtml(p.name ?? '', [{ ...p, seriesName: p.name ?? '' }], {
          showPercent: true,
          unit: spec.unit ?? '',
        })
      },
    },
    series: slices,
  }
}

/* ---------- 横向条形（排名 ≤12 项，必须排序） ---------- */

export interface BarRow {
  name: string
  value: number
}

export function buildBarOption(
  t: ChartTokens,
  spec: { rows: BarRow[]; color?: string; unit?: string },
): EChartsOption {
  const fs = chartFont(t)
  const color = spec.color ?? t.accent
  // 类别轴自下而上：升序排列使最大值落在顶部（排名必须排序）
  const rows = [...spec.rows].sort((a, b) => a.value - b.value)

  const series: BarSeriesOption[] = [
    {
      type: 'bar',
      barWidth: 18,
      showBackground: true,
      backgroundStyle: { color: t.sunken, borderRadius: 3 },
      itemStyle: { color, borderRadius: 3 },
      emphasis: { itemStyle: { color: t.accentHover } },
      label: {
        show: true,
        position: 'right',
        distance: 8,
        color: t.text2,
        fontSize: fs,
        fontFamily: t.fontMono,
        formatter: '{c}',
      },
      data: rows.map((r) => r.value),
    },
  ]

  return {
    animationDuration: 400,
    animationEasing: 'cubicOut',
    grid: { left: 0, right: 56, top: 4, bottom: 4, containLabel: true },
    tooltip: {
      ...tooltipBase(t, fs),
      trigger: 'item',
      formatter: (params: unknown) => {
        const p = params as TipItem
        return tipHtml('', [{ ...p, seriesName: p.name ?? '' }], { unit: spec.unit ?? '' })
      },
    },
    xAxis: { type: 'value', show: false },
    yAxis: {
      type: 'category',
      data: rows.map((r) => r.name),
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: {
        color: t.text1,
        fontSize: fs,
        width: 96,
        overflow: 'truncate',
      },
    },
    series,
  }
}
