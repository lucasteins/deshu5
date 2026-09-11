/**
 * API 通用类型（03-迁移方案 §3.1 / §4）
 *
 * 响应约定（2026-09-11 实测）：
 * - 成功：JSON；列表类端点返回 {items, total, page, per_page}，部分端点包一层 {data: ...}
 * - 失败：API 层为 {error: string}；未匹配路由为 Flask 默认 HTML 404
 *   → 统一由 `api/client.ts` 归一化为 ApiError（见该文件）
 */

/** 分页列表响应（如 GET /api/qa-pairs） */
export interface Paged<T> {
  items: T[]
  total: number
  page: number
  per_page: number
  success?: boolean
}

/** GET /api/health */
export interface HealthStatus {
  status: 'ok' | string
  version: string
  modules: string[]
}

/** 后端错误载荷（尽力解析；HTML 404 等场景不存在） */
export interface ApiErrorPayload {
  error?: string
  message?: string
  [key: string]: unknown
}
