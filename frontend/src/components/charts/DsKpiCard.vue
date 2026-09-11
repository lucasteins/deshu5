<script setup lang="ts">
/**
 * KPI 卡（设计稿 §4.5.3 视觉亮点 1 / §4.4.7 单值 + 微趋势）
 *
 * 三行结构：规模数字（32px 等宽 + 单位）/ 环比（语义色 + 三角箭头）/ 32px sparkline。
 * 卡片可点击（下钻），否则「它只是一张海报」——hover 显示右上角前往箭头。
 *
 * 环比配色遵循本地惯例：涨（up）→ 红 --danger，跌（down）→ 绿 --success（与 mockup 一致）。
 */
import { computed } from 'vue'
import DsSparkline from './DsSparkline.vue'

const props = withDefaults(
  defineProps<{
    label: string
    value: number | string
    unit?: string
    delta?: { text: string; direction: 'up' | 'down' | 'flat' } | null
    /** 真实微趋势序列（无真实历史序列时不传 —— 不造假） */
    series?: number[]
    /** 主色强调（如执行成功率） */
    accent?: boolean
    clickable?: boolean
    /** 下钻目标说明（title 提示） */
    drillHint?: string
  }>(),
  { clickable: true },
)

const emit = defineEmits<{ click: [] }>()

const display = computed(() =>
  typeof props.value === 'number' ? props.value.toLocaleString('zh-CN') : props.value,
)

const ARROW: Record<'up' | 'down' | 'flat', string> = { up: '↑', down: '↓', flat: '' }

function onClick() {
  if (props.clickable) emit('click')
}
function onKey(e: KeyboardEvent) {
  if (!props.clickable) return
  if (e.key === 'Enter' || e.key === ' ') {
    e.preventDefault()
    emit('click')
  }
}
</script>

<template>
  <div
    class="ds-kpi"
    :class="{ 'is-accent': accent, 'is-clickable': clickable }"
    :role="clickable ? 'button' : undefined"
    :tabindex="clickable ? 0 : undefined"
    :title="clickable ? drillHint : undefined"
    @click="onClick"
    @keydown="onKey"
  >
    <div class="ds-kpi__top">
      <span class="ds-kpi__label">{{ label }}</span>
      <span v-if="clickable" class="ds-kpi__go" aria-hidden="true">
        <svg viewBox="0 0 24 24"><path d="M7 17L17 7M17 7h-7M17 7v7" /></svg>
      </span>
    </div>

    <div class="ds-kpi__val">
      <span class="ds-kpi__num">{{ display }}</span>
      <span v-if="unit" class="ds-kpi__unit">{{ unit }}</span>
      <span
        v-if="delta"
        class="ds-kpi__delta"
        :class="`is-${delta.direction}`"
      >
        <template v-if="ARROW[delta.direction]">{{ ARROW[delta.direction] }}</template>
        {{ delta.text }}
      </span>
    </div>

    <div class="ds-kpi__spark">
      <DsSparkline v-if="series && series.length" :points="series" />
    </div>
  </div>
</template>

<style scoped>
.ds-kpi {
  display: flex;
  flex-direction: column;
  height: 126px;
  padding: 16px 18px 12px;
  background: var(--surface-1);
  border: 1px solid var(--line);
  border-radius: var(--r-md);
  box-shadow: var(--e1);
  transition:
    box-shadow var(--dur-base) var(--ease-standard),
    border-color var(--dur-base) var(--ease-standard),
    transform var(--dur-fast) var(--ease-standard);
}
.ds-kpi.is-clickable {
  cursor: pointer;
}
.ds-kpi.is-clickable:hover {
  border-color: var(--line-strong);
  box-shadow: var(--e2);
  transform: translateY(-1px);
}
.ds-kpi.is-clickable:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring);
}
.ds-kpi.is-accent {
  border-color: var(--accent-line);
}
.ds-kpi.is-accent .ds-kpi__num {
  color: var(--accent);
}

.ds-kpi__top {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
}
.ds-kpi__label {
  font-size: var(--fs-caption);
  color: var(--text-2);
}
.ds-kpi__go {
  margin-left: auto;
  display: inline-flex;
  color: var(--text-4);
  opacity: 0;
  transition: opacity var(--dur-fast) var(--ease-standard);
}
.ds-kpi:hover .ds-kpi__go,
.ds-kpi:focus-visible .ds-kpi__go {
  opacity: 1;
}
.ds-kpi__go svg {
  width: 14px;
  height: 14px;
  fill: none;
  stroke: currentColor;
  stroke-width: 1.8;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.ds-kpi__val {
  display: flex;
  align-items: baseline;
  gap: var(--sp-1);
  margin-top: 8px;
}
.ds-kpi__num {
  font-family: var(--font-mono);
  font-size: var(--fs-num-xl); /* 32 */
  line-height: var(--lh-num-xl); /* 36 */
  font-weight: var(--fw-num-xl);
  letter-spacing: var(--ls-num-xl);
  color: var(--text-strong);
  font-variant-numeric: tabular-nums;
}
.ds-kpi__unit {
  font-size: var(--fs-caption);
  color: var(--text-3);
}
.ds-kpi__delta {
  margin-left: auto;
  font-family: var(--font-mono);
  font-size: var(--fs-caption);
  font-weight: 500;
  white-space: nowrap;
  display: inline-flex;
  align-items: center;
  gap: 3px;
}
/* 涨红跌绿（本地惯例，与 mockup 一致） */
.ds-kpi__delta.is-up {
  color: var(--danger);
}
.ds-kpi__delta.is-down {
  color: var(--success);
}
.ds-kpi__delta.is-flat {
  color: var(--text-3);
}

.ds-kpi__spark {
  margin-top: auto;
  height: 32px;
  color: var(--c1);
}
</style>
