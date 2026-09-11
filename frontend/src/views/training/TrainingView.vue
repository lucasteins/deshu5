<script setup lang="ts">
/**
 * 训练模式（F2.1）——设计稿 §4.5「1. 训练模式」+ 旧实现 static/js/app.js 训练模块对等迁移。
 *
 * 流程：出题 → 合理性评价 → SQL 生成（流式）→ 结果判断（正确入问答对库 / 错误归因 / 跳过）。
 * 右侧悬浮面板：训练流程轨（4 步）+ 出题业务域 + 生成上下文（生成路径 / 数据表 / 码值 / 耗时分解）。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElButton, ElCheckbox, ElDialog, ElSelect, ElOption } from 'element-plus'
import { useSSE } from '@/composables'
import type { QaStreamEvent, QaStreamResult, QaThinking } from '@/types'
import {
  DsSqlViewer,
  DsThinkingPanel,
  toast,
  thinkingToEntry,
  type ThinkingEntry,
  type ThinkingStream,
} from '@/components'
import {
  ERROR_DIMENSIONS,
  evaluateQuestion,
  fetchBusinessDomains,
  fetchNextQuestion,
  recordError,
  submitJudgment,
  type BusinessDomains,
  type Judgment,
} from '@/api/training'
import { asQaResult, type QaResult } from '@/api/chat'

/* ================= 业务域 ================= */
const domains = ref<BusinessDomains>({ l1: [], l2: [] })
const domainL1 = ref('')
const domainL2 = ref('')

async function loadDomains() {
  try {
    const res = await fetchBusinessDomains()
    domains.value = res.items || { l1: [], l2: [] }
  } catch (e) {
    toast.warning(e instanceof Error ? e.message : '业务域加载失败')
  }
}
onMounted(loadDomains)

const domainL2Options = computed(() => {
  const scope = domainL1.value
  const pool = scope ? domains.value.l2.filter((d) => d.parent === scope) : domains.value.l2
  return pool
})

function currentDomain(): string {
  return domainL2.value || domainL1.value || ''
}

function domainName(code: string | null | undefined): string {
  if (!code) return '不限'
  const d = domains.value.l2.find((x) => x.code === code) || domains.value.l1.find((x) => x.code === code)
  return d ? d.name : code
}

function domainLabel(code: string | null | undefined): string {
  if (!code) return '不限'
  const l2 = domains.value.l2.find((x) => x.code === code)
  if (l2 && l2.parent) {
    const l1 = domainName(l2.parent)
    if (l1 && l1 !== l2.parent) return `${l1} · ${l2.name}`
  }
  return domainName(code) || code
}

/* ================= 会话与流程 ================= */
interface Session {
  sessionId: string
  qaId: number | null
  question: string
  difficulty: string
  domain: string | null
  generated: boolean
  questionRated: boolean
  questionRating: string | null
}

const session = ref<Session | null>(null)
const flowStep = ref(1) // 1 出题 / 2 合理性评价 / 3 SQL 生成 / 4 结果判断

const sql = ref('')
const rawSql = ref('')
const resultPreview = ref<QaResult['result_preview']>(null)
const showRaw = ref(false)

/** 生成上下文（悬浮面板） */
const context = ref<{
  generationMode: string
  workflow: string
  usage: { llm_calls?: number; total_tokens?: number }
  locatedTables: string[]
  codeValueHits: { cn_name?: string; code_name?: string; matched?: string[] }[]
  genTimers: Record<string, number>
} | null>(null)

/* ================= 思考窗口 ================= */
const thinkingEntries = ref<ThinkingEntry[]>([])
const thinkingStreams = ref<ThinkingStream[]>([])

const sse = useSSE<QaStreamEvent, QaStreamResult>('/generate-sql-stream', {
  onEvent: (evt) => {
    if ('thinking' in evt && evt.thinking) handleThinking(evt.thinking)
  },
})

