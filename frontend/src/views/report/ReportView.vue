<script setup lang="ts">
/**
 * 深度分析 · 报告生成（F2.4）——设计稿 §4.5.4「深入设计 · 深度分析 · 报告生成」
 * + §4.4.4「报告生成的差异」+ §4.5「9. 深度分析」。
 *
 * 三页签（报告生成 / 历史报告 / 模板管理）由 AppShell TabBar 经 URL `#tab=` 驱动；
 * 生成态三栏：大纲（章节进度） | 报告正文（按章节渐显 + 折叠） | 取数任务（逐题进度 + 溯源）。
 * SSE：POST /api/report/generate（plan → question → composed → done）。
 */
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { useSSE } from '@/composables'
import type { ReportStreamEvent } from '@/types'
import {
  createReportTemplate,
  deleteReportRun,
  deleteReportTemplate,
  distillReportTemplate,
  fetchReportRun,
  fetchReportRuns,
  fetchReportTemplate,
  fetchReportTemplates,
  makeReportPlan,
  reportRunExportUrl,
  updateReportTemplate,
  type ReportGenerateResult,
  type ReportPlan,
  type ReportQuestion,
  type ReportQuestionResult,
  type ReportRunDetail,
  type ReportRunItem,
  type ReportTemplate,
  type ReportTemplateQuestion,
  type ReportTemplateSection,
} from '@/api/report'
import { DsEmpty, confirm, toast } from '@/components'

/* ================= 页签（URL #tab 驱动） ================= */

type ReportTab = 'gen' | 'runs' | 'tpl'
const activeTab = ref<ReportTab>('gen')

