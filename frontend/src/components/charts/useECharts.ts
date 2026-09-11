/**
 * ECharts 实例生命周期 composable（F1.2 图表域）
 *
 * 职责（只做三件事）：
 *   1. 挂载时 init（canvas 渲染器）；卸载时 dispose；容器后置挂载（v-if 切换）时自动补建
 *   2. 尺寸自适应：ResizeObserver **跳过 observe 的首次回调**，仅在容器尺寸真正变化时 resize
 *      （observe 会立即触发一次，若在首次 setOption 的构建期内 resize，会让 ECharts 在
 *       坐标系尚未建立时重跑布局，导致 "Cannot read properties of undefined" 报错）
 *   3. 暴露 render()，由宿主在 option 变化时调用（notMerge，避免旧系列残留）
 *
 * 明暗 / 演示模式的取色由 `useChartTokens()` + option 派生负责，本 composable 不感知主题。
 */
import { nextTick, onBeforeUnmount, onMounted, ref, watch, type Ref } from 'vue'
import { echarts, type EChartsInstance, type EChartsOption } from './echarts'

export function useECharts(
  target: Ref<HTMLElement | null>,
  optionFactory: () => EChartsOption,
): { render: () => void } {
  const chart = ref<EChartsInstance | null>(null)
  let observer: ResizeObserver | null = null
  let lastSize = { w: 0, h: 0 }

  function render() {
    const instance = chart.value
    if (!instance) return
    instance.setOption(optionFactory(), true)
  }

  function observe() {
    if (observer || !target.value || typeof ResizeObserver === 'undefined') return
    lastSize = { w: 0, h: 0 }
    observer = new ResizeObserver((entries) => {
      const rect = entries[0]?.contentRect
      if (!rect) return
      const w = Math.round(rect.width)
      const h = Math.round(rect.height)
      // 首次回调（observe 立即触发）只记录尺寸，不 resize
      if (lastSize.w === 0 && lastSize.h === 0) {
        lastSize = { w, h }
        return
      }
      if (w === lastSize.w && h === lastSize.h) return
      lastSize = { w, h }
      chart.value?.resize()
    })
    observer.observe(target.value)
  }

  function ensureInit() {
    if (chart.value || !target.value) return
    chart.value = echarts.init(target.value, undefined, { renderer: 'canvas' })
    render()
    observe()
  }

  onMounted(ensureInit)

  // 容器可能因空态 / 加载态切换而 v-if 重建：target 变为可用时补建实例
  watch(target, () => {
    void nextTick(() => {
      if (!chart.value && target.value) ensureInit()
    })
  })

  onBeforeUnmount(() => {
    observer?.disconnect()
    observer = null
    chart.value?.dispose()
    chart.value = null
  })

  return { render }
}
