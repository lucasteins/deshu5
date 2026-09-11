<script setup lang="ts">
/**
 * 顶栏（TopBar，56px）—— F0.3 布局壳
 *
 * 依据设计稿 §4.4.6「顶栏」：面包屑 + 对象/会话标题 +（右）⌘K 命令面板 + 模型标识
 * + 演示模式开关 + 主题切换。
 * 主题 / 演示开关直接复用 F0.2 的 useAppStore（toggleTheme / toggleDemo），
 * 样式切换由 <html data-theme|data-mode> 驱动，本组件不写死颜色。
 *
 * 说明：命令面板（⌘K）与用户区为壳的占位交互，完整实现见 F2.6；用户区按设计稿
 * mockups 置于侧栏底部（SideRail）。
 */
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useAppStore } from '@/stores/app'
import { resolveNav } from './navigation'

const emit = defineEmits<{ command: [] }>()

const route = useRoute()
const app = useAppStore()

const nav = computed(() => resolveNav(route.name))
const crumbGroup = computed(() => nav.value?.group.label ?? '')
const crumbTitle = computed(() => nav.value?.item.title ?? '')
const isDemo = computed(() => app.displayMode === 'demo')
const isDark = computed(() => app.theme === 'dark')
</script>

<template>
  <header class="topbar">
    <!-- 面包屑 + 模块标题 -->
    <div class="crumb">
      <span class="crumb-group">{{ crumbGroup }}</span>
      <span class="crumb-sep">/</span>
      <b class="crumb-title">{{ crumbTitle }}</b>
    </div>

    <div class="topbar-right">
      <!-- ⌘K 命令面板入口（占位；F2.6 实现） -->
      <button class="searchbox" type="button" @click="emit('command')">
        <el-icon><Search /></el-icon>
        <span class="searchbox-text">搜索页面、表、实体…</span>
        <kbd class="kbd">⌘K</kbd>
      </button>

      <!-- 模型标识 -->
      <span class="chip-model"><i class="dot" aria-hidden="true" />deshu5-sql-v3</span>

      <!-- 演示模式开关 -->
      <el-tooltip content="演示模式（字号 ×1.15 / 行高放宽）" placement="bottom" :show-after="200">
        <button
          class="chip-toggle"
          :class="{ 'is-on': isDemo }"
          type="button"
          :aria-pressed="isDemo"
          @click="app.toggleDemo()"
        >
          <el-icon><Monitor /></el-icon>
          <span>演示</span>
        </button>
      </el-tooltip>

      <!-- 主题切换（明 / 暗副模） -->
      <el-tooltip :content="isDark ? '切换到明色' : '切换到暗色副模'" placement="bottom" :show-after="200">
        <button class="icon-btn" type="button" :aria-pressed="isDark" @click="app.toggleTheme()">
          <el-icon><component :is="isDark ? 'Sunny' : 'Moon'" /></el-icon>
        </button>
      </el-tooltip>
    </div>
  </header>
</template>

<style scoped>
.topbar {
  height: 56px;
  flex: none;
  display: flex;
  align-items: center;
  gap: var(--sp-4);
  padding: 0 var(--sp-5);
  background: var(--surface-1);
  border-bottom: 1px solid var(--line);
}

/* 面包屑 */
.crumb {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  font-size: var(--fs-body-sm);
  color: var(--text-3);
}
.crumb-sep {
  color: var(--text-4);
}
.crumb-title {
  font-size: var(--fs-body-lg);
  font-weight: 600;
  letter-spacing: -0.01em;
  color: var(--text-strong);
  white-space: nowrap;
}

/* 右侧操作位 */
.topbar-right {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 10px;
}

.searchbox {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  width: 220px;
  height: 32px;
  padding: 0 10px;
  border: 1px solid transparent;
  border-radius: var(--r-sm);
  background: var(--sunken);
  color: var(--text-3);
  font-size: var(--fs-body-sm);
  cursor: pointer;
  transition:
    border-color var(--dur-fast) var(--ease-standard),
    background-color var(--dur-fast) var(--ease-standard);
}
.searchbox:hover {
  border-color: var(--line);
}
.searchbox:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring);
}
.searchbox-text {
  flex: 1;
  text-align: left;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.kbd {
  flex: none;
  padding: 1px 5px;
  border: 1px solid var(--line);
  border-radius: 4px;
  background: var(--surface-1);
  color: var(--text-3);
  font-family: var(--font-mono);
  font-size: var(--fs-micro);
}

.chip-model {
  display: flex;
  align-items: center;
  gap: 6px;
  height: 28px;
  padding: 0 10px;
  border: 1px solid var(--accent-line);
  border-radius: var(--r-full);
  background: var(--accent-soft);
  color: var(--accent);
  font-family: var(--font-mono);
  font-size: var(--fs-micro);
  font-weight: 500;
  white-space: nowrap;
}
.chip-model .dot {
  width: 6px;
  height: 6px;
  border-radius: var(--r-full);
  background: var(--accent);
}

/* 演示模式开关（文字胶囊，开启态强调） */
.chip-toggle {
  display: flex;
  align-items: center;
  gap: 6px;
  height: 28px;
  padding: 0 10px;
  border: 1px solid var(--line);
  border-radius: var(--r-full);
  background: var(--surface-1);
  color: var(--text-2);
  font-size: var(--fs-caption);
  font-weight: 500;
  cursor: pointer;
  transition:
    background-color var(--dur-fast) var(--ease-standard),
    border-color var(--dur-fast) var(--ease-standard),
    color var(--dur-fast) var(--ease-standard);
}
.chip-toggle:hover {
  border-color: var(--line-strong);
  color: var(--text-1);
}
.chip-toggle.is-on {
  border-color: var(--ember);
  background: var(--ember-soft);
  color: var(--ember);
}
.chip-toggle:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring);
}

/* 图标按钮（主题切换） */
.icon-btn {
  width: 32px;
  height: 32px;
  display: grid;
  place-items: center;
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  background: var(--surface-1);
  color: var(--text-2);
  font-size: 16px;
  cursor: pointer;
  transition:
    background-color var(--dur-fast) var(--ease-standard),
    border-color var(--dur-fast) var(--ease-standard),
    color var(--dur-fast) var(--ease-standard);
}
.icon-btn:hover {
  background: var(--surface-2);
  border-color: var(--line-strong);
  color: var(--text-1);
}
.icon-btn:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring);
}
</style>
