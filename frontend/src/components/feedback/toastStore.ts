/**
 * Toast 状态存储（设计稿 §4.4.1）——与渲染组件解耦，避免循环依赖
 *
 * 语义：同 key 合并计数；danger / progress 常驻（duration 0，手动关闭）；
 *      其余按级别定时消失（成功/信息 4s、警告 6s）；极值刷屏有安全上限。
 */
import { reactive } from 'vue'

export type ToastLevel = 'success' | 'info' | 'warning' | 'danger' | 'progress'

export interface ToastAction {
  text: string
  handler: () => void
}

export interface ToastOptions {
  title: string
  desc?: string
  level?: ToastLevel
  action?: ToastAction
  /** 同 key 合并为一条并显示计数（如连续失败的同一操作） */
  key?: string
  /** 覆盖默认停留时长（ms）；0 = 常驻需手动关闭 */
  duration?: number
}

export interface ToastItem {
  id: number
  level: ToastLevel
  title: string
  desc?: string
  action?: ToastAction
  key?: string
  duration: number
  count: number
}

/** 各级别默认停留（§4.4.1：成功/信息 4s；警告 6s；危险与进行中不自动消失） */
export const TOAST_DURATION: Record<ToastLevel, number> = {
  success: 4000,
  info: 4000,
  warning: 6000,
  danger: 0,
  progress: 0,
}

/** 安全上限：极端刷屏时丢弃最旧的自动关闭条目（常驻条目保留） */
const MAX_ITEMS = 30

export const toasts = reactive<ToastItem[]>([])

let uid = 0
const timers = new Map<number, ReturnType<typeof setTimeout>>()

function clearTimer(id: number) {
  const t = timers.get(id)
  if (t !== undefined) {
    clearTimeout(t)
    timers.delete(id)
  }
}

function armTimer(item: ToastItem) {
  clearTimer(item.id)
  if (item.duration > 0) {
    timers.set(
      item.id,
      setTimeout(() => dismissToast(item.id), item.duration),
    )
  }
}

export function pushToast(options: ToastOptions): ToastItem {
  const level = options.level ?? 'info'
  const duration = options.duration ?? TOAST_DURATION[level]

  if (options.key) {
    const existing = toasts.find((t) => t.key === options.key)
    if (existing) {
      // 同 key 合并：计数累加 + 刷新停留时长 + 更新尾部信息
      existing.count += 1
      if (options.desc !== undefined) existing.desc = options.desc
      if (options.action !== undefined) existing.action = options.action
      existing.duration = duration
      armTimer(existing)
      return existing
    }
  }

  const item: ToastItem = {
    id: ++uid,
    level,
    title: options.title,
    desc: options.desc,
    action: options.action,
    key: options.key,
    duration,
    count: 1,
  }
  toasts.push(item)
  armTimer(item)

  if (toasts.length > MAX_ITEMS) {
    const oldest = toasts.find((t) => t.duration > 0)
    if (oldest) dismissToast(oldest.id)
  }
  return item
}

/** 原地替换（进行中 → 完成，§4.4.1）；key 不存在时按新 toast 处理 */
export function updateToast(key: string, options: ToastOptions): ToastItem {
  const existing = toasts.find((t) => t.key === key)
  if (!existing) return pushToast({ ...options, key })
  const level = options.level ?? existing.level
  existing.level = level
  existing.title = options.title
  existing.desc = options.desc
  existing.action = options.action
  existing.duration = options.duration ?? TOAST_DURATION[level]
  armTimer(existing)
  return existing
}

export function dismissToast(id: number) {
  const idx = toasts.findIndex((t) => t.id === id)
  if (idx >= 0) toasts.splice(idx, 1)
  clearTimer(id)
}

export function dismissByKey(key: string) {
  const t = toasts.find((x) => x.key === key)
  if (t) dismissToast(t.id)
}

export function dismissAll() {
  for (const t of [...toasts]) dismissToast(t.id)
}
