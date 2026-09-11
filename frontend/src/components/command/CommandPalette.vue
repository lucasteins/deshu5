<script setup lang="ts">
/**
 * 命令面板（⌘K / Ctrl+K）—— F2.6 · 设计稿 §4.4.6「命令面板」
 *
 * - 唤起：AppShell 全局键盘监听（⌘/Ctrl+K）或顶栏搜索框点击
 * - 三类结果分组：跳转页面 / 搜索对象 / 执行操作
 * - 键盘优先：输入即搜、↑↓ 选择、Enter 执行、Esc 关闭
 * - 动效：面板 180ms opacity + scale(.98→1)；reduced-motion 降为 opacity 80ms
 */
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useAppStore } from '@/stores/app'
import {
  buildActionCommands,
  buildPageCommands,
  searchObjectCommands,
  type CommandItem,
  type CommandKind,
} from './command'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ close: [] }>()

const app = useAppStore()

const query = ref('')
const selectedId = ref('')
const inputRef = ref<HTMLInputElement | null>(null)

const objectCommands = ref<CommandItem[]>([])
const objectLoading = ref(false)

const baseCommands = computed<CommandItem[]>(() => [
  ...buildPageCommands(),
  ...buildActionCommands(app),
])

function matches(item: CommandItem, q: string): boolean {
  if (!q) return true
  const hay = [item.label, item.hint ?? '', ...(item.keywords ?? [])].join(' ').toLowerCase()
  return hay.includes(q)
}

/** 展平可见结果（键盘上下按此顺序游走，跨分组） */
const flat = computed<CommandItem[]>(() => {
  const q = query.value.trim().toLowerCase()
  return [
    ...baseCommands.value.filter((c) => matches(c, q)),
    ...objectCommands.value,
  ]
})

interface Group {
  key: CommandKind
  label: string
  items: CommandItem[]
}

const groups = computed<Group[]>(() => {
  const q = query.value.trim()
  const res: Group[] = []
  const push = (key: CommandKind, label: string, items: CommandItem[]) => {
    if (items.length) res.push({ key, label, items })
  }
  push('page', '跳转页面', flat.value.filter((c) => c.kind === 'page'))
  push('action', '执行操作', flat.value.filter((c) => c.kind === 'action'))
  // 「搜索对象」仅在输入后出现（空查询不触发对象搜索，避免默认态噪音）
  if (q) push('object', '搜索对象', flat.value.filter((c) => c.kind === 'object'))
  return res
})

const hasResult = computed(() => flat.value.length > 0)

/* ---------- 搜索对象（防抖） ---------- */
let searchTimer: ReturnType<typeof setTimeout> | undefined
let searchSeq = 0

watch(query, () => {
  clearTimeout(searchTimer)
  const q = query.value.trim()
  if (!q) {
    objectCommands.value = []
    objectLoading.value = false
    return
  }
  objectLoading.value = true
  const seq = ++searchSeq
  searchTimer = setTimeout(async () => {
    try {
      const items = await searchObjectCommands(q)
      // 丢弃过期请求的结果（防抖 + 竞态）
      if (seq === searchSeq) objectCommands.value = items
    } catch {
      if (seq === searchSeq) objectCommands.value = []
    } finally {
      if (seq === searchSeq) objectLoading.value = false
    }
  }, 220)
})

/* ---------- 打开 / 关闭 ---------- */
watch(
  () => props.open,
  async (open) => {
    if (!open) return
    query.value = ''
    objectCommands.value = []
    objectLoading.value = false
    selectedId.value = ''
    await nextTick()
    inputRef.value?.focus()
  },
)

watch(flat, () => {
  const ids = flat.value.map((c) => c.id)
  if (!ids.includes(selectedId.value)) selectedId.value = ids[0] ?? ''
})

function close() {
  emit('close')
}

