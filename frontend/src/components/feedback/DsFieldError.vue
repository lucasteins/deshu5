<script setup lang="ts">
/**
 * 行内校验错误文案（设计稿 §4.4.1）
 * 控件下方 6px；文案 12px + --danger + 14px 图标；文案结构「问题 + 原因 + 怎么改」。
 * 触发时机由调用方控制：失焦后 / 提交后（输入中不实时报错，改为控件加 .ds-invalid）。
 */
defineProps<{ message?: string }>()
</script>

<template>
  <p v-if="message" class="ds-field-error" role="alert">
    <svg aria-hidden="true" viewBox="0 0 16 16" fill="none">
      <circle cx="8" cy="8" r="6.5" stroke="currentColor" stroke-width="1.5" />
      <path d="M8 5v3.5M8 11h.01" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
    </svg>
    <span>{{ message }}</span>
  </p>
</template>

<style scoped>
.ds-field-error {
  display: flex;
  align-items: flex-start;
  gap: var(--sp-1);
  margin: 6px 0 0;
  font-size: var(--fs-caption); /* 12px */
  line-height: var(--lh-caption);
  color: var(--danger);
}
.ds-field-error svg {
  flex: none;
  width: 14px;
  height: 14px;
  margin-top: 2px;
}
</style>
