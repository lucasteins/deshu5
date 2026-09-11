<script setup lang="ts">
/**
 * SQL 查看器 DsSqlViewer（设计稿 §4.4.4 / §4.5.1「SQL 块」）。
 *
 * - token 级着色（sqlHighlight.ts）：关键字 --accent、表名 --c1、字段 --text-1、
 *   字符串 --ember、注释 --text-4、函数 --c4、数字 --c2
 * - 打字机逐行揭示（可关闭）；行号栏；右上角复制
 *
 * 用法：
 *   <DsSqlViewer :sql="result.sql" :typing="true" @done="onRevealDone" />
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { escapeHtml, highlightSql } from './sqlHighlight'
import { toast } from '@/components'

const props = withDefaults(
  defineProps<{
    sql: string
    /** 是否打字机揭示；false 直接全量渲染 */
    typing?: boolean
    /** 揭示节奏（字符/秒，仅 typing=true 时生效；默认自适应） */
    speed?: number
    /** 是否显示复制按钮 */
    copyable?: boolean
  }>(),
  { typing: true, copyable: true },
)

const emit = defineEmits<{ done: [] }>()

const reducedMotion =
  typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches

const revealed = ref(0)
let timer: ReturnType<typeof setInterval> | undefined

const lines = computed(() => props.sql.split('\n'))
const total = computed(() => props.sql.length)

/** 每行在完整 SQL 串中的起始字符位（含 \n 占位） */
const lineStarts = computed(() => {
  const starts: number[] = []
  let acc = 0
  for (const line of lines.value) {
    starts.push(acc)
    acc += line.length + 1
  }
  return starts
})

/** 每行完整着色 HTML（预计算，避免打字过程中反复重着色） */
const lineHtml = computed(() => lines.value.map((l) => highlightSql(l)))
const lineEscaped = computed(() => lines.value.map((l) => escapeHtml(l)))

/** 当前已揭示的行数（含部分揭示） */
const visibleLineCount = computed(() => {
  let n = 0
  for (let i = 0; i < lineStarts.value.length; i++) {
    if (revealed.value > lineStarts.value[i]) n++
  }
  return n
})

const displayHtml = computed(() => {
  let out = ''
  const n = lines.value.length
  for (let i = 0; i < n; i++) {
    const start = lineStarts.value[i]
    const len = lines.value[i].length
    if (revealed.value >= start + len) {
      out += `<div class="code-line">${lineHtml.value[i] || '&nbsp;'}</div>`
    } else if (revealed.value > start) {
      const partial = lineEscaped.value[i].slice(0, revealed.value - start)
      out += `<div class="code-line">${partial}<span class="caret"></span></div>`
      break
    } else {
      break
    }
  }
  return out
})

const isTyping = computed(() => revealed.value < total.value)

function stopTimer() {
  if (timer) clearInterval(timer)
  timer = undefined
}

function startTyping() {
  stopTimer()
  revealed.value = 0
  const t = total.value
  if (!t) {
    emit('done')
    return
  }
  if (!props.typing || reducedMotion) {
    revealed.value = t
    emit('done')
    return
  }
  const tick = 18
  const duration = Math.max(600, Math.min(2600, 700 + t * 1.1))
  const ticks = Math.max(8, Math.round(duration / tick))
  const per = Math.max(1, Math.ceil(t / ticks))
  timer = setInterval(() => {
    revealed.value += per
    if (revealed.value >= t) {
      revealed.value = t
      stopTimer()
      emit('done')
    }
  }, tick)
}

watch(
  () => props.sql,
  () => startTyping(),
  { immediate: true },
)

onBeforeUnmount(stopTimer)

async function copy() {
  try {
    await navigator.clipboard.writeText(props.sql)
    toast.success('SQL 已复制')
  } catch {
    toast.danger({ title: '复制失败', desc: '浏览器未授予剪贴板权限，请手动复制' })
  }
}
</script>

<template>
  <div class="sql-viewer">
    <div class="code">
      <div class="code-inner">
        <div class="gutter" aria-hidden="true">
          <span v-for="n in visibleLineCount" :key="n">{{ n }}</span>
        </div>
        <div class="code-body" v-html="displayHtml" />
      </div>
    </div>
    <div v-if="copyable && sql && !isTyping" class="sql-toolbar">
      <button type="button" class="sql-copy" @click="copy">
        <svg viewBox="0 0 24 24"><rect x="9" y="9" width="12" height="12" rx="2" /><path d="M5 15V5a2 2 0 0 1 2-2h10" /></svg>
        复制
      </button>
    </div>
  </div>
</template>

<style scoped>
.sql-viewer {
  position: relative;
}
.code {
  background: var(--sunken);
  border: 1px solid var(--line-subtle);
  border-radius: var(--r-sm);
  font-family: var(--font-mono);
  font-size: 13px;
  line-height: 22px;
  overflow: auto;
  max-height: 340px;
}
.code-inner {
  display: flex;
  min-width: max-content;
}
.gutter {
  flex: none;
  padding: 12px 0;
  text-align: right;
  user-select: none;
  color: var(--text-4);
  font-size: 12px;
  line-height: 22px;
  border-right: 1px solid var(--line-subtle);
}
.gutter span {
  display: block;
  padding: 0 10px 0 12px;
}
.code-body {
  padding: 12px 16px;
  color: var(--text-1);
}
.code-body :deep(.code-line) {
  white-space: pre;
  min-height: 22px;
}
/* token 级着色（§4.5.1） */
.code-body :deep(.sq-keyword) {
  color: var(--accent);
  font-weight: 500;
}
.code-body :deep(.sq-table) {
  color: var(--c1);
}
.code-body :deep(.sq-field) {
  color: var(--text-1);
}
.code-body :deep(.sq-string) {
  color: var(--ember);
}
.code-body :deep(.sq-comment) {
  color: var(--text-4);
  font-style: italic;
}
.code-body :deep(.sq-function) {
  color: var(--c4);
}
.code-body :deep(.sq-number) {
  color: var(--c2);
}
.code-body :deep(.caret) {
  display: inline-block;
  width: 2px;
  height: 15px;
  background: var(--accent);
  vertical-align: -3px;
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
.sql-toolbar {
  position: absolute;
  top: 8px;
  right: 8px;
  display: flex;
  gap: 6px;
}
.sql-copy {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 26px;
  padding: 0 10px;
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  background: var(--surface-1);
  color: var(--text-2);
  font-size: 12px;
  cursor: pointer;
}
.sql-copy:hover {
  border-color: var(--line-strong);
  color: var(--text-1);
}
.sql-copy svg {
  width: 13px;
  height: 13px;
  stroke: currentColor;
  fill: none;
  stroke-width: 1.6;
  stroke-linecap: round;
  stroke-linejoin: round;
}
</style>
