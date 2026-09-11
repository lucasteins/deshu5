import { defineStore } from 'pinia'
import { ref, watch } from 'vue'

export type ThemeMode = 'light' | 'dark'
export type DisplayMode = 'normal' | 'demo'

/** 持久化键名与 index.html 启动脚本（防闪烁）保持一致 */
export const THEME_STORAGE_KEY = 'deshu5.theme'
export const MODE_STORAGE_KEY = 'deshu5.mode'

function prefersDark(): boolean {
  return (
    typeof window !== 'undefined' &&
    window.matchMedia('(prefers-color-scheme: dark)').matches
  )
}

function readStored<T extends string>(key: string, allowed: readonly T[], fallback: T): T {
  try {
    const value = localStorage.getItem(key)
    return value && (allowed as readonly string[]).includes(value) ? (value as T) : fallback
  } catch {
    return fallback
  }
}

/** URL 入口：?theme=dark&mode=demo（与 index.html 启动脚本逻辑保持一致；
 *  取值会随 apply() 持久化，语义与 UI 切换一致） */
function readParam(key: string): string | null {
  try {
    return new URLSearchParams(window.location.search).get(key)
  } catch {
    return null
  }
}

function initialTheme(): ThemeMode {
  const p = readParam('theme')
  if (p === 'light' || p === 'dark') return p
  return readStored(THEME_STORAGE_KEY, ['light', 'dark'] as const, prefersDark() ? 'dark' : 'light')
}

function initialMode(): DisplayMode {
  const p = readParam('mode')
  if (p === 'demo' || p === 'normal') return p
  return readStored(MODE_STORAGE_KEY, ['normal', 'demo'] as const, 'normal')
}

/**
 * 全局少量状态：主题（明色主模 / 暗色副模）与展示模式（常规 / 演示）。
 *
 * 联动契约（设计稿 §4.3）：
 * - <html data-theme="light|dark">  → tokens.css 暗色副模
 * - <html class="dark">             → Element Plus 官方暗色变量
 * - <html data-mode="demo">         → tokens.css 演示模式覆盖（字号 ×1.15 等）
 *
 * 启动防闪烁由 index.html 内联脚本负责（先于 Vue 落地属性），此处负责运行期切换与持久化。
 */
export const useAppStore = defineStore('app', () => {
  const theme = ref<ThemeMode>(initialTheme())
  const displayMode = ref<DisplayMode>(initialMode())

  function apply() {
    const el = document.documentElement
    el.dataset.theme = theme.value
    el.classList.toggle('dark', theme.value === 'dark')
    el.dataset.mode = displayMode.value
    try {
      localStorage.setItem(THEME_STORAGE_KEY, theme.value)
      localStorage.setItem(MODE_STORAGE_KEY, displayMode.value)
    } catch {
      /* 隐私模式等不可写场景：忽略，仅本次会话生效 */
    }
  }

  /** 应用启动时调用一次：落地当前主题/模式并建立后续变更联动 */
  function initAppearance() {
    apply()
    watch([theme, displayMode], () => apply())
  }

  function setTheme(value: ThemeMode) {
    theme.value = value
  }

  function toggleTheme() {
    theme.value = theme.value === 'dark' ? 'light' : 'dark'
  }

  function setDisplayMode(value: DisplayMode) {
    displayMode.value = value
  }

  function toggleDemo() {
    displayMode.value = displayMode.value === 'demo' ? 'normal' : 'demo'
  }

  return {
    theme,
    displayMode,
    initAppearance,
    setTheme,
    toggleTheme,
    setDisplayMode,
    toggleDemo,
  }
})
