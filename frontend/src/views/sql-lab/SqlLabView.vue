<script setup lang="ts">
/**
 * SQL 查询（SQL Lab，F2.6）—— 手写 SQL 取数 + 数据资源目录 + AI 助手。
 *
 * 布局：左主区（工具条 / Monaco 编辑器 / 结果网格或错误面板）+ 右侧栏 ElTabs
 * （数据资源目录 = schema-graph + table-columns；AI 助手 = generate/fix/explain/optimize）。
 * 只读约束由后端 safe_execute_sql 兜底（SELECT/WITH 白名单 + 自动 LIMIT）。
 */
import { computed, nextTick, onMounted, reactive, ref } from 'vue'
import { format as formatSqlText } from 'sql-formatter'
import {
  DsDataTable,
  DsSqlViewer,
  DsStepRail,
  DsThinkingPanel,
  thinkingToEntry,
  toast,
  type StreamStep,
  type ThinkingEntry,
  type ThinkingStream,
} from '@/components'
import SqlEditor, { type SqlSchemaTable } from '@/components/sql/SqlEditor.vue'
import { assistSql, executeSql, type SqlAssistAction, type SqlExecuteResponse } from '@/api/sqllab'
import { saveQa } from '@/api/chat'
import { useSSE } from '@/composables'
import type { QaStreamEvent, QaStreamResult, QaThinking } from '@/types'
import {
  fetchSchemaGraph,
  fetchTableColumns,
  type SchemaNode,
  type TableColumn,
} from '@/api/resources'

/* ==================== 编辑器与执行 ==================== */

const sql = ref('')
const editorRef = ref<InstanceType<typeof SqlEditor>>()
const running = ref(false)
const result = ref<SqlExecuteResponse | null>(null)
const execError = ref('')

const HISTORY_KEY = 'sqllab:history'
const history = ref<string[]>([])

function loadHistory() {
  try {
    history.value = JSON.parse(localStorage.getItem(HISTORY_KEY) || '[]')
  } catch {
    history.value = []
  }
}

function pushHistory(text: string) {
  const trimmed = text.trim()
  if (!trimmed) return
  history.value = [trimmed, ...history.value.filter((h) => h !== trimmed)].slice(0, 20)
  localStorage.setItem(HISTORY_KEY, JSON.stringify(history.value))
}

async function runSql() {
  const text = sql.value.trim()
  if (!text || running.value) return
  running.value = true
  execError.value = ''
  try {
    const resp = await executeSql(text)
    if (resp.success) {
      result.value = resp
      pushHistory(text)
    } else {
      result.value = null
      execError.value = resp.error || '执行失败'
    }
  } catch (e) {
    result.value = null
    execError.value = e instanceof Error ? e.message : String(e)
  } finally {
    running.value = false
  }
}

function formatEditor() {
  const text = sql.value.trim()
  if (!text) return
  try {
    sql.value = formatSqlText(text, { language: 'mysql' })
  } catch {
    toast.danger('SQL 解析失败，无法格式化')
  }
}

function applyHistory(text: string) {
  sql.value = text
  editorRef.value?.focus()
}

function insertToEditor(text: string) {
  editorRef.value?.insertText(text)
}

/* ==================== 加入问答对 ==================== */

const saveDialogOpen = ref(false)
const saveQuestion = ref('')
const saveDifficulty = ref('进阶题')
const saving = ref(false)
/** 最近一次 AI 生成的提问（预填问答对的问题字段） */
let lastAiQuestion = ''

function openSaveDialog() {
  if (!sql.value.trim()) return
  saveQuestion.value = lastAiQuestion
  saveDifficulty.value = '进阶题'
  saveDialogOpen.value = true
}

async function doSaveQa() {
  const question = saveQuestion.value.trim()
  const text = sql.value.trim()
  if (!question) {
    toast.warning('请填写业务问题')
    return
  }
  saving.value = true
  try {
    const resp = await saveQa({
      question,
      sql: text,
      result_preview: '',
      difficulty: saveDifficulty.value,
    })
    if (resp.success) {
      toast.success('已保存到问答对库')
      saveDialogOpen.value = false
    } else {
      toast.danger('保存失败')
    }
  } catch (e) {
    toast.danger({ title: '保存失败', desc: e instanceof Error ? e.message : String(e) })
  } finally {
    saving.value = false
  }
}

