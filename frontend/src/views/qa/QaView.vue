<script setup lang="ts">
/**
 * 智能问答（F2.1 流式叙事首秀）——设计稿 §4.5.1「深入设计 · 智能问答」+ §4.4.4「流式输出叙事」。
 *
 * 三页签（对话 / 历史会话 / 我的提问模板）由 AppShell 的 TabBar 经 URL `#tab=` 驱动；
 * 历史会话数据源为日志库 generation_logs（每次生成落一条），支持搜索 / 状态过滤 /
 * 详情回看（SQL + 执行结果预览）/ 再次提问 / 删除；提问模板暂为简态空态。
 *
 * 流式链路：
 *   POST /api/generate-sql-stream（useSSE）
 *     stage×5   → 流程轨状态机（rag→语义检索 / schema→Schema / llm→LLM / validate→审查 / exec→取数）
 *     thinking×11（含 llm_stream 逐字）→ 思考窗口（分类渲染 + 逐字流）
 *     done/error → SQL 打字机着色 + 结果联动 + 收束条
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElButton } from 'element-plus'
import { useSSE } from '@/composables'
import type { QaStreamEvent, QaStreamResult, QaStage, QaThinking } from '@/types'
import {
  DsClosingBar,
  DsEmpty,
  DsSqlViewer,
  DsStepRail,
  DsThinkingPanel,
  confirm,
  formatSql,
  toast,
  thinkingToEntry,
  useAutoScroll,
  type StreamStep,
  type ThinkingEntry,
  type ThinkingStream,
} from '@/components'
import {
  asQaResult,
  saveQa,
  listQaHistory,
  getQaHistoryDetail,
  deleteQaHistory,
  type QaResult,
  type QaHistoryItem,
  type QaHistoryDetail,
} from '@/api/chat'

/* ================= 页签（URL #tab 驱动） ================= */
const activeTab = ref<'chat' | 'history' | 'templates'>('chat')

