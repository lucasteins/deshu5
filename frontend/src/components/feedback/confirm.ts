/**
 * 三级确认对话框公共 API（设计稿 §4.4.1）——替换现行 65 处 alert 中的二次确认（约 35%）
 *
 *   L1 常规确认：≤1 个对象、操作可逆（宽 420px，纯键盘 Esc / Enter 可完成）
 *   L2 影响范围确认：批量 ≥10 条、或影响其他模块数据（宽 520px，列出具体影响清单 +
 *      勾选「我已确认上述影响范围」才可确认）
 *   L3 危险操作二次确认：删除库表 / 清空归因 / 覆盖本体模型等（宽 560px，危险色实心按钮 +
 *      手动输入对象名称 + 「先导出备份」入口 + 「此操作不可撤销」）
 *
 * 返回 Promise<boolean>：true = 用户确认；false = 取消 / Esc / 关闭（不抛错）。
 * 实现说明：按设计稿需对勾选框 / 输入框做响应式闸门（按钮禁用态），ElMessageBox 只能以
 * DOM 手段干预，故用 ElDialog 封装（与 03-迁移方案「ElMessageBox 封装」的偏差已记录于交接日志）。
 */
import { createApp } from 'vue'
import ConfirmDialog, { type ConfirmDialogPayload } from './ConfirmDialog.vue'

export interface ConfirmCommonOptions {
  title: string
  message?: string
  confirmText?: string
  cancelText?: string
  /** 危险语义：确认按钮红色实心 */
  danger?: boolean
}

export interface ConfirmL2Options extends ConfirmCommonOptions {
  /** 具体影响清单（必填——「必须列出具体影响清单」） */
  impacts: string[]
  /** 勾选文案，默认「我已确认上述影响范围」（设计稿原文） */
  impactCheckText?: string
}

export interface ConfirmL3Options extends ConfirmCommonOptions {
  /** 需手动输入的确认对象名称（原样匹配，前后空白忽略） */
  objectName: string
  /** 「先导出备份」快捷入口 */
  backup?: { text?: string; handler: () => void | Promise<void> }
}

function open(payload: ConfirmDialogPayload): Promise<boolean> {
  return new Promise((resolve) => {
    const el = document.createElement('div')
    document.body.appendChild(el)
    const app = createApp(ConfirmDialog, {
      payload,
      onResolve: (confirmed: boolean) => {
        resolve(confirmed)
        // 对话框退场动画结束后再卸载
        window.setTimeout(() => {
          app.unmount()
          el.remove()
        }, 0)
      },
    })
    app.mount(el)
  })
}

export function confirmL1(options: ConfirmCommonOptions): Promise<boolean> {
  return open({
    level: 1,
    title: options.title,
    message: options.message,
    confirmText: options.confirmText ?? '确认',
    cancelText: options.cancelText ?? '取消',
    danger: options.danger ?? false,
  })
}

export function confirmL2(options: ConfirmL2Options): Promise<boolean> {
  return open({
    level: 2,
    title: options.title,
    message: options.message,
    confirmText: options.confirmText ?? '确认',
    cancelText: options.cancelText ?? '取消',
    danger: options.danger ?? false,
    impacts: options.impacts,
    impactCheckText: options.impactCheckText ?? '我已确认上述影响范围',
  })
}

export function confirmL3(options: ConfirmL3Options): Promise<boolean> {
  return open({
    level: 3,
    title: options.title,
    message: options.message,
    confirmText: options.confirmText ?? '确认',
    cancelText: options.cancelText ?? '取消',
    danger: true, // L3 恒为危险操作
    objectName: options.objectName,
    backupText: options.backup?.text ?? '先导出备份',
    backupHandler: options.backup?.handler,
  })
}

export const confirm = {
  l1: confirmL1,
  l2: confirmL2,
  l3: confirmL3,
}
