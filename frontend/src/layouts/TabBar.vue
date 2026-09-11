<script setup lang="ts">
/**
 * 页签容器（TabBar，46px）—— F0.3 布局壳
 *
 * 依据设计稿 §4.4.6「页签」：下划线式（2px --accent 下划线 + 文字 600），
 * 不做胶囊式；切换动效内容区 120ms opacity + 4px 上移（由 AppShell 页面过渡承担）。
 * 页签状态写入 URL `#tab=<key>`，保证刷新可复现。
 *
 * 溢出：本壳先以横向滚动 + 右侧渐隐遮罩兜底；「溢出⋯菜单」随模块内容在 F2.6 补齐。
 */
import type { NavTab } from './navigation'

defineProps<{ tabs: NavTab[]; active: string }>()
defineEmits<{ select: [key: string] }>()
</script>

<template>
  <div class="tabbar" role="tablist">
    <div class="tabbar-scroll">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        class="tab"
        :class="{ 'is-active': tab.key === active }"
        type="button"
        role="tab"
        :aria-selected="tab.key === active"
        @click="$emit('select', tab.key)"
      >
        <span>{{ tab.label }}</span>
        <span v-if="tab.count" class="badge-num tabular">{{ tab.count }}</span>
      </button>
    </div>
  </div>
</template>

<style scoped>
.tabbar {
  flex: none;
  height: 46px;
  display: flex;
  align-items: stretch;
  padding: 0 var(--sp-5);
  background: var(--surface-1);
  border-bottom: 1px solid var(--line);
}
.tabbar-scroll {
  display: flex;
  align-items: stretch;
  gap: 2px;
  min-width: 0;
  overflow-x: auto;
  scrollbar-width: none;
  /* 溢出时右侧渐隐遮罩 */
  mask-image: linear-gradient(to right, #000 calc(100% - 24px), transparent);
}
.tabbar-scroll::-webkit-scrollbar {
  display: none;
}

.tab {
  position: relative;
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 0 var(--sp-3);
  border: none;
  background: transparent;
  font-size: var(--fs-body-sm);
  color: var(--text-2);
  white-space: nowrap;
  cursor: pointer;
  transition: color var(--dur-fast) var(--ease-standard);
}
.tab:hover {
  color: var(--text-1);
}
.tab:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring);
}
.tab.is-active {
  color: var(--text-strong);
  font-weight: 600;
}
.tab.is-active::after {
  content: '';
  position: absolute;
  left: 8px;
  right: 8px;
  bottom: -1px;
  height: 2px;
  border-radius: 2px 2px 0 0;
  background: var(--accent);
}
.badge-num {
  padding: 1px 5px;
  border-radius: var(--r-full);
  background: var(--sunken);
  color: var(--text-3);
  font-family: var(--font-mono);
  font-size: var(--fs-micro);
  font-weight: 400;
}
</style>
