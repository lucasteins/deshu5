import { createRouter, createWebHistory } from 'vue-router'
import type { RouteRecordRaw } from 'vue-router'

// F0.1 仅落地初始页；F0.3 布局壳接入后按 9 模块补全空路由
const routes: RouteRecordRaw[] = [
  {
    path: '/',
    name: 'home',
    component: () => import('@/views/HomeView.vue'),
    meta: { title: '概览' },
  },
]

const router = createRouter({
  // base 取自 vite base（/app/），保证构建后经 Flask /app 托管时路由正常
  history: createWebHistory(import.meta.env.BASE_URL),
  routes,
})

export default router
