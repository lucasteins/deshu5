/**
 * useSSE —— SSE 流式消费（03-迁移方案 §4.3；语义对齐 static/js/app.js#streamGenerateSQL）
 *
 * - fetch + ReadableStream（POST 流式；EventSource 仅支持 GET，不可用）
 * - `\n\n` 分帧 → 收集 `data:` 行 → JSON.parse → onEvent 回调（坏帧静默跳过，同现行行为）
 * - 终态：done 帧 → status='done' 且 result 落地；error 帧 / 流中断 → status='error'
 * - abort()：主动中断（已消费事件保留，供"停止生成"后展示部分结果）
 *
 * 用法（F2.1 示例）：
 *   const sse = useSSE<QaStreamEvent, QaStreamResult>('/generate-sql-stream', {
 *     onEvent: (evt) => { …流程轨 / 思考窗口分发… },
 *   })
 *   await sse.start({ question, mode: 'training' })
 *   if (sse.status.value === 'error') …
 */
import { computed, ref, shallowRef, type ComputedRef, type Ref, type ShallowRef } from 'vue'
import { API_BASE, normalizeError } from '@/api/client'
import { isDoneFrame, isErrorFrame } from '@/types'

export type SSEStatus = 'idle' | 'connecting' | 'streaming' | 'done' | 'error' | 'aborted'

export interface UseSSEOptions<E> {
  method?: 'POST' | 'GET'
  /** 每个解析成功的事件（含 done / error 帧）到达即回调 */
  onEvent?: (event: E) => void
}

export interface UseSSEReturn<E, R> {
  status: Ref<SSEStatus>
  error: Ref<Error | null>
  /** done 帧的 result（未收到 done 帧保持 null） */
  result: ShallowRef<R | null>
  lastEvent: ShallowRef<E | null>
  isStreaming: ComputedRef<boolean>
  /** 发起流式请求；resolve = 流已收束（成功与否读 status / error） */
  start: (payload?: unknown) => Promise<void>
  /** 主动中断当前流（status → 'aborted'） */
  abort: () => void
  /** 中断并清空全部状态回 idle */
  reset: () => void
}

/** 从缓冲区切出完整帧（`\n\n` 分隔）；返回帧数组与未完整的尾部（纯函数，便于自测） */
export function splitSSEFrames(buffer: string): { frames: string[]; rest: string } {
  const parts = buffer.split('\n\n')
  const rest = parts.pop() ?? ''
  return { frames: parts, rest }
}

/** 解析单帧：提取全部 `data:` 行（换行拼接）→ JSON.parse；非数据帧 / 坏 JSON 返回 undefined */
export function parseSSEFrame(frame: string): unknown {
  const lines = frame
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line.startsWith('data:'))
  if (lines.length === 0) return undefined
  const payload = lines.map((line) => line.slice(5).trimStart()).join('\n')
  if (!payload) return undefined
  try {
    return JSON.parse(payload)
  } catch {
    return undefined
  }
}

export function useSSE<E = unknown, R = unknown>(
  path: string,
  options: UseSSEOptions<E> = {},
): UseSSEReturn<E, R> {
  const status = ref<SSEStatus>('idle')
  const error = ref<Error | null>(null)
  const result = shallowRef<R | null>(null)
  const lastEvent = shallowRef<E | null>(null)

  const isStreaming = computed(() => status.value === 'connecting' || status.value === 'streaming')

  let controller: AbortController | null = null
  /** 运行令牌：重启/复位后旧回调全部失效 */
  let runSeq = 0

  function reset() {
    controller?.abort()
    controller = null
    runSeq += 1
    status.value = 'idle'
    error.value = null
    result.value = null
    lastEvent.value = null
  }

  function abort() {
    if (controller && isStreaming.value) controller.abort()
  }

  async function start(payload?: unknown): Promise<void> {
    // 重启语义：先断上一段流（其回调由 runSeq 令牌失效）
    controller?.abort()
    const runId = ++runSeq
    const ctrl = new AbortController()
    controller = ctrl

    status.value = 'connecting'
    error.value = null
    result.value = null
    lastEvent.value = null

    const method = options.method ?? 'POST'
    const headers: Record<string, string> = { Accept: 'text/event-stream' }
    let body: string | undefined
    if (method !== 'GET' && payload !== undefined) {
      headers['Content-Type'] = 'application/json'
      body = JSON.stringify(payload)
    }

    const isCurrent = () => runId === runSeq

    try {
      const response = await fetch(`${API_BASE}${path}`, {
        method,
        headers,
        body,
        signal: ctrl.signal,
      })
      if (!isCurrent()) return

      if (!response.ok) {
        const data: unknown = await response.json().catch(() => null)
        const message =
          data && typeof data === 'object' && typeof (data as { error?: unknown }).error === 'string'
            ? (data as { error: string }).error
            : `HTTP ${response.status}`
        throw new Error(message)
      }
      if (!response.body) throw new Error('响应体为空，无法读取流')

      status.value = 'streaming'

      let doneSeen = false
      const dispatch = (raw: unknown) => {
        if (!raw || typeof raw !== 'object') return
        const evt = raw as E
        if (isDoneFrame<R>(evt)) {
          doneSeen = true
          result.value = evt.result
        } else if (isErrorFrame(evt)) {
          error.value = new Error(evt.error)
        }
        lastEvent.value = evt
        options.onEvent?.(evt)
      }

      const reader = response.body.getReader()
      const decoder = new TextDecoder('utf-8')
      let buffer = ''
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const { frames, rest } = splitSSEFrames(buffer)
        buffer = rest
        for (const frame of frames) {
          const raw = parseSSEFrame(frame)
          if (raw !== undefined) dispatch(raw)
        }
      }
      // 收尾：服务端以 \n\n 结尾，正常无残留；如有则一并消费
      buffer += decoder.decode()
      if (buffer.trim()) {
        const raw = parseSSEFrame(buffer)
        if (raw !== undefined) dispatch(raw)
      }

      if (!isCurrent()) return
      if (error.value) {
        status.value = 'error'
      } else if (doneSeen) {
        status.value = 'done'
      } else {
        error.value = new Error('流式响应中断')
        status.value = 'error'
      }
    } catch (e) {
      if (!isCurrent()) return
      if (ctrl.signal.aborted) {
        // 主动中断：保留已消费的事件与部分结果
        status.value = 'aborted'
        return
      }
      error.value = normalizeError(e)
      status.value = 'error'
    }
  }

  return { status, error, result, lastEvent, isStreaming, start, abort, reset }
}
