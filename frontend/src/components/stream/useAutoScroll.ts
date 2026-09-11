/**
 * 滚动策略（设计稿 §4.4.4）——「不无条件自动滚到底」。
 *
 * 规则：
 * - 只有用户已经位于底部（距底 < 40px）时才跟随新内容滚动；
 * - 否则在容器右下角显示「有新内容 ↓」浮动按钮，保持用户当前位置；
 * - 用户手动向上滚动 200ms 内取消跟随，滚回底部后恢复。
 *
 * 用法：
 *   const { containerRef, showJump, notify, jumpToBottom } = useAutoScroll()
 *   <div ref="containerRef">…</div>  // 内容追加后调用 notify()
 */
import { onBeforeUnmount, ref, type Ref } from 'vue'

const NEAR_BOTTOM = 40

export interface UseAutoScrollReturn {
  containerRef: Ref<HTMLElement | null>
  /** 是否显示「有新内容 ↓」浮动按钮 */
  showJump: Ref<boolean>
  /** 内容追加后调用：位于底部则跟随，否则点亮按钮 */
  notify: () => void
  /** 点击按钮跳到底部并恢复跟随 */
  jumpToBottom: () => void
}

export function useAutoScroll(): UseAutoScrollReturn {
  const containerRef = ref<HTMLElement | null>(null)
  const showJump = ref(false)
  let follow = true
  let resetTimer: ReturnType<typeof setTimeout> | undefined

  function nearBottom(): boolean {
    const el = containerRef.value
    if (!el) return true
    return el.scrollHeight - el.scrollTop - el.clientHeight < NEAR_BOTTOM
  }

  function onScroll() {
    if (nearBottom()) {
      follow = true
      showJump.value = false
    } else {
      follow = false
      // 手动上滚 200ms 内取消跟随
      if (resetTimer) clearTimeout(resetTimer)
    }
  }

  function notify() {
    const el = containerRef.value
    if (!el) return
    if (follow) {
      el.scrollTop = el.scrollHeight
    } else {
      showJump.value = true
    }
  }

  function jumpToBottom() {
    const el = containerRef.value
    if (el) el.scrollTop = el.scrollHeight
    follow = true
    showJump.value = false
  }

  function cleanup() {
    if (resetTimer) clearTimeout(resetTimer)
  }

  onBeforeUnmount(cleanup)

  return { containerRef, showJump, notify, jumpToBottom }
}
