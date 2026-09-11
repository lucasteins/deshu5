<script setup lang="ts">
/**
 * 图谱基座组件（F2.2，原样复刻 static/js/resource.js 图谱模式）
 *
 * 职责：Canvas 力导向图的 Vue 封装——画布挂载 + 尺寸自适应 + 鼠标/滚轮/双击事件
 * 转调引擎 + 悬停 tooltip + 工具条（搜索定位 / 最短路 / 自动布局 / 重置）+ 图例 +
 * 结果提示 + 路径详情区（如 JOIN SQL 预览，由 pathDetail 回调提供）。
 *
 * 视觉：深色「观测台」窗口（背景点阵 + 顶部暖光 + 浅色节点标签），颜色沿用旧实现
 * 硬编码值，不随明暗主题切换（对齐「沿用原样式」拍板）。
 *
 * 复用（供 F2.3 实体图谱）：
 *   传入归一化后的 ForceNode[] / ForceEdge[] 即可；工具条 / 图例 / 详情区均可按需关闭
 *   （legend 传 []、pathDetail 不传、blockedHub 传 null 禁用禁中转）。
 */
import { computed, onBeforeUnmount, onMounted, ref, useId, watch } from 'vue'
import { ElButton } from 'element-plus'
import { createForceGraph, type ForceEdge, type ForceGraph, type ForceNode } from './forceGraph'

const props = withDefaults(
  defineProps<{
    nodes: ForceNode[]
    edges: ForceEdge[]
    /** 画布高度（px） */
    height?: number
    groupColors?: Record<string, string>
    /** 图例项；传 [] 可关闭图例。缺省 dim/dwd */
    legend?: { label: string; group: string }[]
    /** 最短路禁用的中转节点；null 不禁止 */
    blockedHub?: string | null
    /** 路径详情内容生成器（如 JOIN SQL 预览）；不传则不显示详情区 */
    pathDetail?: (path: string[]) => string | null
    searchPlaceholder?: string
    fromPlaceholder?: string
    toPlaceholder?: string
  }>(),
  {
    height: 560,
    blockedHub: 'dim_cst_mgt_org',
    searchPlaceholder: '搜索表（表名/注释）',
    fromPlaceholder: '起点表',
    toPlaceholder: '终点表',
  },
)

const emit = defineEmits<{
  /** 单击选中节点（父组件据此打开详情） */
  select: [node: ForceNode]
  /** 双击节点（聚焦放大） */
  dblclick: [node: ForceNode]
  /** 重置（父组件可同步关闭详情等） */
  reset: []
}>()

defineExpose({
  locate: (q: string) => graph?.locate(q) ?? null,
  findPath: (from: string, to: string) => graph?.findPath(from, to) ?? null,
  autoLayout: () => graph?.autoLayout(),
  reset: () => onReset(),
})

const DEFAULT_GROUP_COLORS: Record<string, string> = {
  dim: '#5aa2ff',
  dwd: '#ff7a72',
  other: '#9b9086',
}
const DEFAULT_LEGEND = [
  { label: '维表 dim', group: 'dim' },
  { label: '事实表 dwd', group: 'dwd' },
]

const mergedColors = computed<Record<string, string>>(() => ({
  ...DEFAULT_GROUP_COLORS,
  ...(props.groupColors ?? {}),
}))
const legendItems = computed(() => props.legend ?? DEFAULT_LEGEND)

const wrapRef = ref<HTMLDivElement>()
const canvasRef = ref<HTMLCanvasElement>()
const dlId = useId()

/* ---------- tooltip 状态 ---------- */
const tooltip = ref<{ node?: ForceNode; edge?: ForceEdge } | null>(null)
const tooltipX = ref(0)
const tooltipY = ref(0)

/* ---------- 工具条状态 ---------- */
const searchText = ref('')
const fromText = ref('')
const toText = ref('')
const resultText = ref('')
const detailText = ref('')

let graph: ForceGraph | null = null
let ro: ResizeObserver | null = null

function setupCanvas() {
  const canvas = canvasRef.value
  const wrap = wrapRef.value
  if (!canvas || !wrap) return
  const width = Math.max(1, wrap.clientWidth)
  // 高分屏适配（2026-09-11 皮卡丘拍板·仅新 UI）：背板按 devicePixelRatio 放大，
  // 样式尺寸维持 CSS 像素——拾取/绘制坐标逻辑不变（forceGraph 侧以 CSS 像素为逻辑坐标）
  const dpr = window.devicePixelRatio || 1
  canvas.width = Math.round(width * dpr)
  canvas.height = Math.round(props.height * dpr)
  canvas.style.width = `${width}px`
  canvas.style.height = `${props.height}px`
}

