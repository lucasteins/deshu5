/**
 * 零依赖 Canvas 力导向图引擎（F2.2，原样复刻 static/js/resource.js 图谱模式）
 *
 * 与旧实现的对应关系（resource.js → 本文件）：
 *   initGraphData → initGraphData()
 *   graphPhysics   → physics()
 *   gw2s / gs2w    → worldToScreen() / screenToWorld()
 *   graphDraw      → draw()
 *   graphPickNode / graphPickEdge / distToSegment → pickNode() / pickEdge()
 *   bindGraphEvents → pointerDown/Move/Up/dblClick/wheel/leave（事件由组件绑定后转调）
 *   graphSearch / graphPath / graphAutoLayout / graphReset → locate / findPath / autoLayout / reset
 *
 * 视觉常量（图层颜色 / 边 / 标签 / 高亮）为旧实现硬编码值，**原样保留**——图谱是
 * 「观测台」深色窗口，节点与标签的浅色、暖橙高亮不随明暗主题切换（对齐皮卡丘
 * 「沿用原样式原功能」拍板）。升级项（锚点缩放 / 框选 / 键盘 / 布局切换）已搁置。
 */

export interface ForceNode {
  /** 唯一键（表名 / 实体名） */
  id: string
  /** 主标签（业务注释 / 中文名） */
  label: string
  /** 副标签（物理表名 / 英文名） */
  sublabel?: string
  /** 分组键（layer / 域），决定节点颜色 */
  group?: string
  /** 关联数（决定半径权重） */
  weight?: number
  /** 悬停 tooltip 附加行（如「字段 12 · 关联 5」） */
  meta?: string[]
}

export interface ForceEdge {
  source: string
  target: string
  /** 悬停 tooltip 详情（如 JOIN 条件列表） */
  detail?: string[]
}

export interface ForceGraphHoverTarget {
  node?: ForceNode
  edge?: ForceEdge
}

export interface ForceGraphCallbacks {
  /** 单击选中节点（null = 点空白清除选中） */
  onSelect?: (node: ForceNode | null) => void
  /** 双击节点（聚焦放大） */
  onDblClick?: (node: ForceNode) => void
  /** 悬停目标变化（节点 / 边 / 空），供组件刷新 tooltip */
  onHover?: (target: ForceGraphHoverTarget | null) => void
}

export interface ForceGraphOptions {
  nodes: ForceNode[]
  edges: ForceEdge[]
  /** 分组 → 颜色；缺省沿用旧实现 LAYER_COLORS */
  groupColors?: Record<string, string>
  /** BFS 最短路禁用的中转节点（万能枢纽）；null 表示不禁止 */
  blockedHub?: string | null
  callbacks?: ForceGraphCallbacks
}

export interface FindPathResult {
  path: string[]
  edges: ForceEdge[]
}

const DEFAULT_GROUP_COLORS: Record<string, string> = {
  dim: '#5aa2ff',
  dwd: '#ff7a72',
  other: '#9b9086',
}

const EDGE_BASE = (alpha: number) => `rgba(155, 144, 134, ${alpha})`
const HOT = '#ff7a1a'
const LABEL_COLOR = '#f2ece4'
const SUBLABEL_COLOR = '#9b9086'
const DEFAULT_BLOCKED_HUB = 'dim_cst_mgt_org'

interface PNode extends ForceNode {
  x: number
  y: number
  vx: number
  vy: number
  r: number
}

export interface ForceGraph {
  setData: (nodes: ForceNode[], edges: ForceEdge[]) => void
  /** 画布尺寸变更后调用（组件设置 canvas.width/height 后） */
  resize: () => void
  pointerDown: (clientX: number, clientY: number) => void
  pointerMove: (clientX: number, clientY: number, buttons: number) => void
  pointerUp: () => void
  dblClick: (clientX: number, clientY: number) => void
  wheel: (deltaY: number) => void
  leave: () => void
  locate: (query: string) => ForceNode | null
  findPath: (from: string, to: string) => FindPathResult | null
  autoLayout: () => void
  reset: () => void
  destroy: () => void
}

