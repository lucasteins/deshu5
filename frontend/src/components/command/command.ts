/**
 * 命令面板数据源（F2.6 · 设计稿 §4.4.6「命令面板」）
 *
 * 三类结果：
 *   1. 跳转页面（page）   —— 9 模块 + 各子页签（由 navigation.ts 派生，与侧栏/路由同源）
 *   2. 执行操作（action） —— 切换明暗主题 / 切换演示模式 / 打开设置（真实动作）
 *   3. 搜索对象（object） —— 表（schema-graph）/ 问答对（qa-search）/ 错题（error-search）
 *                           实时、去重、带类型徽标；只读复用已提交 API，不写后端。
 *
 * 说明：实体（本体模型）的对象搜索未纳入——`api/ontology.ts` 属 W2 会话①（F2.3）并行域，
 * 收工前其文件域尚不稳定，按「只读复用已提交 + 不阻塞」原则暂缺，留 F3.1 前由守门人裁定。
 */
import router from '@/router'
import { NAV_GROUPS } from '@/layouts/navigation'
import { useAppStore } from '@/stores/app'
import { openSettings } from '@/components/settings'
import { fetchSchemaGraph } from '@/api/resources'
import { searchQaPairs } from '@/api/qa'
import { searchErrors } from '@/api/errors'

export type CommandKind = 'page' | 'action' | 'object'

export interface CommandItem {
  /** 列表内唯一 id（用于键盘选中态） */
  id: string
  kind: CommandKind
  /** 主文案 */
  label: string
  /** 次要说明（分组 / 路径 / 类型说明） */
  hint?: string
  /** Element Plus 图标名（main.ts 已全量全局注册） */
  icon?: string
  /** 类型徽标（搜索对象专用：表 / 问答对 / 错题） */
  badge?: string
  /** 额外检索词（标题 / 副标题 / 分组名等） */
  keywords?: string[]
  run: () => void
}

/** 跳转页面：9 模块 + 每个模块的子页签（带 URL #tab 深链） */
export function buildPageCommands(): CommandItem[] {
  const out: CommandItem[] = []
  for (const group of NAV_GROUPS) {
    for (const item of group.items) {
      out.push({
        id: `page:${item.name}`,
        kind: 'page',
        label: item.label,
        hint: group.label,
        icon: item.icon,
        keywords: [item.title, item.subtitle, group.label],
        run: () => void router.push({ name: item.name }),
      })
      for (const tab of item.tabs) {
        out.push({
          id: `page:${item.name}:${tab.key}`,
          kind: 'page',
          label: `${item.label} · ${tab.label}`,
          hint: `${group.label} · ${tab.label}`,
          icon: item.icon,
          keywords: [item.title, item.subtitle, group.label, tab.label],
          run: () =>
            void router.push({ name: item.name, hash: `#tab=${encodeURIComponent(tab.key)}` }),
        })
      }
    }
  }
  return out
}

/** 执行操作：真实动作（主题 / 演示模式 / 设置），标签随当前状态联动 */
export function buildActionCommands(app: ReturnType<typeof useAppStore>): CommandItem[] {
  const dark = app.theme === 'dark'
  const demo = app.displayMode === 'demo'
  return [
    {
      id: 'action:toggle-theme',
      kind: 'action',
      label: dark ? '切换到明色主题' : '切换到暗色主题',
      hint: '外观',
      icon: dark ? 'Sunny' : 'Moon',
      keywords: ['主题', '明色', '暗色', '观测台', 'theme', 'dark', 'light'],
      run: () => app.toggleTheme(),
    },
    {
      id: 'action:toggle-demo',
      kind: 'action',
      label: demo ? '退出演示模式' : '进入演示模式',
      hint: '演示',
      icon: 'Monitor',
      keywords: ['演示', '投屏', '字号', 'demo'],
      run: () => app.toggleDemo(),
    },
    {
      id: 'action:settings',
      kind: 'action',
      label: '打开设置',
      hint: '数据库 · LLM · 工作流',
      icon: 'Setting',
      keywords: ['设置', '数据库', 'LLM', '配置', '工作流'],
      run: () => openSettings(),
    },
  ]
}

const OBJECT_LIMIT = 8
const MAX_OBJECTS = 20

/** 搜索对象：并行查询 表 / 问答对 / 错题；任一失败静默降级（不阻塞其它结果） */
export async function searchObjectCommands(query: string): Promise<CommandItem[]> {
  const q = query.trim()
  if (!q) return []

  const [schemaRes, qaRes, errRes] = await Promise.allSettled([
    fetchSchemaGraph(),
    searchQaPairs(q, OBJECT_LIMIT),
    searchErrors(q, OBJECT_LIMIT),
  ])

  const out: CommandItem[] = []
  const ql = q.toLowerCase()

  if (schemaRes.status === 'fulfilled') {
    for (const node of schemaRes.value.nodes ?? []) {
      const hay = `${node.name} ${node.comment ?? ''}`.toLowerCase()
      if (!hay.includes(ql)) continue
      out.push({
        id: `object:table:${node.name}`,
        kind: 'object',
        label: node.name,
        hint: node.comment || node.layer,
        icon: 'Coin',
        badge: '表',
        keywords: [node.comment ?? '', node.layer],
        run: () => void router.push({ name: 'resources', hash: '#tab=catalog' }),
      })
    }
  }

  if (qaRes.status === 'fulfilled') {
    for (const it of qaRes.value.items ?? []) {
      out.push({
        id: `object:qa:${it.id}`,
        kind: 'object',
        label: it.question,
        hint: '问答对',
        icon: 'Collection',
        badge: '问答对',
        keywords: ['问答', 'question'],
        run: () => void router.push({ name: 'qa-lib' }),
      })
    }
  }

  if (errRes.status === 'fulfilled') {
    for (const it of errRes.value.items ?? []) {
      out.push({
        id: `object:error:${it.id}`,
        kind: 'object',
        label: it.business_question,
        hint: it.error_type || '错题',
        icon: 'Warning',
        badge: '错题',
        keywords: ['错题', 'error'],
        run: () => void router.push({ name: 'errors' }),
      })
    }
  }

  return out.slice(0, MAX_OBJECTS)
}
