<script setup lang="ts">
// 应用根组件：布局壳（AppShell）作为顶层布局路由承载 侧栏 + 顶栏 + 页签容器，
// 各模块页面经其内部 <router-view> 渲染。详见 src/router/index.ts 与 src/layouts/。
</script>

<template>
  <router-view />
</template>
