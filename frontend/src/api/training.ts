/**
 * 训练模式 API 与类型（对应 modules/training/routes.py 端点，F2.1）。
 *
 * 出题 → 合理性评价 → SQL 生成（流式）→ 结果判断 全流程 + 错误归因记录。
 * SQL 流式生成复用智能问答的 /api/generate-sql-stream（useSSE，见 QaView）。
 */
import { api } from './client'

/* ---- 业务域 ---- */

export interface DomainItem {
  code: string
  name: string
  table_count: number
  /** 仅二级域有：父级一级域码 */
  parent?: string
}

export interface BusinessDomains {
  l1: DomainItem[]
  l2: DomainItem[]
}

export function fetchBusinessDomains(): Promise<{ success: boolean; items: BusinessDomains }> {
  return api.get<{ success: boolean; items: BusinessDomains }>('/business-domains')
}

/* ---- 出题 ---- */

export interface NextQuestionResult {
  success: boolean
  session_id: string
  qa_id: number | null
  question: string
  difficulty: string
  domain: string | null
  source: 'auto' | 'generated' | 'qa'
  generated: boolean
  timing?: Record<string, number>
}

export function fetchNextQuestion(params?: {
  domain?: string
  source?: 'auto' | 'generated' | 'qa'
  difficulty?: string
}): Promise<NextQuestionResult> {
  return api.get<NextQuestionResult>('/next-question', params as Record<string, unknown>)
}

/* ---- 合理性评价 ---- */

export function evaluateQuestion(payload: {
  session_id: string
  rating: '合理' | '不合理'
  feedback?: string
}): Promise<{ success: boolean; message?: string }> {
  return api.post<{ success: boolean; message?: string }>('/evaluate-question', payload)
}

/* ---- 结果判断 ---- */

export type Judgment = '正确' | '错误' | '跳过'

export function submitJudgment(payload: {
  session_id: string
  judgment: Judgment
  feedback?: string
}): Promise<{ success: boolean; message?: string }> {
  return api.post<{ success: boolean; message?: string }>('/judge', payload)
}

/* ---- 错误归因记录 ---- */

export const ERROR_DIMENSIONS = [
  { key: 'table_choice_error', label: '表选择错误' },
  { key: 'field_choice_error', label: '字段选择错误' },
  { key: 'join_path_error', label: '关联条件错误' },
  { key: 'where_condition_error', label: 'WHERE 条件错误' },
  { key: 'aggregation_error', label: '聚合方式错误' },
] as const

export type ErrorDimensionKey = (typeof ERROR_DIMENSIONS)[number]['key']

export function recordError(payload: {
  session_id: string
  error_type: string
  correct_sql: string
  error_detail: string
  [dim: string]: unknown
}): Promise<{ success: boolean; message?: string }> {
  return api.post<{ success: boolean; message?: string }>('/error-record', payload)
}
