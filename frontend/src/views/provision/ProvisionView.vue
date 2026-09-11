<script setup lang="ts">
/**
 * 素材提资（F2.4）——设计稿 §4.5「6. 素材提资」+ §4.4.4「流式输出叙事」。
 *
 * 四步工作流（上传解析 → 校验与映射预览 → 执行转换 → 人工复核）：
 * - 顶部横向阶段条（已完成可点击回退）+ 右侧 DsStepRail 流程总览；
 * - 执行转换走 SSE（convert/keywords/cache/annotate 阶段文案流）+ 收束条；
 * - 预览字段级溯源徽标（direct/system/llm/manual 四色 + 左侧色带 + 按溯源筛选）；
 * - 复核批流：高优先级全宽冲突卡逐条确认、中/低优先级批量同意/不同意、>50 自动分批。
 */
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { API_BASE } from '@/api/client'
import {
  batchConfirmProvisions,
  batchRejectProvisions,
  confirmProvisionItem,
  fetchProvisionPreview,
  fetchProvisionReview,
  fetchProvisionRuns,
  finishProvisionRun,
  rejectProvisionItem,
  uploadProvisionFile,
  uploadProvisionPath,
  type ProvisionExecuteResult,
  type ProvisionField,
  type ProvisionPreviewResponse,
  type ProvisionSheet,
  type ProvisionSheetStat,
  type ProvisionSourceKind,
  type ReviewItem,
  type ReviewPriority,
} from '@/api/provision'
import { DsClosingBar, DsStepRail, DsThinkingPanel, confirm, toast, type StreamStep, type ThinkingEntry } from '@/components'

/* ================= 四步流程定义 ================= */

const STEP_META = [
  { key: 'upload', title: '上传解析', desc: '上传 xlsx 或指定服务器路径' },
  { key: 'preview', title: '校验与映射预览', desc: 'Sheet 校验 + 字段级溯源预览' },
  { key: 'execute', title: '执行转换', desc: '直接转换入库 + LLM 批量标注' },
  { key: 'review', title: '人工复核', desc: '高优先级逐条确认 + 中低批量' },
] as const

const currentStep = ref(0)
const maxReached = ref(0)
const execError = ref('')

const stepStatus = (i: number): StreamStep['status'] => {
  if (i < currentStep.value) return 'done'
  if (i === currentStep.value) {
    if (i === 2 && execError.value) return 'failed'
    return 'running'
  }
  return 'pending'
}

const steps = computed<StreamStep[]>(() =>
  STEP_META.map((s, i) => ({
    key: s.key,
    title: s.title,
    desc: s.desc,
    status: stepStatus(i),
  })),
)

function gotoStep(i: number) {
  if (i < 0 || i > maxReached.value) return
  currentStep.value = i
}

/* ================= ① 上传解析 ================= */

const fileInput = ref<HTMLInputElement | null>(null)
const pathInput = ref('')
const uploading = ref(false)
const uploadMsg = ref('')
const uploadError = ref('')

const runId = ref('')
const fileName = ref('')

const validation = ref<Record<string, ProvisionSheetStat>>({})
const uploadIssues = ref<Record<string, { row: number; level: string; msg: string }[]>>({})

async function doUpload() {
  const file = fileInput.value?.files?.[0]
  const path = pathInput.value.trim()
  if (!file && !path) {
    toast.warning('请选择文件或填写服务器路径')
    return
  }
  uploading.value = true
  uploadMsg.value = '上传解析中…'
  uploadError.value = ''
  try {
    const data = file ? await uploadProvisionFile(file) : await uploadProvisionPath(path)
    if (!data.success) throw new Error(data.error || '上传失败')
    runId.value = data.run_id
    fileName.value = data.file_name
    validation.value = data.validation || {}
    uploadIssues.value = data.issues || {}
    uploadMsg.value = `解析完成：${data.file_name}（run_id=${data.run_id}）`
    currentStep.value = 1
    maxReached.value = Math.max(maxReached.value, 1)
    toast.success('上传解析完成')
    await loadPreview(data.run_id)
  } catch (e) {
    uploadError.value = e instanceof Error ? e.message : String(e)
    toast.danger({ title: '上传失败', desc: uploadError.value })
  } finally {
    uploading.value = false
  }
}

/* ================= ② 校验与映射预览 ================= */

const preview = ref<ProvisionPreviewResponse | null>(null)
const previewLoading = ref(false)
const previewError = ref('')
const sourceFilter = ref<'all' | ProvisionSourceKind>('all')

const SOURCE_LABELS: Record<ProvisionSourceKind, string> = {
  direct: '直接转换',
  system: '系统推导',
  llm: 'LLM 标注',
  manual: '人工标注',
}

const MID_LOW_PRIORITIES = ['中', '低'] as const