function readTab(): 'chat' | 'history' | 'templates' {
  const m = /(?:^#|&)tab=([^&]*)/.exec(window.location.hash)
  const v = m ? decodeURIComponent(m[1]) : ''
  return v === 'history' || v === 'templates' ? v : 'chat'
}
function syncTab() {
  activeTab.value = readTab()
}
onMounted(() => {
  syncTab()
  window.addEventListener('hashchange', syncTab)
})
onBeforeUnmount(() => window.removeEventListener('hashchange', syncTab))

/* ================= 流程轨状态机 ================= */
const STAGE_ORDER = ['rag', 'schema', 'llm', 'validate', 'exec'] as const
const STAGE_TITLES: Record<string, string> = {
  rag: '语义检索',
  schema: 'Schema 选择',
  llm: 'LLM 生成 SQL',
  validate: '自动审查修复',
  exec: '执行取数',
}
const STAGE_DESCS: Record<string, string> = {
  rag: 'RAG 从问答对库召回参考题',
  schema: '确定涉及的表、字段与码值',
  llm: 'LLM 生成 SQL',
  validate: '字段存在性 / 类型 / 关联 / 除零保护',
  exec: '在只读连接上执行并返回结果',
}

const lastIdx = ref(-1)
const stageMs = ref<Record<string, number>>({})
const steps = ref<StreamStep[]>(STAGE_ORDER.map((s, i) => ({
  key: s,
  title: STAGE_TITLES[s],
  desc: STAGE_DESCS[s],
  status: 'pending' as const,
  ms: undefined,
})))

function refreshSteps() {
  steps.value = STAGE_ORDER.map((s, i) => {
    let status: StreamStep['status']
    if (i < lastIdx.value) status = 'done'
    else if (i === lastIdx.value) status = 'done'
    else if (i === lastIdx.value + 1) status = 'running'
    else status = 'pending'
    return { key: s, title: STAGE_TITLES[s], desc: STAGE_DESCS[s], status, ms: stageMs.value[s] }
  })
}

function resetSteps() {
  lastIdx.value = -1
  stageMs.value = {}
  refreshSteps()
}

function applyStage(stage: string, ms: number) {
  const idx = (STAGE_ORDER as readonly string[]).indexOf(stage)
  if (idx < 0) return
  stageMs.value[stage] = ms
  lastIdx.value = idx
  refreshSteps()
}

/* ================= 提问与流式生成 ================= */
const question = ref('')
const generating = ref(false)
const result = ref<QaResult | null>(null)
const streamError = ref<string | null>(null)

const thinkingEntries = ref<ThinkingEntry[]>([])
const thinkingStreams = ref<ThinkingStream[]>([])
const thinkingExpanded = ref(false)

/** 流式期间的实时秒表 */
const elapsedMs = ref(0)
let clockTimer: ReturnType<typeof setInterval> | undefined

const sse = useSSE<QaStreamEvent, QaStreamResult>('/generate-sql-stream', {
  onEvent: (evt) => {
    if ('stage' in evt && typeof evt.stage === 'string') {
      applyStage(evt.stage, evt.ms)
    } else if ('thinking' in evt && evt.thinking) {
      handleThinking(evt.thinking)
    }
  },
})

function handleThinking(t: QaThinking) {
  if (t.kind === 'llm_stream') {
    const phase = t.phase
    const idx = thinkingStreams.value.findIndex((s) => s.phase === phase)
    if (idx >= 0) {
      thinkingStreams.value[idx] = { phase, text: thinkingStreams.value[idx].text + (t.text ?? '') }
    } else {
      thinkingStreams.value.push({ phase, text: t.text ?? '' })
    }
    return
  }
  const entry = thinkingToEntry(t)
  if (entry) thinkingEntries.value.push(entry)
}

function startClock() {
  elapsedMs.value = 0
  stopClock()
  const t0 = Date.now()
  clockTimer = setInterval(() => {
    elapsedMs.value = Date.now() - t0
  }, 80)
}
function stopClock() {
  if (clockTimer) clearInterval(clockTimer)
  clockTimer = undefined
}

async function submit() {
  const q = question.value.trim()
  if (!q) {
    toast.warning('请输入业务问题')
    return
  }
  if (generating.value) return

  // 重置状态
  result.value = null
  streamError.value = null
  thinkingEntries.value = []
  thinkingStreams.value = []
  thinkingExpanded.value = false
  resetSteps()
  startClock()

  generating.value = true
  try {
    await sse.start({ question: q })
  } finally {
    generating.value = false
    stopClock()
  }

  if (sse.status.value === 'done') {
    const r = asQaResult(sse.result.value)
    result.value = r
    // 兜底：未收到的阶段标记完成
    if (lastIdx.value < STAGE_ORDER.length - 1) {
      lastIdx.value = STAGE_ORDER.length - 1
      refreshSteps()
    }
    if (r && !r.success) {
      streamError.value = r.error || '生成失败'
    }
    // 新一轮问答已落 generation_logs，历史页签下次进入时重载
    histLoaded.value = false
  } else if (sse.status.value === 'error') {
    streamError.value = sse.error.value?.message || '流式响应中断'
  }
  // aborted：停止生成，保留已生成内容（无 result，显示「未完成」）
}

function regenerate() {
  if (question.value.trim()) void submit()
}

async function onSaveQa() {
  const r = result.value
  if (!r || !r.sql) return
  try {
    await saveQa({
      question: question.value.trim(),
      sql: r.sql,
      result_preview: JSON.stringify(r.result_preview ?? null),
    })
    toast.success('已保存到问答对库')
  } catch (e) {
    toast.danger({ title: '保存失败', desc: e instanceof Error ? e.message : String(e) })
  }
}

async function copySql() {
  const r = result.value
  if (!r || !r.sql) return
  try {
    await navigator.clipboard.writeText(formatSql(r.sql))
    toast.success('SQL 已复制')
  } catch {
    toast.danger({ title: '复制失败', desc: '浏览器未授予剪贴板权限' })
  }
}

function replayThinking() {
  thinkingExpanded.value = true
}

/* ================= 思考摘要（6 表 · 14 码值 · LLM 3 次） ================= */
const summary = computed(() => {
  const r = result.value
  if (!r) return []
  const tables = r.located_tables?.length ?? r.tables_involved?.length ?? 0
  const cv = r.code_value_hits?.length ?? 0
  const llm = r.usage?.llm_calls
  const out: { label: string; value: string | number }[] = []
  if (tables) out.push({ label: '选中', value: `${tables} 张表` })
  if (cv) out.push({ label: '命中', value: `${cv} 个码值` })
  if (llm) out.push({ label: 'LLM 调用', value: `${llm} 次` })
  return out
})

/* ================= 上下文 chips（生成后的定位表，虚线=系统识别） ================= */
const contextChips = computed<string[]>(() => {
  return result.value?.located_tables ?? []
})

/* ================= 收束条 ================= */
const closingMetrics = computed(() => {
  const r = result.value
  if (!r) return []
  const tables = r.located_tables?.length ?? r.tables_involved?.length ?? 0
  const cv = r.code_value_hits?.length ?? 0
  const llm = r.usage?.llm_calls
  const out: { label: string; value: string | number }[] = []
  if (tables) out.push({ label: '涉及', value: `${tables} 张表` })
  if (cv) out.push({ label: '码值', value: `${cv} 个` })
  if (llm) out.push({ label: 'LLM 调用', value: `${llm} 次` })
  return out
})

const totalMs = computed(() => {
  const t = result.value?.timing
  const total = t && typeof t === 'object' ? (t as Record<string, unknown>).total : undefined
  if (typeof total === 'number') return total
  // 兜底：累加流程轨阶段耗时（mock 或 result.timing 缺失场景）
  const sum = STAGE_ORDER.reduce((s, k) => s + (stageMs.value[k] ?? 0), 0)
  return sum || elapsedMs.value
})

/* ================= 结果表 ================= */
const preview = computed(() => result.value?.result_preview ?? null)
const previewHeaders = computed(() => preview.value?.headers ?? [])
const previewRows = computed(() => preview.value?.rows ?? [])

function cellClass(v: string | number | null): string {
  return typeof v === 'number' ? 'num tabular' : 'tabular'
}
function cellText(v: string | number | null): string {
  return v === null ? 'NULL' : String(v)
}

/* ================= 历史会话（generation_logs 回看） ================= */
const HIST_PER_PAGE = 15
const HIST_STATUS_FILTERS = [
  { value: '', label: '全部' },
  { value: 'success', label: '成功' },
  { value: 'failed', label: '失败' },
] as const

const histItems = ref<QaHistoryItem[]>([])
const histTotal = ref(0)
const histPage = ref(1)
const histQ = ref('')
const histStatus = ref('')
const histLoading = ref(false)
const histError = ref('')
const histLoaded = ref(false)
const histSelectedId = ref<number | null>(null)
const histDetail = ref<QaHistoryDetail | null>(null)
const histDetailLoading = ref(false)

const histPages = computed(() => Math.max(1, Math.ceil(histTotal.value / HIST_PER_PAGE)))
const histPreview = computed(() => histDetail.value?.result_preview ?? null)
const histPreviewHeaders = computed(() => histPreview.value?.headers ?? [])
const histPreviewRows = computed(() => histPreview.value?.rows ?? [])

function fmtHistTime(s: string): string {
  return (s || '').replace('T', ' ').slice(0, 16)
}

async function loadHistory(page = histPage.value) {
  histLoading.value = true
  histError.value = ''
  try {
    const res = await listQaHistory({
      page,
      per_page: HIST_PER_PAGE,
      q: histQ.value.trim() || undefined,
      status: histStatus.value || undefined,
    })
    histItems.value = res.items
    histTotal.value = res.total
    histPage.value = res.page
    histLoaded.value = true
    // 当前选中项已不在本页（翻页/删除/过滤）时清空详情
    if (histSelectedId.value !== null && !res.items.some((it) => it.id === histSelectedId.value)) {
      histSelectedId.value = null
      histDetail.value = null
    }
  } catch (e) {
    histError.value = e instanceof Error ? e.message : String(e)
  } finally {
    histLoading.value = false
  }
}

function onHistSearch() {
  histPage.value = 1
  void loadHistory(1)
}

function onHistStatus(v: string) {
  if (histStatus.value === v) return
  histStatus.value = v
  histPage.value = 1
  void loadHistory(1)
}

async function selectHistory(it: QaHistoryItem) {
  if (histSelectedId.value === it.id && histDetail.value) return
  histSelectedId.value = it.id
  histDetailLoading.value = true
  try {
    const res = await getQaHistoryDetail(it.id)
    histDetail.value = res.item
  } catch (e) {
    histDetail.value = null
    toast.danger({ title: '详情加载失败', desc: e instanceof Error ? e.message : String(e) })
  } finally {
    histDetailLoading.value = false
  }
}

/** 再次提问：把历史问题带回对话页签并直接发起生成 */
function reask(d: QaHistoryDetail) {
  question.value = d.question
  window.location.hash = 'tab=chat'
  void submit()
}

async function copyHistorySql() {
  const sql = histDetail.value?.generated_sql
  if (!sql) return
  try {
    await navigator.clipboard.writeText(formatSql(sql))
    toast.success('SQL 已复制')
  } catch {
    toast.danger({ title: '复制失败', desc: '浏览器未授予剪贴板权限' })
  }
}

async function onHistDelete(d: QaHistoryDetail) {
  const ok = await confirm.l1({
    title: '删除历史会话',
    message: `即将删除「${d.question.slice(0, 30)}${d.question.length > 30 ? '…' : ''}」这条历史记录，此操作不可撤销。`,
    confirmText: '删除',
    danger: true,
  })
  if (!ok) return
  try {
    await deleteQaHistory(d.id)
    toast.success('历史会话已删除')
    histSelectedId.value = null
    histDetail.value = null
    await loadHistory()
  } catch (e) {
    toast.danger({ title: '删除失败', desc: e instanceof Error ? e.message : String(e) })
  }
}

// 进入历史页签时按需加载（仅首次或数据过期后）
watch(activeTab, (t) => {
  if (t === 'history' && !histLoaded.value) void loadHistory(1)
})
onMounted(() => {
  if (activeTab.value === 'history') void loadHistory(1)
})

/* ================= 自动滚动（左栏） ================= */
const { containerRef, showJump, notify, jumpToBottom } = useAutoScroll()

onBeforeUnmount(() => {
  stopClock()
  sse.abort()
})
</script>

<template>
  <div class="qa">
    <!-- 对话页签 -->
    <div v-if="activeTab === 'chat'" class="qa-workbench">
      <section class="qa-col-left" ref="containerRef">
        <!-- 提问卡 -->
        <div class="qa-card ask-card" :class="{ 'is-live': generating }">
          <textarea
            v-model="question"
            class="ask-input"
            rows="2"
            spellcheck="false"
            placeholder="请输入业务问题，例如：查询某管理单位下所有高压客户的总用电量"
            @keydown.ctrl.enter.prevent="submit"
            @keydown.meta.enter.prevent="submit"
          />
          <div v-if="contextChips.length" class="ctx-row">
            <span class="ctx-label">识别到的数据表</span>
            <span v-for="c in contextChips" :key="c" class="ctx is-inferred">
              <span class="v">{{ c }}</span>
            </span>
          </div>
          <div class="ask-actions">
            <span class="hint">Ctrl / ⌘ + Enter 发送 · 生成过程实时可追溯</span>
            <span class="spacer" />
            <ElButton v-if="generating" @click="sse.abort()">停止生成</ElButton>
            <ElButton type="primary" :loading="generating" @click="submit">生成 SQL</ElButton>
          </div>
        </div>

        <!-- 思考过程 -->
        <div v-if="thinkingEntries.length || thinkingStreams.length" class="qa-card">
          <div class="qa-card-hd">
            <h3>思考过程</h3>
            <span v-if="generating" class="meta">已耗时 <span class="tabular">{{ (elapsedMs / 1000).toFixed(1) }}s</span></span>
            <button class="ghost-btn" type="button" @click="thinkingExpanded = !thinkingExpanded">
              {{ thinkingExpanded ? '收起明细' : '展开明细' }}
            </button>
          </div>
          <div v-if="thinkingExpanded" class="qa-card-bd">
            <DsThinkingPanel :entries="thinkingEntries" :streams="thinkingStreams" />
          </div>
          <div v-else class="qa-card-bd">
            <DsThinkingPanel :entries="[]" :streams="thinkingStreams" :summary="summary" />
          </div>
        </div>

        <!-- 生成失败错误条 -->
        <div v-if="streamError" class="qa-error">
          <svg viewBox="0 0 16 16"><circle cx="8" cy="8" r="6.5" /><path d="M8 5v3.5M8 11h.01" /></svg>
          <span>生成失败：{{ streamError }}</span>
          <button type="button" @click="regenerate">重新生成</button>
        </div>

        <!-- SQL -->
        <div v-if="result && result.sql" class="qa-card">
          <div class="qa-card-hd">
            <h3>生成 SQL</h3>
            <span v-if="result.review_status?.passed !== false" class="tag tag-accent">已通过审查修复</span>
            <span v-else class="tag tag-warn">审查警告</span>
            <button class="ghost-btn" type="button" @click="copySql">复制</button>
          </div>
          <div class="qa-card-bd">
            <DsSqlViewer :sql="result.sql" :typing="true" />
          </div>
        </div>

        <!-- 结果 + 收束条 -->
        <div v-if="result && result.result_preview" class="qa-card">
          <div class="qa-card-hd">
            <h3>执行结果</h3>
            <span v-if="preview?.success !== false" class="tag tag-success">执行成功</span>
            <span v-else class="tag tag-danger">执行失败</span>
            <span class="meta">{{ previewRows.length }} 行</span>
          </div>
          <div v-if="preview && !preview.success" class="qa-result-error">{{ preview.error || '执行失败' }}</div>
          <div v-else class="tablewrap">
            <table class="tb">
              <thead>
                <tr>
                  <th v-for="(h, i) in previewHeaders" :key="i">{{ h }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(row, ri) in previewRows" :key="ri">
                  <td v-for="(cell, ci) in row" :key="ci" :class="cellClass(cell)">{{ cellText(cell) }}</td>
                </tr>
              </tbody>
            </table>
            <div v-if="!previewRows.length" class="qa-result-empty">无数据</div>
          </div>
          <DsClosingBar :total-ms="totalMs" :metrics="closingMetrics">
            <button class="btn-sm btn-ember" type="button" @click="onSaveQa">加入问答对库</button>
            <button class="btn-sm" type="button" @click="regenerate">重新生成</button>
          </DsClosingBar>
        </div>

        <button v-if="showJump" class="jump-btn" type="button" @click="jumpToBottom">有新内容 ↓</button>
      </section>

      <!-- 右栏：流程轨 -->
      <aside class="qa-col-right">
        <div class="qa-card rail-card">
          <div class="qa-card-hd">
            <h3>生成流程</h3>
            <span class="meta">总耗时 <span class="tabular">{{ (totalMs / 1000).toFixed(2) }}s</span></span>
          </div>
          <DsStepRail :steps="steps" :elapsed-ms="generating ? elapsedMs : undefined" />
        </div>

        <div class="qa-card">
          <div class="qa-card-hd"><h3>可追溯操作</h3></div>
          <div class="qa-card-bd trace-actions">
            <button class="trace-btn" type="button" @click="replayThinking">回放思考过程</button>
            <button class="trace-btn" type="button" :disabled="!result?.sql" @click="copySql">复制 SQL</button>
            <button class="trace-btn trace-btn--ember" type="button" :disabled="!result?.sql" @click="onSaveQa">加入问答对库</button>
            <button class="trace-btn" type="button" :disabled="!question" @click="regenerate">重新生成</button>
          </div>
        </div>
      </aside>
    </div>

    <!-- 历史会话：左列表 + 右详情 -->
    <div v-else-if="activeTab === 'history'" class="qa-history">
      <section class="qa-card hist-list-card">
        <div class="qa-card-hd">
          <h3>历史会话</h3>
          <span class="meta">共 {{ histTotal }} 条</span>
        </div>
        <div class="hist-toolbar">
          <input
            v-model="histQ"
            class="hist-search"
            placeholder="搜索问题关键词，Enter 检索"
            spellcheck="false"
            @keydown.enter.prevent="onHistSearch"
          />
          <div class="hist-filters">
            <button
              v-for="f in HIST_STATUS_FILTERS"
              :key="f.value"
              class="hist-filter"
              :class="{ active: histStatus === f.value }"
              type="button"
              @click="onHistStatus(f.value)"
            >{{ f.label }}</button>
            <button class="hist-filter" type="button" @click="loadHistory()">刷新</button>
          </div>
        </div>
        <div v-if="histError" class="hist-state hist-state--error">
          加载失败：{{ histError }}
          <button type="button" @click="loadHistory()">重试</button>
        </div>
        <div v-else-if="histLoading" class="hist-state">加载中…</div>
        <div v-else-if="!histItems.length" class="hist-state">
          {{ histQ || histStatus ? '没有符合条件的历史会话' : '还没有历史会话，完成一次智能问答后会沉淀在这里' }}
        </div>
        <template v-else>
          <ul class="hist-list">
            <li
              v-for="it in histItems"
              :key="it.id"
              class="hist-item"
              :class="{ active: histSelectedId === it.id }"
              @click="selectHistory(it)"
            >
              <div class="hist-q">{{ it.question }}</div>
              <div class="hist-meta">
                <span class="tag" :class="it.execution_status === 'success' ? 'tag-success' : 'tag-danger'">
                  {{ it.execution_status === 'success' ? '成功' : '失败' }}
                </span>
                <span>{{ fmtHistTime(it.created_at) }}</span>
                <span v-if="it.row_count != null">{{ it.row_count }} 行</span>
                <span class="tabular">{{ (it.latency_ms / 1000).toFixed(1) }}s</span>
                <span v-if="it.user_judgment" class="hist-judge">已判断：{{ it.user_judgment }}</span>
              </div>
            </li>
          </ul>
          <div class="hist-pager">
            <button class="btn-sm" type="button" :disabled="histPage <= 1" @click="loadHistory(histPage - 1)">上一页</button>
            <span class="meta tabular">{{ histPage }} / {{ histPages }}</span>
            <button class="btn-sm" type="button" :disabled="histPage >= histPages" @click="loadHistory(histPage + 1)">下一页</button>
          </div>
        </template>
      </section>

      <section class="qa-card hist-detail-card">
        <div v-if="histDetailLoading" class="hist-state">加载详情…</div>
        <template v-else-if="histDetail">
          <div class="qa-card-hd">
            <h3>会话详情</h3>
            <span class="meta">{{ fmtHistTime(histDetail.created_at) }}</span>
          </div>
          <div class="qa-card-bd">
            <div class="hist-detail-q">{{ histDetail.question }}</div>
            <div class="hist-detail-meta">
              <span class="tag" :class="histDetail.execution_status === 'success' ? 'tag-success' : 'tag-danger'">
                {{ histDetail.execution_status === 'success' ? '执行成功' : '执行失败' }}
              </span>
              <span v-if="histDetail.review_passed === true" class="tag tag-accent">审查通过</span>
              <span v-else-if="histDetail.review_passed === false" class="tag tag-warn">审查未通过</span>
              <span v-if="histDetail.user_judgment" class="tag tag-accent">人工判断：{{ histDetail.user_judgment }}</span>
              <span class="hist-chip">耗时 <span class="tabular">{{ (histDetail.latency_ms / 1000).toFixed(2) }}s</span></span>
              <span class="hist-chip">生成 <span class="tabular">{{ histDetail.attempts }}</span> 次</span>
              <span class="hist-chip">RAG 召回 <span class="tabular">{{ histDetail.rag_pairs_count }}</span> 条</span>
              <span v-if="histDetail.row_count != null" class="hist-chip">结果 <span class="tabular">{{ histDetail.row_count }}</span> 行</span>
            </div>
            <div v-if="histDetail.generated_sql" class="hist-detail-sql">
              <DsSqlViewer :sql="histDetail.generated_sql" :typing="false" />
            </div>
            <div v-if="histPreview" class="hist-preview-wrap">
              <div class="hist-preview-hd">
                执行结果预览
                <span class="meta">（留存前 {{ histPreviewRows.length }} 行）</span>
              </div>
              <div class="tablewrap">
                <table class="tb">
                  <thead>
                    <tr>
                      <th v-for="(h, i) in histPreviewHeaders" :key="i">{{ h }}</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="(row, ri) in histPreviewRows" :key="ri">
                      <td v-for="(cell, ci) in row" :key="ci" :class="cellClass(cell)">{{ cellText(cell) }}</td>
                    </tr>
                  </tbody>
                </table>
                <div v-if="!histPreviewRows.length" class="qa-result-empty">无数据</div>
              </div>
            </div>
            <div class="hist-detail-actions">
              <button class="btn-sm btn-ember" type="button" @click="reask(histDetail)">再次提问</button>
              <button class="btn-sm" type="button" :disabled="!histDetail.generated_sql" @click="copyHistorySql">复制 SQL</button>
              <button class="btn-sm hist-del" type="button" @click="onHistDelete(histDetail)">删除</button>
            </div>
          </div>
        </template>
        <div v-else class="hist-state hist-state--idle">选择左侧会话查看 SQL 与执行结果</div>
      </section>
    </div>

    <!-- 我的提问模板（简态空态：旧实现无此功能） -->
    <div v-else class="qa-tab-page">
      <DsEmpty
        type="no-data"
        title="还没有提问模板"
        desc="把高频提问保存为模板，下次一键复用"
      />
    </div>
  </div>
</template>

<style scoped>
.qa {
  height: 100%;
  display: flex;
  flex-direction: column;
}
.qa-workbench {
  flex: 1;
  min-height: 0;
  display: flex;
  gap: 16px;
  overflow: hidden;
}
.qa-col-left {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 14px;
  overflow-y: auto;
  padding-right: 2px;
  position: relative;
}
.qa-col-left > * {
  flex: none;
}
.qa-col-right {
  width: 360px;
  flex: none;
  display: flex;
  flex-direction: column;
  gap: 14px;
  overflow-y: auto;
}
/* 目验修复：右栏卡片禁压缩（默认 flex-shrink:1 会在矮视口把卡片压扁，
   overflow:hidden 再把「执行取数 / 可追溯操作」裁掉且不出现滚动条） */
.qa-col-right > * {
  flex: none;
}
.qa-card {
  background: var(--surface-1);
  border: 1px solid var(--line);
  border-radius: var(--r-md);
  box-shadow: var(--e1);
  overflow: hidden;
}
.qa-card-hd {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 16px;
  border-bottom: 1px solid var(--line-subtle);
}
.qa-card-hd h3 {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-strong);
}
.qa-card-hd .meta {
  margin-left: auto;
  font-size: 12px;
  color: var(--text-3);
}
.qa-card-bd {
  padding: 14px 16px;
}
.qa-card-bd > * + * {
  margin-top: 12px;
}

/* 提问卡 */
.ask-card {
  padding: 16px;
  transition: border-color var(--dur-fast), box-shadow var(--dur-fast);
}
.ask-card.is-live {
  border-color: var(--accent-line);
  box-shadow: 0 0 0 3px var(--accent-ring);
}
.ask-input {
  width: 100%;
  border: none;
  outline: none;
  resize: none;
  background: transparent;
  font-family: var(--font-ui);
  font-size: 15px;
  line-height: 26px;
  color: var(--text-strong);
  min-height: 52px;
}
.ask-input::placeholder {
  color: var(--text-3);
}
.ctx-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--line-subtle);
}
.ctx-label {
  font-size: 12px;
  color: var(--text-3);
}
.ctx {
  display: inline-flex;
  align-items: center;
  height: 26px;
  padding: 0 9px;
  border-radius: var(--r-sm);
  font-size: 12.5px;
  background: var(--surface-2);
  border: 1px solid var(--line);
  color: var(--text-1);
}
.ctx.is-inferred {
  border-style: dashed;
  border-color: var(--line-strong);
  background: transparent;
}
.ask-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 14px;
}
.ask-actions .hint {
  font-size: 12px;
  color: var(--text-3);
}
.ask-actions .spacer {
  margin-left: auto;
}

