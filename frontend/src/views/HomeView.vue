<script setup lang="ts">
const stack = [
  'Vue 3.5 + Vite 6',
  'TypeScript strict',
  'vue-router 4 / pinia',
  'Element Plus 2.10',
  'ECharts 5.6',
]
</script>

<template>
  <div class="scaffold">
    <el-card shadow="never">
      <template #header>
        <div class="scaffold__header">
          <span class="scaffold__title">智能问数训练系统 · 前端工程</span>
          <el-tag type="success" effect="plain">F0.1 脚手架就绪</el-tag>
        </div>
      </template>

      <p class="scaffold__desc">
        新界面（Vue 3 + Vite）以 <code>/app</code> 与旧界面 <code>/</code> 并行运行。
        开发期入口为 Vite <code>5174</code>，接口经 <code>/api</code> 代理至后端 <code>5050</code>。
      </p>

      <el-space wrap>
        <el-tag v-for="item in stack" :key="item" type="info" effect="plain">
          {{ item }}
        </el-tag>
      </el-space>
    </el-card>
  </div>
</template>

<style scoped>
.scaffold {
  padding: 24px;
}
.scaffold__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.scaffold__title {
  font-weight: 600;
}
.scaffold__desc {
  margin: 0 0 16px;
  color: var(--text-2);
  line-height: 1.7;
}
code {
  padding: 1px 5px;
  border-radius: 4px;
  background: var(--el-fill-color-light, #f5f7fa);
}
</style>