function handleThinking(t: QaThinking) {
  if (t.kind === 'llm_stream') {
    const idx = thinkingStreams.value.findIndex((s) => s.phase === t.phase)
    if (idx >= 0) {
      thinkingStreams.value[idx] = { phase: t.phase, text: thinkingStreams.value[idx].text + (t.text ?? '') }
    } else {
      thinkingStreams.value.push({ phase: t.phase, text: t.text ?? '' })
    }
    return
  }
  const entry = thinkingToEntry(t)
  if (entry) thinkingEntries.value.push(entry)
}

function resetThinking() {
  thinkingEntries.value = []
  thinkingStreams.value = []
}

/* ================= 出题 ================= */
const generatingQuestion = ref(false)

async function generateQuestion() {
  if (generatingQuestion.value) return
  generatingQuestion.value = true
  try {
    const data = await fetchNextQuestion(currentDomain() ? { domain: currentDomain() } : undefined)
    session.value = {
      sessionId: data.session_id,
      qaId: data.qa_id,
      question: data.question,
      difficulty: data.difficulty,
      domain: data.domain,
      generated: data.generated,
      questionRated: false,
      questionRating: null,
    }
    sql.value = ''
    rawSql.value = ''
    resultPreview.value = null
    context.value = null
    showRaw.value = false
    resetThinking()
    flowStep.value = 2
  } catch (e) {
    toast.danger({ title: '出题失败', desc: e instanceof Error ? e.message : String(e) })
  } finally {
    generatingQuestion.value = false
  }
}

/* ================= 合理性评价 ================= */
const feedback = ref('')
const evaluating = ref(false)

async function rateQuestion(rating: '合理' | '不合理') {
  if (!session.value) return
  evaluating.value = true
  try {
    await evaluateQuestion({
      session_id: session.value.sessionId,
      rating,
      feedback: feedback.value.trim(),
    })
    session.value.questionRated = true
    session.value.questionRating = rating
    if (rating === '不合理') {
      // 不合理题目直接出下一题
      await generateQuestion()
      return
    }
    feedback.value = ''
    flowStep.value = 3
    await generateTrainingSql()
  } catch (e) {
    toast.danger({ title: '评价提交失败', desc: e instanceof Error ? e.message : String(e) })
  } finally {
    evaluating.value = false
  }
}

/* ================= SQL 生成（流式） ================= */
const generatingSql = ref(false)

async function generateTrainingSql() {
  if (!session.value || !session.value.question) return
  generatingSql.value = true
  resetThinking()
  context.value = null
  try {
    await sse.start({
      question: session.value.question,
      no_reference: session.value.generated,
      mode: 'training',
      qa_id: session.value.qaId,
      generated: session.value.generated,
    })
  } finally {
    generatingSql.value = false
  }

  if (sse.status.value === 'done') {
    const r = asQaResult(sse.result.value)
    if (r && r.success) {
      sql.value = r.sql ?? ''
      rawSql.value = r.raw_sql || r.sql || ''
      resultPreview.value = r.result_preview ?? null
      context.value = {
        generationMode: r.generation_mode || '—',
        workflow: r.workflow || '',
        usage: r.usage || {},
        locatedTables: r.located_tables || [],
        codeValueHits: r.code_value_hits || [],
        genTimers: r.gen_timers || {},
      }
      flowStep.value = 4
    } else {
      sql.value = r?.raw_sql || r?.sql || '-- 未生成 SQL'
      toast.danger({ title: 'SQL 生成失败', desc: r?.error || '未知错误' })
    }
  } else if (sse.status.value === 'error') {
    toast.danger({ title: 'SQL 生成失败', desc: sse.error.value?.message || '流式响应中断' })
  }
}

/* ================= 结果判断 ================= */
const judging = ref(false)

async function judge(judgment: Judgment) {
  if (!session.value || judging.value) return
  judging.value = true
  try {
    await submitJudgment({ session_id: session.value.sessionId, judgment, feedback: '' })
    if (judgment === '错误') {
      errorModalVisible.value = true
    } else {
      await generateQuestion()
    }
  } catch (e) {
    toast.danger({ title: '提交失败', desc: e instanceof Error ? e.message : String(e) })
  } finally {
    judging.value = false
  }
}

