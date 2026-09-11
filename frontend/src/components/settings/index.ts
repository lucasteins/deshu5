/**
 * 设置弹窗命令式 API（对等旧实现 static/js/settings.js 的 openSettings）
 *
 * 用法：
 *   import { openSettings } from '@/components'
 *   openSettings()   // 打开设置弹窗，关闭后自动卸载
 *
 * 自挂载到 body（与 toast / confirm 同一模式），SideRail 底部「⚙ 设置」入口调用。
 */
import { createApp } from 'vue'
import SettingsDialog from './SettingsDialog.vue'

let activeEl: HTMLDivElement | null = null
let activeApp: ReturnType<typeof createApp> | null = null

export function openSettings(): void {
  // 防止重复打开
  if (activeEl && activeApp) return

  const el = document.createElement('div')
  document.body.appendChild(el)
  const app = createApp(SettingsDialog, {
    onClose: () => {
      // 对话框退场动画结束后再卸载
      window.setTimeout(() => {
        app.unmount()
        el.remove()
        activeEl = null
        activeApp = null
      }, 0)
    },
  })
  activeEl = el
  activeApp = app
  app.mount(el)
}
