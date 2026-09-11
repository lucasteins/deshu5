<script setup lang="ts">
/**
 * 数据资源（F2.2，四页签：目录模式 / 图谱模式 / 码值库 / 资源管理）
 *
 * 页签状态由 AppShell 的 TabBar 驱动（写入 URL `#tab=<key>`）；本视图读取 hash 决定
 * 显示哪个子页签，与其保持同步（刷新可复现）。schema-graph（nodes/edges）在挂载时
 * 加载一次，供目录 / 图谱共用；码值库 / 资源管理各自懒加载。
 */
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { fetchSchemaGraph, type SchemaEdge, type SchemaNode } from '@/api/resources'
import CatalogView from './CatalogView.vue'
import GraphView from './GraphView.vue'
import CodeCatalogView from './CodeCatalogView.vue'
import ResourceAdminView from './ResourceAdminView.vue'
import TableDetailDrawer from './TableDetailDrawer.vue'

const TABS = ['catalog', 'graph', 'code', 'admin'] as const
type Rtab = (typeof TABS)[number]

const activeTab = ref<Rtab>('catalog')

function readHashTab(): string {
  const m = /(?:^#|&)tab=([^&]*)/.exec(window.location.hash)
  return m ? decodeURIComponent(m[1]) : ''
}
function syncTab() {
  const fromHash = readHashTab()
  activeTab.value = (TABS as readonly string[]).includes(fromHash)
    ? (fromHash as Rtab)
    : 'catalog'
}

/* ---------- schema-graph（目录 / 图谱共用） ---------- */
const nodes = ref<SchemaNode[]>([])
const edges = ref<SchemaEdge[]>([])
const loading = ref(false)
const error = ref<unknown>(null)

async function loadGraph() {
  loading.value = true
  error.value = null
  try {
    const res = await fetchSchemaGraph()
    nodes.value = res.nodes ?? []
    edges.value = res.edges ?? []
  } catch (e) {
    error.value = e
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  syncTab()
  window.addEventListener('hashchange', syncTab)
  void loadGraph()
})
onBeforeUnmount(() => window.removeEventListener('hashchange', syncTab))

/* ---------- 表详情抽屉（目录 / 图谱共用） ---------- */
const drawerTable = ref<string | null>(null)
function openTable(t: string) {
  drawerTable.value = t
}
function closeTable() {
  drawerTable.value = null
}
function navigateTable(t: string) {
  drawerTable.value = t
}
</script>

<template>
  <div class="resources">
    <CatalogView
      v-if="activeTab === 'catalog'"
      :nodes="nodes"
      :loading="loading"
      :error="error"
      @open="openTable"
      @retry="loadGraph"
    />
    <GraphView
      v-else-if="activeTab === 'graph'"
      :nodes="nodes"
      :edges="edges"
      :loading="loading"
      :error="error"
      @open="openTable"
      @close="closeTable"
      @retry="loadGraph"
    />
    <CodeCatalogView v-else-if="activeTab === 'code'" />
    <ResourceAdminView v-else-if="activeTab === 'admin'" />

    <TableDetailDrawer :table="drawerTable" @close="closeTable" @navigate="navigateTable" />
  </div>
</template>

<style scoped>
.resources {
  display: flex;
  flex-direction: column;
}
</style>