/* ================= 错误归因弹窗 ================= */
const errorModalVisible = ref(false)
const errorDims = ref<Record<string, boolean>>({})
const correctSql = ref('')
const errorDetail = ref('')
const submittingError = ref(false)

function openErrorModal() {
  errorDims.value = {}
  correctSql.value = ''
  errorDetail.value = ''
  errorModalVisible.value = true
}

async function submitErrorRecord() {
  if (!session.value) return
  const checked = ERROR_DIMENSIONS.filter((d) => errorDims.value[d.key])
  const errorType = checked.length === 1 ? checked[0].label : checked.length > 1 ? '多维度错误' : '其他'
  const dimPayload: Record<string, boolean> = {}
  for (const d of ERROR_DIMENSIONS) dimPayload[d.key] = !!errorDims.value[d.key]

  submittingError.value = true
  try {
    await recordError({
      session_id: session.value.sessionId,
      error_type: errorType,
      correct_sql: correctSql.value.trim(),
      error_detail: errorDetail.value.trim(),
      ...dimPayload,
    })
    toast.success('错误记录已保存')
    errorModalVisible.value = false
    await generateQuestion()
  } catch (e) {
    toast.danger({ title: '提交失败', desc: e instanceof Error ? e.message : String(e) })
  } finally {
    submittingError.value = false
  }
}

/* ================= 生成上下文展示 ================= */
const genModeLabel = computed(() => {
  const c = context.value
  if (!c) return '—'
  const parts = [c.generationMode || '—']
  if (c.workflow) parts.push(`wf:${c.workflow}`)
  if (c.usage.llm_calls) parts.push(`LLM×${c.usage.llm_calls} · ${c.usage.total_tokens}tok`)
  return parts.join(' · ')
})

const timingRows = computed(() => {
  const t = context.value?.genTimers || {}
  const defs: [string, string][] = [
    ['RAG', 'rag_retrieve'],
    ['意图', 'intent_parse'],
    ['草稿', 'draft_build'],
    ['定位', 'table_locate'],
    ['提示词', 'prompt_build'],
    ['LLM', 'llm_call'],
    ['校验', 'validation'],
  ]
  const rows = defs
    .filter(([, k]) => typeof t[k] === 'number')
    .map(([name, k]) => ({ name, value: t[k] as number }))
  const total = rows.reduce((s, r) => s + r.value, 0) || 1
  return rows.map((r) => ({ ...r, pct: Math.max(2, (r.value / total) * 100) }))
})

const displaySql = computed(() => (showRaw.value ? rawSql.value : sql.value))
const hasRawToggle = computed(() => !!rawSql.value && rawSql.value !== sql.value)

onBeforeUnmount(() => sse.abort())
</script>

