<script setup lang="ts">
/**
 * 实体图谱（F2.3，原 static/js/ontology.js 实体自绘图谱）
 *
 * 复用 F2.2 图谱基座 `DsForceGraph`（深色观测台窗口延续 + 实体域配色）：
 * 拖拽 / 缩放 / 搜索 / 双击聚焦 / 最短路 / 自动布局 / 重置均由基座承担。
 * 实体层色 = master/business/report；实体间无边是成员表级关系聚合，故最短路禁用
 * 中转节点传 `blocked-hub=null`。选中节点在画布下方展开实体详情。
 *
 * 设计偏差（留痕）：实体关系边 hover 复用基座通用「JOIN 条件」头部，内容为成员表级
 * 关系键（非真实 JOIN 条件）——基座未扩展（升级项已搁置）。
 */
import { computed, onMounted, ref } from 'vue'
import { DsForceGraph, DsAsyncSection, type ForceEdge, type ForceNode } from '@/components'
import {
  fetchOntologyEntities,
  fetchOntologyEntityRelations,
  type EntityRelation,
  type OntologyEntity,
} from '@/api/ontology'
import EntityDetail from './EntityDetail.vue'

const GROUP_COLORS: Record<string, string> = {
  master: '#5aa2ff',
  business: '#ff7a72',
  report: '#4ecb8d',
}
const LEGEND = [
  { label: '主数据', group: 'master' },
  { label: '业务数据', group: 'business' },
  { label: '统计报表', group: 'report' },
]

const entities = ref<OntologyEntity[]>([])
const relations = ref<EntityRelation[]>([])
const loading = ref(false)
const error = ref<unknown>(null)
const selectedName = ref<string | null>(null)

const degree = computed<Record<string, number>>(() => {
  const deg: Record<string, number> = {}
  for (const r of relations.value) {
    deg[r.from_entity] = (deg[r.from_entity] ?? 0) + 1
    deg[r.to_entity] = (deg[r.to_entity] ?? 0) + 1
  }
  return deg
})

const forceNodes = computed<ForceNode[]>(() =>
  entities.value.map((e) => ({
    id: e.name,
    label: e.label || e.name,
    sublabel: e.name,
    group: e.layer,
    weight: degree.value[e.name] ?? 0,
    meta: [`成员表 ${e.member_count ?? e.member_tables.length} · 关联 ${degree.value[e.name] ?? 0}`],
  })),
)

const forceEdges = computed<ForceEdge[]>(() =>
  relations.value.map((r) => ({
    source: r.from_entity,
    target: r.to_entity,
    detail: r.member_relations.map((m) => m.replace(/--/g, ' ↔ ')),
  })),
)

/** 最短路详情：逐跳给出实体间成员表级关系与场景。 */
function pathDetail(path: string[]): string {
  const lines: string[] = []
  for (let i = 0; i < path.length - 1; i++) {
    const a = path[i]
    const b = path[i + 1]
    const rel = relations.value.find(
      (r) =>
        (r.from_entity === a && r.to_entity === b) ||
        (r.from_entity === b && r.to_entity === a),
    )
    if (!rel) {
      lines.push(`${a} ↔ ${b}（无成员表级关系）`)
      continue
    }
    const scene = rel.scenarios.length ? `；场景：${rel.scenarios.slice(0, 3).join('、')}` : ''
    lines.push(`${a} ↔ ${b}：${rel.member_relations.length} 条表级关系${scene}`)
  }
  return lines.join('\n')
}

function onSelect(node: ForceNode) {
  selectedName.value = node.id
}

async function load() {
  loading.value = true
  error.value = null
  try {
    const [entRes, relRes] = await Promise.all([
      fetchOntologyEntities(),
      fetchOntologyEntityRelations(),
    ])
    entities.value = entRes.items ?? []
    relations.value = relRes.items ?? []
  } catch (e) {
    error.value = e
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="onto-graph">
    <DsAsyncSection
      :loading="loading"
      :error="error"
      :is-empty="entities.length === 0"
      skeleton="chart"
      empty-title="暂无实体图谱数据"
      empty-desc="实体与关系由本体构建管道生产，请确认本体已构建"
      @retry="load"
    >
      <DsForceGraph
        :nodes="forceNodes"
        :edges="forceEdges"
        :group-colors="GROUP_COLORS"
        :legend="LEGEND"
        :blocked-hub="null"
        :path-detail="pathDetail"
        search-placeholder="搜索实体（名称/中文名）"
        from-placeholder="起点实体"
        to-placeholder="终点实体"
        @select="onSelect"
        @reset="selectedName = null"
      />
    </DsAsyncSection>

    <EntityDetail v-if="selectedName" :key="selectedName" :name="selectedName" @close="selectedName = null" />
  </div>
</template>

<style scoped>
.onto-graph {
  display: flex;
  flex-direction: column;
  gap: var(--sp-3);
}
</style>