/* 错误条 */
.qa-error {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  background: var(--danger-soft);
  border-left: 2px solid var(--danger);
  border-radius: var(--r-sm);
  font-size: 13px;
  color: var(--text-1);
}
.qa-error svg {
  flex: none;
  width: 16px;
  height: 16px;
  stroke: var(--danger);
  fill: none;
  stroke-width: 1.5;
  stroke-linecap: round;
}
.qa-error button {
  margin-left: auto;
  border: none;
  background: none;
  color: var(--danger);
  font-size: 13px;
  cursor: pointer;
}
.qa-error button:hover {
  text-decoration: underline;
}

/* 标签 */
.tag {
  display: inline-flex;
  align-items: center;
  height: 20px;
  padding: 0 8px;
  border-radius: var(--r-xs);
  font-size: 11px;
  font-weight: 500;
}
.tag-accent {
  background: var(--accent-soft);
  color: var(--accent);
}
.tag-success {
  background: var(--success-soft);
  color: var(--success);
}
.tag-warn {
  background: var(--warning-soft);
  color: var(--warning);
}
.tag-danger {
  background: var(--danger-soft);
  color: var(--danger);
}

.ghost-btn {
  margin-left: auto;
  padding: 0;
  border: none;
  background: none;
  font-size: 12.5px;
  color: var(--text-3);
  cursor: pointer;
}
.ghost-btn:hover {
  color: var(--accent);
}