function buildGraph() {
  const canvas = canvasRef.value
  if (!canvas) return
  graph?.destroy()
  setupCanvas()
  graph = createForceGraph(canvas, {
    nodes: props.nodes,
    edges: props.edges,
    groupColors: mergedColors.value,
    blockedHub: props.blockedHub,
    callbacks: {
      onSelect: (node) => {
        if (node) emit('select', node)
      },
      onDblClick: (node) => emit('dblclick', node),
      onHover: (target) => {
        tooltip.value = target
        if (canvasRef.value) {
          canvasRef.value.style.cursor = target ? 'pointer' : 'grab'
        }
      },
    },
  })
}

watch(
  () => [props.nodes, props.edges],
  () => buildGraph(),
)

onMounted(() => {
  buildGraph()
  if (wrapRef.value && typeof ResizeObserver !== 'undefined') {
    ro = new ResizeObserver(() => setupCanvas())
    ro.observe(wrapRef.value)
  }
})

onBeforeUnmount(() => {
  ro?.disconnect()
  graph?.destroy()
})

/* ---------- 事件转调 ---------- */
function onMouseDown(ev: MouseEvent) {
  graph?.pointerDown(ev.clientX, ev.clientY)
}
function onMouseMove(ev: MouseEvent) {
  tooltipX.value = ev.clientX + 14
  tooltipY.value = ev.clientY - 10
  graph?.pointerMove(ev.clientX, ev.clientY, ev.buttons)
}
function onMouseUp() {
  graph?.pointerUp()
}
function onDblClick(ev: MouseEvent) {
  graph?.dblClick(ev.clientX, ev.clientY)
}
function onWheel(ev: WheelEvent) {
  graph?.wheel(ev.deltaY)
}
function onLeave() {
  graph?.leave()
  tooltip.value = null
}

/* ---------- 工具条动作 ---------- */
const idSet = computed(() => new Set(props.nodes.map((n) => n.id)))

function onSearch() {
  if (!graph) return
  const n = graph.locate(searchText.value)
  resultText.value = n ? `已定位: ${n.label || n.id}` : '未找到匹配的表'
}

function onFindPath() {
  const f = fromText.value.trim()
  const t = toText.value.trim()
  if (!f || !t || !idSet.value.has(f) || !idSet.value.has(t)) {
    resultText.value = '请输入有效的起点和终点表名（可选自候选列表）'
    detailText.value = ''
    return
  }
  const res = graph?.findPath(f, t)
  if (!res) {
    resultText.value = '两表之间不存在已定义的关联路径'
    detailText.value = ''
    return
  }
  resultText.value = `路径（${res.path.length - 1} 跳）: ${res.path.join(' → ')}`
  detailText.value = props.pathDetail ? (props.pathDetail(res.path) ?? '') : ''
}

function onAutoLayout() {
  graph?.autoLayout()
  resultText.value = '已重新布局'
  detailText.value = ''
}

function onReset() {
  graph?.reset()
  searchText.value = ''
  fromText.value = ''
  toText.value = ''
  resultText.value = ''
  detailText.value = ''
  tooltip.value = null
  emit('reset')
}
</script>

