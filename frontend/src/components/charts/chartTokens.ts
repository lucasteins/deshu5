/**
 * 图表主题 token —— 把 CSS 变量（设计稿 §4.3）解析为 ECharts 可用的具体色值。
 *
 * ECharts 不识别 CSS 变量，故在渲染时读取 `:root` 上的 `--c1..8 / --seq-1..5 / --text-* /
 * --line*` 等实际值。暗色副模（data-theme=dark）与演示模式（data-mode=demo）切换后，
 * 需重新读取并重绘——由 `useChartTokens()` 监听 store 变化后刷新。
 *
 * 依据：设计稿 §4.4.7「配色映射 / 网格与轴」。
 */
import { onBeforeUnmount, ref, watch, type Ref } from 'vue'
import { useAppStore } from '@/stores/app'

export interface ChartTokens {
  /** 分类色 c1..c8（按顺序取用） */
  categorical: string[]
  /** 连续色阶 seq-1..5（单色明度阶梯，热力网格） */
  seq: string[]
  accent: string
  accentHover: string
  accentSoft: string
  emberFill: string
  successFill: string
  warningFill: string
  dangerFill: string
  infoFill: string
  text1: string
  text2: string
  text3: string
  text4: string
  textStrong: string
  line: string
  lineSubtle: string
  lineStrong: string
  surface1: string
  surface2: string
  sunken: string
  /** 等宽字族（图表数值 / 刻度标签） */
  fontMono: string
  /** 演示模式字号缩放（--fs-scale：1 / 1.15）——图表内文字最小 11px（演示 12px） */
  fsScale: number
  /** 线宽（--chart-stroke：2 / 3px），§4.4.7 可读性硬约束 */
  strokeWidth: number
}

function cssVar(style: CSSStyleDeclaration, name: string, fallback = ''): string {
  const v = style.getPropertyValue(name).trim()
  return v || fallback
}

function cssNum(style: CSSStyleDeclaration, name: string, fallback: number): number {
  const raw = style.getPropertyValue(name).trim()
  const n = parseFloat(raw)
  return Number.isFinite(n) ? n : fallback
}

/** 读取当前 `<html>` 上的设计 token（跟随明 / 暗 / 演示模式） */
export function readChartTokens(): ChartTokens {
  const style =
    typeof window === 'undefined'
      ? null
      : getComputedStyle(document.documentElement)
  if (!style) {
    // 非浏览器环境（SSR / 单测）兜底：不影响运行期
    return {
      categorical: ['#0f7c86', '#3b82f6', '#f59e0b', '#8b5cf6', '#16a34a', '#e5484d', '#06b6d4', '#a16207'],
      seq: ['#e6f2f3', '#b8dde1', '#6fb3ba', '#2e949e', '#0f7c86'],
      accent: '#0f7c86',
      accentHover: '#0b6a73',
      accentSoft: '#e6f2f3',
      emberFill: '#f59e0b',
      successFill: '#16a34a',
      warningFill: '#f59e0b',
      dangerFill: '#e5484d',
      infoFill: '#3b82f6',
      text1: '#2a3038',
      text2: '#5a6472',
      text3: '#67707c',
      text4: '#a7b0bb',
      textStrong: '#181c22',
      line: '#d8dee5',
      lineSubtle: '#e7ebef',
      lineStrong: '#bfc8d2',
      surface1: '#ffffff',
      surface2: '#fafbfc',
      sunken: '#edf1f4',
      fontMono: 'ui-monospace, Menlo, monospace',
      fsScale: 1,
      strokeWidth: 2,
    }
  }

  const c = (i: number) => cssVar(style, `--c${i}`)
  const s = (i: number) => cssVar(style, `--seq-${i}`)

  return {
    categorical: [1, 2, 3, 4, 5, 6, 7, 8].map(c),
    seq: [1, 2, 3, 4, 5].map(s),
    accent: cssVar(style, '--accent'),
    accentHover: cssVar(style, '--accent-hover', cssVar(style, '--accent')),
    accentSoft: cssVar(style, '--accent-soft'),
    emberFill: cssVar(style, '--ember-fill'),
    successFill: cssVar(style, '--success-fill'),
    warningFill: cssVar(style, '--warning-fill'),
    dangerFill: cssVar(style, '--danger-fill'),
    infoFill: cssVar(style, '--info-fill'),
    text1: cssVar(style, '--text-1'),
    text2: cssVar(style, '--text-2'),
    text3: cssVar(style, '--text-3'),
    text4: cssVar(style, '--text-4'),
    textStrong: cssVar(style, '--text-strong'),
    line: cssVar(style, '--line'),
    lineSubtle: cssVar(style, '--line-subtle'),
    lineStrong: cssVar(style, '--line-strong'),
    surface1: cssVar(style, '--surface-1'),
    surface2: cssVar(style, '--surface-2'),
    sunken: cssVar(style, '--sunken'),
    fontMono: cssVar(style, '--font-mono', 'ui-monospace, Menlo, monospace'),
    fsScale: cssNum(style, '--fs-scale', 1),
    strokeWidth: cssNum(style, '--chart-stroke', 2),
  }
}

/**
 * 响应式 token：随明 / 暗 / 演示模式切换自动刷新。
 * 用 `requestAnimationFrame` 等待 store 把 `data-theme` / `data-mode` 落到 `<html>` 后再读取，
 * 避免读到切换前的旧色值。
 */
export function useChartTokens(): Ref<ChartTokens> {
  const app = useAppStore()
  const tokens = ref(readChartTokens())

  const stop = watch(
    () => [app.theme, app.displayMode],
    () => {
      if (typeof window === 'undefined') return
      requestAnimationFrame(() => {
        tokens.value = readChartTokens()
      })
    },
  )
  onBeforeUnmount(stop)

  return tokens
}