/* 结果表 */
.qa-result-error {
  padding: 14px 16px;
  font-size: 13px;
  color: var(--danger);
}
.tablewrap {
  overflow: auto;
  border-bottom: 1px solid var(--line-subtle);
}
.tb {
  border-collapse: separate;
  border-spacing: 0;
  width: 100%;
  font-size: 13px;
}
.tb th {
  position: sticky;
  top: 0;
  background: var(--sunken);
  font-size: 12px;
  font-weight: 500;
  color: var(--text-2);
  text-align: left;
  padding: 9px 14px;
  border-bottom: 1px solid var(--line);
  white-space: nowrap;
}
.tb td {
  padding: 8px 14px;
  border-bottom: 1px solid var(--line-subtle);
  color: var(--text-1);
  white-space: nowrap;
  text-align: left;
}
.tb td.num {
  text-align: right;
}
.tb tbody tr:hover td {
  background: var(--surface-2);
}
.qa-result-empty {
  padding: 20px;
  text-align: center;
  font-size: 13px;
  color: var(--text-3);
}

/* 按钮 */
.btn-sm {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  height: 28px;
  padding: 0 10px;
  border-radius: var(--r-sm);
  border: 1px solid var(--line);
  background: var(--surface-1);
  color: var(--text-1);
  font-size: 12.5px;
  cursor: pointer;
}
.btn-sm:hover {
  background: var(--surface-2);
  border-color: var(--line-strong);
}
.btn-ember {
  background: transparent;
  border-color: var(--ember);
  color: var(--ember);
}
.btn-ember:hover {
  background: var(--ember-soft);
}

