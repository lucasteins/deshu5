/**
 * 侧栏 / 顶栏导航配置 —— F0.3 布局壳的唯一信息架构来源。
 *
 * 依据：
 * - 设计稿 §4.4.6「左侧导航」：10 个平铺项按语义分为 3 组
 *   使用（训练模式 / 智能问答 / SQL 查询）、
 *   资产（数据资源 / 素材提资 / 本体模型 / 问答对库 / 错题集）、分析（统计看板 / 深度分析）。
 * - 03-迁移方案 §五：各模块规划阶段（phase）用于占位页标注。
 * - 子页签（tabs）沿用旧 UI `static/index.html` 现有信息架构，不新增/不删除。
 *
 * 图标：Element Plus 图标组件名（main.ts 已全量全局注册，模板中 <component :is="icon"> 可直接用）。
 */
import type { RouteComponent } from 'vue-router'

export interface NavTab {
  /** 页签键；写入 URL `#tab=<key>`（设计稿 §4.4.6） */
  key: string
  label: string
  /** 可选计数徽标（演示数据） */
  count?: string
}

export interface NavItem {
  /** 路由 name（唯一键，同时也用于当前态判定） */
  name: string
  /** 路由 path（以 / 开头，供 router 与侧栏链接使用） */
  path: string
  /** 侧栏 / 面包屑显示名 */
  label: string
  /** Element Plus 图标名 */
  icon: string
  /** 侧栏右侧计数徽标（可选，演示数据） */
  count?: string
  /** 页面主标题（顶栏面包屑 + 占位页头部） */
  title: string
  /** 页面副标题 */
  subtitle: string
  /** 规划阶段（03-迁移方案 §五 F 项） */
  phase: string
  /** 占位页说明文案 */
  summary: string
  /** 模块内子页签；空数组表示无子页签 */
  tabs: NavTab[]
  /** 路由懒加载组件 */
  load: () => Promise<RouteComponent>
}

export interface NavGroup {
  key: string
  label: string
  items: NavItem[]
}

