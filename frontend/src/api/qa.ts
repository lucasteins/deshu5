/**
 * 问答对库 API（对应 modules/training/routes.py 端点，F1.1）
 *
 * - GET  /api/qa-pairs   分页列表（后端仅分页参数 → 全量取回，交互走客户端管道）
 * - GET  /api/qa-detail  单条详情
 * - POST /api/qa-search  语义搜索（RAG 相似度排序）
 * - POST /api/qa-update  更新（question / standard_sql / difficulty）
 * - POST /api/qa-delete  删除
 */
import { api } from './client'
import type { Paged } from '@/types'

/** 难度枚举（与库内取值一致；旧实现默认「进阶题」） */
export const QA_DIFFICULTIES = ['基础题', '进阶题', '挑战题'] as const
export type QaDifficulty = (typeof QA_DIFFICULTIES)[number]

export interface QaPair {
  id: number
  question: string
  standard_sql: string
  difficulty: string
  source: string
  /** 后端序列化串：「Thu, 10 Sep 2026 15:35:20 GMT」（实为本地时间，见 normalizeQaTime） */
  ingest_time: string | null
  /** 1 = 可用；0 = 待评价 */
  is_usable: number
  /** 已下线列，键保留（后端兼容字段） */
  question_rating?: string | null
  tables_involved?: string[]
}

export interface QaPairDetail {
  id: number
  question: string
  standard_sql: string
  difficulty: string
  source: string
  ingest_time: string | null
}

export interface QaSearchItem extends QaPair {
  similarity_score?: number
  combined_score?: number
}

export interface QaSearchResult {
  success: boolean
  query: string
  count: number
  items: QaSearchItem[]
}

export interface QaUpdatePayload {
  id: number
  question?: string
  standard_sql?: string
  difficulty?: string
}

export interface QaMutationResult {
  success: boolean
  message?: string
}

const PAGE_SIZE = 500

/** 全量取回（试点期数据为百级；超过一页自动续取，防御未来增长） */
export async function fetchAllQaPairs(): Promise<{ items: QaPair[]; total: number }> {
  const first = await api.get<Paged<QaPair>>('/qa-pairs', { page: 1, per_page: PAGE_SIZE })
  const items: QaPair[] = [...(first.items ?? [])]
  const total = first.total ?? items.length
  let page = 2
  while (items.length < total) {
    const next = await api.get<Paged<QaPair>>('/qa-pairs', { page, per_page: PAGE_SIZE })
    const chunk = next.items ?? []
    if (!chunk.length) break
    items.push(...chunk)
    page += 1
  }
  return { items, total }
}

export function fetchQaDetail(id: number): Promise<{ success: boolean; item: QaPairDetail }> {
  return api.get<{ success: boolean; item: QaPairDetail }>('/qa-detail', { id })
}

export function searchQaPairs(query: string, limit = 50): Promise<QaSearchResult> {
  return api.post<QaSearchResult>('/qa-search', { query, limit })
}

export function updateQaPair(payload: QaUpdatePayload): Promise<QaMutationResult> {
  return api.post<QaMutationResult>('/qa-update', payload)
}

export function deleteQaPair(id: number): Promise<QaMutationResult> {
  return api.post<QaMutationResult>('/qa-delete', { id })
}

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

/**
 * 规范化 ingest_time：「Thu, 10 Sep 2026 15:35:20 GMT」→「2026-09-10 15:35:20」
 *
 * 该串虽带 GMT 标注，实为服务器本地时间（Flask 对 naive datetime 的序列化所致），
 * 故按字面分量转换、**不做时区换算**；结果可直接用于字典序排序与展示。
 */
export function normalizeQaTime(raw: string | null | undefined): string {
  if (!raw) return ''
  const m = /(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})\s+(\d{2}:\d{2}:\d{2})/.exec(raw)
  if (!m) return raw
  const mm = MONTHS[m[2]]
  if (!mm) return raw
  return `${m[3]}-${mm}-${m[1].padStart(2, '0')} ${m[4]}`
}
