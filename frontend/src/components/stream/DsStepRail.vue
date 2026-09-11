<script setup lang="ts">
/**
 * 流程轨 DsStepRail（设计稿 §4.4.4 / §4.5.1「生成流程」）。
 *
 * 通用竖向阶段条：5 个节点从空心圆逐个变为实心青环，running 节点脉冲 + 秒表。
 * 状态机由父组件驱动（本组件纯展示）：steps 数组的 status 决定视觉，ms 决定耗时。
 * 节点标题可点击展开/收起 detail（详情）。
 *
 * 用法：
 *   <DsStepRail :steps="steps" :elapsed-ms="elapsedMs" @select="onSelectStep" />
 */
import { computed, ref } from 'vue'

export type StreamStepStatus = 'pending' | 'running' | 'done' | 'failed' | 'skipped'

export interface StreamStep {
  key: string
  title: string
  /** 子步骤说明（pending/running 时展示的具体动作文案） */
  desc?: string
  status: StreamStepStatus
  /** 阶段耗时（ms）；done 时显示，等宽 */
  ms?: number
  /** 点击标题展开的详情（纯文本，多行用 \n 分隔） */
  detail?: string
  /** failed 时的失败原因 */
  error?: string
}

const props = defineProps<{
  steps: StreamStep[]
  /** 当前已耗时（ms），running 节点实时显示秒表 */
  elapsedMs?: number
}>()

const emit = defineEmits<{ select: [step: StreamStep] }>()

const expanded = ref<Record<string, boolean>>({})

function toggle(step: StreamStep) {
  expanded.value[step.key] = !expanded.value[step.key]
}

function isExpanded(step: StreamStep): boolean {
  return !!expanded.value[step.key]
}

function fmtMs(ms: number | undefined): string {
  if (ms == null) return '—'
  return ms >= 1000 ? `${(ms / 1000).toFixed(2)}s` : `${Math.round(ms)}ms`
}

/** running 节点的秒表文案（elapsedMs 驱动） */
function runningText(): string {
  const ms = props.elapsedMs ?? 0
  return `${(ms / 1000).toFixed(1)}s`
}

function timeText(step: StreamStep): string {
  if (step.status === 'running') return runningText()
  if (step.status === 'done') return fmtMs(step.ms)
  if (step.status === 'failed') return '失败'
  if (step.status === 'skipped') return '跳过'
  return '—'
}

function descText(step: StreamStep): string {
  if (step.status === 'failed' && step.error) return step.error
  return step.desc || ''
}

const detailLines = computed(() => {
  const map: Record<string, string[]> = {}
  for (const s of props.steps) {
    map[s.key] = s.detail ? s.detail.split('\n') : []
  }
  return map
})
</script>

<template>
  <div class="steprail">
    <div
      v-for="(step, i) in steps"
      :key="step.key"
      class="step"
      :class="`is-${step.status}`"
    >
      <div class="rail-line" />
      <div class="dot">
        <svg v-if="step.status === 'done'" viewBox="0 0 24 24">
          <path d="M5 12.5l4 4L19 7" />
        </svg>
        <svg v-else-if="step.status === 'failed'" viewBox="0 0 24 24">
          <path d="M6 6l12 12M18 6L6 18" />
        </svg>
        <svg v-else-if="step.status === 'skipped'" viewBox="0 0 24 24">
          <path d="M6 12h12" />
        </svg>
        <span v-else>{{ i + 1 }}</span>
      </div>
      <div class="step-body">
        <div class="step-title" role="button" tabindex="0" @click="toggle(step)" @keyup.enter="toggle(step)">
          <b>{{ step.title }}</b>
          <span class="step-time tabular">{{ timeText(step) }}</span>
        </div>
        <div v-if="descText(step)" class="step-desc">{{ descText(step) }}</div>
        <div v-if="step.detail && isExpanded(step)" class="step-detail">
          <div v-for="(line, li) in detailLines[step.key]" :key="li">{{ line }}</div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.steprail {
  padding: 6px 0 4px;
}
.step {
  position: relative;
  display: flex;
  gap: 12px;
  padding: 0 16px 18px;
}
.step:last-child {
  padding-bottom: 6px;
}
.rail-line {
  position: absolute;
  left: 25px;
  top: 22px;
  bottom: 0;
  width: 1px;
  background: var(--line);
  transition: background-color var(--dur-base) var(--ease-standard);
}
.step:last-child .rail-line {
  display: none;
}
.step.is-done .rail-line {
  background: var(--accent);
}
.dot {
  position: relative;
  z-index: 1;
  flex: none;
  width: 19px;
  height: 19px;
  margin-top: 2px;
  border-radius: var(--r-full);
  border: 1.5px solid var(--line-strong);
  background: var(--surface-1);
  display: grid;
  place-items: center;
  font-family: var(--font-mono);
  font-size: 10.5px;
  color: var(--text-4);
  transition: all var(--dur-base) var(--ease-standard);
}
.dot svg {
  width: 11px;
  height: 11px;
  stroke: currentColor;
  stroke-width: 2.4;
  fill: none;
  stroke-linecap: round;
  stroke-linejoin: round;
}
.step.is-done .dot {
  background: var(--accent);
  border-color: var(--accent);
  color: var(--text-on-accent);
}
.step.is-running .dot {
  border-color: var(--accent);
  color: var(--accent);
  border-width: 2px;
}
.step.is-running .dot::after {
  content: '';
  position: absolute;
  inset: -5px;
  border-radius: var(--r-full);
  border: 1.5px solid var(--accent);
  opacity: 0;
  animation: ds-pulse var(--dur-pulse) var(--ease-out);
}
.step.is-failed .dot {
  background: var(--danger-fill);
  border-color: var(--danger-fill);
  color: #fff;
}
.step.is-skipped .dot {
  border-style: dashed;
  color: var(--text-4);
}
@keyframes ds-pulse {
  0% {
    opacity: 0.55;
    transform: scale(0.72);
  }
  100% {
    opacity: 0;
    transform: scale(1.28);
  }
}
.step-body {
  flex: 1;
  min-width: 0;
  padding-top: 1px;
}
.step-title {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
}
.step-title b {
  font-size: 13.5px;
  font-weight: 600;
  color: var(--text-2);
  transition: color var(--dur-base);
}
.step.is-done .step-title b,
.step.is-running .step-title b {
  color: var(--text-strong);
}
.step-title:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring);
  border-radius: var(--r-xs);
}
.step-time {
  margin-left: auto;
  font-family: var(--font-mono);
  font-size: 11.5px;
  color: var(--text-4);
  white-space: nowrap;
  transition: color var(--dur-base);
}
.step.is-done .step-time {
  color: var(--text-3);
}
.step.is-running .step-time {
  color: var(--accent);
}
.step.is-failed .step-time {
  color: var(--danger);
}
.step-desc {
  font-size: 12.5px;
  color: var(--text-3);
  margin-top: 3px;
  line-height: 18px;
}
.step-detail {
  margin-top: 8px;
  border: 1px solid var(--line-subtle);
  border-radius: var(--r-sm);
  background: var(--surface-2);
  padding: 8px 10px;
  font-size: 12px;
  color: var(--text-2);
  line-height: 18px;
}
</style>
