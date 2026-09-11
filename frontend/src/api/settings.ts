/**
 * 设置 API（对应 modules/settings/routes.py 端点，F2.5）
 *
 * - GET/POST /api/settings/llm         LLM 多 Provider 配置（读写，api_key 脱敏）
 * - POST     /api/settings/llm/test    用表单给定配置发一条最小请求测试连通性（不落库）
 * - GET      /api/settings/db          当前数据库档位 + 全部档位四库连通状态
 * - POST     /api/settings/db/profile  切换档位（失效缓存并重载，免重启）
 * - POST     /api/settings/db/test     按表单配置测试连通性（不落库）
 * - GET/POST /api/workflows            生成工作流预设（列出 / 切换，热生效）
 */
import { api } from './client'

/* ---------- LLM 配置 ---------- */

export interface LlmProvider {
  api_url: string
  model: string
  temperature: number | null
  /** 是否已配置 api_key（脱敏后的字段） */
  has_key: boolean
  api_key_masked: string
}

export interface LlmSettings {
  success: boolean
  active: string
  providers: Record<string, LlmProvider>
  presets?: Record<string, { api_url: string; model: string }>
}

export interface LlmSavePayload {
  provider: string
  api_url?: string
  model?: string
  api_key?: string
  temperature?: number | null
  set_active?: boolean
}

export interface LlmTestResult {
  success?: boolean
  ok: boolean
  elapsed_ms?: number
  model?: string
  reasoning?: boolean
  content?: string
  error?: string
}

export function getLlmSettings(): Promise<LlmSettings> {
  return api.get<LlmSettings>('/settings/llm')
}

export function saveLlmSettings(payload: LlmSavePayload): Promise<LlmSettings> {
  return api.post<LlmSettings>('/settings/llm', payload)
}

export function testLlm(payload: LlmSavePayload): Promise<LlmTestResult> {
  return api.post<LlmTestResult>('/settings/llm/test', payload)
}

/* ---------- 数据库配置 ---------- */

export interface DbProfile {
  name: string
  label: string
  business: string
  governance: string
  log: string
  ontology: string
  connected: boolean
  ping: Record<string, boolean | string>
}

export interface DbSettings {
  success: boolean
  host: string
  port: number
  user: string
  password_masked: string
  current_profile: string
  current_label: string
  profiles: DbProfile[]
  databases: { key: string; name: string; desc: string }[]
}

export interface DbSwitchResult {
  success: boolean
  current_profile: string
  current_label: string
  databases: { key: string; name: string; connected: boolean; error: string }[]
}

export interface DbTestResult {
  success?: boolean
  ok: boolean
  elapsed_ms?: number
  server_version?: string
  error?: string
}

export function getDbSettings(): Promise<DbSettings> {
  return api.get<DbSettings>('/settings/db')
}

export function switchDbProfile(profile: string): Promise<DbSwitchResult> {
  return api.post<DbSwitchResult>('/settings/db/profile', { profile })
}

export function testDb(payload: {
  host?: string
  port?: string
  user?: string
  password?: string
}): Promise<DbTestResult> {
  return api.post<DbTestResult>('/settings/db/test', payload)
}

/* ---------- 生成工作流预设 ---------- */

export interface WorkflowPreset {
  name: string
  note?: string
  overrides?: Record<string, unknown>
}

export interface WorkflowSettings {
  success: boolean
  current: string
  presets: WorkflowPreset[]
}

export function getWorkflows(): Promise<WorkflowSettings> {
  return api.get<WorkflowSettings>('/workflows')
}

export function switchWorkflow(name: string): Promise<{ success: boolean; current: string; config?: unknown }> {
  return api.post<{ success: boolean; current: string; config?: unknown }>('/workflows', { name })
}
