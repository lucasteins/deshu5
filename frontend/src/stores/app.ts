import { defineStore } from 'pinia'
import { ref } from 'vue'

/** 全局少量状态：主题模式（F0.2 完善 token / 暗色副模 / 演示模式联动） */
export const useAppStore = defineStore('app', () => {
  const theme = ref<'light' | 'dark'>('light')

  function setTheme(value: 'light' | 'dark') {
    theme.value = value
  }

  return { theme, setTheme }
})
