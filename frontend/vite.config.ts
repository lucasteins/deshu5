import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

// deshu5 前端（Vue 3 + Vite + TS）
// - build base = /app/  → 构建产物由 Flask 以 /app 并行托管（03-迁移方案 §3.3）
// - dev 5174（避开平台 5173），/api 代理到本地后端 5050（§3.2）
export default defineConfig({
  base: '/app/',
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5174,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:5050',
        changeOrigin: true,
      },
    },
  },
})
