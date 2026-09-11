<script setup lang="ts">
/**
 * 码值域明细面板（F2.2，原 static/js/resource.js toggleCodeItems）
 *
 * 展开单个码值域时按需加载明细，渲染为 chips（名称 + 编码）。
 */
import { onMounted, ref } from 'vue'
import { fetchCodeItems, type CodeItem } from '@/api/resources'
import DsAsyncSection from '@/components/status/DsAsyncSection.vue'

const props = defineProps<{ codeName: string }>()

const items = ref<CodeItem[]>([])
const loading = ref(false)
const error = ref<unknown>(null)

async function load() {
  loading.value = true
  error.value = null
  try {
    const res = await fetchCodeItems(props.codeName)
    items.value = res.items ?? []
  } catch (e) {
    error.value = e
  } finally {
    loading.value = false
  }
}
onMounted(() => {
  // codeName 为空时（如表格展开列预渲染）不发请求，避免 400
  if (props.codeName) void load()
})
</script>

<template>
  <DsAsyncSection
    :loading="loading"
    :error="error"
    :is-empty="items.length === 0"
    skeleton="text"
    empty-title="该域无码值明细"
    @retry="load"
  >
    <div class="chip-list">
      <span v-for="it in items" :key="it.code" class="chip" :title="`编码 ${it.code}`">
        {{ it.name || it.code }}<span class="code">{{ it.code }}</span>
      </span>
    </div>
  </DsAsyncSection>
</template>

<style scoped>
.chip-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  padding: var(--sp-2) var(--sp-4);
}
.chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 22px;
  padding: 0 10px;
  border-radius: var(--r-xs);
  background: var(--sunken);
  color: var(--text-1);
  font-size: var(--fs-caption);
}
.code {
  color: var(--text-3);
  font-family: var(--font-mono);
  font-size: 11px;
}
</style>