/* ---------- 键盘 ---------- */
function move(delta: number) {
  const ids = flat.value.map((c) => c.id)
  if (!ids.length) {
    selectedId.value = ''
    return
  }
  const idx = ids.indexOf(selectedId.value)
  const next =
    idx < 0 ? (delta < 0 ? ids.length - 1 : 0) : (idx + delta + ids.length) % ids.length
  selectedId.value = ids[next]
  void scrollSelectedIntoView()
}

function runSelected() {
  const item = flat.value.find((c) => c.id === selectedId.value)
  if (!item) return
  item.run()
  close()
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'ArrowDown') {
    e.preventDefault()
    move(1)
  } else if (e.key === 'ArrowUp') {
    e.preventDefault()
    move(-1)
  } else if (e.key === 'Enter') {
    e.preventDefault()
    runSelected()
  } else if (e.key === 'Escape') {
    e.preventDefault()
    close()
  }
}

async function scrollSelectedIntoView() {
  await nextTick()
  document
    .querySelector<HTMLElement>('.cmd-item.is-selected')
    ?.scrollIntoView({ block: 'nearest' })
}

/* 面板打开时仍兜底处理 Esc（输入框失焦场景） */
function onDocKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape' && props.open) {
    e.preventDefault()
    close()
  }
}

watch(
  () => props.open,
  (open) => {
    if (open) document.addEventListener('keydown', onDocKeydown)
    else document.removeEventListener('keydown', onDocKeydown)
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  clearTimeout(searchTimer)
  document.removeEventListener('keydown', onDocKeydown)
})
</script>

<template>
  <Teleport to="body">
    <Transition name="cmd" appear>
      <div
        v-if="open"
        class="cmd-overlay"
        role="dialog"
        aria-modal="true"
        aria-label="命令面板"
        @mousedown.self="close"
      >
        <div class="cmd-panel" @mousedown.prevent>
          <div class="cmd-input-row">
            <el-icon class="cmd-search-ico"><Search /></el-icon>
            <input
              ref="inputRef"
              v-model="query"
              class="cmd-input"
              type="text"
              placeholder="搜索页面、表、问答对、错题…"
              aria-label="搜索命令"
              autocomplete="off"
              spellcheck="false"
              @keydown="onKeydown"
            />
            <kbd class="cmd-kbd">esc</kbd>
          </div>

          <div class="cmd-results">
            <template v-if="hasResult">
              <div v-for="group in groups" :key="group.key" class="cmd-group">
                <div class="cmd-group-label">{{ group.label }}</div>
                <div
                  v-for="item in group.items"
                  :key="item.id"
                  class="cmd-item"
                  :class="{ 'is-selected': item.id === selectedId }"
                  role="option"
                  :aria-selected="item.id === selectedId"
                  @mouseenter="selectedId = item.id"
                  @click="runSelected"
                >
                  <el-icon v-if="item.icon" class="cmd-item-ico">
                    <component :is="item.icon" />
                  </el-icon>
                  <span class="cmd-item-label">{{ item.label }}</span>
                  <span v-if="item.badge" class="cmd-item-badge">{{ item.badge }}</span>
                  <span v-if="item.hint" class="cmd-item-hint">{{ item.hint }}</span>
                </div>
              </div>
            </template>

            <div v-else class="cmd-empty">
              <template v-if="objectLoading">
                <span>正在搜索对象…</span>
              </template>
              <template v-else>没有匹配的命令或对象</template>
            </div>
          </div>

          <div class="cmd-footer">
            <span><kbd>↑↓</kbd> 选择</span>
            <span><kbd>↵</kbd> 执行</span>
            <span><kbd>esc</kbd> 关闭</span>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.cmd-overlay {
  position: fixed;
  inset: 0;
  z-index: 3000;
  display: flex;
  justify-content: center;
  align-items: flex-start;
  padding-top: 12vh;
  background: var(--overlay-scrim);
}

.cmd-panel {
  width: min(620px, calc(100vw - 48px));
  max-height: min(70vh, 640px);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: var(--surface-1);
  border: 1px solid var(--line);
  border-radius: var(--r-lg);
  box-shadow: var(--e4);
}

