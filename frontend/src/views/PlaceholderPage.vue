<script setup lang="ts">
/**
 * 模块占位页 —— F0.3 布局壳
 *
 * 9 个模块尚未迁移（规划见 03-迁移方案 §五），此处先给出统一占位：
 * 模块标题 / 副标题 / 规划阶段（F 项）/ 子页签预览。
 * 各模块真实内容在 F1 / F2 阶段替换本占位。
 */
import { computed } from 'vue'
import { resolveNav } from '@/layouts/navigation'

const props = defineProps<{ name: string }>()
const nav = computed(() => resolveNav(props.name))
</script>

<template>
  <div v-if="nav" class="ph">
    <header class="ph-head">
      <div class="ph-heading">
        <h1 class="ph-title">{{ nav.item.title }}</h1>
        <p class="ph-sub">{{ nav.item.subtitle }}</p>
      </div>
      <el-tag class="ph-phase" effect="plain" type="info" size="small">
        {{ nav.item.phase }} 规划中
      </el-tag>
    </header>

    <section class="ph-card">
      <div class="ph-ico" aria-hidden="true">
        <el-icon><component :is="nav.item.icon" /></el-icon>
      </div>
      <p class="ph-summary">{{ nav.item.summary }}</p>

      <div v-if="nav.item.tabs.length" class="ph-tabs">
        <span class="ph-tabs-label">本模块子页签</span>
        <div class="ph-tabs-list">
          <span v-for="tab in nav.item.tabs" :key="tab.key" class="ph-tab">{{ tab.label }}</span>
        </div>
      </div>

      <p class="ph-note">布局壳已就绪，模块内容按迁移计划分批落地。</p>
    </section>
  </div>
</template>

<style scoped>
.ph {
  padding: var(--sp-6) var(--sp-5);
}
.ph-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--sp-4);
  margin-bottom: var(--sp-5);
}
.ph-title {
  margin: 0;
  font-size: var(--fs-display-2);
  font-weight: 600;
  letter-spacing: -0.015em;
  color: var(--text-strong);
}
.ph-sub {
  margin: 6px 0 0;
  font-size: var(--fs-body-sm);
  color: var(--text-3);
}
.ph-phase {
  flex: none;
  margin-top: 6px;
}

.ph-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--sp-4);
  padding: var(--sp-10) var(--sp-6);
  border: 1px solid var(--line);
  border-radius: var(--r-md);
  background: var(--surface-1);
  text-align: center;
}
.ph-ico {
  width: 48px;
  height: 48px;
  display: grid;
  place-items: center;
  border-radius: var(--r-full);
  background: var(--accent-soft);
  color: var(--accent);
  font-size: 24px;
}
.ph-summary {
  max-width: 560px;
  margin: 0;
  font-size: var(--fs-body);
  line-height: var(--lh-body);
  color: var(--text-2);
}
.ph-tabs {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--sp-2);
}
.ph-tabs-label {
  font-size: var(--fs-micro);
  letter-spacing: 0.06em;
  color: var(--text-4);
}
.ph-tabs-list {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: var(--sp-2);
}
.ph-tab {
  padding: 2px 10px;
  border: 1px solid var(--line-subtle);
  border-radius: var(--r-full);
  background: var(--sunken);
  color: var(--text-2);
  font-size: var(--fs-caption);
}
.ph-note {
  margin: 0;
  font-size: var(--fs-caption);
  color: var(--text-4);
}
</style>
