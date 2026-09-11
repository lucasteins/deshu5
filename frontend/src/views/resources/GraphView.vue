<script setup lang="ts">
/**
 * 图谱模式（F2.2，原 static/js/resource.js 图谱模式）
 *
 * 把 schema 节点 / 边映射为图谱基座的归一化视图模型，并提供 JOIN SQL 预览生成器。
 * 交互（拖拽 / 缩放 / 搜索 / 双击聚焦 / 最短路 / 自动布局 / 重置）全部由 DsForceGraph 承担。
 */
import { computed } from 'vue'
import { DsForceGraph, DsAsyncSection, type ForceEdge, type ForceNode } from '@/components'
import type { SchemaEdge, SchemaNode } from '@/api/resources'

const props = defineProps<{
  nodes: SchemaNode[]
  edges: SchemaEdge[]
  loading?: boolean
  error?: unknown
}>()
const emit = defineEmits<{ open: [table: string]; close: []; retry: [] }>()

const forceNodes = computed<ForceNode[]>(() =>
  props.nodes.map((n) => ({
    id: n.name,
    label: n.comment || n.name,
    sublabel: n.name,
    group: n.layer,
    weight: n.rel_count,
    meta: [`字段 ${n.column_count} · 关联 ${n.rel_count}`],
  })),
)

const forceEdges = computed<ForceEdge[]>(() =>
  props.edges.map((e) => ({
    source: e.from,
    target: e.to,
    detail: e.join_conditions,
  })),
)

/** 生成 JOIN SQL 预览（原 graphPath 里的 SQL 拼装逻辑） */
function pathDetail(path: string[]): string {
  const edges = props.edges
  let sql = ''
  for (let i = 0; i < path.length - 1; i++) {
    const a = path[i]
    const b = path[i + 1]
    const edge = edges.find(
      (e) => (e.from === a && e.to === b) || (e.from === b && e.to === a),
    )
    const cond = edge ? edge.join_conditions.join(' AND ') : '?'
    sql += (i === 0 ? `FROM ${a}\n` : '') + `JOIN ${b} ON ${cond}\n`
  }
  return sql
}

function onSelect(node: ForceNode) {
  emit('open', node.id)
}
</script>

<template>
  <DsAsyncSection
    :loading="loading ?? false"
    :error="error"
    :is-empty="nodes.length === 0"
    skeleton="chart"
    empty-title="暂无图谱数据"
    empty-desc="表关系图由 DDL 预加载管道生产，请确认后端已启动"
    @retry="emit('retry')"
  >
    <DsForceGraph
      :nodes="forceNodes"
      :edges="forceEdges"
      :path-detail="pathDetail"
      @select="onSelect"
      @reset="emit('close')"
    />
  </DsAsyncSection>
</template>