export function createForceGraph(canvas: HTMLCanvasElement, options: ForceGraphOptions): ForceGraph {
  const ctx = canvas.getContext('2d')!
  const groupColors: Record<string, string> = { ...DEFAULT_GROUP_COLORS, ...(options.groupColors ?? {}) }
  const blockedHub = options.blockedHub === undefined ? DEFAULT_BLOCKED_HUB : options.blockedHub
  const cb = options.callbacks ?? {}

  let nodes: ForceNode[] = options.nodes
  let edges: ForceEdge[] = options.edges
  let nm: Record<string, PNode> = {}
  let adj: Record<string, string[]> = {}

  let sel: string | null = null
  const pathNodes = new Set<string>()
  const pathEdges = new Set<ForceEdge>()

  let scale = 1
  let ox = 0
  let oy = 0
  let dragNode: PNode | null = null
  let panStart: [number, number] | null = null
  let hoverNode: PNode | null = null
  let hoverEdge: ForceEdge | null = null

  let rafId = 0
  let running = false

  /* ---------- 数据 / 布局 ---------- */

  function initGraphData() {
    nm = {}
    adj = {}
    nodes.forEach((n, i) => {
      const angle = (i / Math.max(1, nodes.length)) * Math.PI * 2
      const radius = 240 + (i % 5) * 30
      nm[n.id] = {
        ...n,
        x: Math.cos(angle) * radius,
        y: Math.sin(angle) * radius,
        vx: 0,
        vy: 0,
        r: 10 + Math.min(12, (n.weight ?? 0) * 1.6),
      }
    })
    edges.forEach((e) => {
      ;(adj[e.source] = adj[e.source] || []).push(e.target)
      ;(adj[e.target] = adj[e.target] || []).push(e.source)
    })
  }

  /* ---------- 物理 ---------- */

  function physics() {
    const rep = 6000
    const att = 0.02
    const damp = 0.86
    const names = Object.keys(nm)
    for (const aName of names) {
      const a = nm[aName]
      if (a === dragNode) continue
      let fx = 0
      let fy = 0
      for (const bName of names) {
        if (aName === bName) continue
        const b = nm[bName]
        const dx = a.x - b.x
        const dy = a.y - b.y
        const d = Math.max(20, Math.hypot(dx, dy))
        const f = rep / (d * d)
        fx += (dx / d) * f
        fy += (dy / d) * f
      }
      for (const nb of adj[aName] || []) {
        const b = nm[nb]
        fx -= (a.x - b.x) * att
        fy -= (a.y - b.y) * att
      }
      a.vx = a.vx * damp + fx * 0.01
      a.vy = a.vy * damp + fy * 0.01
      a.x += a.vx
      a.y += a.vy
    }
  }

  /* ---------- 坐标变换 ---------- */

  function worldToScreen(wx: number, wy: number): [number, number] {
    return [(wx + ox) * scale + canvas.width / 2, (wy + oy) * scale + canvas.height / 2]
  }
  function screenToWorld(sx: number, sy: number): [number, number] {
    return [(sx - canvas.width / 2) / scale - ox, (sy - canvas.height / 2) / scale - oy]
  }

  /* ---------- 绘制 ---------- */

  function neighborSet(name: string): Set<string> {
    const s = new Set<string>([name])
    for (const nb of adj[name] || []) s.add(nb)
    return s
  }

  function draw() {
    ctx.clearRect(0, 0, canvas.width, canvas.height)

    const hasSel = !!sel
    const neighbors = hasSel ? neighborSet(sel as string) : new Set<string>()
    const hasPath = pathNodes.size > 0

    // 边
    for (const e of edges) {
      const a = nm[e.source]
      const b = nm[e.target]
      if (!a || !b) continue
      const [x1, y1] = worldToScreen(a.x, a.y)
      const [x2, y2] = worldToScreen(b.x, b.y)
      const alpha = hasPath ? (pathEdges.has(e) ? 1 : 0.06) : hasSel ? (neighbors.has(e.source) && neighbors.has(e.target) ? 0.85 : 0.06) : 0.45
      const isHot = hoverEdge === e || (hasPath && pathEdges.has(e))
      ctx.strokeStyle = isHot ? HOT : EDGE_BASE(alpha)
      ctx.lineWidth = isHot ? 2 : 1
      ctx.beginPath()
      ctx.moveTo(x1, y1)
      ctx.lineTo(x2, y2)
      ctx.stroke()
    }

    // 节点
    for (const name of Object.keys(nm)) {
      const n = nm[name]
      const [x, y] = worldToScreen(n.x, n.y)
      const r = n.r * scale
      let alpha = 1
      if (hasPath) alpha = pathNodes.has(name) ? 1 : 0.15
      else if (hasSel) alpha = neighbors.has(name) ? 1 : 0.15

      const isSel = sel === name
      const isHover = hoverNode === n
      ctx.globalAlpha = alpha
      ctx.fillStyle = groupColors[n.group ?? ''] || groupColors.other
      ctx.beginPath()
      ctx.arc(x, y, Math.max(4, r), 0, Math.PI * 2)
      ctx.fill()
      if (isSel || isHover || (hasPath && pathNodes.has(name))) {
        ctx.strokeStyle = HOT
        ctx.lineWidth = 2.5
        ctx.stroke()
      }
      // 标签：注释主标签 + 表名副标签（原样式浅色，观测台深色底）
      ctx.globalAlpha = Math.min(1, alpha + 0.15)
      ctx.fillStyle = LABEL_COLOR
      ctx.font = '600 11px "PingFang SC", "Microsoft YaHei", sans-serif'
      ctx.textAlign = 'center'
      ctx.fillText(n.label || n.id, x, y + Math.max(4, r) + 14)
      ctx.fillStyle = SUBLABEL_COLOR
      ctx.font = '9px "JetBrains Mono", Menlo, monospace'
      ctx.fillText(n.sublabel || '', x, y + Math.max(4, r) + 26)
      ctx.globalAlpha = 1
    }
  }

  /* ---------- 拾取 ---------- */

  function pickNode(wx: number, wy: number): PNode | null {
    const names = Object.keys(nm).reverse()
    for (const name of names) {
      const n = nm[name]
      const dx = wx - n.x
      const dy = wy - n.y
      if (dx * dx + dy * dy < Math.pow(n.r * 1.6, 2)) return n
    }
    return null
  }

  function pickEdge(wx: number, wy: number): ForceEdge | null {
    for (const e of edges) {
      const a = nm[e.source]
      const b = nm[e.target]
      if (!a || !b) continue
      const d = distToSegment(wx, wy, a.x, a.y, b.x, b.y)
      if (d < 5 / scale + 2) return e
    }
    return null
  }

  function distToSegment(px: number, py: number, x1: number, y1: number, x2: number, y2: number): number {
    const dx = x2 - x1
    const dy = y2 - y1
    const len2 = dx * dx + dy * dy || 1
    let t = ((px - x1) * dx + (py - y1) * dy) / len2
    t = Math.max(0, Math.min(1, t))
    return Math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))
  }

  /* ---------- 主循环 ---------- */

  function loop() {
    for (let i = 0; i < 4; i++) physics()
    draw()
    rafId = requestAnimationFrame(loop)
  }

  function start() {
    if (running) return
    running = true
    initGraphData()
    rafId = requestAnimationFrame(loop)
  }

  /* ---------- 事件处理（由组件绑定后转调） ---------- */

  function pointerDown(clientX: number, clientY: number) {
    const rect = canvas.getBoundingClientRect()
    const [wx, wy] = screenToWorld(clientX - rect.left, clientY - rect.top)
    const n = pickNode(wx, wy)
    if (n) {
      dragNode = n
    } else {
      panStart = [clientX, clientY]
      sel = null
      pathNodes.clear()
      pathEdges.clear()
      cb.onSelect?.(null)
    }
  }

  function pointerMove(clientX: number, clientY: number, buttons: number) {
    const rect = canvas.getBoundingClientRect()
    const [wx, wy] = screenToWorld(clientX - rect.left, clientY - rect.top)
    if (dragNode) {
      dragNode.x = wx
      dragNode.y = wy
      return
    }
    if (panStart && buttons) {
      ox += (clientX - panStart[0]) / scale
      oy += (clientY - panStart[1]) / scale
      panStart = [clientX, clientY]
      return
    }
    const n = pickNode(wx, wy)
    hoverNode = n
    hoverEdge = n ? null : pickEdge(wx, wy)
    if (n) {
      cb.onHover?.({ node: n })
    } else if (hoverEdge) {
      cb.onHover?.({ edge: hoverEdge })
    } else {
      cb.onHover?.(null)
    }
  }

  function pointerUp() {
    if (dragNode) {
      sel = dragNode.id
      cb.onSelect?.(dragNode)
      dragNode = null
    }
    panStart = null
  }

  function dblClick(clientX: number, clientY: number) {
    const rect = canvas.getBoundingClientRect()
    const [wx, wy] = screenToWorld(clientX - rect.left, clientY - rect.top)
    const n = pickNode(wx, wy)
    if (n) {
      ox = -n.x
      oy = -n.y
      scale = Math.min(2.2, scale * 1.6)
      cb.onDblClick?.(n)
    }
  }

  function wheel(deltaY: number) {
    const ns = scale * (deltaY > 0 ? 0.9 : 1.1)
    scale = Math.max(0.2, Math.min(4, ns))
  }

  function leave() {
    hoverNode = null
    hoverEdge = null
    cb.onHover?.(null)
  }

  /* ---------- 工具 ---------- */

  function locate(query: string): ForceNode | null {
    const kw = query.trim()
    if (!kw) return null
    const n = nodes.find((x) => x.id === kw) || nodes.find((x) => (x.label || '').includes(kw) || x.id.includes(kw))
    if (n) {
      const g = nm[n.id]
      sel = n.id
      ox = -g.x
      oy = -g.y
      scale = Math.max(scale, 1.4)
      cb.onSelect?.(n)
      return n
    }
    return null
  }

  function findPath(from: string, to: string): FindPathResult | null {
    const f = from.trim()
    const t = to.trim()
    if (!f || !t || !nm[f] || !nm[t]) return null
    // BFS 最短路（禁止 blockedHub 中转——它是万能枢纽，经过它的路径无业务意义）
    const prev: Record<string, string | null> = { [f]: null }
    const queue: string[] = [f]
    while (queue.length) {
      const cur = queue.shift()!
      if (cur === t) break
      for (const nb of adj[cur] || []) {
        if (nb === blockedHub && f !== blockedHub && t !== blockedHub) continue
        if (!(nb in prev)) {
          prev[nb] = cur
          queue.push(nb)
        }
      }
    }
    if (!(t in prev)) return null
    const path: string[] = []
    for (let cur: string | null = t; cur !== null; cur = prev[cur]) path.unshift(cur)
    pathNodes.clear()
    pathEdges.clear()
    path.forEach((p) => pathNodes.add(p))
    const pathSet = new Set(path)
    edges.forEach((e) => {
      if (
        pathSet.has(e.source) &&
        pathSet.has(e.target) &&
        Math.abs(path.indexOf(e.source) - path.indexOf(e.target)) === 1
      ) {
        pathEdges.add(e)
      }
    })
    const mid = nm[path[Math.floor(path.length / 2)]]
    ox = -mid.x
    oy = -mid.y
    scale = Math.max(scale, 1.2)
    return { path, edges: [...pathEdges] }
  }

  function autoLayout() {
    initGraphData()
    ox = 0
    oy = 0
    scale = 1
    sel = null
    pathNodes.clear()
    pathEdges.clear()
  }

  function reset() {
    sel = null
    pathNodes.clear()
    pathEdges.clear()
    ox = 0
    oy = 0
    scale = 1
    dragNode = null
    panStart = null
    hoverNode = null
    hoverEdge = null
  }

  function setData(nextNodes: ForceNode[], nextEdges: ForceEdge[]) {
    nodes = nextNodes
    edges = nextEdges
    initGraphData()
    reset()
  }

  function resize() {
    // 画布尺寸由组件写入 canvas.width/height；此处无额外状态
  }

  function destroy() {
    running = false
    cancelAnimationFrame(rafId)
  }

  start()

  return {
    setData,
    resize,
    pointerDown,
    pointerMove,
    pointerUp,
    dblClick,
    wheel,
    leave,
    locate,
    findPath,
    autoLayout,
    reset,
    destroy,
  }
}
