/**
 * SQL 查询（SQL Lab）API（F2.6，对应 modules/training/sqllab/routes.py 端点）
 *
 * - POST /api/sqllab/execute → 手写 SQL 只读执行（SELECT/WITH 白名单，后端自动补 LIMIT）
 * - POST /api/sqllab/assist  → LLM 辅助：generate / fix / explain / optimize
 *
 * 右侧数据资源目录直接复用 resources.ts 的 fetchSchemaGraph / fetchTableColumns。
 */
import { api } from './client'

/* ==================== 执行 ==================== */

export interface SqlExecuteResponse {
  success: boolean
  headers: string[]
  /** 单元格已序列化为 string | null */
  rows: (string | null)[][]
  row_count: number
  elapsed_ms?: number
  error?: string
}

export function executeSql(sql: string): Promise<SqlExecuteResponse> {
  return api.post<SqlExecuteResponse>('/sqllab/execute', { sql })
}

/* ==================== LLM 辅助 ==================== */

export type SqlAssistAction = 'generate' | 'fix' | 'explain' | 'optimize'

export interface SqlAssistRequest {
  action: SqlAssistAction
  /** generate 必填：自然语言需求 */
  question?: string
  /** fix / explain / optimize 必填：编辑器当前 SQL */
  sql?: string
  /** fix 必填：执行错误信息 */
  error?: string
}

export interface SqlAssistResponse {
  success: boolean
  action: SqlAssistAction
  /** generate / fix / optimize 返回的 SQL 文本（不保证已执行） */
  sql?: string
  /** generate 附加返回：无中文别名的纯代码 SQL（备查） */
  raw_sql?: string
  /** generate 的生成说明 */
  explanation?: string
  /** fix / optimize 的修改说明 */
  note?: string
  /** explain 的解读（Markdown） */
  tables_involved?: string[]
  result_preview?: {
    success: boolean
    headers: string[]
    rows: (string | null)[][]
    row_count: number
  } | null
  error?: string
}

export function assistSql(payload: SqlAssistRequest): Promise<SqlAssistResponse> {
  // generate 走完整 NL2SQL 管线（RAG + 审查循环），耗时可超普通查询，放宽到 300s
  return api.post<SqlAssistResponse>('/sqllab/assist', payload, { timeout: 300_000 })
}