/* ==================== 结果网格 ==================== */

const resultColumns = computed(() =>
  (result.value?.headers ?? []).map((h, i) => ({
    key: `c${i}`,
    label: h || `col_${i + 1}`,
    minWidth: 120,
    sortable: true,
  })),
)

const resultRows = computed(() =>
  (result.value?.rows ?? []).map((r, ri) => {
    const obj: Record<string, string> = { __idx: String(ri) }
    r.forEach((cell, ci) => {
      obj[`c${ci}`] = cell === null ? 'NULL' : cell
    })
    return obj
  }),
)

/* ==================== 数据资源目录（右栏 Tab 1） ==================== */

const catalogLoading = ref(false)
const catalogNodes = ref<SchemaNode[]>([])
const catalogKeyword = ref('')
/** 已展开的表 → 字段列表（加载过的缓存） */
const expandedTables = reactive<Record<string, TableColumn[]>>({})

const LAYER_LABELS: Record<string, string> = {
  dim: '维表',
  dwd: '事实表',
  ads: '报表层',
  other: '其他',
}
const LAYER_ORDER = ['dim', 'dwd', 'ads', 'other']

const catalogGroups = computed(() => {
  const kw = catalogKeyword.value.trim().toLowerCase()
  const hit = catalogNodes.value.filter(
    (n) =>
      !kw ||
      n.name.toLowerCase().includes(kw) ||
      (n.comment || '').toLowerCase().includes(kw),
  )
  const byLayer = new Map<string, SchemaNode[]>()
  for (const n of hit) {
    const layer = LAYER_LABELS[n.layer] ? n.layer : 'other'
    if (!byLayer.has(layer)) byLayer.set(layer, [])
    byLayer.get(layer)!.push(n)
  }
  return LAYER_ORDER.filter((l) => byLayer.has(l)).map((l) => ({
    layer: l,
    label: LAYER_LABELS[l],
    tables: byLayer.get(l)!,
  }))
})

/** 编辑器补全元数据：表清单 + 已加载过字段的表 */
const schemaForEditor = computed<SqlSchemaTable[]>(() =>
  catalogNodes.value.map((n) => ({
    name: n.name,
    comment: n.comment,
    columns: expandedTables[n.name]?.map((c) => ({ name: c.name, comment: c.comment })),
  })),
)

async function toggleTable(table: string) {
  if (expandedTables[table]) {
    delete expandedTables[table]
    return
  }
  try {
    const resp = await fetchTableColumns(table)
    if (resp.success) expandedTables[table] = resp.columns
  } catch (e) {
    toast.danger(e instanceof Error ? e.message : '字段加载失败')
  }
}

async function loadCatalog() {
  catalogLoading.value = true
  try {
    const resp = await fetchSchemaGraph()
    if (resp.success) catalogNodes.value = resp.nodes
    else toast.danger(resp.error || '目录加载失败')
  } catch (e) {
    toast.danger(e instanceof Error ? e.message : '目录加载失败')
  } finally {
    catalogLoading.value = false
  }
}

/* ==================== AI 助手（右栏 Tab 2） ==================== */

/** 流式生成过程快照（复用智能问答 /generate-sql-stream 的阶段轨 + 思考事件） */
interface AiProcess {
  steps: StreamStep[]
  entries: ThinkingEntry[]
  streams: ThinkingStream[]
  elapsedMs: number
}

interface AiMessage {
  id: number
  role: 'user' | 'assistant'
  /** text=纯文本；sql=SQL 卡片（带插入/替换按钮） */
  kind: 'text' | 'sql'
  text?: string
  sql?: string
  loading?: boolean
  /** 该消息对应的动作（决定卡片按钮组） */
  action?: SqlAssistAction
  /** generate 的流式过程（生成中实时渲染；完成后可折叠回看） */
  process?: AiProcess
  /** 过程区展开态（生成中恒展开） */
  showProcess?: boolean
}

