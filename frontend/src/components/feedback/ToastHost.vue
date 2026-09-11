<script setup lang="ts">
/**
 * Toast 渲染宿主（设计稿 §4.4.1）
 * 右下角 24px；宽度 320–420px；同屏最多 3 条，超出折叠为「+N 条更多」；
 * 进入 240ms / 退出 180ms；role=status（危险类 alert + assertive）。
 */
import { computed, ref } from 'vue'
import { dismissToast, toasts, type ToastItem } from './toastStore'

const MAX_VISIBLE = 3
const expanded = ref(false)

const overflow = computed(() => Math.max(0, toasts.length - MAX_VISIBLE))
const visible = computed(() => (expanded.value ? toasts : toasts.slice(-MAX_VISIBLE)))

function onAction(item: ToastItem) {
  const handler = item.action?.handler
  dismissToast(item.id)
  handler?.()
}
</script>

<template>
  <div class="ds-toast-host">
    <button v-if="overflow > 0" class="ds-toast-more" type="button" @click="expanded = !expanded">
      {{ expanded ? '收起' : `+${overflow} 条更多` }}
    </button>
    <TransitionGroup name="ds-toast" tag="div" class="ds-toast-list">
      <div
        v-for="item in visible"
        :key="item.id"
        class="ds-toast"
        :class="`ds-toast--${item.level}`"
        :role="item.level === 'danger' ? 'alert' : 'status'"
        :aria-live="item.level === 'danger' ? 'assertive' : 'polite'"
      >
        <span class="ds-toast__icon" aria-hidden="true">
          <span v-if="item.level === 'progress'" class="ds-spinner" />
          <svg v-else-if="item.level === 'success'" viewBox="0 0 16 16" fill="none">
            <circle cx="8" cy="8" r="6.5" stroke="currentColor" stroke-width="1.5" />
            <path
              d="M5.2 8.3l1.9 1.9 3.7-4.1"
              stroke="currentColor"
              stroke-width="1.5"
              stroke-linecap="round"
              stroke-linejoin="round"
            />
          </svg>
          <svg v-else-if="item.level === 'warning'" viewBox="0 0 16 16" fill="none">
            <path
              d="M7.1 1.9a1 1 0 0 1 1.8 0l5.8 10.4a1 1 0 0 1-.9 1.5H2.2a1 1 0 0 1-.9-1.5L7.1 1.9Z"
              stroke="currentColor"
              stroke-width="1.5"
              stroke-linejoin="round"
            />
            <path d="M8 6v3M8 11.6h.01" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
          </svg>
          <svg v-else-if="item.level === 'danger'" viewBox="0 0 16 16" fill="none">
            <circle cx="8" cy="8" r="6.5" stroke="currentColor" stroke-width="1.5" />
            <path d="M5.8 5.8l4.4 4.4M10.2 5.8l-4.4 4.4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
          </svg>
          <svg v-else viewBox="0 0 16 16" fill="none">
            <circle cx="8" cy="8" r="6.5" stroke="currentColor" stroke-width="1.5" />
            <path d="M8 7.2V11M8 4.8h.01" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
          </svg>
        </span>
        <div class="ds-toast__body">
          <div class="ds-toast__title">
            {{ item.title }}
            <span v-if="item.count > 1" class="ds-toast__count">×{{ item.count }}</span>
          </div>
          <div v-if="item.desc" class="ds-toast__desc">{{ item.desc }}</div>
          <button v-if="item.action" class="ds-toast__action" type="button" @click="onAction(item)">
            {{ item.action.text }}
          </button>
        </div>
        <button class="ds-toast__close" type="button" aria-label="关闭" @click="dismissToast(item.id)">
          <svg viewBox="0 0 16 16" fill="none">
            <path d="M4.5 4.5l7 7M11.5 4.5l-7 7" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
          </svg>
        </button>
      </div>
    </TransitionGroup>
  </div>
</template>

<style scoped>
.ds-toast-host {
  position: fixed;
  right: var(--sp-6); /* 24px */
  bottom: var(--sp-6);
  z-index: 3500; /* 高于 EP 弹层（2000+），确认框弹出时反馈仍可见 */
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: var(--sp-2);
  pointer-events: none; /* 宿主不挡点击，条目自身恢复 */
}

