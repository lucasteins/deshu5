/**
 * 统计看板 API（F1.2）
 *
 * 端点（以源码为准）：
 * - GET  /api/stats            → modules/resources/routes.py:348  看板全量指标快照
 * - GET  /api/generation-logs  → modules/resources/routes.py:489  运行日志分页（工作流运行表）
 *
 * 时间序列说明（真实数据口径）：/api/stats 为**快照**，不含历史序列；看板的
 * 「趋势 / 活跃度」两图改由 /api/qa-pairs 的 `ingest_time` 聚合而来（见 fetchAllQaPairs 复用）。
 */
import { api } from './client'

/* ---------- /api/stats ---------- */

/** 通用「名称 + 计数」分布项（难度 / 来源） */
export interface NamedCount {
  name: string
  count: number
}

/** 错题归因分布项（字段名为 type，与后端一致） */
export interface ErrorTypeCount {
  type: string
  count: number
}

export interface StatsData {
  qa_pairs: { total: number }
  error_records: {
    total: number
    resolved: number
    unresolved: number
    type_distribution: ErrorTypeCount[]
  }
  /** 生成判定分布（正确 / 错误 / 跳过） */
  generation: { total: number; correct: number; error: number; skipped: number }
  qa_pairs_dist: { difficulty: NamedCount[]; source: NamedCount[] }
  generation_health: {
    avg_latency_ms: number
    /** 后端 round(...,2) 结果，JSON 中可能为数值或数值字符串 */
    avg_attempts: number
    exec_success_rate: number
  }
  basic: {
    tables: number
    columns: number
    relationships: number
    code_domains: number
    code_items: number
    qa_usable: number
    qa_pending: number
  }
  human_annotation: {
    annotated_count: number
    table_choice_correct: number
    field_choice_correct: number
    join_path_correct: number
    where_condition_correct: number
    aggregation_correct: number
  }
}

export interface StatsResponse {
  success: boolean
  data: StatsData
  error?: string
}

export function fetchStats(): Promise<StatsResponse> {
  return api.get<StatsResponse>('/stats')
}

/* ---------- /api/generation-logs ---------- */

export interface GenerationLog {
  id: number
  question: string
  generated_sql: string
  execution_status: string
  review_passed: boolean
  user_judgment: string
  attempts: number
  latency_ms: number
  row_count: number | null
  /** 「Tue, 08 Sep 2026 18:58:37 GMT」（实为本地时间，见 api/qa.ts#normalizeQaTime） */
  created_at: string
}

export interface GenerationLogsResponse {
  success: boolean
  total: number
  page: number
  per_page: number
  items: GenerationLog[]
}

export function fetchGenerationLogs(
  page = 1,
  perPage = 20,
): Promise<GenerationLogsResponse> {
  return api.get<GenerationLogsResponse>('/generation-logs', {
    page,
    per_page: perPage,
  })
}

/* ---------- 问答对时间事实（趋势 / 活跃度图的数据源） ---------- */
/*
 * 看板的「问答对入库趋势」「入库活跃度分布」由 `ingest_time` 聚合得到；
 * 全量取回复用 F1.1 的 `fetchAllQaPairs()`（`@/api/qa`，只读导入，不修改对方文件域）。
 */