function readTab(): ReportTab {
  const m = /(?:^#|&)tab=([^&]*)/.exec(window.location.hash)
  const v = m ? decodeURIComponent(m[1]) : ''
  return v === 'runs' || v === 'tpl' ? v : 'gen'
}
function syncTab() {
  activeTab.value = readTab()
  if (activeTab.value === 'runs') void loadRuns()
  if (activeTab.value === 'tpl') void loadTemplates()
}
onMounted(() => {
  syncTab()
  window.addEventListener('hashchange', syncTab)
  void loadTemplates()
})
onBeforeUnmount(() => {
  window.removeEventListener('hashchange', syncTab)
  stopReveal()
  sse.abort()
})

/* ================= 计划（输入态） ================= */

const mode = ref<'intent' | 'template'>('intent')
const intent = ref('')
const templateList = ref<ReportTemplate[]>([])
const templateId = ref('')
const period = ref('')
const org = ref('')
const plan = ref<ReportPlan | null>(null)
const planLoading = ref(false)
const planError = ref('')

async function loadTemplates() {
  try {
    const data = await fetchReportTemplates()
    if (data.success) templateList.value = data.items || []
  } catch {
    /* 静默 */
  }
}

async function onMakePlan() {
  if (mode.value === 'intent') {
    if (!intent.value.trim()) {
      toast.warning('请输入报告意图')
      return
    }
    planLoading.value = true
    planError.value = ''
    try {
      const data = await makeReportPlan({ intent_text: intent.value.trim() })
      if (!data.success) throw new Error(data.error || '分解失败')
      plan.value = data.plan
    } catch (e) {
      planError.value = e instanceof Error ? e.message : String(e)
    } finally {
      planLoading.value = false
    }
  } else {
    if (!templateId.value) {
      toast.warning('请先选择模板')
      return
    }
    planLoading.value = true
    planError.value = ''
    try {
      const data = await makeReportPlan({
        template_id: Number(templateId.value),
        period: period.value.trim(),
        org: org.value.trim(),
      })
      if (!data.success) throw new Error(data.error || '加载失败')
      plan.value = data.plan
    } catch (e) {
      planError.value = e instanceof Error ? e.message : String(e)
    } finally {
      planLoading.value = false
    }
  }
}

function switchMode(m: 'intent' | 'template') {
  if (mode.value === m) return
  mode.value = m
  plan.value = null
  planError.value = ''
}

function addQuestion(si: number, e: KeyboardEvent) {
  const input = e.target as HTMLInputElement
  const text = input.value.trim()
  if (!text || !plan.value) return
  const qid = `q${Date.now()}`
  plan.value.sections[si].questions.push({ qid, question: text, source: 'generated' })
  plan.value.question_count = plan.value.sections.reduce((n, s) => n + s.questions.length, 0)
  input.value = ''
}

function removeQuestion(si: number, qid: string) {
  if (!plan.value) return
  const sec = plan.value.sections[si]
  sec.questions = sec.questions.filter((q) => q.qid !== qid)
  if (!sec.questions.length) plan.value.sections.splice(si, 1)
  plan.value.question_count = plan.value.sections.reduce((n, s) => n + s.questions.length, 0)
}

function onQuestionEdited(q: ReportQuestion) {
  if (q.source !== 'qa_pair') return
  q.source = 'generated'
  delete q.qa_id
  delete q.standard_sql
  delete q.match_score
  delete q.qa_question
}

const readonlyPlan = computed(() => mode.value === 'template')

/* ================= 生成（SSE + 三栏） ================= */

const phase = ref<'input' | 'stream'>('input')
const generating = ref(false)
const degraded = ref(false)
const runId = ref<number | null>(null)
const reportMd = ref('')
const durationMs = ref(0)
const usage = ref<ReportGenerateResult['usage'] | null>(null)
const generateError = ref('')
const qResults = reactive<Record<string, ReportQuestionResult>>({})

function resetQuestionResults() {
  for (const key of Object.keys(qResults)) delete qResults[key]
}

function initQuestionResults() {
  resetQuestionResults()
  for (const sec of plan.value?.sections ?? []) {
    for (const q of sec.questions) {
      qResults[q.qid] = { qid: q.qid, question: q.question, status: 'pending' }
    }
  }
}

function applyQuestion(d: ReportQuestionResult) {
  qResults[d.qid] = { ...(qResults[d.qid] ?? {}), ...d }
}

const sse = useSSE<ReportStreamEvent, ReportGenerateResult>('/report/generate', {
  onEvent: (evt) => {
    if (!('kind' in evt)) return
    if (evt.kind === 'plan') {
      plan.value = evt.plan as unknown as ReportPlan
      initQuestionResults()
    } else if (evt.kind === 'question') {
      applyQuestion(evt.detail as ReportQuestionResult)
    } else if (evt.kind === 'composed') {
      degraded.value = evt.degraded
    }
  },
})

async function onGenerate() {
  if (!plan.value || !plan.value.question_count) {
    toast.warning('请先分解问题')
    return
  }
  generateError.value = ''
  degraded.value = false
  reportMd.value = ''
  runId.value = null
  durationMs.value = 0
  usage.value = null
  resetQuestionResults()
  phase.value = 'stream'
  generating.value = true

  try {
    await sse.start({ plan: plan.value })
  } finally {
    generating.value = false
  }

  if (sse.status.value === 'done') {
    const r = sse.result.value
    if (r) {
      runId.value = r.run_id
      reportMd.value = r.report_md || ''
      degraded.value = r.degraded
      durationMs.value = r.duration_ms ?? 0
      usage.value = r.usage ?? null
      if (r.detail) for (const d of r.detail) applyQuestion(d)
      startReveal()
      void loadRuns()
    }
  } else if (sse.status.value === 'error') {
    generateError.value = sse.error.value?.message || '流式响应中断'
  }
}

function regenerate() {
  if (phase.value === 'stream') phase.value = 'input'
}

function backToInput() {
  phase.value = 'input'
  generating.value = false
}

/* ================= 报告拆分与渲染 ================= */

interface MdSection {
  level: number
  title: string
  body: string
  charCount: number
}

function splitReport(md: string): { title: string; sections: MdSection[] } {
  const lines = (md || '').replace(/\r\n/g, '\n').split('\n')
  let title = ''
  const sections: MdSection[] = []
  let cur: MdSection | null = null
  for (const line of lines) {
    const h1 = line.match(/^#\s+(.*)$/)
    const h = line.match(/^(#{2,4})\s+(.*)$/)
    if (h1 && !title) {
      title = h1[1].trim()
      continue
    }
    if (h) {
      if (cur) sections.push(cur)
      cur = { level: h[1].length, title: h[2].trim(), body: '', charCount: 0 }
    } else if (cur) {
      cur.body += (cur.body ? '\n' : '') + line
    }
  }
  if (cur) sections.push(cur)
  for (const s of sections) s.charCount = s.body.replace(/\s/g, '').length
  return { title, sections }
}

const reportParts = computed(() => splitReport(reportMd.value))
const reportTitle = computed(() => reportParts.value.title || plan.value?.report_title || '')
const reportSections = computed(() => reportParts.value.sections)

/* ================= 正文渐显（章节级，前端表现层） ================= */

const revealCount = ref(0)
const expandedOverride = reactive<Record<number, boolean>>({})
let revealTimer: ReturnType<typeof setInterval> | undefined

const revealing = computed(() => revealCount.value < reportSections.value.length)

function stopReveal() {
  if (revealTimer) clearInterval(revealTimer)
  revealTimer = undefined
}

function startReveal() {
  stopReveal()
  revealCount.value = 0
  for (const k of Object.keys(expandedOverride)) delete expandedOverride[Number(k)]
  if (!reportSections.value.length) return
  revealTimer = setInterval(() => {
    revealCount.value += 1
    if (revealCount.value >= reportSections.value.length) stopReveal()
  }, 520)
}

function sectionOpen(i: number): boolean {
  if (i >= revealCount.value) return false
  if (revealing.value) return i === revealCount.value - 1
  return expandedOverride[i] ?? i === reportSections.value.length - 1
}

function toggleSection(i: number) {
  expandedOverride[i] = !sectionOpen(i)
}

function chapterCharCount(title: string): number {
  const s = reportSections.value.find(
    (x) => x.title === title || x.title.includes(title) || title.includes(x.title),
  )
  return s ? s.charCount : 0
}

/* ================= 大纲 / 取数任务进度 ================= */

const allQuestions = computed(() =>
  (plan.value?.sections ?? []).flatMap((s) => s.questions),
)

const questionTotal = computed(() => allQuestions.value.length)
const questionDone = computed(
  () => allQuestions.value.filter((q) => qResults[q.qid]?.status === 'ok').length,
)
const questionFailed = computed(
  () => allQuestions.value.filter((q) => qResults[q.qid]?.status === 'fail').length,
)

function chapterStatus(questions: ReportQuestion[]): 'pending' | 'running' | 'done' {
  const states = questions.map((q) => qResults[q.qid]?.status ?? 'pending')
  if (states.every((s) => s === 'ok')) return 'done'
  if (states.some((s) => s === 'ok' || s === 'fail')) return 'running'
  return 'pending'
}

function fmtMs(ms: number | undefined | null): string {
  if (ms == null) return '—'
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`
}

/* ================= Markdown 渲染（无外部依赖，对齐旧实现） ================= */

function escapeHtml(s: string): string {
  return String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

function renderInline(s: string): string {
  return escapeHtml(s)
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
}

function renderMarkdown(md: string): string {
  if (!md) return ''
  const lines = md.replace(/\r\n/g, '\n').split('\n')
  let html = ''
  let i = 0
  while (i < lines.length) {
    const line = lines[i]
    if (/^\s*\|.*\|\s*$/.test(line) && i + 1 < lines.length && /^\s*\|[\s\-:|]+\|\s*$/.test(lines[i + 1])) {
      const headers = line.split('|').slice(1, -1).map((c) => c.trim())
      i += 2
      const rowCells: string[][] = []
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) {
        rowCells.push(lines[i].split('|').slice(1, -1).map((c) => c.trim()))
        i++
      }
      html += `<table class="rp-table"><thead><tr><th>${headers.map(renderInline).join('</th><th>')}</th></tr></thead><tbody>${rowCells
        .map((cells) => `<tr>${cells.map((c) => `<td>${renderInline(c)}</td>`).join('')}</tr>`)
        .join('')}</tbody></table>`
      continue
    }
    const h = line.match(/^(#{1,4})\s+(.*)$/)
    if (h) {
      const lv = h[1].length
      html += `<h${lv}>${renderInline(h[2])}</h${lv}>`
      i++
      continue
    }
    if (/^\s*([-*+]|\d+\.)\s+/.test(line)) {
      const ordered = /^\s*\d+\.\s+/.test(line)
      html += ordered ? '<ol>' : '<ul>'
      while (i < lines.length && /^\s*([-*+]|\d+\.)\s+/.test(lines[i])) {
        html += `<li>${renderInline(lines[i].replace(/^\s*([-*+]|\d+\.)\s+/, ''))}</li>`
        i++
      }
      html += ordered ? '</ol>' : '</ul>'
      continue
    }
    if (/^\s*---+\s*$/.test(line)) {
      html += '<hr>'
      i++
      continue
    }
    if (line.trim() === '') {
      i++
      continue
    }
    html += `<p>${renderInline(line)}</p>`
    i++
  }
  return html
}

/* ================= 历史报告 ================= */

const runs = ref<ReportRunItem[]>([])
const runsLoading = ref(false)
const runsError = ref('')
const viewRun = ref<ReportRunDetail | null>(null)

async function loadRuns() {
  runsLoading.value = true
  runsError.value = ''
  try {
    const data = await fetchReportRuns(20)
    if (!data.success) throw new Error(data.error || '加载失败')
    runs.value = data.items || []
  } catch (e) {
    runsError.value = e instanceof Error ? e.message : String(e)
  } finally {
    runsLoading.value = false
  }
}

async function viewReportRun(id: number) {
  try {
    const data = await fetchReportRun(id)
    if (!data.success) throw new Error(data.error || '加载失败')
    viewRun.value = data.item
  } catch (e) {
    toast.danger({ title: '加载失败', desc: e instanceof Error ? e.message : String(e) })
  }
}

function exportRun(id: number) {
  window.open(reportRunExportUrl(id), '_blank')
}

async function removeRun(id: number) {
  const ok = await confirm.l1({
    title: '删除历史报告',
    message: `确认删除报告 run#${id}？此操作不可恢复。`,
    danger: true,
  })
  if (!ok) return
  try {
    const data = await deleteReportRun(id)
    if (!data.success) throw new Error(data.error || '删除失败')
    if (viewRun.value?.id === id) viewRun.value = null
    toast.success('已删除')
    await loadRuns()
  } catch (e) {
    toast.danger({ title: '删除失败', desc: e instanceof Error ? e.message : String(e) })
  }
}

/* ================= 模板管理 ================= */

const templates = ref<ReportTemplate[]>([])
const templatesLoading = ref(false)
const templatesError = ref('')
const tplId = ref<number | null>(null)
const tplName = ref('')
const tplTriggers = ref('')
const tplRemark = ref('')
const tplSections = ref<ReportTemplateSection[]>([{ section_title: '', hint: '', questions: [] }])

async function loadTemplateList() {
  templatesLoading.value = true
  templatesError.value = ''
  try {
    const data = await fetchReportTemplates()
    if (!data.success) throw new Error(data.error || '加载失败')
    templates.value = data.items || []
  } catch (e) {
    templatesError.value = e instanceof Error ? e.message : String(e)
  } finally {
    templatesLoading.value = false
  }
}

function resetTemplateForm() {
  tplId.value = null
  tplName.value = ''
  tplTriggers.value = ''
  tplRemark.value = ''
  tplSections.value = [{ section_title: '', hint: '', questions: [] }]
}

async function editTemplate(id: number) {
  let t: ReportTemplate | null = null
  try {
    const data = await fetchReportTemplate(id)
    if (data.success) t = data.item
  } catch {
    /* 回退列表缓存 */
  }
  if (!t) t = templates.value.find((x) => x.id === id) ?? null
  if (!t) return
  tplId.value = t.id
  tplName.value = t.name
  tplTriggers.value = (t.trigger_words || []).join(',')
  tplRemark.value = t.remark || ''
  tplSections.value = normalizeOutline(t.outline)
}

function normalizeOutline(outline: unknown): ReportTemplateSection[] {
  const list = (Array.isArray(outline) ? outline : []) as Array<{
    section_title?: string
    hint?: string
    questions?: unknown[]
  }>
  const sections = list.map((s) => ({
    section_title: s.section_title || '',
    hint: s.hint || '',
    questions: (s.questions || []).map((q): ReportTemplateQuestion => {
      if (typeof q === 'object' && q !== null) {
        const o = q as { question?: string; qa_id?: number | null; standard_sql?: string; has_sql?: boolean; is_usable?: boolean }
        return {
          question: o.question || '',
          qa_id: o.qa_id ?? null,
          has_sql: !!(o.has_sql || o.standard_sql),
          is_usable: o.is_usable !== false,
          standard_sql: o.standard_sql || '',
        }
      }
      return { question: String(q), qa_id: null, has_sql: false, is_usable: false, standard_sql: '' }
    }),
  }))
  return sections.length ? sections : [{ section_title: '', hint: '', questions: [] }]
}

function tplAddSection() {
  tplSections.value.push({ section_title: '', hint: '', questions: [] })
}

function tplRemoveSection(si: number) {
  tplSections.value.splice(si, 1)
  if (!tplSections.value.length) tplSections.value = [{ section_title: '', hint: '', questions: [] }]
}

function tplAddQuestion(si: number, e: KeyboardEvent) {
  const input = e.target as HTMLInputElement
  const text = input.value.trim()
  if (!text) return
  tplSections.value[si].questions.push({ question: text, qa_id: null, has_sql: false, is_usable: false, standard_sql: '' })
  input.value = ''
}

function tplRemoveQuestion(si: number, qi: number) {
  tplSections.value[si].questions.splice(qi, 1)
}

async function saveTemplate() {
  const name = tplName.value.trim()
  if (!name) {
    toast.warning('模板名称不能为空')
    return
  }
  const outline: ReportTemplateSection[] = tplSections.value
    .map((s) => ({
      section_title: s.section_title.trim(),
      hint: (s.hint || '').trim(),
      questions: s.questions
        .map((q): ReportTemplateQuestion | null => {
          const text = q.question.trim()
          if (!text) return null
          const item: ReportTemplateQuestion = { question: text }
          if (q.qa_id) item.qa_id = q.qa_id
          if (q.standard_sql) item.standard_sql = q.standard_sql
          return item
        })
        .filter((x): x is ReportTemplateQuestion => x !== null),
    }))
    .filter((s) => s.section_title)
  const payload = {
    name,
    trigger_words: tplTriggers.value.split(/[,，]/).map((s) => s.trim()).filter(Boolean),
    outline,
    remark: tplRemark.value.trim(),
    enabled: true,
  }
  try {
    const data = tplId.value
      ? await updateReportTemplate(tplId.value, payload)
      : await createReportTemplate(payload)
    if (!data.success) throw new Error(data.error || '保存失败')
    if (data.sync) {
      toast.success(
        `模板已保存。问答对联动：新增 ${data.sync.inserted}，变更 ${data.sync.changed}，删除 ${data.sync.deleted}，关联已有 ${data.sync.linked}`,
      )
    } else {
      toast.success('模板已保存')
    }
    resetTemplateForm()
    await Promise.all([loadTemplateList(), loadTemplates()])
  } catch (e) {
    toast.danger({ title: '保存失败', desc: e instanceof Error ? e.message : String(e) })
  }
}

async function removeTemplate(id: number) {
  const ok = await confirm.l1({
    title: '删除模板',
    message: '确认删除该模板？关联的问答对会置为无效。',
    danger: true,
  })
  if (!ok) return
  try {
    const data = await deleteReportTemplate(id)
    if (!data.success) throw new Error(data.error || '删除失败')
    toast.success('已删除')
    await Promise.all([loadTemplateList(), loadTemplates()])
  } catch (e) {
    toast.danger({ title: '删除失败', desc: e instanceof Error ? e.message : String(e) })
  }
}

async function onDistill() {
  if (!runId.value) {
    toast.warning('仅支持对已落库的报告提炼模板')
    return
  }
  try {
    const data = await distillReportTemplate(runId.value)
    if (!data.success) throw new Error(data.error || '提炼失败')
    resetTemplateForm()
    tplName.value = data.template.name || ''
    tplTriggers.value = (data.template.trigger_words || []).join(',')
    tplRemark.value = data.template.remark || ''
    tplSections.value = normalizeOutline(data.template.outline)
    activeTab.value = 'tpl'
  } catch (e) {
    toast.danger({ title: '提炼失败', desc: e instanceof Error ? e.message : String(e) })
  }
}

async function copyMd() {
  if (!reportMd.value) return
  try {
    await navigator.clipboard.writeText(reportMd.value)
    toast.success('Markdown 已复制')
  } catch {
    toast.danger({ title: '复制失败', desc: '浏览器未授予剪贴板权限' })
  }
}

function downloadMd() {
  if (!reportMd.value) return
  const blob = new Blob([reportMd.value], { type: 'text/markdown;charset=utf-8' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = 'report.md'
  a.click()
  URL.revokeObjectURL(a.href)
}
</script>

<template>
  <div class="rp">
    <!-- ================= 报告生成 ================= -->
    <div v-if="activeTab === 'gen'" class="rp-gen">
      <!-- 输入态 -->
      <div v-if="phase === 'input'" class="rp-input">
        <div class="rp-card">
          <div class="rp-card-hd"><h3>报告生成</h3></div>
          <div class="rp-card-bd">
            <div class="mode-tabs">
              <button type="button" class="mode-btn" :class="{ active: mode === 'intent' }" @click="switchMode('intent')">意图识别</button>
              <button type="button" class="mode-btn" :class="{ active: mode === 'template' }" @click="switchMode('template')">选择模板</button>
            </div>

            <div v-if="mode === 'intent'">
              <textarea
                v-model="intent"
                class="ta"
                rows="2"
                placeholder="例如：帮我写一份2026年3月杭州公司设备管理月报"
              />
              <div class="actions">
                <button class="btn" type="button" :disabled="planLoading" @click="onMakePlan">
                  {{ planLoading ? '分解中…' : '匹配模板并分解' }}
                </button>
                <button class="btn btn-primary" type="button" :disabled="!plan || !plan.question_count" @click="onGenerate">生成报告</button>
              </div>
            </div>

            <div v-else>
              <div class="actions">
                <select v-model="templateId" class="txt">
                  <option value="" disabled>— 请选择模板 —</option>
                  <option v-for="t in templateList.filter((x) => x.enabled)" :key="t.id" :value="t.id">{{ t.name }}</option>
                </select>
                <input v-model="period" class="txt" type="text" placeholder="期间，如 2026年3月" />
                <input v-model="org" class="txt" type="text" placeholder="单位，如 杭州公司" />
                <button class="btn" type="button" :disabled="planLoading" @click="onMakePlan">加载问题</button>
                <button class="btn btn-primary" type="button" :disabled="!plan || !plan.question_count" @click="onGenerate">生成报告</button>
              </div>
              <div class="hint">选择模板并填写期间/单位后加载问题，可在下方编辑，再生成报告。</div>
            </div>

            <div v-if="planError" class="rp-error">{{ planError }}</div>

            <!-- 分解计划预览（可编辑） -->
            <div v-if="plan" class="plan">
              <div class="plan-hd">
                <h4>分解计划</h4>
                <span class="hint">
                  《{{ plan.report_title }}》 {{ (plan.org_scope || []).join('、') }} {{ plan.period }}
                  <template v-if="plan.template_name"> | 模板: {{ plan.template_name }}</template>
                  | 共 {{ plan.question_count }} 题
                  <template v-if="readonlyPlan"> | 模板问题只读（修改请到「模板管理」）</template>
                </span>
              </div>
              <div v-for="(sec, si) in plan.sections" :key="si" class="plan-sec">
                <div class="plan-sec-title">{{ sec.section_title }}</div>
                <div v-for="q in sec.questions" :key="q.qid" class="plan-q">
                  <template v-if="readonlyPlan">
                    <span class="q-text">{{ q.question }}</span>
                  </template>
                  <template v-else>
                    <input v-model="q.question" class="txt grow" type="text" @change="onQuestionEdited(q)" />
                  </template>
                  <span v-if="q.source === 'qa_pair'" class="src-badge qa">标准问答对#{{ q.qa_id }} {{ q.match_score?.toFixed(2) }}</span>
                  <span v-else-if="q.from_template" class="src-badge tpl">模板问题#{{ q.qa_id || '' }}</span>
                  <span v-else class="src-badge gen">实时生成</span>
                  <button v-if="!readonlyPlan" class="ghost danger" type="button" @click="removeQuestion(si, q.qid)">删除</button>
                </div>
                <div v-if="!readonlyPlan" class="plan-add">
                  <input class="txt grow" type="text" placeholder="手动新增问题，回车添加" @keydown.enter.prevent="addQuestion(si, $event)" />
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- 生成态：三栏布局 -->
      <div v-else class="rp-stream">
        <aside class="rp-pane rp-pane-l">
          <div class="pane-hd"><span class="pane-ttl">分析意图</span></div>
          <div class="pane-bd">
            <div class="intent-card">
              <div class="lb">报告</div>
              <div class="intent-title">{{ reportTitle }}</div>
              <div v-if="(plan?.org_scope || []).length || plan?.period" class="intent-tags">
                <span v-for="(o, i) in plan?.org_scope || []" :key="i" class="mini-tag">{{ o }}</span>
                <span v-if="plan?.period" class="mini-tag">{{ plan.period }}</span>
              </div>
              <div v-if="plan?.template_name" class="hint">模板：{{ plan.template_name }}</div>
            </div>

            <div class="pane-sub"><span class="pane-ttl">章节进度</span></div>
            <div class="chapters">
              <div
                v-for="(sec, i) in plan?.sections || []"
                :key="i"
                class="chapter"
                :class="`is-${chapterStatus(sec.questions)}`"
              >
                <span class="ch-dot" />
                <div class="ch-body">
                  <div class="ch-t">{{ sec.section_title }}</div>
                  <div class="ch-sub">
                    <template v-if="chapterStatus(sec.questions) === 'done'">
                      {{ chapterCharCount(sec.section_title) }} 字
                    </template>
                    <template v-else-if="chapterStatus(sec.questions) === 'running'">生成中…</template>
                    <template v-else>待生成</template>
                  </div>
                </div>
              </div>
            </div>

            <div class="pane-actions">
              <button class="btn" type="button" @click="backToInput">重新规划大纲</button>
            </div>
          </div>
        </aside>

        <section class="rp-pane rp-pane-c">
          <div class="pane-hd rp-toolbar">
            <span class="pane-ttl">报告正文</span>
            <span class="spacer" />
            <template v-if="!generating && reportMd">
              <button class="btn-sm" type="button" @click="copyMd">复制 Markdown</button>
              <button class="btn-sm" type="button" @click="downloadMd">下载 .md</button>
              <button class="btn-sm" type="button" @click="onDistill">模板提炼</button>
              <button class="btn-sm" type="button" @click="regenerate">重新生成</button>
            </template>
          </div>
          <div class="pane-bd doc-wrap">
            <div v-if="generating && !reportMd" class="doc-loading">
              <DsEmpty type="no-data" title="正在生成报告" desc="取数任务正在并发执行，正文即将呈现" />
            </div>
            <div v-else-if="generateError" class="rp-error">{{ generateError }}</div>
            <div v-else-if="reportMd" class="doc">
              <h1 class="doc-title">{{ reportTitle }}</h1>
              <section v-for="(s, i) in reportSections.slice(0, revealCount)" :key="i" class="doc-sec">
                <button type="button" class="doc-sec-hd" @click="toggleSection(i)">
                  <span class="hd-line" :class="`lv${s.level}`" />
                  <h2>{{ s.title }}</h2>
                  <span class="hd-count">{{ s.charCount }} 字</span>
                  <span class="hd-caret">{{ sectionOpen(i) ? '▾' : '▸' }}</span>
                </button>
                <div v-if="sectionOpen(i)" class="doc-sec-bd">
                  <div class="doc-md" v-html="renderMarkdown(s.body)" />
                  <div v-if="revealing && i === revealCount - 1" class="rp-caret" />
                </div>
              </section>
            </div>
          </div>
        </section>

        <aside class="rp-pane rp-pane-r">
          <div class="pane-hd"><span class="pane-ttl">取数任务</span></div>
          <div class="pane-bd">
            <div class="task-summary">
              <span>并发取数 <b>{{ questionDone }}</b> / {{ questionTotal }} 已完成</span>
              <span v-if="questionFailed" class="fail">· {{ questionFailed }} 失败</span>
              <span v-if="usage" class="hint">成功 {{ usage.questions_ok }}/{{ usage.questions_total }}</span>
            </div>
            <div class="tasks">
              <div v-for="q in allQuestions" :key="q.qid" class="task" :class="`is-${qResults[q.qid]?.status || 'pending'}`">
                <span class="task-ic">
                  <template v-if="qResults[q.qid]?.status === 'ok'">✓</template>
                  <template v-else-if="qResults[q.qid]?.status === 'fail'">✗</template>
                  <template v-else-if="qResults[q.qid]?.status === 'running'">⟳</template>
                  <template v-else>○</template>
                </span>
                <div class="task-body">
                  <div class="task-q"><sup class="q-badge">{{ q.qid }}</sup>{{ q.question }}</div>
                  <div class="task-meta">
                    <template v-if="qResults[q.qid]?.status === 'ok'">
                      {{ fmtMs(qResults[q.qid]?.ms) }} · {{ qResults[q.qid]?.row_count ?? 0 }} 行
                    </template>
                    <template v-else-if="qResults[q.qid]?.status === 'fail'">
                      {{ qResults[q.qid]?.error || '执行失败' }}
                    </template>
                    <template v-else-if="qResults[q.qid]?.status === 'running'">正在执行…</template>
                    <template v-else>等待执行</template>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </aside>
      </div>
    </div>

    <!-- ================= 历史报告 ================= -->
    <div v-else-if="activeTab === 'runs'" class="rp-tab-page">
      <div class="rp-card">
        <div class="rp-card-hd"><h3>历史报告</h3></div>
        <div class="rp-card-bd">
          <div v-if="runsLoading" class="hint">加载中…</div>
          <div v-else-if="runsError" class="rp-error">{{ runsError }}</div>
          <DsEmpty v-else-if="!runs.length" type="no-data" title="暂无历史报告" desc="生成一份报告后，它会出现在这里" />
          <div v-else class="run-list">
            <div v-for="it in runs" :key="it.id" class="run-item">
              <div class="run-main" @click="viewReportRun(it.id)">
                <div class="run-title">{{ it.report_title }}</div>
                <div class="hint">{{ it.created_at }} | {{ it.status }} | {{ fmtMs(it.duration_ms) }}</div>
              </div>
              <div class="run-actions">
                <button class="btn-sm" type="button" @click="viewReportRun(it.id)">查看</button>
                <button class="btn-sm" type="button" @click="exportRun(it.id)">导出</button>
                <button class="btn-sm danger" type="button" @click="removeRun(it.id)">删除</button>
              </div>
            </div>
          </div>
          <div v-if="viewRun" class="run-detail">
            <div class="rp-card-hd">
              <h4>{{ viewRun.report_title }}</h4>
              <span class="hint">run#{{ viewRun.id }} | {{ viewRun.created_at }} | {{ viewRun.status }}</span>
            </div>
            <div class="doc-md" v-html="renderMarkdown(viewRun.report_md)" />
          </div>
        </div>
      </div>
    </div>

    <!-- ================= 模板管理 ================= -->
    <div v-else class="rp-tab-page">
      <div class="rp-card">
        <div class="rp-card-hd"><h3>模板管理</h3></div>
        <div class="rp-card-bd">
          <div v-if="templatesLoading" class="hint">加载中…</div>
          <div v-else-if="templatesError" class="rp-error">{{ templatesError }}</div>
          <DsEmpty v-else-if="!templates.length" type="no-data" title="暂无模板" desc="可在下方新增一个报告模板" />
          <div v-else class="run-list">
            <div v-for="t in templates" :key="t.id" class="run-item">
              <div class="run-main">
                <div class="run-title">{{ t.enabled ? '' : '（停用）' }}{{ t.name }}</div>
                <div class="hint">触发词: {{ (t.trigger_words || []).join('、') || '—' }} | {{ (t.outline || []).length }} 章节</div>
              </div>
              <div class="run-actions">
                <button class="btn-sm" type="button" @click="editTemplate(t.id)">编辑</button>
                <button class="btn-sm danger" type="button" @click="removeTemplate(t.id)">删除</button>
              </div>
            </div>
          </div>

          <div class="tpl-editor">
            <div class="rp-card-hd"><h4>新增/编辑模板</h4></div>
            <div class="form">
              <input v-model="tplName" class="txt" type="text" placeholder="模板名称，如：设备管理月报" />
              <input v-model="tplTriggers" class="txt" type="text" placeholder="触发词（逗号分隔），如：设备管理月报,设备月报" />
              <div class="hint">章节与问数问题（保存后自动联动问答对库：新增/变更的题后台自动生成 SQL，删除的题置为无效）</div>
              <div v-for="(sec, si) in tplSections" :key="si" class="tpl-sec">
                <div class="tpl-sec-head">
                  <input v-model="sec.section_title" class="txt" type="text" placeholder="章节名，如：设备概况" />
                  <input v-model="sec.hint" class="txt" type="text" placeholder="取数提示（可选）" />
                  <button class="btn-sm danger" type="button" @click="tplRemoveSection(si)">删章节</button>
                </div>
                <table class="tpl-table">
                  <thead>
                    <tr><th style="width: 58%">业务问题（写入问答对库）</th><th>SQL 状态</th><th>操作</th></tr>
                  </thead>
                  <tbody>
                    <tr v-for="(q, qi) in sec.questions" :key="qi">
                      <td><input v-model="q.question" class="txt" type="text" /></td>
                      <td>
                        <span v-if="q.qa_id" :class="['src-badge', q.has_sql ? 'qa' : 'gen']">
                          {{ q.has_sql ? '已验证#' + q.qa_id : '待生成#' + q.qa_id }}
                        </span>
                        <span v-else class="hint">新问题</span>
                      </td>
                      <td><button class="btn-sm danger" type="button" @click="tplRemoveQuestion(si, qi)">删除</button></td>
                    </tr>
                    <tr>
                      <td colspan="3">
                        <input class="txt" type="text" placeholder="新增业务问题，回车添加" @keydown.enter.prevent="tplAddQuestion(si, $event)" />
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <div class="actions">
                <button class="btn" type="button" @click="tplAddSection">+ 新增章节</button>
              </div>
              <input v-model="tplRemark" class="txt" type="text" placeholder="备注（可选）" />
              <div class="actions">
                <button class="btn btn-primary" type="button" @click="saveTemplate">保存模板</button>
                <button class="btn" type="button" @click="resetTemplateForm">清空</button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.rp {
  height: 100%;
  display: flex;
  flex-direction: column;
}
.rp-gen {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.rp-input {
  flex: 1;
  overflow-y: auto;
  padding: 2px;
}
.rp-card {
  background: var(--surface-1);
  border: 1px solid var(--line);
  border-radius: var(--r-md);
  box-shadow: var(--e1);
  overflow: hidden;
}
.rp-card-hd {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 16px;
  border-bottom: 1px solid var(--line-subtle);
}
.rp-card-hd h3,
.rp-card-hd h4 {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-strong);
}
.rp-card-hd .hint {
  margin-left: auto;
}
.rp-card-bd {
  padding: 14px 16px;
}
.rp-card-bd > * + * {
  margin-top: 12px;
}
.hint {
  font-size: 12px;
  color: var(--text-3);
}
.fail {
  color: var(--danger);
}
.rp-error {
  padding: 8px 12px;
  border-left: 2px solid var(--danger);
  background: var(--danger-soft);
  border-radius: var(--r-sm);
  color: var(--danger);
  font-size: 13px;
}
.txt {
  height: 32px;
  padding: 0 10px;
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  background: var(--surface-1);
  color: var(--text-1);
  font-size: 13px;
}
.txt:focus,
.ta:focus {
  outline: none;
  border-color: var(--accent-line);
  box-shadow: var(--focus-ring);
}
.txt.grow {
  flex: 1;
  min-width: 0;
}
.ta {
  width: 100%;
  min-height: 52px;
  resize: vertical;
  padding: 10px 12px;
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  background: var(--surface-1);
  color: var(--text-1);
  font-size: 14px;
  line-height: 24px;
}
.btn,
.btn-sm {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  background: var(--surface-1);
  color: var(--text-1);
  cursor: pointer;
  font-size: 13px;
}
.btn {
  height: 32px;
  padding: 0 14px;
}
.btn-sm {
  height: 28px;
  padding: 0 10px;
  font-size: 12.5px;
}
.btn:hover:not(:disabled),
.btn-sm:hover:not(:disabled) {
  background: var(--surface-2);
  border-color: var(--line-strong);
}
.btn-primary {
  background: var(--accent);
  border-color: var(--accent);
  color: var(--text-on-accent);
}
.btn-primary:hover:not(:disabled) {
  background: var(--accent-hover);
  border-color: var(--accent-hover);
}
.btn:disabled,
.btn-sm:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.mode-tabs {
  display: inline-flex;
  gap: 6px;
}
.mode-btn {
  height: 28px;
  padding: 0 12px;
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  background: var(--surface-1);
  color: var(--text-2);
  font-size: 12.5px;
  cursor: pointer;
}
.mode-btn.active {
  background: var(--accent-soft);
  border-color: var(--accent-line);
  color: var(--accent);
  font-weight: 600;
}
.ghost {
  padding: 0;
  border: none;
  background: none;
  font-size: 12.5px;
  color: var(--text-3);
  cursor: pointer;
}
.ghost:hover {
  color: var(--accent);
}
.ghost.danger:hover {
  color: var(--danger);
}

/* 计划预览 */
.plan {
  border: 1px solid var(--line-subtle);
  border-radius: var(--r-sm);
  overflow: hidden;
}
.plan-hd {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-bottom: 1px solid var(--line-subtle);
  background: var(--surface-2);
}
.plan-hd h4 {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-1);
}
.plan-sec {
  padding: 10px 12px;
  border-bottom: 1px solid var(--line-subtle);
}
.plan-sec:last-child {
  border-bottom: none;
}
.plan-sec-title {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--text-2);
  margin-bottom: 6px;
}
.plan-q {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}
.plan-q:last-of-type {
  margin-bottom: 0;
}
.plan-add {
  display: flex;
  margin-top: 6px;
}
.q-text {
  flex: 1;
  font-size: 13px;
  color: var(--text-1);
}
.src-badge {
  flex: none;
  display: inline-flex;
  align-items: center;
  height: 20px;
  padding: 0 8px;
  border-radius: var(--r-xs);
  font-size: 11px;
}
.src-badge.qa {
  background: var(--accent-soft);
  color: var(--accent);
}
.src-badge.tpl {
  border: 1px solid var(--ember);
  color: var(--ember);
}
.src-badge.gen {
  background: var(--sunken);
  color: var(--text-2);
}

/* 生成态三栏 */
.rp-stream {
  flex: 1;
  min-height: 0;
  display: flex;
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: var(--r-md);
  background: var(--surface-1);
  box-shadow: var(--e1);
}
.rp-pane {
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.rp-pane-l {
  width: 264px;
  flex: none;
  border-right: 1px solid var(--line);
}
.rp-pane-c {
  flex: 1;
  min-width: 0;
  background: var(--canvas);
}
.rp-pane-r {
  width: 288px;
  flex: none;
  border-left: 1px solid var(--line);
}
.pane-hd {
  flex: none;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 16px;
  border-bottom: 1px solid var(--line-subtle);
}
.pane-ttl {
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.05em;
  color: var(--text-2);
  text-transform: uppercase;
}
.spacer {
  margin-left: auto;
}
.pane-bd {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 12px 0;
}
.intent-card {
  margin: 0 12px 12px;
  padding: 10px 12px;
  background: var(--surface-2);
  border: 1px solid var(--line-subtle);
  border-radius: var(--r-sm);
}
.intent-card .lb {
  font-size: 11px;
  color: var(--text-3);
  margin-bottom: 4px;
}
.intent-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-1);
}
.intent-tags {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin-top: 8px;
}
.mini-tag {
  font-size: 11px;
  padding: 1px 6px;
  border-radius: var(--r-xs);
  background: var(--sunken);
  color: var(--text-2);
}
.pane-sub {
  padding: 10px 16px 6px;
}
.chapters {
  display: flex;
  flex-direction: column;
}
.chapter {
  display: flex;
  gap: 10px;
  padding: 9px 16px;
  border-left: 2px solid transparent;
}
.chapter .ch-dot {
  flex: none;
  width: 16px;
  height: 16px;
  margin-top: 2px;
  border-radius: var(--r-full);
  border: 1.5px solid var(--line-strong);
  background: var(--surface-1);
}
.chapter.is-done .ch-dot {
  background: var(--accent);
  border-color: var(--accent);
}
.chapter.is-running {
  background: var(--accent-soft);
  border-left-color: var(--accent);
}
.chapter.is-running .ch-dot {
  border-color: var(--accent);
  border-width: 2px;
}
.chapter.is-running .ch-t {
  color: var(--accent);
  font-weight: 600;
}
.ch-body {
  min-width: 0;
}
.ch-t {
  font-size: 13px;
  color: var(--text-1);
}
.ch-sub {
  font-size: 11.5px;
  color: var(--text-3);
  margin-top: 2px;
}
.pane-actions {
  padding: 10px 16px;
}

/* 报告正文 */
.rp-toolbar {
  background: var(--surface-1);
}
.doc-wrap {
  padding: 0;
}
.doc-loading {
  padding: 40px 16px;
}
.doc {
  max-width: 760px;
  margin: 0 auto;
  padding: 24px 32px 48px;
}
.doc-title {
  font-size: 22px;
  font-weight: 700;
  line-height: 32px;
  color: var(--text-strong);
  margin: 0 0 20px;
}
.doc-sec {
  border-top: 1px solid var(--line-subtle);
}
.doc-sec-hd {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 14px 0;
  border: none;
  background: none;
  text-align: left;
  cursor: pointer;
}
.doc-sec-hd .hd-line {
  flex: none;
  width: 4px;
  height: 18px;
  border-radius: 2px;
  background: var(--ember);
}
.doc-sec-hd .hd-line.lv3 {
  height: 14px;
}
.doc-sec-hd .hd-line.lv4 {
  height: 12px;
  opacity: 0.7;
}
.doc-sec-hd h2 {
  flex: 1;
  min-width: 0;
  font-size: 17px;
  font-weight: 600;
  color: var(--text-strong);
}
.doc-sec-hd .hd-count {
  font-size: 12px;
  color: var(--text-3);
}
.doc-sec-hd .hd-caret {
  width: 18px;
  text-align: center;
  color: var(--text-4);
}
.doc-sec-bd {
  padding: 2px 0 16px;
}
.rp-caret {
  display: inline-block;
  width: 2px;
  height: 20px;
  background: var(--accent);
  animation: rp-blink 1000ms step-end infinite;
}
@keyframes rp-blink {
  0%,
  49% {
    opacity: 1;
  }
  50%,
  100% {
    opacity: 0;
  }
}

/* Markdown 渲染 */
.doc-md :deep(h3),
.doc-md :deep(h4) {
  margin: 14px 0 8px;
  color: var(--text-1);
  font-weight: 600;
}
.doc-md :deep(p) {
  margin: 0 0 10px;
  font-size: 15px;
  line-height: 26px;
  color: var(--text-1);
}
.doc-md :deep(strong) {
  color: var(--text-strong);
}
.doc-md :deep(code) {
  font-family: var(--font-mono);
  font-size: 13px;
  background: var(--sunken);
  border-radius: var(--r-xs);
  padding: 1px 5px;
  color: var(--ember);
}
.doc-md :deep(ul),
.doc-md :deep(ol) {
  margin: 0 0 10px 20px;
  padding: 0;
}
.doc-md :deep(li) {
  margin: 4px 0;
  font-size: 15px;
  line-height: 26px;
  color: var(--text-1);
}
.doc-md :deep(hr) {
  border: none;
  border-top: 1px solid var(--line);
  margin: 12px 0;
}
.rp-table {
  width: 100%;
  border-collapse: separate;
  border-spacing: 0;
  margin: 10px 0 14px;
  font-size: 13px;
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  overflow: hidden;
}
.rp-table th {
  text-align: left;
  padding: 8px 10px;
  background: var(--sunken);
  color: var(--text-2);
  font-weight: 500;
  border-bottom: 1px solid var(--line);
}
.rp-table td {
  padding: 7px 10px;
  border-bottom: 1px solid var(--line-subtle);
  color: var(--text-1);
}
.rp-table tr:last-child td {
  border-bottom: none;
}

/* 取数任务 */
.task-summary {
  margin: 0 12px 10px;
  padding: 10px 12px;
  background: var(--surface-2);
  border: 1px solid var(--line-subtle);
  border-radius: var(--r-sm);
  font-size: 12.5px;
  color: var(--text-2);
}
.task-summary b {
  font-family: var(--font-mono);
  color: var(--text-strong);
}
.tasks {
  display: flex;
  flex-direction: column;
}
.task {
  display: flex;
  gap: 10px;
  padding: 10px 16px;
  border-bottom: 1px solid var(--line-subtle);
}
.task:last-child {
  border-bottom: none;
}
.task-ic {
  flex: none;
  width: 16px;
  text-align: center;
  color: var(--text-4);
}
.task.is-ok .task-ic {
  color: var(--success);
}
.task.is-fail .task-ic {
  color: var(--danger);
}
.task.is-running .task-ic {
  color: var(--accent);
}
.task-body {
  min-width: 0;
}
.task-q {
  font-size: 13px;
  color: var(--text-1);
  line-height: 19px;
}
.q-badge {
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--accent);
  background: var(--accent-soft);
  border-radius: var(--r-xs);
  padding: 1px 4px;
  margin-right: 4px;
}
.task-meta {
  font-size: 11.5px;
  color: var(--text-3);
  margin-top: 2px;
}

