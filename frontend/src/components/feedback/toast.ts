/**
 * Toast 公共 API（设计稿 §4.4.1）——右下角 24px；同键合并计数；危险提示常驻
 *
 * 用法：
 *   import { toast } from '@/components'
 *   toast.success('已保存')
 *   toast.danger({ title: '保存失败', desc: '网络不可达，请确认后端服务已启动', action: { text: '重试', handler: retry } })
 *   toast.progress({ title: '正在生成报告…', key: 'report' })
 *   toast.update('report', { level: 'success', title: '报告已生成', desc: '耗时 42s' })
 *
 * 宿主（ToastHost）首次使用时自动挂载到 body——F0.4 与 F0.3（AppShell）并行期约定：
 * 反馈体系不要求 AppShell 预置挂载点，F2 起如统一治理可改为 AppShell 挂载一次。
 */
import { createApp } from 'vue'
import ToastHost from './ToastHost.vue'
import {
  dismissAll,
  dismissByKey,
  dismissToast,
  pushToast,
  updateToast,
  type ToastItem,
  type ToastLevel,
  type ToastOptions,
} from './toastStore'

export * from './toastStore'

let hostMounted = false

function ensureHost() {
  if (hostMounted || typeof document === 'undefined') return
  hostMounted = true
  const el = document.createElement('div')
  el.id = 'ds-toast-host'
  document.body.appendChild(el)
  createApp(ToastHost).mount(el)
}

type ToastInput = string | ToastOptions

function normalize(input: ToastInput, level: ToastLevel): ToastOptions {
  return typeof input === 'string' ? { title: input, level } : { ...input, level }
}

export const toast = {
  /** 完整形态（标题 + 描述 + 操作 + 合并键） */
  show(options: ToastOptions): ToastItem {
    ensureHost()
    return pushToast(options)
  },
  success(input: ToastInput): ToastItem {
    ensureHost()
    return pushToast(normalize(input, 'success'))
  },
  info(input: ToastInput): ToastItem {
    ensureHost()
    return pushToast(normalize(input, 'info'))
  },
  warning(input: ToastInput): ToastItem {
    ensureHost()
    return pushToast(normalize(input, 'warning'))
  },
  /** 危险提示常驻（不自动消失） */
  danger(input: ToastInput): ToastItem {
    ensureHost()
    return pushToast(normalize(input, 'danger'))
  },
  /** 进行中提示常驻；完成时用 update(key, …) 原地替换为结果 toast */
  progress(input: ToastInput): ToastItem {
    ensureHost()
    return pushToast(normalize(input, 'progress'))
  },
  update(key: string, options: ToastOptions): ToastItem {
    ensureHost()
    return updateToast(key, options)
  },
  dismiss(id: number) {
    dismissToast(id)
  },
  dismissByKey(key: string) {
    dismissByKey(key)
  },
  dismissAll,
}
