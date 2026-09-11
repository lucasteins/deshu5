<script setup lang="ts">
/**
 * 错误态两型（设计稿 §4.4.3）：
 *   page   页面级（阻断）：居中卡片 = 图标 + 「页面加载失败」+ 一句话原因 + 错误码（等宽可复制）
 *          + [重试] [返回] [复制错误详情]。禁止只显示 "Error 500"。
 *   module 模块级（局部）：错误条（--danger-soft 底 + 左侧 2px --danger 竖条）+ [重试]，
 *          不影响页面其他模块。
 * 错误文案公式：发生了什么 + 为什么 + 怎么办。
 */
import { computed } from 'vue'
import { ElButton } from 'element-plus'
import { toast } from '../feedback/toast'

const props = withDefaults(
  defineProps<{
    variant?: 'page' | 'module'
    title?: string
    /** 一句话原因（错误文案公式的「为什么」） */
    reason?: string
    /** 错误码（等宽字体展示，可复制） */
    code?: string
    retryText?: string
  }>(),
  { variant: 'page' },
)

const emit = defineEmits<{ retry: []; back: [] }>()

const view = computed(() => ({
  title:
    props.title ?? (props.variant === 'page' ? '页面加载失败' : '模块加载失败'),
  reason: props.reason ?? '请求未成功，请稍后重试',
  retryText: props.retryText ?? '重试',
}))

async function copyDetails() {
  const parts = [view.value.title, view.value.reason, props.code].filter(Boolean).join('\n')
  try {
    await navigator.clipboard.writeText(parts)
    toast.success('错误详情已复制')
  } catch {
    // 降级：execCommand（http / 旧环境）
    try {
      const ta = document.createElement('textarea')
      ta.value = parts
      ta.style.position = 'fixed'
      ta.style.opacity = '0'
      document.body.appendChild(ta)
      ta.select()
      document.execCommand('copy')
      ta.remove()
      toast.success('错误详情已复制')
    } catch {
      toast.danger('复制失败，请手动选择错误码复制')
    }
  }
}
</script>

<template>
  <!-- 模块级：错误条 -->
  <div v-if="variant === 'module'" class="ds-error ds-error--module" role="status">
    <svg class="ds-error__icon" aria-hidden="true" viewBox="0 0 16 16" fill="none">
      <circle cx="8" cy="8" r="6.5" stroke="currentColor" stroke-width="1.5" />
      <path d="M8 5v3.5M8 11h.01" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
    </svg>
    <span class="ds-error__text">{{ view.reason }}</span>
    <button class="ds-error__retry" type="button" @click="emit('retry')">{{ view.retryText }}</button>
  </div>

  <!-- 页面级：居中卡片 -->
  <div v-else class="ds-error ds-error--page">
    <div class="ds-error__card">
      <svg class="ds-error__icon" aria-hidden="true" viewBox="0 0 40 40" fill="none">
        <path
          d="M17.6 5.1a2.5 2.5 0 0 1 4.8 0l13 32a2.5 2.5 0 0 1-2.4 3.4H7.2a2.5 2.5 0 0 1-2.4-3.4l12.8-32Z"
          stroke="currentColor"
          stroke-width="2"
          stroke-linejoin="round"
          transform="scale(0.95) translate(1 0)"
        />
        <path d="M20 15v9M20 29.6h.01" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
      </svg>
      <h3 class="ds-error__title">{{ view.title }}</h3>
      <p class="ds-error__reason">{{ view.reason }}</p>
      <div v-if="code" class="ds-error__code">{{ code }}</div>
      <div class="ds-error__actions">
        <ElButton type="primary" @click="emit('retry')">{{ view.retryText }}</ElButton>
        <ElButton @click="emit('back')">返回</ElButton>
        <ElButton text @click="copyDetails">复制错误详情</ElButton>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* ---- 模块级 ---- */
.ds-error--module {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  padding: 8px 12px;
  background: var(--danger-soft);
  border-left: 2px solid var(--danger);
  border-radius: var(--r-sm);
  font-size: var(--fs-body-sm);
  line-height: var(--lh-body-sm);
}
.ds-error--module .ds-error__icon {
  flex: none;
  width: 16px;
  height: 16px;
  color: var(--danger);
}
.ds-error__text {
  flex: 1;
  min-width: 0;
  color: var(--text-1);
}
.ds-error__retry {
  flex: none;
  padding: 0;
  border: none;
  background: none;
  font-size: var(--fs-body-sm);
  color: var(--danger);
  cursor: pointer;
}
.ds-error__retry:hover {
  text-decoration: underline;
}

/* ---- 页面级 ---- */
.ds-error--page {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 320px;
  padding: var(--sp-8) var(--sp-6);
}
.ds-error__card {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  width: min(520px, 100%);
  padding: var(--sp-8);
  background: var(--surface-1);
  border: 1px solid var(--line);
  border-radius: var(--r-lg);
  box-shadow: var(--e1);
}
.ds-error--page .ds-error__icon {
  width: 40px;
  height: 40px;
  color: var(--danger);
}
.ds-error__title {
  margin: var(--sp-4) 0 0;
  font-size: var(--fs-h2); /* 16 / 600 */
  font-weight: var(--fw-h2);
  line-height: var(--lh-h2);
  color: var(--text-strong);
}
.ds-error__reason {
  margin: var(--sp-2) 0 0;
  font-size: var(--fs-body-sm);
  line-height: var(--lh-body-sm);
  color: var(--text-2);
}
.ds-error__code {
  margin-top: var(--sp-3);
  padding: var(--sp-1) var(--sp-3);
  background: var(--sunken);
  border-radius: var(--r-sm);
  font-family: var(--font-mono); /* 等宽 */
  font-size: var(--fs-caption);
  line-height: var(--lh-caption);
  color: var(--text-2);
  user-select: all; /* 一键全选复制 */
}
.ds-error__actions {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  margin-top: var(--sp-5);
}
</style>
