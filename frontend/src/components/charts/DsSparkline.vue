<script setup lang="ts">
/**
 * Sparkline（设计稿 §4.4.7：单值 + 微趋势，KPI 卡底部）
 * 无坐标轴、无网格、无交互——只表达趋势形状；高 32px。
 * 纯 SVG 实现（不引 ECharts 实例，避免 KPI 行开 5 个 canvas）。
 */
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    points: number[]
    /** 线色；默认 currentColor（由父级用 token 变量着色） */
    stroke?: string
    width?: number
    height?: number
  }>(),
  { stroke: 'currentColor', width: 120, height: 32 },
)

/** 线宽 2，留出上下各 2px 边距避免裁切 */
const PAD = 2

const geometry = computed(() => {
  const pts = props.points.filter((n) => Number.isFinite(n))
  const w = props.width
  const h = props.height
  if (pts.length === 0) return null
  const min = Math.min(...pts)
  const max = Math.max(...pts)
  const span = max - min
  const innerH = h - PAD * 2
  const step = pts.length > 1 ? w / (pts.length - 1) : 0
  const coords = pts.map((v, i) => {
    const x = pts.length > 1 ? i * step : w / 2
    // span=0（序列恒定）时画中线，避免除零
    const ratio = span === 0 ? 0.5 : (v - min) / span
    const y = PAD + (1 - ratio) * innerH
    return `${round(x)},${round(y)}`
  })
  return { polyline: coords.join(' ') }
})

function round(n: number): number {
  return Math.round(n * 100) / 100
}
</script>

<template>
  <svg
    v-if="geometry"
    class="ds-spark"
    :viewBox="`0 0 ${width} ${height}`"
    :width="width"
    :height="height"
    preserveAspectRatio="none"
    aria-hidden="true"
  >
    <polyline
      :points="geometry.polyline"
      fill="none"
      :stroke="stroke"
      stroke-width="2"
      stroke-linecap="round"
      stroke-linejoin="round"
      vector-effect="non-scaling-stroke"
    />
  </svg>
</template>

<style scoped>
.ds-spark {
  display: block;
  width: 100%;
  height: 100%;
  overflow: visible;
}
</style>
