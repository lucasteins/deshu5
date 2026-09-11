import { createRouter, createWebHistory } from 'vue-router'
import type { RouteRecordRaw } from 'vue-router'
import AppShell from '@/layouts/AppShell.vue'
import { NAV_GROUPS } from '@/layouts/navigation'

/**
 * 路由元信息（F0.3）：分组 / 标题 / 副标题。
 * 与 layouts/navigation.ts 同源，供顶栏面包屑与占位页使用。
 */
declare module 'vue-router' {
  interface RouteMeta {
    /** 侧栏分组标签（面包屑一级） */
    group: string
    /** 模块标题 */
    title: string
    /** 页面副标题 */
    subtitle: string
  }
}

/**
 * 9 模块路由 —— 由导航配置生成，避免与侧栏信息架构出现两份定义。
 * 全部挂在 AppShell 布局路由下，共享侧栏 / 顶栏 / 页签容器。
 */
const moduleRoutes: RouteRecordRaw[] = NAV_GROUPS.flatMap((group) =>
  group.items.map((item) => ({
    path: item.path.replace(/^\//, ''),
    name: item.name,
    component: item.load,
    meta: { group: group.label, title: item.title, subtitle: item.subtitle },
  })),
)

const routes: RouteRecordRaw[] = [
  {
    path: '/',
    component: AppShell,
    children: [
      // 根路径重定向到首个模块（训练模式）
      { path: '', redirect: { name: 'training' } },
      ...moduleRoutes,
      // 未匹配路由兜底回首个模块（配合 Flask /app SPA fallback）
      { path: ':pathMatch(.*)*', redirect: { name: 'training' } },
    ],
  },
]

const router = createRouter({
  // base 取自 vite base（F3.2 切换后为 /），保证构建后经 Flask 根路径托管时路由正常
  history: createWebHistory(import.meta.env.BASE_URL),
  routes,
})

export default router
