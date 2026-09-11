<script setup lang="ts">
/**
 * 通用 ECharts 宿主（设计稿 §4.4.7）—— 负责实例生命周期、尺寸自适应与图表内空态。
 *
 * - 页面级「加载 / 空 / 错」三态由 DsAsyncSection 统一接管；本组件只处理
 *   **图表内空态**（§4.4.7「图表内的状态」：网格 + 居中说明）
 * - option 由父级以 computed 传入（依赖 useChartTokens() → 明暗 / 演示模式切换自动重绘）
 */
import { computed, ref, watch } from 'vue'
import type { EChartsOption } from './echarts'
import { useECharts } from './useECharts'

const props = withDefaults(
  defineProps<{
    option: EChartsOption
    /** 图表区高度（px）；默认 280（§4.5.3 折线 / 环形高 280） */
    height?: number
    /** 图表内空态（数据为空时置 true） */
    empty?: boolean
    emptyText?: string
    /** 无坐标轴类的图表（环形 / 条形）不需要网格底纹 */
    emptyPlain?: boolean
    ariaLabel?: string
  }>(),
  { height: 280, emptyText: '暂无数据', emptyPlain: false },
)

const el = ref<HTMLElement | null>(null)
const { render } = useECharts(el, () => props.option)

watch(() => props.option, () => render())

const boxStyle = computed(() => ({ height: `${props.height}px` }))
</script>

<template>
  <div class="ds-chart" :style="boxStyle">
    <div v-if="empty" class="ds-chart__empty">
      <div v-if="!emptyPlain" class="ds-chart__grid" aria-hidden="true" />
      <p class="ds-chart__empty-text">{{ emptyText }}</p>
    </div>
    <div
      v-else
      ref="el"
      class="ds-chart__canvas"
      role="img"
      :aria-label="ariaLabel"
    />
  </div>
</template>

<style scoped>
.ds-chart {
  position: relative;
  width: 100%;
}
.ds-chart__canvas {
  width: 100%;
  height: 100%;
}
.ds-chart__empty {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  overflow: hidden;
}
/* 空态网格底纹（§4.4.7：空态＝网格 + 居中说明） */
.ds-chart__grid {
  position: absolute;
  inset: 0;
  background-image: repeating-linear-gradient(
    to bottom,
    var(--line-subtle) 0,
    var(--line-subtle) 1px,
    transparent 1px,
    transparent 25%
  );
  opacity: 0.9;
}
.ds-chart__empty-text {
  position: relative;
  margin: 0;
  padding: var(--sp-1) var(--sp-3);
  background: var(--surface-1);
  border: 1px solid var(--line-subtle);
  border-radius: var(--r-sm);
  font-size: var(--fs-caption);
  color: var(--text-3);
}
</style>