<template>
  <div class="training">
    <div class="training-workbench">
      <!-- 主工作区 -->
      <main class="training-main">
        <!-- 题目卡 -->
        <div class="tr-card">
          <div class="tr-card-hd">
            <h3>业务问题</h3>
            <span v-if="session" class="badge badge--info">难度：{{ session.difficulty }}</span>
            <span v-if="session" class="badge badge--muted">业务域：{{ domainLabel(session.domain) }}</span>
          </div>
          <div class="tr-question">{{ session?.question || '点击右侧「生成题目」开始训练' }}</div>
        </div>

        <!-- 思考过程 -->
        <div v-if="thinkingEntries.length || thinkingStreams.length" class="tr-card">
          <div class="tr-card-hd"><h3>思考过程</h3></div>
          <div class="tr-card-bd">
            <DsThinkingPanel :entries="thinkingEntries" :streams="thinkingStreams" />
          </div>
        </div>

        <!-- 问题合理性评价 -->
        <div v-if="session && !session.questionRated" class="tr-card">
          <div class="tr-card-hd"><h3>问题合理性评价</h3></div>
          <div class="tr-card-bd">
            <p class="tr-hint">请先判断这道业务题是否合理，再生成 SQL 进行训练。</p>
            <textarea
              v-model="feedback"
              class="tr-textarea"
              rows="2"
              placeholder="选填：补充反馈（如问题歧义、无法回答等）"
            />
            <div class="tr-actions">
              <ElButton type="success" :loading="evaluating" @click="rateQuestion('合理')">合理</ElButton>
              <ElButton type="danger" :loading="evaluating" @click="rateQuestion('不合理')">不合理</ElButton>
            </div>
          </div>
        </div>

        <!-- 生成的 SQL -->
        <div v-if="sql" class="tr-card">
          <div class="tr-card-hd">
            <h3>生成的 SQL</h3>
            <button v-if="hasRawToggle" class="ghost-btn" type="button" @click="showRaw = !showRaw">
              {{ showRaw ? '查看别名 SQL' : '查看原始 SQL' }}
            </button>
          </div>
          <div class="tr-card-bd">
            <DsSqlViewer :sql="displaySql" :typing="true" />
          </div>
        </div>

        <!-- 执行结果 -->
        <div v-if="resultPreview" class="tr-card">
          <div class="tr-card-hd">
            <h3>执行结果预览</h3>
            <span v-if="resultPreview.success !== false" class="badge badge--success">执行成功</span>
            <span v-else class="badge badge--danger">执行失败</span>
          </div>
          <div v-if="resultPreview.success === false" class="tr-result-error">{{ resultPreview.error }}</div>
          <div v-else class="tr-tablewrap">
            <table class="tr-table">
              <thead>
                <tr><th v-for="(h, i) in resultPreview.headers" :key="i">{{ h }}</th></tr>
              </thead>
              <tbody>
                <tr v-for="(row, ri) in resultPreview.rows" :key="ri">
                  <td v-for="(c, ci) in row" :key="ci" :class="{ num: typeof c === 'number' }">{{ c === null ? 'NULL' : c }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <!-- 结果判断（大热区按钮组） -->
        <div v-if="flowStep === 4" class="tr-card">
          <div class="tr-card-bd judge-row">
            <span class="judge-label">人工判定：</span>
            <ElButton type="success" size="large" :loading="judging" @click="judge('正确')">正确 · 入问答对库</ElButton>
            <ElButton type="danger" size="large" :loading="judging" @click="judge('错误')">错误 · 归因</ElButton>
            <ElButton size="large" :loading="judging" @click="judge('跳过')">跳过</ElButton>
          </div>
        </div>
      </main>

      <!-- 右侧悬浮面板 -->
      <aside class="training-side">
        <!-- 训练流程轨 -->
        <div class="tr-card rail">
          <div class="tr-card-hd"><h3>训练流程</h3></div>
          <div class="flow-list">
            <div
              v-for="(step, i) in ['出题', '合理性评价', 'SQL 生成', '结果判断']"
              :key="i"
              class="flow-step"
              :class="{ done: i + 1 < flowStep, active: i + 1 === flowStep }"
            >
              <span class="flow-num">{{ i + 1 }}</span>
              <span class="flow-title">{{ step }}</span>
            </div>
          </div>
          <div class="rail-section">
            <div class="rail-label">出题业务域</div>
            <ElSelect v-model="domainL1" placeholder="全部业务域" clearable class="rail-select" @change="domainL2 = ''">
              <ElOption v-for="d in domains.l1" :key="d.code" :label="`${d.name}（${d.table_count} 表）`" :value="d.code" />
            </ElSelect>
            <ElSelect v-model="domainL2" placeholder="全部二级域" clearable class="rail-select rail-select--mt">
              <ElOption v-for="d in domainL2Options" :key="d.code" :label="`${d.name}（${d.table_count} 表）`" :value="d.code" />
            </ElSelect>
          </div>
          <div class="rail-footer">
            <ElButton type="primary" :loading="generatingQuestion" @click="generateQuestion">生成题目</ElButton>
          </div>
        </div>

        <!-- 生成上下文 -->
        <div class="tr-card rail">
          <div class="tr-card-hd"><h3>生成上下文</h3></div>
          <div v-if="!context" class="rail-empty">生成题目并产出 SQL 后，这里展示生成路径、定位数据表、码值命中与耗时分解。</div>
          <div v-else class="ctx-body">
            <div class="ctx-block">
              <div class="rail-label">生成路径</div>
              <div class="ctx-mode">{{ genModeLabel }}</div>
            </div>
            <div v-if="context.locatedTables.length" class="ctx-block">
              <div class="rail-label">定位数据表</div>
              <div class="chip-list">
                <span v-for="t in context.locatedTables" :key="t" class="chip">{{ t }}</span>
              </div>
            </div>
            <div v-if="context.codeValueHits.length" class="ctx-block">
              <div class="rail-label">码值命中</div>
              <div class="chip-list">
                <span
                  v-for="(h, i) in context.codeValueHits"
                  :key="i"
                  class="chip"
                  :class="{ 'chip--hit': h.matched && h.matched.length }"
                  :title="h.matched && h.matched.length ? `命中: ${h.matched.join('、')}` : h.code_name || ''"
                >
                  {{ h.cn_name || h.code_name }}
                </span>
              </div>
            </div>
            <div v-if="timingRows.length" class="ctx-block">
              <div class="rail-label">耗时分解</div>
              <div class="timing-bars">
                <div v-for="r in timingRows" :key="r.name" class="timing-row">
                  <span class="timing-name">{{ r.name }}</span>
                  <div class="timing-track"><div class="timing-fill" :style="{ width: r.pct + '%' }" /></div>
                  <span class="timing-val tabular">{{ Math.round(r.value) }}ms</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </aside>
    </div>

    <!-- 错误归因弹窗 -->
    <ElDialog v-model="errorModalVisible" title="错误归因" width="560px">
      <div class="err-dlg">
        <div class="err-label">错误维度（可多选）</div>
        <div class="err-dims">
          <ElCheckbox v-for="d in ERROR_DIMENSIONS" :key="d.key" v-model="errorDims[d.key]">
            {{ d.label }}
          </ElCheckbox>
        </div>
        <div class="err-label">修正 SQL（选填）</div>
        <textarea v-model="correctSql" class="tr-textarea" rows="4" placeholder="如已定位正确写法，可在此提供修正 SQL" />
        <div class="err-label">错误说明（选填）</div>
        <textarea v-model="errorDetail" class="tr-textarea" rows="3" placeholder="补充错误原因，帮助改进生成质量" />
      </div>
      <template #footer>
        <ElButton @click="errorModalVisible = false">取消</ElButton>
        <ElButton type="danger" :loading="submittingError" @click="submitErrorRecord">提交记录</ElButton>
      </template>
    </ElDialog>
  </div>
</template>

<style scoped>
.training {
  height: 100%;
  display: flex;
  flex-direction: column;
}
.training-workbench {
  flex: 1;
  min-height: 0;
  display: flex;
  gap: 16px;
  overflow: hidden;
}
.training-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 14px;
  overflow-y: auto;
}
.training-main > * {
  flex: none;
}
.training-side {
  width: 340px;
  flex: none;
  display: flex;
  flex-direction: column;
  gap: 14px;
  overflow-y: auto;
}
.tr-card {
  background: var(--surface-1);
  border: 1px solid var(--line);
  border-radius: var(--r-md);
  box-shadow: var(--e1);
  overflow: hidden;
}
.tr-card-hd {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 16px;
  border-bottom: 1px solid var(--line-subtle);
}
.tr-card-hd h3 {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-strong);
}
.tr-card-bd {
  padding: 14px 16px;
}
.tr-card-bd > * + * {
  margin-top: 12px;
}
.tr-question {
  padding: 14px 16px;
  font-size: 15px;
  line-height: 26px;
  color: var(--text-strong);
}
.tr-hint {
  font-size: 13px;
  color: var(--text-3);
}
.tr-textarea {
  width: 100%;
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  padding: 8px 10px;
  font-family: var(--font-ui);
  font-size: 13px;
  line-height: 20px;
  color: var(--text-1);
  resize: vertical;
  background: var(--surface-1);
}
.tr-textarea:focus {
  outline: none;
  border-color: var(--accent);
  box-shadow: var(--focus-ring);
}
.tr-actions {
  display: flex;
  gap: 10px;
}
.badge {
  display: inline-flex;
  align-items: center;
  height: 22px;
  padding: 0 10px;
  border-radius: var(--r-full);
  font-size: 12px;
  font-weight: 500;
}
.badge--info {
  background: var(--info-soft);
  color: var(--info);
}
.badge--muted {
  background: var(--sunken);
  color: var(--text-3);
}
.badge--success {
  background: var(--success-soft);
  color: var(--success);
}
.badge--danger {
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
.tr-result-error {
  padding: 14px 16px;
  font-size: 13px;
  color: var(--danger);
}
.tr-tablewrap {
  overflow: auto;
}
.tr-table {
  border-collapse: separate;
  border-spacing: 0;
  width: 100%;
  font-size: 13px;
}
.tr-table th {
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
.tr-table td {
  padding: 8px 14px;
  border-bottom: 1px solid var(--line-subtle);
  color: var(--text-1);
  white-space: nowrap;
}
.tr-table td.num {
  text-align: right;
}
.tr-table tbody tr:hover td {
  background: var(--surface-2);
}

/* 判断按钮 */
.judge-row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.judge-label {
  font-size: 14px;
  color: var(--text-2);
}

/* 流程轨 */
.rail {
  overflow: visible;
}
.flow-list {
  padding: 12px 16px 4px;
}
.flow-step {
  position: relative;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 0;
  color: var(--text-3);
}
.flow-step::before {
  content: '';
  position: absolute;
  left: 11px;
  top: 32px;
  bottom: -8px;
  width: 1px;
  background: var(--line);
}
.flow-step:last-child::before {
  display: none;
}
.flow-num {
  flex: none;
  width: 22px;
  height: 22px;
  border-radius: var(--r-full);
  border: 1.5px solid var(--line-strong);
  display: grid;
  place-items: center;
  font-family: var(--font-mono);
  font-size: 11px;
  background: var(--surface-1);
  z-index: 1;
}
.flow-step.done .flow-num {
  background: var(--accent);
  border-color: var(--accent);
  color: var(--text-on-accent);
}
.flow-step.active .flow-num {
  border-color: var(--accent);
  color: var(--accent);
  border-width: 2px;
}
.flow-step.active .flow-title {
  color: var(--text-strong);
  font-weight: 600;
}
.flow-title {
  font-size: 13.5px;
}

.rail-section {
  padding: 12px 16px;
  border-top: 1px solid var(--line-subtle);
}
.rail-label {
  font-size: 12px;
  color: var(--text-3);
  margin-bottom: 6px;
}
.rail-select {
  width: 100%;
}
.rail-select--mt {
  margin-top: 8px;
}
.rail-footer {
  padding: 12px 16px;
  border-top: 1px solid var(--line-subtle);
}
.rail-footer :deep(.el-button) {
  width: 100%;
}
.rail-empty {
  padding: 14px 16px;
  font-size: 12.5px;
  color: var(--text-3);
  line-height: 20px;
}
.ctx-body {
  padding: 8px 16px 14px;
}
.ctx-block {
  margin-top: 10px;
}
.ctx-mode {
  font-size: 12.5px;
  color: var(--text-1);
  line-height: 18px;
  word-break: break-word;
}
.chip-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.chip {
  padding: 1px 8px;
  border-radius: var(--r-full);
  background: var(--surface-2);
  border: 1px solid var(--line);
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text-2);
}
.chip--hit {
  background: var(--accent-soft);
  border-color: var(--accent-line);
  color: var(--accent);
}
.timing-bars {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.timing-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.timing-name {
  flex: none;
  width: 40px;
  font-size: 12px;
  color: var(--text-3);
}
.timing-track {
  flex: 1;
  height: 6px;
  border-radius: var(--r-full);
  background: var(--sunken);
  overflow: hidden;
}
.timing-fill {
  height: 100%;
  border-radius: var(--r-full);
  background: var(--accent);
}
.timing-val {
  flex: none;
  font-family: var(--font-mono);
  font-size: 11.5px;
  color: var(--text-2);
}

/* 错误归因弹窗 */
.err-dlg {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.err-label {
  font-size: 13px;
  color: var(--text-2);
  font-weight: 500;
}
.err-dims {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 16px;
}
</style>