async function loadPreview(id: string) {
  previewLoading.value = true
  previewError.value = ''
  try {
    const data = await fetchProvisionPreview(id)
    if (!data.success) throw new Error(data.error || '预览失败')
    preview.value = data
  } catch (e) {
    previewError.value = e instanceof Error ? e.message : String(e)
  } finally {
    previewLoading.value = false
  }
}

function fieldMatches(f: ProvisionField): boolean {
  return sourceFilter.value === 'all' || f.kind === sourceFilter.value
}

function sheetHasMatch(sh: ProvisionSheet): boolean {
  return sh.rows.some((r) => r.fields.some(fieldMatches))
}

const filteredSheets = computed(() => {
  if (!preview.value) return []
  if (sourceFilter.value === 'all') return preview.value.sheets
  return preview.value.sheets.filter(sheetHasMatch)
})

/* ================= ③ 执行转换（SSE） ================= */

const executing = ref(false)
const execLog = ref<ThinkingEntry[]>([])
const execResult = ref<ProvisionExecuteResult | null>(null)
const elapsedMs = ref(0)
let clockTimer: ReturnType<typeof setInterval> | undefined

const STAGE_LABELS: Record<string, string> = {
  convert: '直接转换',
  keywords: '关键词提取',
  cache: '缓存刷新',
  annotate: 'LLM 标注',
}

function stageEntry(stage: string, msg: string): ThinkingEntry {
  return { tag: STAGE_LABELS[stage] || stage || '进度', body: msg || '' }
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

async function doExecute() {
  if (!runId.value || executing.value) return
  currentStep.value = 2
  maxReached.value = Math.max(maxReached.value, 2)
  execLog.value = []
  execResult.value = null
  execError.value = ''
  executing.value = true
  startClock()
  try {
    const resp = await fetch(`${API_BASE}/provision/${runId.value}/execute`, { method: 'POST' })
    if (!resp.ok) {
      const err = (await resp.json().catch(() => ({}))) as { error?: string }
      throw new Error(err.error || `HTTP ${resp.status}`)
    }
    const reader = resp.body!.getReader()
    const decoder = new TextDecoder('utf-8')
    let buffer = ''
    let final: ProvisionExecuteResult | null = null
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const parts = buffer.split('\n\n')
      buffer = parts.pop() ?? ''
      for (const part of parts) {
        const line = part.trim()
        if (!line.startsWith('data:')) continue
        let evt: { error?: string; done?: boolean; result?: ProvisionExecuteResult; stage?: string; msg?: string }
        try {
          evt = JSON.parse(line.slice(5))
        } catch {
          continue
        }
        if (evt.error) {
          execError.value = evt.error
        } else if (evt.done) {
          final = evt.result ?? null
        } else if (evt.msg) {
          execLog.value.push(stageEntry(evt.stage ?? '', evt.msg))
        }
        if (execError.value) break
      }
      if (execError.value) break
    }
    if (final) {
      execResult.value = final
      maxReached.value = 3
      currentStep.value = 3
      await loadReview(runId.value)
    } else if (!execError.value) {
      execError.value = '流式响应中断'
    }
  } catch (e) {
    execError.value = e instanceof Error ? e.message : String(e)
  } finally {
    executing.value = false
    stopClock()
  }
}

const execMetrics = computed(() => {
  const r = execResult.value
  if (!r) return []
  const converted = Object.values(r.converted ?? {}).reduce((a, b) => a + b, 0)
  const skipped = Object.values(r.skipped ?? {}).reduce((a, b) => a + b, 0)
  const out: { label: string; value: string | number }[] = []
  out.push({ label: '转换', value: `${converted} 行` })
  if (skipped) out.push({ label: '跳过', value: `${skipped} 行` })
  if (r.provenance_rows != null) out.push({ label: '溯源', value: `${r.provenance_rows} 条` })
  if (r.llm_annotated != null) out.push({ label: 'LLM 标注', value: `${r.llm_annotated} 条` })
  out.push({ label: '待复核', value: `${r.pending_review ?? 0} 条` })
  return out
})

/* ================= ④ 人工复核 ================= */

const BATCH_SIZE = 50
const reviewRuns = ref<{ run_id: string; file_name: string | null; status: string; pending: number }[]>([])
const reviewRunId = ref('')
const reviewLoading = ref(false)
const reviewError = ref('')
const reviewFilter = ref('')
const reviewAllItems = ref<ReviewItem[]>([])
const processedIds = ref<string[]>([])
const batchIndex = ref(0)
const draftValues = reactive<Record<string, string>>({})

const batches = computed(() => {
  const out: ReviewItem[][] = []
  for (let i = 0; i < reviewAllItems.value.length; i += BATCH_SIZE) {
    out.push(reviewAllItems.value.slice(i, i + BATCH_SIZE))
  }
  return out
})

const currentBatch = computed(() => batches.value[batchIndex.value] ?? [])

function matchesFilter(item: ReviewItem): boolean {
  const kw = reviewFilter.value.trim().toLowerCase()
  if (!kw) return true
  return [item.target_table, item.target_key, item.field_name, item.field_value, item.sheet_name]
    .filter(Boolean)
    .some((v) => String(v).toLowerCase().includes(kw))
}

