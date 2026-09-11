<script setup lang="ts">
/**
 * 本体差异明细（F2.3，复用于顶部漂移横幅与变更提案详情）
 *
 * 结构覆盖类 / 属性 / 关系 / 实体 / 码值域 / 业务概念；数量上限 50 项防长列表失控。
 * 数据源 = /api/ontology/summary 的 drift.diff、/drift 的 diff、/proposals/<pid> 的 diff。
 */
import { computed } from 'vue'
import type { OntologyDiff } from '@/api/ontology'

const props = defineProps<{ diff?: OntologyDiff | null }>()

interface Section {
  title: string
  items: string[]
}

const sections = computed<Section[]>(() => {
  const d = props.diff ?? {}
  const cl = d.classes ?? {}
  const pr = d.properties ?? {}
  const rl = d.relations ?? {}
  const en = d.enumerations ?? {}
  const co = d.concepts ?? {}
  const ent = d.entities ?? {}

  const out: Section[] = [
    { title: '新增表', items: cl.added ?? [] },
    { title: '删除表', items: cl.removed ?? [] },
    { title: '表注释变更', items: cl.changed ?? [] },
    { title: '新增列', items: pr.added ?? [] },
    { title: '删除列', items: pr.removed ?? [] },
    {
      title: '列变更',
      items: (pr.changed ?? []).map((x) => `${x.name}：${JSON.stringify(x.old)} → ${JSON.stringify(x.new)}`),
    },
    { title: '新增关系', items: rl.added ?? [] },
    { title: '删除关系', items: rl.removed ?? [] },
    {
      title: '关系变更',
      items: (rl.changed ?? []).map((x) => x.key),
    },
    { title: '新增实体', items: ent.added ?? [] },
    { title: '删除实体', items: ent.removed ?? [] },
    {
      title: '实体变更',
      items: (ent.changed ?? []).map((x) => x.name),
    },
    { title: '新增码值域', items: en.added ?? [] },
    { title: '删除码值域', items: en.removed ?? [] },
    { title: '新增概念', items: co.added ?? [] },
    { title: '删除概念', items: co.removed ?? [] },
    { title: '概念映射变更', items: co.changed ?? [] },
  ]
  return out.filter((s) => s.items.length > 0)
})

function slice(items: string[]): { shown: string[]; rest: number } {
  return { shown: items.slice(0, 50), rest: Math.max(0, items.length - 50) }
}
</script>

<template>
  <div v-if="sections.length" class="onto-diff">
    <section v-for="s in sections" :key="s.title" class="onto-diff__section">
      <strong class="onto-diff__title">{{ s.title }}（{{ s.items.length }}）</strong>
      <ul class="onto-diff__list">
        <li v-for="(x, i) in slice(s.items).shown" :key="i">{{ x }}</li>
        <li v-if="slice(s.items).rest" class="onto-diff__more">… 共 {{ s.items.length }} 项</li>
      </ul>
    </section>
  </div>
  <div v-else class="onto-diff__empty">无结构差异明细</div>
</template>

<style scoped>
.onto-diff {
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
  font-size: var(--fs-caption);
}
.onto-diff__section {
  margin: 0;
}
.onto-diff__title {
  color: var(--text-1);
}
.onto-diff__list {
  margin: var(--sp-1) 0 0 18px;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
  color: var(--text-2);
}
.onto-diff__more {
  color: var(--text-3);
}
.onto-diff__empty {
  color: var(--text-3);
}
</style>