/* 历史 / 模板 */
.rp-tab-page {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 2px;
}
.run-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.run-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 12px;
  border: 1px solid var(--line-subtle);
  border-radius: var(--r-sm);
}
.run-main {
  flex: 1;
  min-width: 0;
  cursor: pointer;
}
.run-title {
  font-size: 13.5px;
  font-weight: 600;
  color: var(--text-1);
}
.run-actions {
  display: flex;
  gap: 6px;
  flex: none;
}
.btn-sm.danger {
  border-color: var(--danger);
  color: var(--danger);
}
.btn-sm.danger:hover:not(:disabled) {
  background: var(--danger-soft);
}
.run-detail {
  margin-top: 12px;
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  overflow: hidden;
}
.run-detail .doc-md {
  padding: 16px;
}

/* 模板编辑器 */
.tpl-editor {
  margin-top: 16px;
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  overflow: hidden;
}
.form {
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.tpl-sec {
  border: 1px solid var(--line-subtle);
  border-radius: var(--r-sm);
  padding: 10px;
}
.tpl-sec-head {
  display: flex;
  gap: 8px;
  margin-bottom: 8px;
}
.tpl-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12.5px;
}
.tpl-table th {
  text-align: left;
  padding: 6px 8px;
  background: var(--sunken);
  color: var(--text-2);
  font-weight: 500;
}
.tpl-table td {
  padding: 6px 8px;
  border-bottom: 1px solid var(--line-subtle);
}
</style>