const aiInput = ref('')
const aiBusy = ref(false)
const aiMessages = ref<AiMessage[]>([])
const threadRef = ref<HTMLElement>()
let aiSeq = 0

function pushAi(msg: Omit<AiMessage, 'id'>): AiMessage {
  const full: AiMessage = { ...msg, id: ++aiSeq }
  aiMessages.value.push(full)
  scrollThread()
  return full
}

function replaceAi(target: AiMessage, patch: Partial<AiMessage>) {
  const idx = aiMessages.value.findIndex((m) => m.id === target.id)
  if (idx >= 0) aiMessages.value[idx] = { ...aiMessages.value[idx], ...patch }
  scrollThread()
}

function scrollThread() {
  nextTick(() => {
    const el = threadRef.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

/* ---- 流式生成过程（阶段轨口径与智能问答 QaView 一致） ---- */

const GEN_STAGE_ORDER = ['rag', 'schema', 'llm', 'validate', 'exec'] as const
const GEN_STAGE_TITLES: Record<string, string> = {
  rag: '语义检索',
  schema: 'Schema 选择',
  llm: 'LLM 生成 SQL',
  validate: '自动审查修复',
  exec: '执行取数',
}

function newAiProcess(): AiProcess {
  return {
    steps: GEN_STAGE_ORDER.map((s, i) => ({
      key: s,
      title: GEN_STAGE_TITLES[s],
      status: (i === 0 ? 'running' : 'pending') as StreamStep['status'],
      ms: undefined,
    })),
    entries: [],
    streams: [],
    elapsedMs: 0,
  }
}

function applyGenStage(proc: AiProcess, stage: string, ms: number) {
  const idx = (GEN_STAGE_ORDER as readonly string[]).indexOf(stage)
  if (idx < 0) return
  proc.steps = GEN_STAGE_ORDER.map((s, i) => ({
    key: s,
    title: GEN_STAGE_TITLES[s],
    status: (i <= idx ? 'done' : i === idx + 1 ? 'running' : 'pending') as StreamStep['status'],
    ms: i === idx ? ms : proc.steps[i]?.ms,
  }))
}

function handleGenThinking(proc: AiProcess, t: QaThinking) {
  if (t.kind === 'llm_stream') {
    const phase = t.phase
    const idx = proc.streams.findIndex((s) => s.phase === phase)
    if (idx >= 0) {
      proc.streams[idx] = { phase, text: proc.streams[idx].text + (t.text ?? '') }
    } else {
      proc.streams.push({ phase, text: t.text ?? '' })
    }
    return
  }
  const entry = thinkingToEntry(t)
  if (entry) proc.entries.push(entry)
}

function fmtElapsed(ms: number): string {
  return `${(ms / 1000).toFixed(1)}s`
}

async function callAssist(payload: Parameters<typeof assistSql>[0], pending: AiMessage) {
  aiBusy.value = true
  try {
    const resp = await assistSql(payload)
    if (!resp.success) {
      replaceAi(pending, { loading: false, kind: 'text', text: `失败：${resp.error || '未知错误'}` })
      return
    }
    if (payload.action === 'explain') {
      replaceAi(pending, { loading: false, kind: 'text', text: resp.explanation || '（无解读）' })
    } else {
      replaceAi(pending, {
        loading: false,
        kind: 'sql',
        sql: resp.sql || '',
        text: resp.explanation || resp.note || '',
      })
    }
  } catch (e) {
    replaceAi(pending, {
      loading: false,
      kind: 'text',
      text: `调用失败：${e instanceof Error ? e.message : String(e)}`,
    })
  } finally {
    aiBusy.value = false
  }
}

/** 自然语言生成 SQL：走智能问答同款流式端点，过程（阶段轨 + 思考明细）实时入卡 */
async function sendGenerate() {
  const question = aiInput.value.trim()
  if (!question || aiBusy.value) return
  aiInput.value = ''
  lastAiQuestion = question
  pushAi({ role: 'user', kind: 'text', text: question })
  const pending = pushAi({
    role: 'assistant',
    kind: 'text',
    loading: true,
    action: 'generate',
    process: newAiProcess(),
  })
  aiBusy.value = true

  const t0 = Date.now()
  const clock = setInterval(() => {
    if (pending.process) pending.process.elapsedMs = Date.now() - t0
  }, 80)

  const sse = useSSE<QaStreamEvent, QaStreamResult>('/generate-sql-stream', {
    onEvent: (evt) => {
      const proc = pending.process
      if (!proc) return
      if ('stage' in evt && typeof evt.stage === 'string') {
        applyGenStage(proc, evt.stage, evt.ms)
      } else if ('thinking' in evt && evt.thinking) {
        handleGenThinking(proc, evt.thinking)
      }
      scrollThread()
    },
  })

  try {
    await sse.start({ question, mode: 'qa' })
  } finally {
    clearInterval(clock)
    if (pending.process) pending.process.elapsedMs = Date.now() - t0
  }

  const res = sse.result.value
  if (sse.status.value === 'done' && res?.success) {
    // 兜底：未收到阶段事件的节点补齐为完成态（与智能问答一致）
    if (pending.process) {
      pending.process.steps = pending.process.steps.map((s) =>
        s.status === 'done' ? s : { ...s, status: 'done' as const })
    }
    replaceAi(pending, {
      loading: false,
      kind: 'sql',
      sql: (res.sql as string) || '',
      text: (res.explanation as string) || '',
    })
  } else {
    const errText = (res && (res.error as string)) || sse.error.value?.message || '生成失败'
    replaceAi(pending, { loading: false, kind: 'text', text: `失败：${errText}` })
  }
  aiBusy.value = false
}

/** 快捷操作：explain / optimize（作用于编辑器当前 SQL） */
async function quickAction(action: 'explain' | 'optimize') {
  const text = sql.value.trim()
  if (!text) {
    toast.warning('编辑器为空，请先编写 SQL')
    return
  }
  if (aiBusy.value) return
  const label = action === 'explain' ? '解释当前 SQL' : '优化当前 SQL'
  pushAi({ role: 'user', kind: 'text', text: label })
  const pending = pushAi({ role: 'assistant', kind: 'text', loading: true, action })
  await callAssist({ action, sql: text }, pending)
}

/** 执行报错后的「AI 纠错」入口 */
async function fixFromError() {
  const text = sql.value.trim()
  if (!text || !execError.value || aiBusy.value) return
  pushAi({ role: 'user', kind: 'text', text: `修复执行报错：${execError.value.slice(0, 120)}` })
  const pending = pushAi({ role: 'assistant', kind: 'text', loading: true, action: 'fix' })
  await callAssist({ action: 'fix', sql: text, error: execError.value }, pending)
}

function insertAiSql(msg: AiMessage) {
  if (msg.sql) insertToEditor(msg.sql)
}

function replaceWithAiSql(msg: AiMessage) {
  if (!msg.sql) return
  sql.value = msg.sql
  editorRef.value?.focus()
}

async function replaceAndRun(msg: AiMessage) {
  if (!msg.sql) return
  sql.value = msg.sql
  await runSql()
}

/* ==================== 生命周期 ==================== */

onMounted(() => {
  loadHistory()
  loadCatalog()
})
</script>

<template>
  <div class="sqllab">
    <!-- 左主区 -->
    <section class="sqllab__main">
      <div class="sqllab__toolbar">
        <el-button type="primary" :loading="running" @click="runSql">
          <el-icon><component is="CaretRight" /></el-icon>
          运行（Ctrl+Enter）
        </el-button>
        <el-button @click="formatEditor">格式化</el-button>
        <el-button @click="sql = ''">清空</el-button>
        <el-button :disabled="!sql.trim()" @click="openSaveDialog">
          <el-icon><component is="Collection" /></el-icon>
          加入问答对
        </el-button>
        <el-select
          v-if="history.length"
          class="sqllab__history"
          placeholder="历史语句"
          clearable
          @change="(v: string) => v && applyHistory(v)"
        >
          <el-option
            v-for="(h, i) in history"
            :key="i"
            :value="h"
            :label="h.replace(/\s+/g, ' ').slice(0, 80)"
          />
        </el-select>
      </div>

      <div class="sqllab__editor">
        <SqlEditor ref="editorRef" v-model="sql" :schema="schemaForEditor" @run="runSql" />
      </div>

      <div class="sqllab__result">
        <div v-if="execError" class="sqllab__error">
          <div class="sqllab__error-head">
            <el-icon><component is="WarningFilled" /></el-icon>
            <span>执行失败</span>
            <el-button size="small" type="primary" plain :loading="aiBusy" @click="fixFromError">
              AI 纠错
            </el-button>
          </div>
          <pre class="sqllab__error-body">{{ execError }}</pre>
        </div>

        <template v-else-if="result">
          <div class="sqllab__result-meta">
            <el-tag size="small" type="success">{{ result.row_count }} 行</el-tag>
            <el-tag v-if="result.elapsed_ms != null" size="small" type="info">
              {{ result.elapsed_ms }} ms
            </el-tag>
          </div>
          <DsDataTable
            :columns="resultColumns"
            :rows="resultRows"
            row-key="__idx"
            table-id="sqllab-result"
            :density-control="false"
          />
        </template>

        <el-empty v-else description="编写 SQL 后按 Ctrl+Enter 运行（仅支持 SELECT / WITH 只读查询）" />
      </div>
    </section>

    <!-- 右侧栏 -->
    <aside class="sqllab__side">
      <el-tabs class="sqllab__tabs">
        <el-tab-pane label="数据资源目录">
          <div class="sqllab__catalog">
            <el-input
              v-model="catalogKeyword"
              placeholder="搜索表名 / 注释"
              clearable
              size="small"
            />
            <div v-loading="catalogLoading" class="sqllab__catalog-list">
              <div v-for="group in catalogGroups" :key="group.layer" class="catalog-group">
                <div class="catalog-group__label">
                  {{ group.label }}（{{ group.tables.length }}）
                </div>
                <div v-for="t in group.tables" :key="t.name" class="catalog-table">
                  <div class="catalog-table__head" @click="toggleTable(t.name)">
                    <el-icon class="catalog-table__caret">
                      <component :is="expandedTables[t.name] ? 'ArrowDown' : 'ArrowRight'" />
                    </el-icon>
                    <span class="catalog-table__name" :title="t.comment" @click.stop="insertToEditor(t.name)">
                      {{ t.name }}
                    </span>
                    <span class="catalog-table__comment">{{ t.comment }}</span>
                  </div>
                  <div v-if="expandedTables[t.name]" class="catalog-table__cols">
                    <div
                      v-for="c in expandedTables[t.name]"
                      :key="c.name"
                      class="catalog-col"
                      :title="`${c.type}${c.comment ? ' · ' + c.comment : ''}`"
                      @click="insertToEditor(`${t.name}.${c.name}`)"
                    >
                      <span class="catalog-col__name">
                        <el-icon v-if="c.pk" class="catalog-col__pk"><component is="Key" /></el-icon>
                        {{ c.name }}
                      </span>
                      <span class="catalog-col__comment">{{ c.comment }}</span>
                    </div>
                  </div>
                </div>
              </div>
              <el-empty
                v-if="!catalogLoading && !catalogGroups.length"
                description="无匹配表"
                :image-size="60"
              />
            </div>
          </div>
        </el-tab-pane>

        <el-tab-pane label="AI 助手">
          <div class="sqllab__ai">
            <div class="sqllab__ai-quick">
              <el-button size="small" :disabled="aiBusy" @click="quickAction('explain')">
                解释当前 SQL
              </el-button>
              <el-button size="small" :disabled="aiBusy" @click="quickAction('optimize')">
                优化当前 SQL
              </el-button>
            </div>

            <div ref="threadRef" class="sqllab__ai-thread">
              <div
                v-for="m in aiMessages"
                :key="m.id"
                class="ai-msg"
                :class="`ai-msg--${m.role}`"
              >
                <div v-if="m.loading && !m.process" class="ai-msg__loading">
                  <el-icon class="is-loading"><component is="Loading" /></el-icon> 正在生成…
                </div>

                <!-- 流式生成过程：生成中实时展开；完成后折叠为摘要，可点开回看 -->
                <div v-if="m.process" class="ai-msg__process">
                  <div v-if="m.loading" class="ai-msg__loading">
                    <el-icon class="is-loading"><component is="Loading" /></el-icon>
                    正在生成 · {{ fmtElapsed(m.process.elapsedMs) }}
                  </div>
                  <button v-else class="process-toggle" type="button" @click="m.showProcess = !m.showProcess">
                    <el-icon><component :is="m.showProcess ? 'ArrowDown' : 'ArrowRight'" /></el-icon>
                    生成过程 · {{ fmtElapsed(m.process.elapsedMs) }}
                  </button>
                  <div v-show="m.loading || m.showProcess" class="ai-msg__process-body">
                    <DsStepRail
                      :steps="m.process.steps"
                      :elapsed-ms="m.loading ? m.process.elapsedMs : undefined"
                    />
                    <DsThinkingPanel
                      v-if="m.process.entries.length || m.process.streams.length"
                      :entries="m.process.entries"
                      :streams="m.process.streams"
                    />
                  </div>
                </div>

                <template v-if="!m.loading">
                  <div v-if="m.text" class="ai-msg__text">{{ m.text }}</div>
                  <div v-if="m.kind === 'sql' && m.sql" class="ai-msg__sql">
                    <DsSqlViewer :sql="m.sql" :typing="false" />
                    <div class="ai-msg__actions">
                      <el-button size="small" @click="insertAiSql(m)">插入光标处</el-button>
                      <el-button size="small" @click="replaceWithAiSql(m)">替换全部</el-button>
                      <el-button size="small" type="primary" @click="replaceAndRun(m)">
                        替换并运行
                      </el-button>
                    </div>
                  </div>
                </template>
              </div>
              <el-empty
                v-if="!aiMessages.length"
                description="描述取数需求，AI 生成 SQL 后可插入编辑器"
                :image-size="60"
              />
            </div>

            <div class="sqllab__ai-input">
              <el-input
                v-model="aiInput"
                type="textarea"
                :rows="3"
                placeholder="例：查全省各单位本月售电量，按单位排序"
                @keydown.ctrl.enter="sendGenerate"
              />
              <el-button
                type="primary"
                :loading="aiBusy"
                :disabled="!aiInput.trim()"
                @click="sendGenerate"
              >
                生成 SQL
              </el-button>
            </div>
          </div>
        </el-tab-pane>
      </el-tabs>
    </aside>

    <!-- 加入问答对弹窗 -->
    <el-dialog v-model="saveDialogOpen" title="加入问答对" width="560px" append-to-body>
      <div class="save-form">
        <div class="save-form__label">业务问题</div>
        <el-input
          v-model="saveQuestion"
          type="textarea"
          :rows="2"
          placeholder="这段 SQL 回答的业务问题，例：查全省各单位本月售电量"
        />
        <div class="save-form__label">难度</div>
        <el-select v-model="saveDifficulty" style="width: 160px">
          <el-option label="基础题" value="基础题" />
          <el-option label="进阶题" value="进阶题" />
          <el-option label="挑战题" value="挑战题" />
        </el-select>
        <div class="save-form__label">SQL</div>
        <DsSqlViewer :sql="sql" :typing="false" :copyable="false" />
      </div>
      <template #footer>
        <el-button @click="saveDialogOpen = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="doSaveQa">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.sqllab {
  display: flex;
  gap: 12px;
  height: 100%;
  min-height: 0;
}

.sqllab__main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.sqllab__toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
}

.sqllab__history {
  width: 260px;
  margin-left: auto;
}

.sqllab__editor {
  height: 240px;
  flex-shrink: 0;
}

.sqllab__result {
  flex: 1;
  min-height: 0;
  overflow: auto;
  background: var(--surface-1);
  border: 1px solid var(--line-subtle);
  border-radius: 8px;
  padding: 10px;
}

.sqllab__result-meta {
  display: flex;
  gap: 8px;
  margin-bottom: 8px;
}

.sqllab__error {
  border: 1px solid var(--el-color-danger-light-5);
  background: var(--el-color-danger-light-9);
  border-radius: 8px;
  padding: 10px 12px;
}

.sqllab__error-head {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--el-color-danger);
  font-weight: 600;
}

