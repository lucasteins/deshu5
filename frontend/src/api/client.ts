/**
 * axios 统一实例（03-迁移方案 §3.1）
 *
 * - baseURL `/api`：开发期经 Vite proxy → 127.0.0.1:5050；生产为 Flask 同源
 * - 错误归一化：一切失败以 `ApiError` 抛出（含 status 与后端 error 文案），
 *   供 UI 按设计稿 §4.4.3「错误文案公式＝发生了什么+为什么+怎么办」拼接展示
 * - 流式接口（SSE）不走本实例，见 `composables/useSSE.ts`
 */
import axios, { type AxiosError, type AxiosRequestConfig } from 'axios'
import type { ApiErrorPayload, HealthStatus } from '@/types'

export const API_BASE = '/api'

/** 统一超时（普通查询/统计；LLM 长任务走 SSE 不受此限） */
const DEFAULT_TIMEOUT = 120_000

export class ApiError extends Error {
  /** HTTP 状态码；网络层失败（超时/不可达）为 null */
  readonly status: number | null
  /** 后端错误载荷（若可解析） */
  readonly payload: ApiErrorPayload | null

  constructor(
    message: string,
    options: { status?: number | null; payload?: ApiErrorPayload | null } = {},
  ) {
    super(message)
    this.name = 'ApiError'
    this.status = options.status ?? null
    this.payload = options.payload ?? null
  }
}

/** 归一化任意抛出：ApiError 原样透传；axios 错误 → 后端文案 / HTTP 状态 / 网络文案 */
export function normalizeError(err: unknown): ApiError {
  if (err instanceof ApiError) return err
  if (axios.isAxiosError(err)) {
    const ax = err as AxiosError<ApiErrorPayload>
    const raw = ax.response?.data
    const payload: ApiErrorPayload | null =
      raw && typeof raw === 'object' ? (raw as ApiErrorPayload) : null
    if (ax.response) {
      const message = payload?.error || payload?.message || `HTTP ${ax.response.status}`
      return new ApiError(message, { status: ax.response.status, payload })
    }
    if (ax.code === 'ECONNABORTED') return new ApiError('请求超时，请稍后重试')
    return new ApiError('网络不可达，请确认后端服务（5050）已启动')
  }
  return new ApiError(err instanceof Error ? err.message : String(err))
}

export const http = axios.create({
  baseURL: API_BASE,
  timeout: DEFAULT_TIMEOUT,
  headers: { 'Content-Type': 'application/json' },
})

http.interceptors.response.use(
  (response) => response,
  (error: unknown) => Promise.reject(normalizeError(error)),
)

/** 发起请求并取响应体（错误已归一化为 ApiError） */
export async function request<T>(config: AxiosRequestConfig): Promise<T> {
  const { data } = await http.request<T>(config)
  return data
}

/** 动词糖：`api.get<Paged<QaPair>>('/qa-pairs', { page: 1 })` */
export const api = {
  get: <T>(url: string, params?: Record<string, unknown>, config?: AxiosRequestConfig) =>
    request<T>({ ...config, method: 'GET', url, params }),
  post: <T>(url: string, data?: unknown, config?: AxiosRequestConfig) =>
    request<T>({ ...config, method: 'POST', url, data }),
  put: <T>(url: string, data?: unknown, config?: AxiosRequestConfig) =>
    request<T>({ ...config, method: 'PUT', url, data }),
  patch: <T>(url: string, data?: unknown, config?: AxiosRequestConfig) =>
    request<T>({ ...config, method: 'PATCH', url, data }),
  delete: <T>(url: string, config?: AxiosRequestConfig) =>
    request<T>({ ...config, method: 'DELETE', url }),
}

/** 健康检查（联调自测/启动探活） */
export function getHealth(): Promise<HealthStatus> {
  return api.get<HealthStatus>('/health')
}
