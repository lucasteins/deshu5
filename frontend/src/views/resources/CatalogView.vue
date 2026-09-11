<script setup lang="ts">
/**
 * 目录模式（F2.2，原 static/js/resource.js renderCatalog）
 *
 * 搜索（表名 / 注释）+ 层级筛选（全部 / dim / dwd）+ 计数 + 卡片网格；
 * 点击卡片 → 打开表详情抽屉。数据由 ResourcesView 统一加载传入（受控组件）。
 */
import { computed, ref } from 'vue'
import { ElInput, ElOption, ElSelect } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import type { SchemaNode } from '@/api/resources'
import DsAsyncSection from '@/components/status/DsAsyncSection.vue'

const props = defineProps<{
  nodes: SchemaNode[]
  loading: boolean
  error: unknown
}>()
const emit = defineEmits<{ open: [table: string]; retry: [] }>()

const kw = ref('')
const layer = ref('')

const filtered = computed(() => {
  const q = kw.value.trim().toLowerCase()
  let out = props.nodes
  if (layer.value) out = out.filter((n) => n.layer === layer.value)
  if (q) {
    out = out.filter(
      (n) => n.name.toLowerCase().includes(q) || (n.comment || '').toLowerCase().includes(q),
    )
  }
  return out
})

const totalCols = computed(() => filtered.value.reduce((s, n) => s + (n.column_count || 0), 0))

function layerText(n: SchemaNode): string {
  return n.layer === 'dim' ? 'dim' : 'dwd'
}
function pkText(n: SchemaNode): string {
  return n.pk && n.pk.length ? `PK: ${n.pk.join(', ')}` : '—'
}
</script>

<template>
  <div class="catalog">
    <div class="catalog-toolbar">
      <ElInput
        v-model="kw"
        class="catalog-search"
        placeholder="搜索表名或业务注释"
        clearable
        :prefix-icon="Search"
      />
      <ElSelect v-model="layer" class="catalog-layer" placeholder="全部层级">
        <ElOption label="全部层级" value="" />
        <ElOption label="维表 dim" value="dim" />
        <ElOption label="事实表 dwd" value="dwd" />
      </ElSelect>
      <span class="catalog-count">共 {{ filtered.length }} 张表 · {{ totalCols }} 个字段</span>
    </div>

    <DsAsyncSection
      :loading="loading"
      :error="error"
      :is-empty="filtered.length === 0"
      skeleton="cards"
      empty-title="没有匹配的数据表"
      empty-desc="尝试调整搜索关键词或层级筛选"
      @retry="emit('retry')"
    >
      <div class="catalog-grid">
        <button
          v-for="n in filtered"
          :key="n.name"
          type="button"
          class="catalog-card"
          @click="emit('open', n.name)"
        >
          <div class="catalog-card-title">{{ n.comment || n.name }}</div>
          <div class="catalog-card-name">{{ n.name }}</div>
          <div class="chip-list">
            <span class="chip" :class="{ 'chip-hit': n.layer === 'dim' }">{{ layerText(n) }}</span>
            <span class="chip">{{ n.column_count }} 字段</span>
            <span class="chip">{{ n.rel_count }} 关联</span>
          </div>
          <div class="catalog-card-pk">{{ pkText(n) }}</div>
        </button>
      </div>
    </DsAsyncSection>
  </div>
</template>

<style scoped>
.catalog {
  display: flex;
  flex-direction: column;
  gap: var(--sp-3);
}
.catalog-toolbar {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  flex-wrap: wrap;
}
.catalog-search {
  width: 264px;
}
.catalog-layer {
  width: 150px;
}
.catalog-count {
  font-size: var(--fs-caption);
  color: var(--text-3);
}

.catalog-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(262px, 1fr));
  gap: var(--sp-3);
}
.catalog-card {
  position: relative;
  text-align: left;
  background: var(--surface-1);
  border: 1px solid var(--line-subtle);
  border-radius: var(--r-md);
  padding: 15px 17px;
  cursor: pointer;
  transition:
    border-color var(--dur-fast) var(--ease-standard),
    box-shadow var(--dur-fast) var(--ease-standard),
    transform var(--dur-fast) var(--ease-standard);
}
.catalog-card:hover {
  border-color: var(--accent-line);
  box-shadow: var(--e2);
  transform: translateY(-2px);
}
.catalog-card-title {
  font-size: var(--fs-body);
  font-weight: 600;
  color: var(--text-1);
}
.catalog-card-name {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text-3);
  margin-top: 2px;
}
.catalog-card-pk {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text-2);
  margin-top: 6px;
}

.chip-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin: 8px 0 6px;
}
.chip {
  display: inline-flex;
  align-items: center;
  height: 20px;
  padding: 0 8px;
  border-radius: var(--r-xs);
  background: var(--sunken);
  color: var(--text-2);
  font-size: var(--fs-micro);
}
.chip-hit {
  background: var(--accent-soft);
  color: var(--accent);
  font-weight: 600;
}
</style>