<template>
  <div class="force-graph">
    <div class="graph-toolbar">
      <template v-if="legendItems.length">
        <span v-for="lg in legendItems" :key="lg.group" class="legend">
          <i class="sw" :style="{ background: mergedColors[lg.group] ?? mergedColors.other }" />{{ lg.label }}
        </span>
      </template>
      <input
        v-model="searchText"
        class="graph-input"
        :placeholder="searchPlaceholder"
        :list="dlId"
        @input="onSearch"
      />
      <input v-model="fromText" class="graph-input" :placeholder="fromPlaceholder" :list="dlId" />
      <input v-model="toText" class="graph-input" :placeholder="toPlaceholder" :list="dlId" />
      <ElButton size="small" type="primary" @click="onFindPath">最短路</ElButton>
      <ElButton size="small" @click="onAutoLayout">自动布局</ElButton>
      <ElButton size="small" @click="onReset">重置</ElButton>
      <span v-if="resultText" class="graph-result">{{ resultText }}</span>
      <datalist :id="dlId">
        <option v-for="n in nodes" :key="n.id" :value="n.id">{{ n.label }}</option>
      </datalist>
    </div>

    <div ref="wrapRef" class="graph-wrap">
      <canvas
        ref="canvasRef"
        @mousedown="onMouseDown"
        @mousemove="onMouseMove"
        @mouseup="onMouseUp"
        @dblclick="onDblClick"
        @wheel.prevent="onWheel"
        @mouseleave="onLeave"
      />

      <div
        v-if="tooltip"
        class="graph-tip"
        :style="{ left: `${tooltipX}px`, top: `${tooltipY}px` }"
      >
        <template v-if="tooltip.node">
          <b>{{ tooltip.node.label || tooltip.node.id }}</b><br />
          <span class="tip-sub">{{ tooltip.node.sublabel }}</span><br />
          <template v-for="m in tooltip.node.meta" :key="m">
            {{ m }}<br />
          </template>
        </template>
        <template v-else-if="tooltip.edge">
          <b>JOIN 条件</b><br />
          <template v-for="c in tooltip.edge.detail" :key="c">
            <span class="tip-mono">{{ c }}</span><br />
          </template>
        </template>
      </div>

      <div v-if="detailText" class="graph-detail">
        <pre>{{ detailText }}</pre>
      </div>
    </div>
  </div>
</template>

<style scoped>
.force-graph {
  display: flex;
  flex-direction: column;
  gap: var(--sp-3);
}

/* 工具条（明色页面内，适配 token 的浅色控件） */
.graph-toolbar {
  display: flex;
  gap: var(--sp-2);
  align-items: center;
  flex-wrap: wrap;
}
.legend {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: var(--fs-caption);
  color: var(--text-2);
  white-space: nowrap;
}
.sw {
  display: inline-block;
  width: 10px;
  height: 10px;
  border-radius: 3px;
}
.graph-input {
  width: 150px;
  padding: 6px 11px;
  background: var(--surface-1);
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  font-family: inherit;
  font-size: var(--fs-body-sm);
  color: var(--text-1);
  transition: border-color var(--dur-fast) var(--ease-standard);
}
.graph-input:focus {
  outline: none;
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-ring);
}
.graph-input::placeholder {
  color: var(--text-4);
}
.graph-result {
  font-size: var(--fs-caption);
  color: var(--text-3);
  margin-left: auto;
}

/* 观测台深色画布（原样式，颜色沿用旧实现硬编码值） */
.graph-wrap {
  position: relative;
  background:
    radial-gradient(rgba(255, 235, 210, 0.035) 1px, transparent 1.4px) 0 0 / 26px 26px,
    radial-gradient(720px 380px at 50% -10%, rgba(255, 138, 46, 0.05), transparent 65%),
    #0c0906;
  border: 1px solid #2b231a;
  border-radius: 12px;
  overflow: hidden;
  box-shadow: inset 0 0 60px rgba(0, 0, 0, 0.45);
}
.graph-wrap canvas {
  display: block;
  cursor: grab;
}

/* 悬停 tooltip（fixed 跟随视口坐标，与旧实现 #graph-tip 一致） */
.graph-tip {
  position: fixed;
  z-index: 500;
  max-width: 320px;
  padding: 9px 13px;
  background: rgba(29, 23, 18, 0.95);
  border: 1px solid #3a2f22;
  border-radius: 10px;
  font-size: var(--fs-caption);
  line-height: 1.6;
  color: #e8ddcf;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.5);
  pointer-events: none;
}
.tip-sub,
.tip-mono {
  font-family: var(--font-mono);
  font-size: 11px;
  color: #9b9086;
}

/* 路径详情（JOIN SQL 预览，原 #graph-sql 深色 mono 块） */
.graph-detail {
  position: absolute;
  left: 12px;
  right: 12px;
  bottom: 12px;
  background: rgba(10, 8, 6, 0.93);
  border: 1px solid #2b231a;
  border-left: 2px solid rgba(255, 138, 46, 0.55);
  border-radius: 10px;
  padding: 11px 13px;
  max-height: 140px;
  overflow-y: auto;
  box-shadow: 0 14px 34px -14px rgba(0, 0, 0, 0.8);
}
.graph-detail pre {
  margin: 0;
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.6;
  color: #f0dcc4;
  white-space: pre-wrap;
}
</style>
