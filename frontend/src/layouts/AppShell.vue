<script setup lang="ts">
/**
 * 布局壳 AppShell —— F0.3（03-迁移方案 §五 F0.3 / 设计稿 §4.4.6）
 *
 * 结构：侧栏（240/64px 可折叠）+ 主区（顶栏 56px + 页签容器 46px + 内容区）。
 * 职责边界：本组件只负责「壳」——导航布局、折叠、页签容器与页面过渡；
 * 各模块的实际内容由 src/views/ 下的页面提供（当前为占位页）。
 *
 * 状态：
 * - 侧栏折叠：本地持久化（localStorage: deshu5.sidebar-collapsed），不占用全局 store。
 * - 页签：由导航配置（layouts/navigation.ts）驱动，选中态写入 URL `#tab=<key>`（§4.4.6）。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import SideRail from './SideRail.vue'
import TopBar from './TopBar.vue'
import TabBar from './TabBar.vue'
import { CommandPalette } from '@/components/command'
import { resolveNav } from './navigation'

const COLLAPSE_KEY = 'deshu5.sidebar-collapsed'

const route = useRoute()

/* ---------- 侧栏折叠 ---------- */
function readCollapsed(): boolean {
  try {
    return localStorage.getItem(COLLAPSE_KEY) === '1'
  } catch {
    return false
  }
}
const collapsed = ref(readCollapsed())
function toggleCollapse() {
  collapsed.value = !collapsed.value
  try {
    localStorage.setItem(COLLAPSE_KEY, collapsed.value ? '1' : '0')
  } catch {
    /* 隐私模式等不可写场景：忽略，仅本次会话生效 */
  }
}

/* ---------- 页签（当前模块 + URL #tab 同步） ---------- */
const tabs = computed(() => resolveNav(route.name)?.item.tabs ?? [])
const activeTab = ref('')

/* ---------- 页签切换过渡（设计稿 §4.4.6：内容区 120ms opacity + 4px 上移） ---------- */
const contentRef = ref<HTMLElement | null>(null)
let tabAnimTimer: ReturnType<typeof setTimeout> | undefined

function triggerTabTransition() {
  const el = contentRef.value
  if (!el) return
  el.classList.remove('tab-switch')
  void el.offsetWidth // 强制重排，重置并可靠重触发同一动画
  el.classList.add('tab-switch')
  clearTimeout(tabAnimTimer)
  tabAnimTimer = setTimeout(() => el.classList.remove('tab-switch'), 140)
}

function readHashTab(): string {
  const m = /(?:^#|&)tab=([^&]*)/.exec(window.location.hash)
  return m ? decodeURIComponent(m[1]) : ''
}

function syncTab(fromHashEvent = false) {
  const list = tabs.value
  if (!list.length) {
    activeTab.value = ''
    return
  }
  const fromHash = readHashTab()
  const next = list.some((t) => t.key === fromHash) ? fromHash : list[0].key
  const changed = activeTab.value !== next
  activeTab.value = next
  // 仅「同页页签切换」触发内容区过渡；路由切换由 page 过渡承担，初始同步不触发
  if (changed && fromHashEvent) triggerTabTransition()
}

function selectTab(key: string) {
  const changed = activeTab.value !== key
  activeTab.value = key
  const next = `#tab=${encodeURIComponent(key)}`
  if (window.location.hash !== next) window.location.hash = next // 触发 hashchange → syncTab（幂等）
  if (changed) triggerTabTransition()
}

watch(
  () => route.fullPath,
  () => syncTab(false),
  { immediate: true },
)

function onHashChange() {
  syncTab(true)
}

onMounted(() => window.addEventListener('hashchange', onHashChange))
onBeforeUnmount(() => {
  window.removeEventListener('hashchange', onHashChange)
  clearTimeout(tabAnimTimer)
})

/* ---------- 命令面板（⌘K，F2.6） ---------- */
const showCommand = ref(false)

function openCommand() {
  showCommand.value = true
}

function closeCommand() {
  showCommand.value = false
}

function onGlobalKeydown(e: KeyboardEvent) {
  // ⌘K / Ctrl+K：全站唤起/关闭命令面板（设计稿 §4.4.6）
  if ((e.metaKey || e.ctrlKey) && !e.altKey && !e.shiftKey && e.key.toLowerCase() === 'k') {
    e.preventDefault()
    showCommand.value = !showCommand.value
  }
}

onMounted(() => window.addEventListener('keydown', onGlobalKeydown))
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onGlobalKeydown)
})
</script>

<template>
  <div class="app">
    <SideRail :collapsed="collapsed" @toggle-collapse="toggleCollapse" />

    <div class="main">
      <TopBar @command="openCommand" />
      <TabBar v-if="tabs.length" :tabs="tabs" :active="activeTab" @select="selectTab" />

      <main ref="contentRef" class="content">
        <RouterView v-slot="{ Component, route: current }">
          <Transition name="page" mode="out-in">
            <component :is="Component" :key="current.path" />
          </Transition>
        </RouterView>
      </main>
    </div>

    <CommandPalette :open="showCommand" @close="closeCommand" />
  </div>
</template>

<style scoped>
.app {
  display: flex;
  height: 100%;
  overflow: hidden;
}
.main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.content {
  flex: 1;
  min-height: 0;
  overflow: auto;
  background: var(--canvas);
  /* 设计稿 §4.5.3：内容区四周 20px（--sp-5）。box-sizing 收起 padding，
     避免 flex 子项在 content-box 下溢出出现横向滚动条。 */
  padding: var(--sp-5);
  box-sizing: border-box;
}
/* 页签切换过渡（§4.4.6：内容区 120ms opacity + 4px 上移，不做横向滑动） */
.content.tab-switch {
  animation: appshell-tab-fade var(--dur-fast) var(--ease-standard);
}
@keyframes appshell-tab-fade {
  from {
    opacity: 0;
    transform: translateY(4px);
  }
  to {
    opacity: 1;
    transform: none;
  }
}
/* 降低动效偏好（§4.3.5）：页签过渡降为 opacity 切换 */
@media (prefers-reduced-motion: reduce) {
  .content.tab-switch {
    animation-name: appshell-tab-fade-opacity;
  }
}
@keyframes appshell-tab-fade-opacity {
  from {
    opacity: 0;
  }
  to {
    opacity: 1;
  }
}
</style>

<!-- 页面过渡（设计稿 §4.4.6：页面级切换 180ms 淡入，不做整页位移）。
     过渡类作用在路由组件根节点上，非本组件模板元素，故用非 scoped 块承载。 -->
<style>
.page-enter-active,
.page-leave-active {
  transition: opacity var(--dur-base) var(--ease-standard);
}
.page-enter-from,
.page-leave-to {
  opacity: 0;
}
</style>
