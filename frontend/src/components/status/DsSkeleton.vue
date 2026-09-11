<script setup lang="ts">
/**
 * 骨架屏（设计稿 §4.4.3）——必须与真实布局同构：相同的列数、相同的行高、相同的卡片结构。
 * 材质：基底 --sunken；高光 linear-gradient(90deg, transparent, rgba(255,255,255,.72), transparent)，
 * 1600ms linear 无限位移；暗色副模高光 rgba(255,255,255,.06)。
 *
 * variant：text（文字行组）/ table（表头 + N 行 × M 列）/ cards / chart
 */
import { computed } from 'vue'

const props = defineProps<{
  variant?: 'text' | 'table' | 'cards' | 'chart'
  /** 行数（表格默认 5，其余默认 3） */
  rows?: number
  /** 表格列数（默认 5） */
  cols?: number
}>()

const variant = computed(() => props.variant ?? 'text')
const rowCount = computed(() => props.rows ?? (variant.value === 'table' ? 5 : 3))
const colCount = computed(() => props.cols ?? 5)

const gridStyle = computed(() => ({ gridTemplateColumns: `repeat(${colCount.value}, 1fr)` }))

const TEXT_WIDTHS = ['100%', '92%', '78%']
function textWidth(i: number) {
  if (i === rowCount.value) return '60%' // 末行收短，模拟段落
  return TEXT_WIDTHS[(i - 1) % TEXT_WIDTHS.length] ?? '100%'
}
</script>

<template>
  <div class="ds-skeleton" :class="`ds-skeleton--${variant}`" aria-hidden="true">
    <template v-if="variant === 'text'">
      <div
        v-for="i in rowCount"
        :key="i"
        class="ds-skeleton__bar ds-skeleton__bar--text"
        :style="{ width: textWidth(i) }"
      />
    </template>

    <template v-else-if="variant === 'table'">
      <div class="ds-skeleton__tr ds-skeleton__tr--head" :style="gridStyle">
        <div v-for="c in colCount" :key="c" class="ds-skeleton__bar ds-skeleton__bar--cell" />
      </div>
      <div v-for="r in rowCount" :key="r" class="ds-skeleton__tr" :style="gridStyle">
        <div v-for="c in colCount" :key="c" class="ds-skeleton__bar ds-skeleton__bar--cell" />
      </div>
    </template>

    <template v-else-if="variant === 'cards'">
      <div v-for="i in rowCount" :key="i" class="ds-skeleton__card">
        <div class="ds-skeleton__bar ds-skeleton__bar--title" />
        <div class="ds-skeleton__bar ds-skeleton__bar--text" style="width: 82%" />
        <div class="ds-skeleton__bar ds-skeleton__bar--text" style="width: 64%" />
      </div>
    </template>

    <template v-else>
      <div class="ds-skeleton__bar ds-skeleton__bar--chart" />
      <div class="ds-skeleton__legend">
        <div
          v-for="i in 4"
          :key="i"
          class="ds-skeleton__bar ds-skeleton__bar--legend"
          :style="{ width: `${76 - i * 12}px` }"
        />
      </div>
    </template>
  </div>
</template>

<style scoped>
.ds-skeleton {
  display: flex;
  flex-direction: column;
  gap: var(--sp-3);
}

/* 基底 + shimmer（所有占位条共用） */
.ds-skeleton__bar {
  position: relative;
  overflow: hidden;
  background: var(--sunken);
  border-radius: var(--r-xs);
}
.ds-skeleton__bar::after {
  content: '';
  position: absolute;
  inset: 0;
  transform: translateX(-100%);
  background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.72), transparent);
  animation: ds-shimmer var(--dur-shimmer) linear infinite;
}
[data-theme='dark'] .ds-skeleton__bar::after {
  background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.06), transparent);
}
@keyframes ds-shimmer {
  to {
    transform: translateX(100%);
  }
}

/* text */
.ds-skeleton__bar--text {
  height: 14px;
  border-radius: var(--r-sm);
}

/* table（与真实表格同构：表头 40px / 行高 --table-row-h / 1px 行线） */
.ds-skeleton--table {
  gap: 0;
}
.ds-skeleton__tr {
  display: grid;
  gap: var(--sp-4);
  align-items: center;
  height: var(--table-row-h);
  border-bottom: 1px solid var(--line-subtle);
}
.ds-skeleton__tr--head {
  height: 40px;
  border-bottom: 1px solid var(--line);
}
.ds-skeleton__bar--cell {
  height: 12px;
}

/* cards */
.ds-skeleton__card {
  padding: var(--sp-4);
  background: var(--surface-1);
  border: 1px solid var(--line);
  border-radius: var(--r-md);
  display: flex;
  flex-direction: column;
  gap: var(--sp-3);
}
.ds-skeleton__bar--title {
  height: 14px;
  width: 40%;
}

/* chart */
.ds-skeleton__bar--chart {
  height: 220px;
  border-radius: var(--r-md);
}
.ds-skeleton__legend {
  display: flex;
  gap: var(--sp-4);
}
.ds-skeleton__bar--legend {
  height: 12px;
  border-radius: var(--r-full);
}

@media (prefers-reduced-motion: reduce) {
  .ds-skeleton__bar::after {
    animation: none;
    opacity: 0.5;
  }
}
</style>