.cmd-input-row {
  flex: none;
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  height: 52px;
  padding: 0 var(--sp-4);
  border-bottom: 1px solid var(--line-subtle);
}
.cmd-search-ico {
  flex: none;
  font-size: 16px;
  color: var(--text-3);
}
.cmd-input {
  flex: 1;
  min-width: 0;
  height: 100%;
  border: none;
  outline: none;
  background: transparent;
  font-size: var(--fs-body-lg);
  line-height: var(--lh-body-lg);
  color: var(--text-1);
}
.cmd-input::placeholder {
  color: var(--text-3);
}
.cmd-kbd {
  flex: none;
  padding: 1px 6px;
  border: 1px solid var(--line);
  border-radius: 4px;
  background: var(--surface-2);
  color: var(--text-3);
  font-family: var(--font-mono);
  font-size: var(--fs-micro);
}

.cmd-results {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: var(--sp-2);
}

.cmd-group + .cmd-group {
  margin-top: var(--sp-2);
}
.cmd-group-label {
  padding: var(--sp-2) var(--sp-2) 4px;
  font-size: var(--fs-micro);
  font-weight: var(--fw-micro);
  letter-spacing: 0.06em;
  color: var(--text-4);
}

.cmd-item {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  height: 36px;
  padding: 0 var(--sp-2);
  border-radius: var(--r-sm);
  color: var(--text-1);
  cursor: pointer;
}
.cmd-item:hover {
  background: var(--surface-2);
}
.cmd-item.is-selected {
  background: var(--accent-soft);
}
.cmd-item-ico {
  flex: none;
  width: 18px;
  font-size: 15px;
  color: var(--text-2);
}
.cmd-item.is-selected .cmd-item-ico {
  color: var(--accent);
}
.cmd-item-label {
  flex: 0 1 auto;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  font-size: var(--fs-body-sm);
}
.cmd-item-badge {
  flex: none;
  padding: 0 6px;
  border-radius: var(--r-xs);
  background: var(--sunken);
  color: var(--text-3);
  font-size: var(--fs-micro);
  line-height: 18px;
}
.cmd-item-hint {
  flex: none;
  max-width: 220px;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  margin-left: auto;
  padding-left: var(--sp-2);
  font-size: var(--fs-caption);
  color: var(--text-3);
}

.cmd-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--sp-2);
  padding: var(--sp-10) var(--sp-4);
  color: var(--text-3);
  font-size: var(--fs-body-sm);
}

.cmd-footer {
  flex: none;
  display: flex;
  align-items: center;
  gap: var(--sp-4);
  padding: var(--sp-2) var(--sp-4);
  border-top: 1px solid var(--line-subtle);
  color: var(--text-3);
  font-size: var(--fs-caption);
}
.cmd-footer kbd {
  margin-right: 2px;
  padding: 1px 5px;
  border: 1px solid var(--line);
  border-radius: 4px;
  background: var(--surface-2);
  color: var(--text-2);
  font-family: var(--font-mono);
  font-size: var(--fs-micro);
}
</style>

<style>
/* 面板进出：opacity + scale(.98→1)，180ms（§4.3.5 面板展开默认） */
.cmd-enter-active,
.cmd-leave-active {
  transition:
    opacity var(--dur-base) var(--ease-standard),
    transform var(--dur-base) var(--ease-standard);
}
.cmd-enter-from,
.cmd-leave-to {
  opacity: 0;
}
.cmd-enter-from .cmd-panel,
.cmd-leave-to .cmd-panel {
  transform: scale(0.98);
}

/* 降低动效偏好（§4.3.5）：只留 opacity，且 80ms */
@media (prefers-reduced-motion: reduce) {
  .cmd-enter-active,
  .cmd-leave-active {
    transition-duration: 80ms;
  }
  .cmd-enter-from .cmd-panel,
  .cmd-leave-to .cmd-panel {
    transform: none;
  }
}
</style>
