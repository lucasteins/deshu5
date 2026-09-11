import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import * as ElementPlusIconsVue from '@element-plus/icons-vue'

import 'element-plus/dist/index.css'
import 'element-plus/theme-chalk/dark/css-vars.css'
import '@/styles/tokens.css'
import '@/styles/element.css'
import '@/styles/index.css'

import App from '@/App.vue'
import router from '@/router'
import { useAppStore } from '@/stores/app'

const app = createApp(App)

// 全量注册 Element Plus 图标（模板中可直接 <el-icon><Search /></el-icon>）
for (const [name, component] of Object.entries(ElementPlusIconsVue)) {
  app.component(name, component)
}

app.use(createPinia())
app.use(router)
app.use(ElementPlus)

// 主题 / 演示模式：落地到 <html>（与 index.html 启动脚本配合，避免首屏闪烁）
useAppStore().initAppearance()

app.mount('#app')
