/**
 * 错题集 API（对应 modules/training/routes.py 端点，F2.5）
 *
 * - GET  /api/error-list   分页列表（error_type / resolved 服务端过滤；本页全量取回走客户端管道）
 * - GET  /api/error-detail 单条详情（含 resolution_note / tables_involved）
 * - POST /api/error-search 语义搜索（RAG 相似度排序）
 * - POST /api/error-update 更新（business_question / generated_sql / correct_sql / error_type / …）
 * - POST /api/error-delete 删除
 * - POST /api/error-resolve 标记已修复 / 取消已修复
 */
import { api } from './client'
import type { Paged } from '@/types'

/** 错误类型枚举（与旧 UI static/index.html 下拉一致） */
export const ERROR_TYPES = [
  '表选择错误',
  '字段选择错误',
  '关联条件错误',
  'WHERE条件错误',
  '聚合方式错误',
  '排序/分组错误',
  '其他',
] as const
export type ErrorType = (typeof ERROR_TYPES)[number]

/** 列表项字段（error-list 不返回 resolution_note / tables_involved；correct_sql / error_detail 可为 null） */
export interface ErrorRecord {
  id: number
  business_question: string
  generated_sql: string
  correct_sql: string | null
  error_type: string
  error_detail: string | null
  is_resolved: boolean
  frequency: number
  created_at: string
}

/** 单条详情（error-detail 在列表基础上多两个字段） */
export interface ErrorDetail extends ErrorRecord {
  resolution_note: string
  tables_involved: string
}

/** 语义搜索结果项（附相似度） */
export interface ErrorSearchItem extends ErrorRecord {
  similarity_score?: number
  match_type?: string
}

export interface ErrorSearchResult {
  success: boolean
  query: string
  count: number
  items: ErrorSearchItem[]
}

export interface ErrorUpdatePayload {
  id: number
  business_question?: string
  generated_sql?: string
  correct_sql?: string | null
  error_type?: string
  error_analysis?: string | null
  sql_tips?: string | null
}

export interface ErrorMutationResult {
  success: boolean
  message?: string
}

const PAGE_SIZE = 500

/** 全量取回（错题百级以内；超过一页自动续取，防御未来增长） */
export async function fetchAllErrors(): Promise<{ items: ErrorRecord[]; total: number }> {
  const first = await api.get<Paged<ErrorRecord>>('/error-list', { page: 1, per_page: PAGE_SIZE })
  const items: ErrorRecord[] = [...(first.items ?? [])]
  const total = first.total ?? items.length
  let page = 2
  while (items.length < total) {
    const next = await api.get<Paged<ErrorRecord>>('/error-list', { page, per_page: PAGE_SIZE })
    const chunk = next.items ?? []
    if (!chunk.length) break
    items.push(...chunk)
    page += 1
  }
  return { items, total }
}

export function fetchErrorDetail(id: number): Promise<{ success: boolean; item: ErrorDetail }> {
  return api.get<{ success: boolean; item: ErrorDetail }>('/error-detail', { id })
}

export function searchErrors(query: string, limit = 50): Promise<ErrorSearchResult> {
  return api.post<ErrorSearchResult>('/error-search', { query, limit })
}

export function updateError(payload: ErrorUpdatePayload): Promise<ErrorMutationResult> {
  return api.post<ErrorMutationResult>('/error-update', payload)
}

export function deleteError(id: number): Promise<ErrorMutationResult> {
  return api.post<ErrorMutationResult>('/error-delete', { id })
}

export function resolveError(id: number, isResolved = true): Promise<ErrorMutationResult> {
  return api.post<ErrorMutationResult>('/error-resolve', { id, is_resolved: isResolved })
}

/**
 * 规范化 created_at（与 qa 的 normalizeQaTime 同口径）：
 * 后端对 naive datetime 的序列化可能带 GMT 标注，实为服务器本地时间，
 * 故按字面分量转换、不做时区换算。
 */
const MONTHS: Record<string, string> = {
  Jan: '01',
  Feb: '02',
  Mar: '03',
  Apr: '04',
  May: '05',
  Jun: '06',
  Jul: '07',
  Aug: '08',
  Sep: '09',
  Oct: '10',
  Nov: '11',
  Dec: '12',
}

export function normalizeErrorTime(raw: string | null | undefined): string {
  if (!raw) return ''
  const m = /(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})\s+(\d{2}:\d{2}:\d{2})/.exec(raw)
  if (!m) return raw
  const mm = MONTHS[m[2]]
  if (!mm) return raw
  return `${m[3]}-${mm}-${m[1].padStart(2, '0')} ${m[4]}`
}