const visibleBatchItems = computed(() =>
  currentBatch.value.filter((it) => !processedIds.value.includes(it.id) && matchesFilter(it)),
)

const priorityGroups = computed(() => {
  const groups: Record<ReviewPriority, ReviewItem[]> = { 高: [], 中: [], 低: [] }
  for (const it of visibleBatchItems.value) groups[it.priority].push(it)
  return groups
})

const processedCount = computed(() => processedIds.value.length)
const reviewTotal = computed(() => reviewAllItems.value.length)
const remainingCount = computed(() => Math.max(0, reviewTotal.value - processedCount.value))
const remainingMinutes = computed(() => Math.ceil((remainingCount.value * 2.5) / 60))

async function loadRuns() {
  try {
    const data = await fetchProvisionRuns()
    if (!data.success) return
    reviewRuns.value = (data.items || []).filter((r) => r.pending > 0)
  } catch {
    /* 静默：不影响上传/执行主流程 */
  }
}

async function loadReview(id: string) {
  if (!id) return
  reviewRunId.value = id
  reviewLoading.value = true
  reviewError.value = ''
  try {
    const data = await fetchProvisionReview(id)
    if (!data.success) throw new Error(data.error || '复核队列加载失败')
    reviewAllItems.value = data.items || []
    processedIds.value = []
    batchIndex.value = 0
    reviewFilter.value = ''
    for (const it of reviewAllItems.value) {
      draftValues[it.id] = it.field_value ?? ''
    }
  } catch (e) {
    reviewError.value = e instanceof Error ? e.message : String(e)
  } finally {
    reviewLoading.value = false
  }
}

function markProcessed(ids: Array<string | number>) {
  const set = new Set(processedIds.value)
  for (const id of ids) set.add(String(id))
  processedIds.value = [...set]
}

async function onConfirmItem(item: ReviewItem) {
  try {
    await confirmProvisionItem(item.id, draftValues[item.id] ?? null)
    markProcessed([item.id])
    toast.success(`已确认 #${item.id}`)
  } catch (e) {
    toast.danger({ title: '确认失败', desc: e instanceof Error ? e.message : String(e) })
  }
}

async function onRejectItem(item: ReviewItem) {
  try {
    await rejectProvisionItem(item.id)
    markProcessed([item.id])
    toast.info(`已否决 #${item.id}（保留现状）`)
  } catch (e) {
    toast.danger({ title: '否决失败', desc: e instanceof Error ? e.message : String(e) })
  }
}

async function onBatchConfirm(pri: Exclude<ReviewPriority, '高'>) {
  const ids = priorityGroups.value[pri].map((it) => it.id)
  if (!ids.length) return
  const ok = await confirm.l2({
    title: `批量同意「${pri}」优先级`,
    message: `将按模板值覆盖库内对应字段，共 ${ids.length} 条。`,
    impacts: [
      `本次影响 ${ids.length} 条 ${pri} 优先级记录`,
      '语义类（高优先级）字段不会被本次批量操作处理',
      'row_count 等客观事实字段会自动按业务库实际值校准',
    ],
  })
  if (!ok) return
  try {
    const data = await batchConfirmProvisions(ids)
    if (!data.success) throw new Error(data.error || '批量确认失败')
    const refused = data.refused?.length ?? 0
    const failed = data.failed?.length ?? 0
    const failedSet = new Set((data.failed || []).map((f) => String(f[0])))
    const refusedSet = new Set((data.refused || []).map((f) => String(f[0])))
    markProcessed(ids.filter((id) => !failedSet.has(String(id)) && !refusedSet.has(String(id))))
    let msg = `已确认 ${data.confirmed} 条`
    if (refused) msg += `；服务端拒绝 ${refused} 条`
    if (failed) msg += `；失败 ${failed} 条`
    toast.success(msg)
  } catch (e) {
    toast.danger({ title: '批量确认失败', desc: e instanceof Error ? e.message : String(e) })
  }
}

async function onBatchReject(pri: ReviewPriority) {
  const ids = priorityGroups.value[pri].map((it) => it.id)
  if (!ids.length) return
  const ok = await confirm.l2({
    title: `批量不同意「${pri}」优先级`,
    message: `库内将保留现状，不做任何改动，共 ${ids.length} 条。`,
    impacts: [`本次影响 ${ids.length} 条 ${pri} 优先级记录`, '这是安全操作，不写入任何业务数据'],
  })
  if (!ok) return
  try {
    const data = await batchRejectProvisions(ids)
    if (!data.success) throw new Error(data.error || '批量否决失败')
    const failed = data.failed?.length ?? 0
    const failedSet = new Set((data.failed || []).map((f) => String(f[0])))
    markProcessed(ids.filter((id) => !failedSet.has(String(id))))
    let msg = `已否决 ${data.rejected} 条（库内保留现状）`
    if (data.skipped) msg += `；跳过 ${data.skipped} 条`
    if (failed) msg += `；失败 ${failed} 条`
    toast.success(msg)
  } catch (e) {
    toast.danger({ title: '批量否决失败', desc: e instanceof Error ? e.message : String(e) })
  }
}