.ds-toast-list {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: var(--sp-3); /* 12px */
}

.ds-toast {
  pointer-events: auto;
  display: flex;
  align-items: flex-start;
  gap: var(--sp-2);
  box-sizing: border-box;
  width: max-content;
  min-width: min(320px, calc(100vw - var(--sp-12)));
  max-width: min(420px, calc(100vw - var(--sp-12)));
  padding: 12px 14px;
  background: var(--surface-1);
  border: 1px solid var(--line);
  border-radius: var(--r-md);
  box-shadow: var(--e3);
}

.ds-toast__icon {
  flex: none;
  width: 16px;
  height: 16px;
  margin-top: 2px;
}
.ds-toast__icon svg {
  display: block;
  width: 16px;
  height: 16px;
}
.ds-toast--success .ds-toast__icon {
  color: var(--success);
}
.ds-toast--info .ds-toast__icon {
  color: var(--info);
}
.ds-toast--warning .ds-toast__icon {
  color: var(--warning);
}
.ds-toast--danger .ds-toast__icon {
  color: var(--danger);
}
.ds-toast--progress .ds-toast__icon {
  display: flex;
  align-items: center;
  justify-content: center;
}

.ds-toast__body {
  flex: 1;
  min-width: 0;
}

.ds-toast__title {
  font-size: var(--fs-h3); /* 14 / 600 */
  font-weight: var(--fw-h3);
  line-height: var(--lh-h3);
  color: var(--text-1);
}
.ds-toast__count {
  margin-left: var(--sp-1);
  font-family: var(--font-mono);
  font-size: var(--fs-caption);
  font-weight: 400;
  color: var(--text-3);
}
.ds-toast__desc {
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2; /* 最多 2 行 */
  overflow: hidden;
  margin-top: 2px;
  font-size: var(--fs-body-sm); /* 13 / 400 */
  line-height: var(--lh-body-sm);
  color: var(--text-2);
}
.ds-toast__action {
  margin-top: var(--sp-1);
  padding: 0;
  border: none;
  background: none;
  font-size: var(--fs-body-sm);
  color: var(--accent);
  cursor: pointer;
}
.ds-toast--danger .ds-toast__action {
  color: var(--danger);
}
.ds-toast__action:hover {
  text-decoration: underline;
}

.ds-toast__close {
  flex: none;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  margin-top: 1px;
  padding: 0;
  border: none;
  border-radius: var(--r-sm);
  background: none;
  color: var(--text-4);
  cursor: pointer;
}
.ds-toast__close svg {
  width: 12px;
  height: 12px;
}
.ds-toast__close:hover {
  color: var(--text-2);
  background: var(--sunken);
}

.ds-toast-more {
  pointer-events: auto;
  padding: 4px 10px;
  border: 1px solid var(--line);
  border-radius: var(--r-full);
  background: var(--surface-1);
  box-shadow: var(--e1);
  font-size: var(--fs-caption);
  color: var(--text-2);
  cursor: pointer;
}
.ds-toast-more:hover {
  background: var(--surface-2);
  color: var(--text-1);
}

/* 进入 240ms（透明度 + 12px 上移）／退出 180ms */
.ds-toast-enter-active {
  transition:
    opacity var(--dur-slow) var(--ease-standard),
    transform var(--dur-slow) var(--ease-standard);
}
.ds-toast-leave-active {
  transition:
    opacity var(--dur-base) var(--ease-out),
    transform var(--dur-base) var(--ease-out);
}
.ds-toast-enter-from {
  opacity: 0;
  transform: translateY(12px);
}
.ds-toast-leave-to {
  opacity: 0;
  transform: translateY(8px);
}
.ds-toast-move {
  transition: transform var(--dur-base) var(--ease-in-out);
}

@media (prefers-reduced-motion: reduce) {
  .ds-toast-enter-active,
  .ds-toast-leave-active,
  .ds-toast-move {
    transition-duration: 1ms;
  }
}
</style>