.sqllab__error-head .el-button {
  margin-left: auto;
}

.sqllab__error-body {
  margin: 8px 0 0;
  white-space: pre-wrap;
  word-break: break-all;
  font-size: 12px;
  color: var(--text-2);
}

/* 右侧栏 */
.sqllab__side {
  width: 360px;
  flex-shrink: 0;
  background: var(--surface-1);
  border: 1px solid var(--line-subtle);
  border-radius: 8px;
  padding: 4px 10px 10px;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.sqllab__tabs {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.sqllab__tabs :deep(.el-tabs__content) {
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.sqllab__tabs :deep(.el-tab-pane) {
  height: 100%;
}

/* 目录 */
.sqllab__catalog {
  height: 100%;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.sqllab__catalog-list {
  flex: 1;
  min-height: 0;
  overflow: auto;
}

.catalog-group__label {
  font-size: 12px;
  color: var(--text-3);
  margin: 10px 0 4px;
}

.catalog-table__head {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 4px 2px;
  cursor: pointer;
  border-radius: 4px;
}

.catalog-table__head:hover {
  background: var(--surface-2);
}

.catalog-table__caret {
  color: var(--text-4);
  flex-shrink: 0;
}

.catalog-table__name {
  font-family: var(--el-font-family-mono, monospace);
  font-size: 12px;
  color: var(--accent);
}

.catalog-table__comment,
.catalog-col__comment {
  font-size: 12px;
  color: var(--text-3);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.catalog-table__cols {
  margin-left: 18px;
  border-left: 1px dashed var(--line);
  padding-left: 8px;
}

.catalog-col {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 2px 4px;
  cursor: pointer;
  border-radius: 4px;
  font-size: 12px;
}

.catalog-col:hover {
  background: var(--surface-2);
}

.catalog-col__name {
  font-family: var(--el-font-family-mono, monospace);
  color: var(--text-1);
  display: inline-flex;
  align-items: center;
  gap: 3px;
}

.catalog-col__pk {
  color: var(--el-color-warning);
}

/* AI 助手 */
.sqllab__ai {
  height: 100%;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.sqllab__ai-quick {
  display: flex;
  gap: 8px;
}

.sqllab__ai-thread {
  flex: 1;
  min-height: 0;
  overflow: auto;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.ai-msg {
  max-width: 100%;
  border-radius: 8px;
  padding: 8px 10px;
  font-size: 13px;
}

.ai-msg--user {
  align-self: flex-end;
  background: var(--accent-soft);
  color: var(--text-1);
}

.ai-msg--assistant {
  align-self: flex-start;
  background: var(--surface-2);
  border: 1px solid var(--line-subtle);
  width: 100%;
}

.ai-msg__text {
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--text-1);
}

.ai-msg__loading {
  color: var(--text-3);
  display: flex;
  align-items: center;
  gap: 6px;
}

.ai-msg__process {
  margin-bottom: 4px;
}

.process-toggle {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  border: none;
  background: none;
  padding: 2px 0;
  font-size: 12px;
  color: var(--text-3);
  cursor: pointer;
}

.process-toggle:hover {
  color: var(--accent);
}

.ai-msg__process-body {
  margin-top: 6px;
  padding-top: 6px;
  border-top: 1px dashed var(--line);
}

.ai-msg__sql {
  margin-top: 8px;
}

.ai-msg__actions {
  display: flex;
  gap: 6px;
  margin-top: 6px;
}

.sqllab__ai-input {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.sqllab__ai-input .el-button {
  align-self: flex-end;
}

/* 加入问答对弹窗 */
.save-form {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.save-form__label {
  font-size: 12px;
  color: var(--text-3);
  margin-top: 4px;
}
</style>
