/**
 * 智能问答 API 与结果类型（对应 modules/training/routes.py 端点，F2.1）。
 *
 * 流式端点 POST /api/generate-sql-stream 由 useSSE 直接消费（见 QaView），
 * 其事件 schema 见 `types/sse.ts`；本文件补全 done.result 的完整字段类型
 * （QaStreamResult 仅声明了核心字段 + index signature，此处给出强类型）。
 */
import { api } from './client'
import type { QaStreamResult } from '@/types'

/** RAG 召回的一条参考问答 */
export interface RetrievedPair {
  question: string
  standard_sql?: string
  similarity_score?: number
  combined_score?: number
}

/** 码值命中项（code_value_hits） */
export interface CodeValueHit {
  cn_name?: string
  code_name?: string
  form?: string
  columns?: string[]
  matched?: string[]
}

/** 执行结果预览（safe_execute_sql 返回；与 sse.ts 的简化类型不同，此处为准） */
export interface ResultPreview {
  success: boolean
  error?: string
  headers: string[]
  rows: (string | number | null)[][]
  row_count: number
}

/** 审查状态 */
export interface ReviewStatus {
  passed?: boolean
  message?: string
  action?: string
}

/** 生成结果（done.result / POST /api/generate-sql 返回）的强类型视图 */
export interface QaResult extends QaStreamResult {
  generation_mode?: string
  workflow?: string
  located_tables?: string[]
  code_value_hits?: CodeValueHit[]
  retrieved_pairs?: RetrievedPair[]
  review_status?: ReviewStatus | null
  usage?: { llm_calls?: number; total_tokens?: number }
  gen_timers?: Record<string, number>
  attempts?: number
  explanation?: string
  result_preview?: ResultPreview | null
}

/** 把 useSSE 的 QaStreamResult 收窄为强类型 QaResult（结构一致，仅类型增强） */
export function asQaResult(raw: QaStreamResult | null | undefined): QaResult | null {
  return (raw as QaResult | null) ?? null
}

/** 生成 SQL（JSON 版，非流式；流式走 /generate-sql-stream） */
export function generateSql(payload: {
  question: string
  no_reference?: boolean
  mode?: 'qa' | 'training'
  qa_id?: number | null
  generated?: boolean
}): Promise<QaResult> {
  return api.post<QaResult>('/generate-sql', payload)
}

/** 保存为问答对（收束条「加入问答对库」） */
export function saveQa(payload: {
  question: string
  sql: string
  result_preview: string
}): Promise<{ success: boolean; message?: string }> {
  return api.post<{ success: boolean; message?: string }>('/save-qa', payload)
}