async function onFinishRun() {
  if (!reviewRunId.value) return
  const pending = remainingCount.value
  const ok = await confirm.l2({
    title: '完成收尾',
    message: pending > 0 ? `还有 ${pending} 条未确认，确定现在收尾吗？` : '本批已全部确认，确定完成收尾？',
    impacts:
      pending > 0
        ? [`仍有 ${pending} 条待复核`, '收尾后剩余项仍保留在队列，可随时回来继续处理']
        : ['本批将标记为 finished'],
  })
  if (!ok) return
  try {
    const data = await finishProvisionRun(reviewRunId.value)
    if (!data.success) throw new Error(data.error || '收尾失败')
    if (data.pending_review > 0) {
      toast.warning(`已收尾，剩余 ${data.pending_review} 条仍可继续处理`)
    } else {
      toast.success('本批已全部确认并完成收尾')
    }
    await loadRuns()
  } catch (e) {
    toast.danger({ title: '收尾失败', desc: e instanceof Error ? e.message : String(e) })
  }
}

function batchPrev() {
  if (batchIndex.value > 0) batchIndex.value -= 1
}
function batchNext() {
  if (batchIndex.value < batches.value.length - 1) batchIndex.value += 1
}

/* ================= 溯源辅助 ================= */

function originalValue(item: ReviewItem): string {
  const m = /^冲突：库内\[(.*?)\] vs 模板\[(.*?)\]$/.exec(item.source_ref || '')
  if (m) return m[1]
  if (item.source_kind === 'llm') return '（库内暂无 / 待标注）'
  return '（见库内现状）'
}

function suggestedValue(item: ReviewItem): string {
  const m = /^冲突：库内\[(.*?)\] vs 模板\[(.*?)\]$/.exec(item.source_ref || '')
  if (m) return m[2]
  return item.field_value ?? ''
}

function conflictReason(item: ReviewItem): string {
  const parts: string[] = []
  if (item.priority_reason) parts.push(item.priority_reason)
  if (item.source_ref && !item.source_ref.startsWith('冲突：')) parts.push(item.source_ref)
  return parts.join(' · ') || '—'
}

/* ================= 自动分批推进 ================= */

watch(
  () => visibleBatchItems.value.length,
  (n) => {
    if (n === 0 && batchIndex.value < batches.value.length - 1) {
      batchIndex.value += 1
    }
  },
)

/* ================= 初始化 ================= */

onMounted(() => {
  void loadRuns()
})

onBeforeUnmount(() => {
  stopClock()
})
</script>

