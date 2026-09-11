<script setup lang="ts">
/**
 * 码值库页签（F2.2，原 static/js/resource.js 码值库页签）
 *
 * 码值域清单（搜索过滤：中文名 / 英文名 / 业务域）+ 行展开查看明细。
 * 复用 DsDataTable 客户端管道（全量取回，客户端筛选 / 排序 / 分页）。
 */
import { computed, onMounted, ref } from 'vue'
import { ElInput } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import { DsDataTable, type DsColumn } from '@/components'
import { fetchCodeDomains, type CodeDomain } from '@/api/resources'
import CodeItemsPanel from './CodeItemsPanel.vue'

const allRows = ref<CodeDomain[]>([])
const loading = ref(false)
const error = ref<unknown>(null)

async function load() {
  loading.value = true
  error.value = null
  try {
    const res = await fetchCodeDomains()
    allRows.value = res.items ?? []
  } catch (e) {
    error.value = e
  } finally {
    loading.value = false
  }
}
onMounted(load)

const kw = ref('')

const displayRows = computed<CodeDomain[]>(() => {
  const q = kw.value.trim().toLowerCase()
  if (!q) return allRows.value
  return allRows.value.filter(
    (d) =>
      (d.code_name || '').toLowerCase().includes(q) ||
      (d.cn_name || '').toLowerCase().includes(q) ||
      (d.domain || '').toLowerCase().includes(q),
  )
})

const totalItems = computed(() => displayRows.value.reduce((s, d) => s + (d.item_count || 0), 0))

const columns: DsColumn<CodeDomain>[] = [
  { key: 'cn_name', label: '码值域', minWidth: 220, sortable: true, slot: 'domain' },
  { key: 'code_name', label: '英文名', minWidth: 200, sortable: true, slot: 'code' },
  { key: 'domain', label: '业务域', width: 140, value: (d) => d.domain || '—' },
  { key: 'item_count', label: '明细数', width: 96, align: 'center', sortable: true, sortType: 'number', slot: 'count' },
  { key: 'columns', label: '落列字段', minWidth: 220, slot: 'columns' },
]
</script>

<template>
  <DsDataTable
    :columns="columns"
    :rows="displayRows"
    row-key="code_name"
    :loading="loading"
    :error="error"
    expandable
    table-id="code-catalog"
    skeleton="table"
    @retry="load"
  >
    <template #toolbar>
      <ElInput
        v-model="kw"
        class="code-search"
        placeholder="搜索码值域（中文名 / 英文名 / 业务域）"
        clearable
        :prefix-icon="Search"
      />
      <span class="code-count">共 {{ displayRows.length }} 个码值域 · {{ totalItems }} 条明细</span>
    </template>

    <template #cell-domain="{ row }">
      <span class="code-domain">{{ row.cn_name || row.code_name }}</span>
    </template>

    <template #cell-code="{ row }">
      <span class="mono">{{ row.code_name }}</span>
    </template>

    <template #cell-count="{ row }">
      <span class="chip chip-hit">{{ row.item_count }}</span>
    </template>

    <template #cell-columns="{ row }">
      <template v-if="row.columns.length">
        <span v-for="c in row.columns.slice(0, 2)" :key="c" class="chip">{{ c }}</span>
      </template>
      <span v-else class="muted">未落列</span>
    </template>

    <template #expanded="{ row }">
      <CodeItemsPanel :code-name="row.code_name" />
    </template>
  </DsDataTable>
</template>

<style scoped>
.code-search {
  width: 300px;
}
.code-count {
  font-size: var(--fs-caption);
  color: var(--text-3);
  margin-left: auto;
}
.code-domain {
  color: var(--text-1);
}
.mono {
  font-family: var(--font-mono);
  font-size: 12px;
}
.chip {
  display: inline-flex;
  align-items: center;
  height: 20px;
  padding: 0 8px;
  margin: 1px 4px 1px 0;
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
.muted {
  color: var(--text-4);
  font-size: var(--fs-caption);
}
</style>
