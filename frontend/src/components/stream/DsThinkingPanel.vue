<script setup lang="ts">
/**
 * 思考窗口 DsThinkingPanel（设计稿 §4.5.1「思考过程」）。
 *
 * 展示「多步推理」的流水线细节：左侧标签 + 正文 + 可选 chips / 子行，
 * 底部可选一条逐字流（LLM reasoning/output）。默认折叠为摘要行（父组件控制）。
 * 纯展示，数据由父组件经 thinking.ts 映射后传入。
 *
 * 用法：
 *   <DsThinkingPanel :entries="entries" :streams="streams" />
 */
import type { ThinkingEntry, ThinkingStream } from './thinking'

defineProps<{
  /** 离散思考条目（按时间顺序） */
  entries: ThinkingEntry[]
  /** 逐字流（reasoning / output 各一条，累积文本） */
  streams?: ThinkingStream[]
  /** 摘要行数字（「6 表 / 14 码值 / LLM 3 次」），父组件折叠态展示 */
  summary?: { label: string; value: string | number }[]
}>()
</script>

<template>
  <div class="think">
    <div v-if="summary && summary.length" class="think-summary">
      <span v-for="(s, i) in summary" :key="i" class="think-summary__item">
        {{ s.label }} <b class="tabular">{{ s.value }}</b>
      </span>
    </div>

    <div v-if="!entries.length && !(streams && streams.length)" class="think-empty">
      暂无思考过程
    </div>

    <div class="think-list">
      <div
        v-for="(e, i) in entries"
        :key="i"
        class="think-row"
        :class="`tone-${e.tone || 'normal'}`"
      >
        <span class="think-tag">{{ e.tag }}</span>
        <div class="think-body">
          <div class="think-text">{{ e.body }}</div>
          <div v-if="e.chips && e.chips.length" class="think-chips">
            <span v-for="(c, ci) in e.chips" :key="ci" class="think-chip">{{ c }}</span>
          </div>
          <div v-for="(s, si) in e.sub || []" :key="si" class="think-sub">{{ s }}</div>
        </div>
      </div>

      <!-- 逐字流：reasoning / output 各一条 -->
      <div
        v-for="s in streams || []"
        :key="s.phase"
        class="think-row think-stream"
      >
        <span class="think-tag">{{ s.phase === 'reasoning' ? 'LLM 思考' : 'LLM 输出' }}</span>
        <div class="think-body">
          <div class="think-text think-text--mono">{{ s.text }}<span class="caret" /></div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.think {
  display: flex;
  flex-direction: column;
}
.think-summary {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  padding: 8px 0;
}
.think-summary__item {
  font-size: 12.5px;
  color: var(--text-3);
}
.think-summary__item b {
  font-family: var(--font-mono);
  font-size: 14px;
  font-weight: 600;
  color: var(--text-strong);
}
.think-empty {
  padding: var(--sp-4) 0;
  font-size: var(--fs-body-sm);
  color: var(--text-3);
}
.think-list {
  display: flex;
  flex-direction: column;
}
.think-row {
  display: flex;
  gap: 10px;
  padding: 8px 0;
  border-bottom: 1px solid var(--line-subtle);
  font-size: 13px;
}
.think-row:last-child {
  border-bottom: none;
}
.think-tag {
  flex: none;
  width: 64px;
  color: var(--text-3);
  font-size: 12.5px;
}
.think-body {
  flex: 1;
  min-width: 0;
}
.think-text {
  color: var(--text-1);
  line-height: 20px;
  white-space: pre-wrap;
  word-break: break-word;
}
.think-text--mono {
  font-family: var(--font-mono);
  font-size: 12.5px;
  color: var(--text-2);
}
.think-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 6px;
}
.think-chip {
  padding: 1px 8px;
  border-radius: var(--r-full);
  background: var(--sunken);
  border: 1px solid var(--line-subtle);
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text-2);
}
.think-sub {
  margin-top: 4px;
  font-size: 12.5px;
  color: var(--text-3);
}
/* 语义色（tone） */
.tone-ok .think-text {
  color: var(--success);
}
.tone-err .think-text {
  color: var(--danger);
}
.tone-dim .think-text {
  color: var(--text-3);
}
.tone-warn .think-text {
  color: var(--warning);
}
/* 打字机光标（§4.4.4） */
.caret {
  display: inline-block;
  width: 2px;
  height: 14px;
  background: var(--accent);
  vertical-align: -2px;
  margin-left: 1px;
  animation: ds-blink 1000ms step-end infinite;
}
@keyframes ds-blink {
  0%,
  49% {
    opacity: 1;
  }
  50%,
  100% {
    opacity: 0;
  }
}
</style>
