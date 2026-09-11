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
import { ElMessage } from 'element-plus'
import SideRail from './SideRail.vue'
import TopBar from './TopBar.vue'
import TabBar from './TabBar.vue'
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

function readHashTab(): string {
  const m = /(?:^#|&)tab=([^&]*)/.exec(window.location.hash)
  return m ? decodeURIComponent(m[1]) : ''
}

function syncTab() {
  const list = tabs.value
  if (!list.length) {
    activeTab.value = ''
    return
  }
  const fromHash = readHashTab()
  activeTab.value = list.some((t) => t.key === fromHash) ? fromHash : list[0].key
}

function selectTab(key: string) {
  activeTab.value = key
  const next = `#tab=${encodeURIComponent(key)}`
  if (window.location.hash !== next) window.location.hash = next // 触发 hashchange → syncTab（幂等）
}

watch(() => route.fullPath, syncTab, { immediate: true })
onMounted(() => window.addEventListener('hashchange', syncTab))
onBeforeUnmount(() => window.removeEventListener('hashchange', syncTab))

/* ---------- 命令面板占位（F2.6 实现） ---------- */
function onCommand() {
  ElMessage.info('命令面板（⌘K）将在 F2.6 提供')
}
</script>

<template>
  <div class="app">
    <SideRail :collapsed="collapsed" @toggle-collapse="toggleCollapse" />

    <div class="main">
      <TopBar @command="onCommand" />
      <TabBar v-if="tabs.length" :tabs="tabs" :active="activeTab" @select="selectTab" />

      <main class="content">
        <RouterView v-slot="{ Component, route: current }">
          <Transition name="page" mode="out-in">
            <component :is="Component" :key="current.path" />
          </Transition>
        </RouterView>
      </main>
    </div>
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