/* 可追溯操作 */
.trace-actions {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.trace-btn {
  display: flex;
  align-items: center;
  justify-content: flex-start;
  width: 100%;
  height: 32px;
  padding: 0 12px;
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  background: var(--surface-1);
  color: var(--text-1);
  font-size: 13px;
  cursor: pointer;
}
.trace-btn:hover:not(:disabled) {
  background: var(--surface-2);
  border-color: var(--line-strong);
}
.trace-btn--ember {
  border-color: var(--ember);
  color: var(--ember);
}
.trace-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

/* 跳底按钮 */
.jump-btn {
  position: sticky;
  bottom: 8px;
  align-self: center;
  padding: 6px 14px;
  border-radius: var(--r-full);
  border: 1px solid var(--line);
  background: var(--surface-1);
  box-shadow: var(--e2);
  color: var(--accent);
  font-size: 12.5px;
  cursor: pointer;
}

/* 历史会话 */
.qa-history {
  flex: 1;
  min-height: 0;
  display: flex;
  gap: 16px;
  overflow: hidden;
}
.hist-list-card {
  width: 400px;
  flex: none;
  display: flex;
  flex-direction: column;
  min-height: 0;
}
.hist-toolbar {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px 16px;
  border-bottom: 1px solid var(--line-subtle);
}
.hist-search {
  width: 100%;
  height: 32px;
  padding: 0 10px;
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  background: var(--surface-1);
  font-family: var(--font-ui);
  font-size: 13px;
  color: var(--text-strong);
  outline: none;
}
.hist-search:focus {
  border-color: var(--accent-line);
  box-shadow: 0 0 0 3px var(--accent-ring);
}
.hist-filters {
  display: flex;
  gap: 6px;
}
.hist-filter {
  height: 24px;
  padding: 0 10px;
  border: 1px solid var(--line);
  border-radius: var(--r-full);
  background: var(--surface-1);
  color: var(--text-2);
  font-size: 12px;
  cursor: pointer;
}
.hist-filter:hover {
  border-color: var(--line-strong);
}
.hist-filter.active {
  border-color: var(--accent-line);
  background: var(--accent-soft);
  color: var(--accent);
}
.hist-list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  list-style: none;
  margin: 0;
  padding: 6px 8px;
}
.hist-item {
  padding: 10px 10px;
  border-radius: var(--r-sm);
  cursor: pointer;
}
.hist-item:hover {
  background: var(--surface-2);
}
.hist-item.active {
  background: var(--accent-soft);
}
.hist-q {
  font-size: 13px;
  line-height: 20px;
  color: var(--text-strong);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.hist-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 6px;
  font-size: 12px;
  color: var(--text-3);
}
.hist-judge {
  color: var(--text-2);
}
.hist-pager {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 10px 16px;
  border-top: 1px solid var(--line-subtle);
}
.hist-pager .meta {
  font-size: 12px;
  color: var(--text-3);
}
.hist-state {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 40px 24px;
  font-size: 13px;
  color: var(--text-3);
  text-align: center;
}
.hist-state--error {
  color: var(--danger);
}
.hist-state--error button {
  border: none;
  background: none;
  color: var(--danger);
  font-size: 13px;
  cursor: pointer;
  text-decoration: underline;
}
.hist-detail-card {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow-y: auto;
}
.hist-detail-q {
  font-size: 15px;
  line-height: 24px;
  font-weight: 600;
  color: var(--text-strong);
}
.hist-detail-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.hist-chip {
  display: inline-flex;
  align-items: center;
  height: 24px;
  padding: 0 9px;
  border-radius: var(--r-sm);
  font-size: 12px;
  background: var(--surface-2);
  border: 1px solid var(--line);
  color: var(--text-2);
}
.hist-detail-sql {
  border: 1px solid var(--line-subtle);
  border-radius: var(--r-sm);
  overflow: hidden;
}
.hist-preview-wrap {
  border: 1px solid var(--line-subtle);
  border-radius: var(--r-sm);
  overflow: hidden;
}
.hist-preview-hd {
  padding: 8px 12px;
  font-size: 12px;
  color: var(--text-2);
  background: var(--sunken);
  border-bottom: 1px solid var(--line-subtle);
}
.hist-preview-hd .meta {
  color: var(--text-3);
}
.hist-detail-actions {
  display: flex;
  gap: 8px;
}
.hist-del {
  margin-left: auto;
  border-color: var(--danger);
  color: var(--danger);
}
.hist-del:hover {
  background: var(--danger-soft);
  border-color: var(--danger);
}

/* 简态页签 */
.qa-tab-page {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
}
</style>