<template>
  <div class="pv">
    <div class="pv-workbench">
      <!-- 左栏：四步内容 -->
      <section class="pv-col-left">
        <!-- 顶部横向阶段条（可回退） -->
        <nav class="pv-stepper" aria-label="提资四步">
          <button
            v-for="(s, i) in STEP_META"
            :key="s.key"
            type="button"
            class="pv-stepper__item"
            :class="{ 'is-active': i === currentStep, 'is-done': i < currentStep, 'is-disabled': i > maxReached }"
            :disabled="i > maxReached"
            @click="gotoStep(i)"
          >
            <span class="pv-stepper__idx">{{ i + 1 }}</span>
            <span class="pv-stepper__text">{{ s.title }}</span>
          </button>
        </nav>

        <!-- ① 上传解析 -->
        <div v-if="currentStep === 0" class="pv-card">
          <div class="pv-card-hd"><h3>上传素材提资模板（xlsx）</h3></div>
          <div class="pv-card-bd">
            <div class="upload-row">
              <label class="file-ctl">
                <span>{{ fileName ? '重新选择文件' : '选择文件' }}</span>
                <input ref="fileInput" type="file" accept=".xlsx" @change="fileName = ''" />
              </label>
              <span class="sep">或</span>
              <input
                v-model="pathInput"
                class="txt"
                type="text"
                placeholder="服务器路径，如 C:/Users/…/素材提资模板20260820.xlsx"
              />
              <button class="btn btn-primary" type="button" :disabled="uploading" @click="doUpload">
                {{ uploading ? '上传解析中…' : '上传解析' }}
              </button>
            </div>
            <div v-if="uploadMsg" class="hint ok">{{ uploadMsg }}</div>
            <div v-if="uploadError" class="pv-error">{{ uploadError }}</div>
          </div>
        </div>

        <!-- ② 校验与映射预览 -->
        <div v-else-if="currentStep === 1" class="pv-card">
          <div class="pv-card-hd">
            <h3>校验与映射预览</h3>
            <button class="ghost" type="button" @click="gotoStep(0)">返回上传</button>
          </div>
          <div class="pv-card-bd">
            <!-- 校验汇总 -->
            <div v-if="Object.keys(validation).length" class="pv-subcard">
              <h4>校验结果</h4>
              <table class="pv-table">
                <thead>
                  <tr><th>Sheet</th><th class="num">ok</th><th class="num">warn</th><th class="num">fail</th></tr>
                </thead>
                <tbody>
                  <tr v-for="(s, k) in validation" :key="k">
                    <td>{{ k }}</td>
                    <td class="num ok">{{ s.ok }}</td>
                    <td class="num warn">{{ s.warn }}</td>
                    <td class="num fail">{{ s.fail }}</td>
                  </tr>
                </tbody>
              </table>
            </div>

            <!-- 溯源图例 + 按溯源筛选 -->
            <div class="pv-legend">
              <span v-for="(label, kind) in SOURCE_LABELS" :key="kind" class="pv-badge" :class="`pv-${kind}`">
                {{ label }}
              </span>
              <label class="filter">
                <span>按溯源筛选</span>
                <select v-model="sourceFilter" class="txt">
                  <option value="all">全部</option>
                  <option value="direct">直接转换</option>
                  <option value="system">系统推导</option>
                  <option value="llm">LLM 标注</option>
                  <option value="manual">人工标注</option>
                </select>
              </label>
            </div>

            <div v-if="previewLoading" class="hint">预览加载中…</div>
            <div v-else-if="previewError" class="pv-error">{{ previewError }}</div>

            <div v-else-if="filteredSheets.length" class="pv-sheets">
              <div v-for="sh in filteredSheets" :key="sh.key" class="pv-sheet">
                <div class="pv-sheet-hd">
                  <h4>{{ sh.title }}</h4>
                  <span class="hint">ok={{ sh.stats.ok }} warn={{ sh.stats.warn }} fail={{ sh.stats.fail }}</span>
                </div>
                <div v-if="sh.issues?.length" class="pv-issues">
                  <div v-for="(it, i) in sh.issues" :key="i" class="pv-issue" :class="`lv-${it.level}`">
                    <b>{{ it.level }}</b><span>行 {{ it.row }}</span><span>{{ it.msg }}</span>
                  </div>
                </div>
                <div class="pv-rows">
                  <div v-for="r in sh.rows" :key="r.src_row" class="pv-row" :class="{ 'is-fail': r.status === 'fail' }">
                    <span class="pv-row-no">#{{ r.src_row }}</span>
                    <span v-for="f in r.fields.filter(fieldMatches)" :key="f.name" class="pv-field">
                      <span class="pv-badge" :class="`pv-${f.kind}`" :title="f.ref">{{ SOURCE_LABELS[f.kind] }}</span>
                      <b>{{ f.name }}</b><span class="eq">=</span><span class="val">{{ f.value ?? '' }}</span>
                    </span>
                  </div>
                </div>
              </div>
            </div>
            <div v-else class="hint">无匹配溯源字段</div>

            <div class="actions">
              <button class="btn btn-primary" type="button" :disabled="!preview" @click="doExecute">执行转换</button>
            </div>
          </div>
        </div>

        <!-- ③ 执行转换 -->
        <div v-else-if="currentStep === 2" class="pv-card">
          <div class="pv-card-hd">
            <h3>执行进度</h3>
            <span v-if="executing" class="hint">已耗时 <span class="tabular">{{ (elapsedMs / 1000).toFixed(1) }}s</span></span>
            <button class="ghost" type="button" @click="gotoStep(1)">返回预览</button>
          </div>
          <div class="pv-card-bd">
            <DsThinkingPanel :entries="execLog" />
            <div v-if="execError" class="pv-error">执行失败：{{ execError }}</div>
            <div v-if="execResult" class="pv-subcard">
              <h4>执行完成</h4>
              <DsClosingBar :total-ms="elapsedMs" :metrics="execMetrics">
                <button class="btn-sm" type="button" @click="gotoStep(3)">进入人工复核</button>
              </DsClosingBar>
            </div>
          </div>
        </div>

        <!-- ④ 人工复核 -->
        <div v-else class="pv-card">
          <div class="pv-card-hd">
            <h3>人工复核队列（pending）</h3>
            <button class="ghost" type="button" @click="gotoStep(2)">返回执行</button>
          </div>
          <div class="pv-card-bd">
            <div class="review-toolbar">
              <select v-model="reviewRunId" class="txt" @change="loadReview(reviewRunId)">
                <option value="" disabled>— 选择批次 —</option>
                <option v-for="r in reviewRuns" :key="r.run_id" :value="r.run_id">
                  {{ r.file_name || '' }}｜{{ r.run_id }}｜{{ r.status === 'finished' ? '已收尾·剩' : '处理中·剩' }} {{ r.pending }} 条
                </option>
              </select>
              <input
                v-model="reviewFilter"
                class="txt grow"
                type="text"
                placeholder="过滤：目标表/字段/值关键字"
              />
              <button class="btn" type="button" :disabled="!reviewRunId" @click="loadReview(reviewRunId)">刷新</button>
            </div>

            <div v-if="reviewLoading" class="hint">复核队列加载中…</div>
            <div v-else-if="reviewError" class="pv-error">{{ reviewError }}</div>
            <div v-else-if="!reviewAllItems.length" class="hint">无待复核记录</div>

            <div v-else>
              <!-- 进度与分批显性化 -->
              <div class="review-progress">
                <span>已处理 <b class="tabular">{{ processedCount }}</b> / {{ reviewTotal }} 条</span>
                <span class="hint">剩余预估约 {{ remainingMinutes }} 分钟</span>
                <template v-if="batches.length > 1">
                  <span class="batch-info">
                    已自动分为 {{ batches.length }} 批（{{ batches.map((b) => b.length).join(' + ') }}）
                  </span>
                  <span class="batch-nav">
                    <button class="btn-sm" type="button" :disabled="batchIndex === 0" @click="batchPrev">上一批</button>
                    <b class="tabular">{{ batchIndex + 1 }} / {{ batches.length }}</b>
                    <button class="btn-sm" type="button" :disabled="batchIndex >= batches.length - 1" @click="batchNext">下一批</button>
                  </span>
                </template>
              </div>

              <!-- 高优先级：全宽冲突卡逐条确认 -->
              <section v-if="priorityGroups['高'].length" class="pri-section">
                <div class="pri-head">
                  <h4><span class="pri pri-high">高优先级</span>{{ priorityGroups['高'].length }} 条（逐条人工确认）</h4>
                  <button class="btn-sm btn-danger" type="button" @click="onBatchReject('高')">批量不同意（保留现状）</button>
                </div>
                <div class="conflict-cards">
                  <div v-for="it in priorityGroups['高']" :key="it.id" class="conflict-card">
                    <div class="conflict-meta">
                      <span class="mono">#{{ it.id }}</span>
                      <span>{{ it.sheet_name }} · 行 {{ it.src_row }}</span>
                      <span>{{ it.target_table }}.{{ it.field_name }}</span>
                      <span v-if="it.target_key" class="hint">{{ it.target_key }}</span>
                    </div>
                    <div class="conflict-body">
                      <div class="orig"><b>原值</b><div>{{ originalValue(it) }}</div></div>
                      <div class="arrow">→</div>
                      <div class="sugg"><b>建议值</b><div>{{ suggestedValue(it) }}</div></div>
                    </div>
                    <div class="conflict-reason">冲突原因：{{ conflictReason(it) }}</div>
                    <div class="conflict-actions">
                      <input v-model="draftValues[it.id]" class="txt" type="text" placeholder="修改建议值（确认时覆盖）" />
                      <button class="btn btn-primary" type="button" @click="onConfirmItem(it)">同意变更</button>
                      <button class="btn" type="button" @click="onRejectItem(it)">不同意变更</button>
                    </div>
                  </div>
                </div>
              </section>

              <!-- 中/低优先级：表格 + 批量 -->
              <section v-for="pri in MID_LOW_PRIORITIES" :key="pri" class="pri-section" v-show="priorityGroups[pri].length">
                <div class="pri-head">
                  <h4><span class="pri" :class="`pri-${pri === '中' ? 'mid' : 'low'}`">{{ pri }}优先级</span>{{ priorityGroups[pri].length }} 条</h4>
                  <button class="btn-sm" type="button" @click="onBatchConfirm(pri)">批量同意本节（{{ priorityGroups[pri].length }} 条）</button>
                  <button class="btn-sm btn-danger" type="button" @click="onBatchReject(pri)">批量不同意（保留现状）</button>
                </div>
                <table class="pv-table">
                  <thead>
                    <tr>
                      <th>ID</th><th>Sheet</th><th>行</th><th>目标</th><th>字段</th><th>优先级原因/冲突</th><th>值（可编辑）</th><th>操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="it in priorityGroups[pri]" :key="it.id">
                      <td class="mono">{{ it.id }}</td>
                      <td>{{ it.sheet_name }}</td>
                      <td class="num">{{ it.src_row }}</td>
                      <td>{{ it.target_table }}<div class="hint">{{ it.target_key }}</div></td>
                      <td>{{ it.field_name }}</td>
                      <td class="hint">{{ conflictReason(it) }}</td>
                      <td><input v-model="draftValues[it.id]" class="txt narrow" type="text" /></td>
                      <td class="row-actions">
                        <button class="btn-sm" type="button" @click="onConfirmItem(it)">同意</button>
                        <button class="btn-sm" type="button" @click="onRejectItem(it)">不同意</button>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </section>

              <div class="actions">
                <button class="btn" type="button" :disabled="!reviewRunId" @click="onFinishRun">完成收尾</button>
              </div>
            </div>
          </div>
        </div>
      </section>

      <!-- 右栏：流程总览 -->
      <aside class="pv-col-right">
        <div class="pv-card rail-card">
          <div class="pv-card-hd"><h3>提资流程</h3></div>
          <DsStepRail :steps="steps" :elapsed-ms="executing ? elapsedMs : undefined" />
        </div>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.pv {
  height: 100%;
  display: flex;
  flex-direction: column;
}
.pv-workbench {
  flex: 1;
  min-height: 0;
  display: flex;
  gap: 16px;
  overflow: hidden;
}
.pv-col-left {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 14px;
  overflow-y: auto;
  padding-right: 2px;
}
.pv-col-left > * {
  flex: none;
}
.pv-col-right {
  width: 340px;
  flex: none;
  display: flex;
  flex-direction: column;
  gap: 14px;
  overflow-y: auto;
}
.pv-col-right > * {
  flex: none;
}

