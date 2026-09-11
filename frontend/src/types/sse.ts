/**
 * SSE 事件 schema（03-迁移方案 §4.3）
 *
 * 依据：后端源码（modules/{training,report,provision}/routes.py 实测探查）
 *      + 现行消费者 static/js/app.js#streamGenerateSQL / _renderThinkingEvent 双重核对
 *
 * 线格式：`data: {json}\n\n`（json.dumps ensure_ascii=False；单帧单 data 行）
 * 消费语义见 `composables/useSSE.ts`。
 */

/* ================================ 通用 ================================ */

/** 终帧：done（status 为 HTTP 语义状态，可缺省） */
export interface SSEDoneFrame<R> {
  done: true
  status?: number
  result: R
}

/** 终帧：error（服务端异常信息，直接可展示） */
export interface SSEErrorFrame {
  error: string
}

export function isDoneFrame<R>(evt: unknown): evt is SSEDoneFrame<R> {
  return typeof evt === 'object' && evt !== null && (evt as { done?: unknown }).done === true
}

export function isErrorFrame(evt: unknown): evt is SSEErrorFrame {
  return (
    typeof evt === 'object' && evt !== null && typeof (evt as { error?: unknown }).error === 'string'
  )
}

/* ==================== ① 智能问答 POST /api/generate-sql-stream ==================== */

/** 阶段完成事件（流程轨）。取值对齐 app.js STAGE_LABELS */
export type QaStage = 'rag' | 'schema' | 'llm' | 'validate' | 'exec' | 'audit'

export interface QaStageEvent {
  stage: QaStage
  /** 该阶段耗时（毫秒） */
  ms: number
}

/* ---- 思考事件（thinking.kind；字段依据 app.js#_renderThinkingEvent 消费点收窄） ---- */

export interface QaIntentThinking {
  kind: 'intent'
  question_type?: string
  tables?: string[]
  fields?: { agg?: string; name?: string }[]
  filters?: { field: string; op: string; value: unknown }[]
}

export interface QaTablesThinking {
  kind: 'tables'
  tables: string[]
  sources?: { rule?: string[]; llm?: string[]; draft?: string[] }
}

export interface QaColumnsThinking {
  kind: 'columns'
  summary: { table: string; shown: number; total: number }[]
}

export interface QaCodeValuesThinking {
  kind: 'code_values'
  items: {
    cn_name?: string
    code_name?: string
    form?: string
    columns?: string[]
    matched?: string[]
  }[]
}

export interface QaLlmThinking {
  kind: 'llm'
  phase: 'assemble' | 'done' | 'error'
  prompt_chars?: number
  model?: string
  ms?: number
  sql_len?: number
  error?: string
}

export interface QaAuditLlmThinking {
  kind: 'audit_llm'
  phase?: string
  verdict?: 'pass' | 'fixed' | 'unavailable' | 'fail_nosql' | 'fix_still_broken' | 'fix_invalid' | (string & {})
  reason?: string
  ms?: number
  exec_ok?: boolean
  row_count?: number
  error?: string
}

export interface QaRepairThinking {
  kind: 'repair'
  attempt: number
  reason?: string
}

export interface QaTemplateThinking {
  kind: 'template'
  name?: string
  via?: string
}

export interface QaDraftThinking {
  kind: 'draft'
  mode?: string
  sql_len?: number
}

export interface QaReviewThinking {
  kind: 'review'
  attempt: number
  action?: string
  message?: string
}

export interface QaExecThinking {
  kind: 'exec'
  status: 'success' | 'failed' | (string & {})
  row_count?: number
  ms?: number
  error?: string
}

export interface QaAuditThinking {
  kind: 'audit'
  status?: string
  post_audit?: boolean
}

/** LLM 逐字流：同 phase 连续追加 text；phase 切换 = 新起一行（打字机依据） */
export interface QaLlmStreamThinking {
  kind: 'llm_stream'
  phase: 'reasoning' | 'output'
  text?: string
}

export type QaThinking =
  | QaIntentThinking
  | QaTablesThinking
  | QaColumnsThinking
  | QaCodeValuesThinking
  | QaLlmThinking
  | QaAuditLlmThinking
  | QaRepairThinking
  | QaTemplateThinking
  | QaDraftThinking
  | QaReviewThinking
  | QaExecThinking
  | QaAuditThinking
  | QaLlmStreamThinking

export interface QaThinkingEvent {
  thinking: QaThinking
}

/** done.result：完整生成结果（字段依据 app.js 消费点；其余保留 F2.1 细化） */
export interface QaStreamResult {
  success: boolean
  error?: string
  session_id?: string
  sql?: string
  raw_sql?: string
  result_preview?: { columns?: string[]; rows?: unknown[][] } | null
  tables_involved?: string[]
  [key: string]: unknown
}

export type QaDoneEvent = SSEDoneFrame<QaStreamResult>
export type QaErrorEvent = SSEErrorFrame

export type QaStreamEvent = QaStageEvent | QaThinkingEvent | QaDoneEvent | QaErrorEvent

/* ==================== ② 报告生成 POST /api/report/generate ==================== */

export interface ReportPlanSection {
  section_title: string
  /** 章节下的题目（结构随 F2.4 报告页细化） */
  questions: unknown[]
}

export interface ReportPlanEvent {
  kind: 'plan'
  plan: { sections: ReportPlanSection[] }
}

export interface ReportQuestionDetail {
  qid: number | string
  question: string
  status: 'ok' | 'fail'
  row_count?: number
  ms?: number
  error?: string
}

export interface ReportQuestionEvent {
  kind: 'question'
  detail: ReportQuestionDetail
}

export interface ReportComposedEvent {
  kind: 'composed'
  degraded: boolean
}

export interface ReportStreamResult {
  run_id: number | string
  report_md: string
  degraded: boolean
  detail?: unknown
  usage?: unknown
  duration_ms?: number
  [key: string]: unknown
}

export type ReportDoneEvent = SSEDoneFrame<ReportStreamResult>
export type ReportErrorEvent = SSEErrorFrame

export type ReportStreamEvent =
  | ReportPlanEvent
  | ReportQuestionEvent
  | ReportComposedEvent
  | ReportDoneEvent
  | ReportErrorEvent

/* ==================== ③ 素材提资 POST /api/provision/<run_id>/execute ==================== */

export type ProvisionStage = 'convert' | 'keywords' | 'cache' | 'annotate'

export interface ProvisionStageEvent {
  stage: ProvisionStage
  /** 中文过程文案（直接展示） */
  msg: string
}

export interface ProvisionStreamResult {
  converted?: number
  skipped?: number
  provenance_rows?: number
  conflicts?: number
  llm_annotated?: number
  llm_degraded?: boolean
  pending_review?: number
  [key: string]: unknown
}

export type ProvisionDoneEvent = SSEDoneFrame<ProvisionStreamResult>
export type ProvisionErrorEvent = SSEErrorFrame

export type ProvisionStreamEvent = ProvisionStageEvent | ProvisionDoneEvent | ProvisionErrorEvent

/* ==================== 汇总 ==================== */

/** 三流事件联合（useSSE 泛型未收窄时的兜底） */
export type AnyStreamEvent = QaStreamEvent | ReportStreamEvent | ProvisionStreamEvent
