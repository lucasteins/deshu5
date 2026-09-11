/**
 * 报告生成 API（F2.4，对应 modules/report/routes.py 端点）。
 *
 * 流式端点 POST /api/report/generate 由 useSSE 直接消费（见 ReportView），
 * 其事件 schema 见 types/sse.ts（ReportStreamEvent）。
 */
import { api, API_BASE } from './client'

/* ==================== 计划 ==================== */

export type ReportQuestionSource = 'generated' | 'qa_pair' | string

export interface ReportQuestion {
  qid: string
  question: string
  source: ReportQuestionSource
  qa_id?: number | null
  from_template?: boolean
  standard_sql?: string
  match_score?: number
  qa_question?: string
}

export interface ReportPlanSection {
  section_title: string
  questions: ReportQuestion[]
}

export interface ReportPlan {
  report_title: string
  org_scope: string[]
  period: string
  intent_text?: string
  template_id?: number | null
  template_name?: string | null
  sections: ReportPlanSection[]
  question_count: number
}

export interface ReportPlanResponse {
  success: boolean
  plan: ReportPlan
  error?: string
}

/* ==================== 生成结果（done.result） ==================== */

export interface ReportQuestionResult {
  qid: string
  question: string
  status: 'ok' | 'fail' | string
  row_count?: number
  ms?: number
  error?: string
  sql?: string
  tables?: string[]
  headers?: string[]
  rows?: unknown[][]
  [key: string]: unknown
}

export interface ReportGenerateResult {
  run_id: number
  report_md: string
  degraded: boolean
  detail: ReportQuestionResult[]
  usage: {
    plan?: Record<string, unknown>
    compose?: Record<string, unknown>
    questions_ok: number
    questions_total: number
    [key: string]: unknown
  }
  duration_ms: number
}

/* ==================== 模板 ==================== */

export interface ReportTemplateQuestion {
  question: string
  qa_id?: number | null
  has_sql?: boolean
  is_usable?: boolean
  standard_sql?: string
  _sql?: string
}

export interface ReportTemplateSection {
  section_title: string
  hint?: string
  questions: ReportTemplateQuestion[]
}

export interface ReportTemplate {
  id: number
  name: string
  trigger_words: string[]
  outline: ReportTemplateSection[]
  enabled: boolean
  remark: string
}

export interface ReportTemplatePayload {
  name: string
  trigger_words: string[]
  outline: ReportTemplateSection[]
  remark: string
  enabled: boolean
}

export interface ReportTemplateSync {
  inserted: number
  linked: number
  changed: number
  deleted: number
  sql_filled: number
}

export interface ReportTemplatesResponse {
  success: boolean
  items: ReportTemplate[]
  error?: string
}

export interface ReportTemplateResponse {
  success: boolean
  item: ReportTemplate
  error?: string
}

export interface ReportTemplateMutationResponse {
  success: boolean
  id?: number
  sync?: ReportTemplateSync
  error?: string
}

export interface ReportDistillResponse {
  success: boolean
  template: {
    name: string
    trigger_words: string[]
    outline: ReportTemplateSection[]
    remark: string
  }
  error?: string
}

/* ==================== 历史 ==================== */

export interface ReportRunItem {
  id: number
  session_id?: string
  intent_text: string
  template_id?: number | null
  report_title: string
  status: string
  duration_ms: number | null
  created_at: string | null
}

export interface ReportRunDetail extends ReportRunItem {
  plan: ReportPlan | null
  detail: ReportQuestionResult[]
  report_md: string
  usage: Record<string, unknown>
}

export interface ReportRunsResponse {
  success: boolean
  total: number
  page: number
  items: ReportRunItem[]
  error?: string
}

export interface ReportRunResponse {
  success: boolean
  item: ReportRunDetail
  error?: string
}

/* ==================== 函数 ==================== */

export function makeReportPlan(
  payload:
    | { intent_text: string }
    | { template_id: number; period?: string; org?: string },
): Promise<ReportPlanResponse> {
  return api.post<ReportPlanResponse>('/report/plan', payload)
}

export function fetchReportTemplates(): Promise<ReportTemplatesResponse> {
  return api.get<ReportTemplatesResponse>('/report/templates')
}

export function fetchReportTemplate(id: number): Promise<ReportTemplateResponse> {
  return api.get<ReportTemplateResponse>(`/report/templates/${id}`)
}

export function createReportTemplate(
  payload: ReportTemplatePayload,
): Promise<ReportTemplateMutationResponse> {
  return api.post<ReportTemplateMutationResponse>('/report/templates', payload)
}

export function updateReportTemplate(
  id: number,
  payload: ReportTemplatePayload,
): Promise<ReportTemplateMutationResponse> {
  return api.put<ReportTemplateMutationResponse>(`/report/templates/${id}`, payload)
}

export function deleteReportTemplate(id: number): Promise<{ success: boolean; error?: string }> {
  return api.delete<{ success: boolean; error?: string }>(`/report/templates/${id}`)
}

export function distillReportTemplate(runId: number): Promise<ReportDistillResponse> {
  return api.post<ReportDistillResponse>('/report/distill-template', { run_id: runId })
}

export function fetchReportRuns(perPage = 20, page = 1): Promise<ReportRunsResponse> {
  return api.get<ReportRunsResponse>('/report/runs', { per_page: perPage, page })
}

export function fetchReportRun(id: number): Promise<ReportRunResponse> {
  return api.get<ReportRunResponse>(`/report/runs/${id}`)
}

export function deleteReportRun(id: number): Promise<{ success: boolean; error?: string }> {
  return api.delete<{ success: boolean; error?: string }>(`/report/runs/${id}`)
}

/** 历史报告 Markdown 下载地址（直接 window.open 触发下载） */
export function reportRunExportUrl(id: number): string {
  return `${API_BASE}/report/runs/${id}/export`
}