/* 顶部横向阶段条 */
.pv-stepper {
  display: flex;
  gap: 6px;
  background: var(--surface-1);
  border: 1px solid var(--line);
  border-radius: var(--r-md);
  padding: 8px;
  box-shadow: var(--e1);
}
.pv-stepper__item {
  flex: 1;
  display: flex;
  align-items: center;
  gap: 8px;
  height: 36px;
  padding: 0 12px;
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  background: var(--surface-1);
  color: var(--text-2);
  font-size: 13px;
  cursor: pointer;
}
.pv-stepper__item.is-active {
  border-color: var(--accent-line);
  background: var(--accent-soft);
  color: var(--accent);
  font-weight: 600;
}
.pv-stepper__item.is-done {
  color: var(--text-1);
}
.pv-stepper__item.is-disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
.pv-stepper__idx {
  flex: none;
  width: 20px;
  height: 20px;
  display: grid;
  place-items: center;
  border-radius: var(--r-full);
  border: 1px solid currentColor;
  font-family: var(--font-mono);
  font-size: 11px;
}
.pv-stepper__item.is-active .pv-stepper__idx {
  background: var(--accent);
  border-color: var(--accent);
  color: var(--text-on-accent);
}
.pv-stepper__text {
  white-space: nowrap;
}

