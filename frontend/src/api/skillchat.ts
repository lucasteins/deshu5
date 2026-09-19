/**
 * 技能对话 API 与类型（对应 modules/training/skillchat/routes.py 端点）。
 *
 * skill 列表 / 示例命令（静态 JSON + LLM 刷新写回）/ 会话与消息持久化；
 * 聊天回复走 SSE：POST /skillchat/chat/stream（useSSE，见 views/training/SkillChatView.vue）。
 */
import { api } from './client'
import type { SSEDoneFrame, SSEErrorFrame } from '@/types'

/* ---- 技能 ---- */

export interface SkillChatSkill {
  name: string
  title: string
  description: string
  when_to_use?: string
}

export function fetchSkills(): Promise<{ success: boolean; items: SkillChatSkill[] }> {
  return api.get<{ success: boolean; items: SkillChatSkill[] }>('/skillchat/skills')
}

/* ---- 示例命令 ---- */

export function fetchExamples(skill: string): Promise<{ success: boolean; examples: string[] }> {
  return api.get<{ success: boolean; examples: string[] }>(
    `/skillchat/skills/${encodeURIComponent(skill)}/examples`,
  )
}

/** LLM 读 SKILL.md 重新生成示例并写回静态文件（较慢，调用方需给 loading 态） */
export function refreshExamples(skill: string): Promise<{ success: boolean; examples: string[] }> {
  return api.post<{ success: boolean; examples: string[] }>(
    `/skillchat/skills/${encodeURIComponent(skill)}/examples/refresh`,
  )
}

/* ---- 会话 ---- */

export interface SkillChatSession {
  id: string
  skill: string
  title: string
  created_at?: string
  updated_at?: string
  message_count?: number
}

export interface SkillChatMessage {
  id: number
  role: 'user' | 'assistant' | (string & {})
  content: string
  usage?: Record<string, unknown> | null
  created_at?: string
}

export function createSession(
  skill: string,
): Promise<{ success: boolean; session: SkillChatSession }> {
  return api.post<{ success: boolean; session: SkillChatSession }>('/skillchat/sessions', { skill })
}

export function fetchSessions(
  skill: string,
): Promise<{ success: boolean; items: SkillChatSession[] }> {
  return api.get<{ success: boolean; items: SkillChatSession[] }>('/skillchat/sessions', { skill })
}

export function fetchSessionMessages(
  sessionId: string,
): Promise<{ success: boolean; session: SkillChatSession; messages: SkillChatMessage[] }> {
  return api.get<{ success: boolean; session: SkillChatSession; messages: SkillChatMessage[] }>(
    `/skillchat/sessions/${encodeURIComponent(sessionId)}`,
  )
}

export function deleteSession(sessionId: string): Promise<{ success: boolean }> {
  return api.delete<{ success: boolean }>(`/skillchat/sessions/${encodeURIComponent(sessionId)}`)
}

/* ---- 聊天 SSE（POST /skillchat/chat/stream） ---- */

/** 思考增量帧（reasoning 逐块追加） */
export interface SkillChatThinkingEvent {
  thinking: string
}

/** 正文增量帧 */
export interface SkillChatContentEvent {
  content: string
}

/** done 帧 result：完整回复 + usage */
export interface SkillChatStreamResult {
  content: string
  usage?: Record<string, unknown> | null
  [key: string]: unknown
}

export type SkillChatStreamEvent =
  | SkillChatThinkingEvent
  | SkillChatContentEvent
  | SSEDoneFrame<SkillChatStreamResult>
  | SSEErrorFrame
