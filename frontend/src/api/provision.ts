/**
 * 素材提资 API（F2.4，对应 modules/provision/routes.py 端点）。
 *
 * 四步工作流：上传解析 → 校验与映射预览 → 执行转换（SSE）→ 人工复核。
 * 流式端点 POST /api/provision/<run_id>/execute 由 useSSE 直接消费（见 ProvisionView），
 * 其事件 schema 见 types/sse.ts（ProvisionStreamEvent）。
 */
import { api } from './client'

/* ==================== 上传 / 校验 ==================== */

export interface ProvisionIssue {
  row: number
  level: 'warn' | 'fail' | string
  msg: string
}

export interface ProvisionSheetStat {
  ok: number
  warn: number
  fail: number
}

export interface ProvisionUploadResponse {
  success: boolean
  run_id: string
  file_name: string
  validation: Record<string, ProvisionSheetStat>
  issues: Record<string, ProvisionIssue[]>
  error?: string
}

/* ==================== 预览 ==================== */

/** 字段级溯源（direct/system/llm/manual） */
export type ProvisionSourceKind = 'direct' | 'system' | 'llm' | 'manual'

export interface ProvisionField {
  name: string
  value: string | null
  kind: ProvisionSourceKind
  /** 溯源引用（如 Sheet!单元格 / llm:模型名） */
  ref: string
}

export interface ProvisionRow {
  src_row: number
  status: 'ok' | 'fail' | string
  fields: ProvisionField[]
}

export interface ProvisionSheet {
  key: string
  title: string
  stats: ProvisionSheetStat
  issues: ProvisionIssue[]
  rows: ProvisionRow[]
}

export interface ProvisionPreviewResponse {
  success: boolean
  run_id: string
  run?: Record<string, unknown>
  sheets: ProvisionSheet[]
  error?: string
}

/* ==================== 执行结果（done.result） ==================== */

export interface ProvisionExecuteResult {
  converted?: Record<string, number>
  skipped?: Record<string, number>
  provenance_rows?: number
  conflicts?: unknown[]
  llm_annotated?: number
  llm_degraded?: number
  pending_review?: number
}

/* ==================== 历史 run ==================== */

export interface ProvisionRun {
  run_id: string
  file_name: string | null
  status: string
  created_at: string | null
  pending: number
}

export interface ProvisionRunsResponse {
  success: boolean
  items: ProvisionRun[]
  error?: string
}

/* ==================== 复核队列 ==================== */

export type ReviewPriority = '高' | '中' | '低'

export interface ReviewItem {
  id: string
  run_id: string
  sheet_name: string
  src_row: string
  target_table: string
  target_key: string
  field_name: string
  source_kind: string
  source_ref: string
  field_value: string | null
  review_status: string
  created_at: string | null
  priority: ReviewPriority
  priority_reason: string
}

export interface ProvisionReviewResponse {
  success: boolean
  items: ReviewItem[]
  error?: string
}

export interface ProvisionBatchConfirmResponse {
  success: boolean
  confirmed: number
  refused: [number, string][]
  failed: [number, string][]
  error?: string
}

export interface ProvisionBatchRejectResponse {
  success: boolean
  rejected: number
  skipped: number
  failed: [number, string][]
  error?: string
}

export interface ProvisionConfirmResponse {
  success: boolean
  item: {
    id: number
    target_table: string
    target_key: string
    field_name: string
    field_value: string | null
    source_kind: string
    review_status: string
  }
  error?: string
}

export interface ProvisionRejectResponse extends ProvisionConfirmResponse {}

export interface ProvisionFinishResponse {
  success: boolean
  run_id: string
  pending_review: number
  status: string
  error?: string
}

/* ==================== 函数 ==================== */

/** multipart 上传 xlsx */
export function uploadProvisionFile(file: File): Promise<ProvisionUploadResponse> {
  const fd = new FormData()
  fd.append('file', file)
  return api.post<ProvisionUploadResponse>('/provision/upload', fd)
}

/** 服务器路径上传（JSON） */
export function uploadProvisionPath(path: string): Promise<ProvisionUploadResponse> {
  return api.post<ProvisionUploadResponse>('/provision/upload', { path })
}

export function fetchProvisionPreview(runId: string): Promise<ProvisionPreviewResponse> {
  return api.get<ProvisionPreviewResponse>(`/provision/${runId}/preview`)
}

export function fetchProvisionRuns(): Promise<ProvisionRunsResponse> {
  return api.get<ProvisionRunsResponse>('/provision/runs')
}

export function fetchProvisionReview(runId: string): Promise<ProvisionReviewResponse> {
  return api.get<ProvisionReviewResponse>(`/provision/${runId}/review`)
}

export function batchConfirmProvisions(
  ids: Array<string | number>,
): Promise<ProvisionBatchConfirmResponse> {
  return api.post<ProvisionBatchConfirmResponse>('/provision/review/batch-confirm', { ids })
}

export function confirmProvisionItem(
  id: string | number,
  fieldValue: string | null,
): Promise<ProvisionConfirmResponse> {
  return api.post<ProvisionConfirmResponse>(`/provision/review/${id}/confirm`, {
    field_value: fieldValue,
  })
}

export function rejectProvisionItem(
  id: string | number,
): Promise<ProvisionRejectResponse> {
  return api.post<ProvisionRejectResponse>(`/provision/review/${id}/reject`)
}

export function batchRejectProvisions(
  ids: Array<string | number>,
): Promise<ProvisionBatchRejectResponse> {
  return api.post<ProvisionBatchRejectResponse>('/provision/review/batch-reject', { ids })
}

export function finishProvisionRun(runId: string): Promise<ProvisionFinishResponse> {
  return api.post<ProvisionFinishResponse>(`/provision/${runId}/finish`)
}

export function extractKeywords(rebuild = true): Promise<{ success: boolean; stats?: unknown; error?: string }> {
  return api.post<{ success: boolean; stats?: unknown; error?: string }>('/provision/keywords/extract', {
    rebuild,
  })
}