/* 卡片 */
.pv-card {
  background: var(--surface-1);
  border: 1px solid var(--line);
  border-radius: var(--r-md);
  box-shadow: var(--e1);
  overflow: hidden;
}
.pv-card-hd {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 16px;
  border-bottom: 1px solid var(--line-subtle);
}
.pv-card-hd h3 {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-strong);
}
.pv-card-hd .hint {
  margin-left: auto;
}
.pv-card-bd {
  padding: 14px 16px;
}
.pv-card-bd > * + * {
  margin-top: 12px;
}
.pv-subcard {
  border: 1px solid var(--line-subtle);
  border-radius: var(--r-sm);
  background: var(--surface-2);
  padding: 12px 14px;
}
.pv-subcard > * + * {
  margin-top: 10px;
}
.pv-subcard h4,
.pv-sheet h4,
.pri-head h4 {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-1);
}

.hint {
  font-size: 12px;
  color: var(--text-3);
}
.hint.ok {
  color: var(--success);
}
.mono {
  font-family: var(--font-mono);
}
.num {
  text-align: right;
  font-variant-numeric: tabular-nums;
}
.ok {
  color: var(--success);
}
.warn {
  color: var(--warning);
}
.fail {
  color: var(--danger);
}

/* 表单与按钮 */
.txt {
  height: 32px;
  padding: 0 10px;
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  background: var(--surface-1);
  color: var(--text-1);
  font-size: 13px;
}
.txt:focus {
  outline: none;
  border-color: var(--accent-line);
  box-shadow: var(--focus-ring);
}
.txt.grow {
  flex: 1;
  min-width: 0;
}
.txt.narrow {
  width: 100%;
  min-width: 110px;
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
.btn-danger {
  border-color: var(--danger);
  color: var(--danger);
}
.btn-danger:hover:not(:disabled) {
  background: var(--danger-soft);
}
.btn:disabled,
.btn-sm:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.ghost {
  margin-left: auto;
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

/* 上传 */
.upload-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.file-ctl {
  display: inline-flex;
  align-items: center;
  height: 32px;
  padding: 0 12px;
  border: 1px dashed var(--line-strong);
  border-radius: var(--r-sm);
  color: var(--text-2);
  font-size: 13px;
  cursor: pointer;
}
.file-ctl input {
  display: none;
}
.sep {
  font-size: 12px;
  color: var(--text-4);
}
.actions {
  display: flex;
  align-items: center;
  gap: 10px;
}
.pv-error {
  padding: 8px 12px;
  border-left: 2px solid var(--danger);
  background: var(--danger-soft);
  border-radius: var(--r-sm);
  color: var(--danger);
  font-size: 13px;
}

/* 溯源徽标：固定 4px 左侧色带 + 标签 */
.pv-legend {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.pv-badge {
  display: inline-flex;
  align-items: center;
  height: 20px;
  padding: 0 8px 0 10px;
  border-left: 4px solid var(--line-strong);
  border-radius: var(--r-xs);
  background: var(--sunken);
  font-size: 11px;
  color: var(--text-2);
  white-space: nowrap;
}
.pv-direct {
  border-left-color: var(--success);
}
.pv-system {
  border-left-color: var(--info);
}
.pv-llm {
  border-left-color: var(--ember);
}
.pv-manual {
  border-left-color: var(--accent);
}
.filter {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--text-3);
}

/* 预览 Sheet / 行 */
.pv-sheets {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.pv-sheet {
  border: 1px solid var(--line-subtle);
  border-radius: var(--r-sm);
  overflow: hidden;
}
.pv-sheet-hd {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-bottom: 1px solid var(--line-subtle);
  background: var(--surface-2);
}
.pv-issues {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 8px 12px;
  border-bottom: 1px solid var(--line-subtle);
}
.pv-issue {
  display: flex;
  gap: 8px;
  font-size: 12px;
  color: var(--text-2);
}
.pv-issue b {
  text-transform: uppercase;
}
.pv-issue.lv-fail b,
.pv-issue.lv-fail {
  color: var(--danger);
}
.pv-issue.lv-warn b,
.pv-issue.lv-warn {
  color: var(--warning);
}
.pv-rows {
  display: flex;
  flex-direction: column;
}
.pv-row {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 8px 12px;
  border-bottom: 1px solid var(--line-subtle);
  flex-wrap: wrap;
}
.pv-row:last-child {
  border-bottom: none;
}
.pv-row.is-fail {
  background: var(--danger-soft);
}
.pv-row-no {
  flex: none;
  width: 44px;
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text-4);
}
.pv-field {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12.5px;
  color: var(--text-1);
}
.pv-field b {
  font-weight: 600;
  color: var(--text-2);
}
.pv-field .eq {
  color: var(--text-4);
}
.pv-field .val {
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: var(--font-mono);
}

/* 复核 */
.review-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
}
.review-progress {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
  padding: 10px 12px;
  border: 1px solid var(--line-subtle);
  border-radius: var(--r-sm);
  background: var(--surface-2);
  font-size: 13px;
}
.review-progress b {
  font-family: var(--font-mono);
}
.batch-info {
  color: var(--text-2);
}
.batch-nav {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.pri-section {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.pri-head {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.pri-head h4 {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.pri {
  display: inline-flex;
  align-items: center;
  height: 20px;
  padding: 0 8px;
  border-radius: var(--r-xs);
  font-size: 11px;
  font-weight: 600;
}
.pri-high {
  background: var(--danger-soft);
  color: var(--danger);
}
.pri-mid {
  background: var(--warning-soft);
  color: var(--warning);
}
.pri-low {
  background: var(--info-soft);
  color: var(--info);
}

/* 高优先级冲突卡 */
.conflict-cards {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.conflict-card {
  border: 1px solid var(--danger-line, var(--line));
  border-left: 3px solid var(--danger);
  border-radius: var(--r-sm);
  padding: 12px 14px;
}
.conflict-meta {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  font-size: 12px;
  color: var(--text-2);
}
.conflict-body {
  display: flex;
  gap: 12px;
  align-items: stretch;
  margin-top: 10px;
}
.conflict-body .orig,
.conflict-body .sugg {
  flex: 1;
  min-width: 0;
  padding: 10px 12px;
  border-radius: var(--r-sm);
  background: var(--surface-2);
}
.conflict-body .sugg {
  border: 1px solid var(--accent-line);
  background: var(--accent-soft);
}
.conflict-body b {
  display: block;
  font-size: 12px;
  color: var(--text-3);
  margin-bottom: 6px;
}
.conflict-body div {
  font-size: 13px;
  color: var(--text-1);
  word-break: break-all;
}
.conflict-body .arrow {
  align-self: center;
  color: var(--text-4);
}
.conflict-reason {
  margin-top: 8px;
  font-size: 12px;
  color: var(--text-3);
}
.conflict-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 10px;
  flex-wrap: wrap;
}
.conflict-actions .txt {
  flex: 1;
  min-width: 180px;
}

/* 表格 */
.pv-table {
  width: 100%;
  border-collapse: separate;
  border-spacing: 0;
  font-size: 12.5px;
}
.pv-table th {
  text-align: left;
  padding: 8px 10px;
  background: var(--sunken);
  color: var(--text-2);
  font-weight: 500;
  border-bottom: 1px solid var(--line);
  white-space: nowrap;
}
.pv-table td {
  padding: 7px 10px;
  border-bottom: 1px solid var(--line-subtle);
  color: var(--text-1);
  vertical-align: top;
}
.pv-table tbody tr:hover td {
  background: var(--surface-2);
}
.pv-table th.num,
.pv-table td.num {
  text-align: right;
}
.row-actions {
  display: flex;
  gap: 6px;
  white-space: nowrap;
}
</style>