export const NAV_GROUPS: NavGroup[] = [
  {
    key: 'use',
    label: '使用',
    items: [
      {
        name: 'training',
        path: '/training',
        label: '训练模式',
        icon: 'EditPen',
        title: '训练模式',
        subtitle: '出题 · 合理性评价 · SQL 生成 · 结果判断',
        phase: 'F2.1',
        summary: '训练流程轨 + 出题与评估工作台，规划于 F2.1 落地。',
        tabs: [],
        load: () => import('@/views/training/TrainingView.vue'),
      },
      {
        name: 'skill-chat',
        path: '/skill-chat',
        label: '技能对话',
        icon: 'MagicStick',
        title: '技能对话',
        subtitle: '选择技能 · 示例命令 · 流式对话',
        phase: 'F2.7',
        summary: '选择 skills 能力域与大模型多轮对话，示例命令一键发送，会话持久化可回看。',
        tabs: [],
        load: () => import('@/views/skill-chat/SkillChatView.vue'),
      },
      {
        name: 'qa',
        path: '/qa',
        label: '智能问答',
        icon: 'ChatDotRound',
        title: '智能问答',
        subtitle: '自然语言提问 · 流式生成 SQL · 结果联动',
        phase: 'F2.1',
        summary: '流式叙事首秀（流程轨 / 思考窗口 / 打字机 / SQL 着色），规划于 F2.1 落地。',
        tabs: [
          { key: 'chat', label: '对话', count: '1' },
          { key: 'history', label: '历史会话', count: '84' },
          { key: 'templates', label: '我的提问模板' },
        ],
        load: () => import('@/views/qa/QaView.vue'),
      },
      {
        name: 'sql-lab',
        path: '/sql-lab',
        label: 'SQL 查询',
        icon: 'Search',
        title: 'SQL 查询',
        subtitle: '手写 SQL 取数 · 资源目录 · AI 辅助编写',
        phase: 'F2.6',
        summary: 'Monaco 编辑器手写 SQL 取数（只读），右栏数据资源目录 + AI 助手（生成/纠错/解释/优化）。',
        tabs: [],
        load: () => import('@/views/sql-lab/SqlLabView.vue'),
      },
    ],
  },
  {
    key: 'asset',
    label: '资产',
    items: [
      {
        name: 'resources',
        path: '/resources',
        label: '数据资源',
        icon: 'Coin',
        title: '数据资源',
        subtitle: '目录 / 图谱 / 码值库 / 资源管理',
        phase: 'F2.2',
        summary: '数据资源四视图（含 G6 图谱），规划于 F2.2 落地。',
        tabs: [
          { key: 'catalog', label: '目录模式' },
          { key: 'graph', label: '图谱模式' },
          { key: 'code', label: '码值库' },
          { key: 'admin', label: '资源管理' },
        ],
        load: () => import('@/views/resources/ResourcesView.vue'),
      },
      {
        name: 'provision',
        path: '/provision',
        label: '素材提资',
        icon: 'UploadFilled',
        title: '素材提资',
        subtitle: '四步流转 · 流式过程 · 溯源徽标 · 复核批流',
        phase: 'F2.4',
        summary: '提资四步（流式 + 溯源徽标 + 复核批流），规划于 F2.4 落地。',
        tabs: [],
        load: () => import('@/views/provision/ProvisionView.vue'),
      },
      {
        name: 'ontology',
        path: '/ontology',
        label: '本体模型',
        icon: 'Share',
        title: '本体模型',
        subtitle: '实体 / 图谱 / 物理表 / 关系 / 码值 / 概念 / 提案',
        phase: 'F2.3',
        summary: '本体模型七视图（含实体图谱），规划于 F2.3 落地。',
        tabs: [
          { key: 'entities', label: '实体视图' },
          { key: 'egraph', label: '实体图谱' },
          { key: 'classes', label: '物理表' },
          { key: 'relations', label: '关系' },
          { key: 'enums', label: '码值枚举' },
          { key: 'concepts', label: '业务概念' },
          { key: 'proposals', label: '变更提案' },
        ],
        load: () => import('@/views/ontology/OntologyView.vue'),
      },
      {
        name: 'qa-lib',
        path: '/qa-lib',
        label: '问答对库',
        icon: 'Collection',
        title: '问答对库',
        subtitle: '问答对管理 · 排序筛选 · 批量编辑',
        phase: 'F1.1',
        summary: '表格页全功能（排序 / 筛选 / 分页 / 编辑）+ 真实 API 联调，规划于 F1.1 试点。',
        tabs: [],
        load: () => import('@/views/qa-lib/QaLibView.vue'),
      },
      {
        name: 'errors',
        path: '/errors',
        label: '错题集',
        icon: 'Warning',
        title: '错题集',
        subtitle: '错题归因 · 生成 SQL vs 修正 SQL 对比',
        phase: 'F2.5',
        summary: '错题集（含对比视图），规划于 F2.5 落地。',
        tabs: [],
        load: () => import('@/views/errors/ErrorsView.vue'),
      },
    ],
  },
  {
    key: 'analysis',
    label: '分析',
    items: [
      {
        name: 'stats',
        path: '/stats',
        label: '统计看板',
        icon: 'DataAnalysis',
        title: '统计看板',
        subtitle: 'KPI 概览 · 分布与占比 · 工作流运行',
        phase: 'F1.2',
        summary: 'KPI 卡 + ECharts 图表（分布 / 占比）+ 真实 stats API，规划于 F1.2 试点。',
        // F1.2 起按设计稿 §4.5.3 改为**单页看板**（KPI / 折线 / 环形 / 横条 / 热力 / 工作流表），
        // 原「基础数据 / 知识库 / 工作流运行」三子页签的信息已并入同一页，不再保留页签。
        tabs: [],
        load: () => import('@/views/stats/StatsView.vue'),
      },
      {
        name: 'report',
        path: '/report',
        label: '深度分析',
        icon: 'Document',
        title: '深度分析',
        subtitle: '报告生成 · 逐题进度 · 渲染与角标溯源',
        phase: 'F2.4',
        summary: '报告（逐题进度 + 渲染 + 角标溯源），规划于 F2.4 落地。',
        tabs: [
          { key: 'gen', label: '报告生成' },
          { key: 'runs', label: '历史报告' },
          { key: 'tpl', label: '模板管理' },
        ],
        load: () => import('@/views/report/ReportView.vue'),
      },
    ],
  },
]

/** 全部模块项（扁平） */
export const NAV_ITEMS: NavItem[] = NAV_GROUPS.flatMap((group) => group.items)

/** 按路由 name 解析「分组 + 模块项」；未命中返回 null */
export function resolveNav(name: unknown): { group: NavGroup; item: NavItem } | null {
  if (typeof name !== 'string') return null
  for (const group of NAV_GROUPS) {
    const item = group.items.find((it) => it.name === name)
    if (item) return { group, item }
  }
  return null
}
