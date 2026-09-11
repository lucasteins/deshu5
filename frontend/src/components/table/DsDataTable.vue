<script setup lang="ts" generic="Row extends object">
/**
 * 统一数据表格（设计稿 §4.4.2，F1.1 试点首落）
 *
 * 结构：工具栏（`#toolbar` 插槽 + 密度切换）→ 筛选条 chips → 表格（列头漏斗筛选 /
 *       三态排序 / 行展开 `#expanded`）→ 分页器。
 * 数据：v1 为**客户端模式**——传入全量 rows，组件内完成「筛选 → 排序 → 分页」
 *       （管道见 useClientTable）；服务端分页表格 F2 按需扩展。
 * 状态：加载 / 空 / 错 走 DsAsyncSection（§4.4.3 阶梯）；错误重试 emit('retry')。
 * 单元格：#cell-<slot> 自定义；缺省渲染取值文本（cellText）。
 * 暂未实现（F2 按需补，已记录交接日志）：列控制抽屉 / 行选择与批量 / 键盘导航 / URL 同步。
 */
import { computed, ref, watch } from 'vue'
import { ElOption, ElPagination, ElSelect, ElTable, ElTableColumn } from 'element-plus'
import DsAsyncSection from '../status/DsAsyncSection.vue'
import DsColumnFilter from './DsColumnFilter.vue'
import DsFilterChips from './DsFilterChips.vue'
import { cellText, useClientTable } from './useClientTable'
import type { DsFilterChip, DsSort } from './types'

const props = withDefaults(
  defineProps<{
    columns: import('./types').DsColumn<Row>[]
    rows: Row[]
    rowKey?: string
    loading?: boolean
    /** 任意错误对象（Error / ApiError / string / null） */
    error?: unknown
    /** 显示行展开列（内容由 #expanded 插槽提供） */
    expandable?: boolean
    defaultSort?: DsSort | null
    defaultPageSize?: number
    /** 表格标识：密度记忆（localStorage）按「表格 ID」区分 */
    tableId?: string
    /** 显示密度切换（紧凑 / 舒适 / 宽松） */
    densityControl?: boolean
    skeleton?: 'text' | 'table' | 'cards' | 'chart' | 'custom'
  }>(),
  {
    rowKey: 'id',
    loading: false,
    expandable: false,
    defaultSort: null,
    defaultPageSize: 20,
    tableId: 'default',
    densityControl: true,
    skeleton: 'table',
  },
)

const emit = defineEmits<{
  retry: []
  'empty-action': []
  'empty-clear': []
  'sort-change': [sort: DsSort | null]
}>()

const {
  sort,
  enumFilters,
  page,
  pageSize,
  pageRows,
  total,
  filterChips,
  applySort,
  setEnumFilter,
  clearColumn,
  clearFilters,
  setPage,
  setPageSize,
} = useClientTable<Row>({
  rows: () => props.rows,
  columns: () => props.columns,
  defaultSort: props.defaultSort,
  defaultPageSize: props.defaultPageSize,
})

const SORT_ORDERS: ('ascending' | 'descending' | null)[] = ['ascending', 'descending', null]

function onSortChange(e: { prop?: string | null; order?: string | null }) {
  if (!e.prop) return
  applySort(e.prop, (e.order as 'ascending' | 'descending' | null) ?? null)
  emit('sort-change', sort.value)
}

const isEmpty = computed(() => total.value === 0)
const emptyType = computed(() => (props.rows.length === 0 ? 'no-data' : 'no-result'))

const rangeStart = computed(() => (total.value === 0 ? 0 : (page.value - 1) * pageSize.value + 1))
const rangeEnd = computed(() => Math.min(page.value * pageSize.value, total.value))

function onEmptyClear() {
  clearFilters()
  emit('empty-clear')
}

/* 密度：紧凑 / 舒适 / 宽松，按表格 ID 记忆（设计稿 §4.4.2「行高密度可切换并记忆」） */
type Density = 'compact' | 'default' | 'loose'
const DENSITY_OPTIONS: { value: Density; label: string }[] = [
  { value: 'compact', label: '紧凑' },
  { value: 'default', label: '舒适' },
  { value: 'loose', label: '宽松' },
]
const densityKey = computed(() => `deshu5.table.${props.tableId}.density`)
const density = ref<Density>('default')
const stored = typeof localStorage !== 'undefined' ? localStorage.getItem(densityKey.value) : null
if (stored === 'compact' || stored === 'default' || stored === 'loose') {
  density.value = stored
}
watch(density, (v) => localStorage.setItem(densityKey.value, v))
</script>

<template>
  <div class="ds-table-card" :data-density="density">
    <div class="ds-table-toolbar">
      <div class="ds-table-toolbar__main">
        <slot name="toolbar" :filtered="total" :all="rows.length" />
      </div>
      <div class="ds-table-toolbar__side">
        <ElSelect
          v-if="densityControl"
          :model-value="density"
          size="small"
          class="ds-density-select"
          @update:model-value="(v: unknown) => (density = v as Density)"
        >
          <ElOption v-for="d in DENSITY_OPTIONS" :key="d.value" :value="d.value" :label="d.label" />
        </ElSelect>
      </div>
    </div>

    <DsFilterChips :chips="filterChips" @remove="clearColumn" @clear="clearFilters" />

    <DsAsyncSection
      :loading="loading"
      :error="error"
      :is-empty="isEmpty"
      :skeleton="skeleton"
      :empty-type="emptyType"
      :empty-filters="filterChips.map((c: DsFilterChip) => c.label)"
      @retry="emit('retry')"
      @empty-action="emit('empty-action')"
      @empty-clear="onEmptyClear"
    >
      <template v-if="$slots.empty" #empty>
        <slot name="empty" :clear="clearFilters" />
      </template>

      <ElTable :data="pageRows" :row-key="rowKey" class="ds-table" @sort-change="onSortChange">
        <ElTableColumn v-if="expandable" type="expand" width="44">
          <template #default="{ row }">
            <slot name="expanded" :row="(row as Row)" />
          </template>
        </ElTableColumn>

        <ElTableColumn
          v-for="col in columns"
          :key="col.key"
          :prop="col.key"
          :label="col.label"
          :width="col.width"
          :min-width="col.minWidth"
          :align="col.align ?? 'left'"
          :fixed="col.fixed"
          :sortable="col.sortable ? 'custom' : false"
          :sort-orders="SORT_ORDERS"
        >
          <template v-if="col.filter" #header>
            <DsColumnFilter
              :label="col.label"
              :options="col.filter.type === 'enum' ? col.filter.options : []"
              :model-value="enumFilters[col.key] ?? []"
              @update:model-value="(v: string[]) => setEnumFilter(col.key, v)"
              @clear="clearColumn(col.key)"
            />
          </template>
          <template #default="{ row }">
            <slot :name="`cell-${col.slot ?? col.key}`" :row="(row as Row)" :column="col">
              <span class="ds-cell-text">{{ cellText(row as Row, col) }}</span>
            </slot>
          </template>
        </ElTableColumn>
      </ElTable>

      <div class="ds-table-footer">
        <span class="ds-table-footer__range">第 {{ rangeStart }}–{{ rangeEnd }} 条 / 共 {{ total }} 条</span>
        <ElPagination
          :current-page="page"
          :page-size="pageSize"
          :total="total"
          :page-sizes="[20, 50, 100]"
          layout="sizes, prev, pager, next, jumper"
          @update:current-page="setPage"
          @update:page-size="setPageSize"
        />
      </div>
    </DsAsyncSection>
  </div>
</template>

<style scoped>
.ds-table-card {
  background: var(--surface-1);
  border: 1px solid var(--line-subtle);
  border-radius: var(--r-lg);
  overflow: hidden;
}
.ds-table-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-4);
  padding: var(--sp-3) var(--sp-4);
  border-bottom: 1px solid var(--line-subtle);
}
.ds-table-toolbar__main {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  flex: 1;
  min-width: 0;
}
.ds-table-toolbar__side {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex-shrink: 0;
}
.ds-density-select {
  width: 88px;
}

.ds-table {
  --el-table-border-color: var(--line-subtle);
  --el-table-header-bg-color: var(--sunken);
  --el-table-header-text-color: var(--text-2);
  --el-table-tr-bg-color: var(--surface-1);
  --el-table-row-hover-bg-color: var(--surface-2);
  font-size: var(--fs-body-sm);
}
.ds-table :deep(th.el-table__cell) {
  font-size: var(--fs-caption);
  font-weight: var(--fw-h3);
  letter-spacing: 0.02em;
}
/* 排序三态指示：默认 --text-4，激活主色（设计稿 §4.4.2） */
.ds-table :deep(.sort-caret.ascending) {
  border-bottom-color: var(--text-4);
}
.ds-table :deep(.sort-caret.descending) {
  border-top-color: var(--text-4);
}
.ds-table :deep(th.ascending .sort-caret.ascending) {
  border-bottom-color: var(--accent);
}
.ds-table :deep(th.descending .sort-caret.descending) {
  border-top-color: var(--accent);
}
.ds-table :deep(.el-table__expand-icon) {
  color: var(--text-3);
}
.ds-table :deep(.ds-cell-text) {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 行高密度（设计稿 §4.4.2：紧凑 36 / 舒适 44 / 宽松 52） */
.ds-table-card[data-density='compact'] :deep(.el-table__body .el-table__cell) {
  padding: 3px 0;
}
.ds-table-card[data-density='default'] :deep(.el-table__body .el-table__cell) {
  padding: 8px 0;
}
.ds-table-card[data-density='loose'] :deep(.el-table__body .el-table__cell) {
  padding: 13px 0;
}

.ds-table-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-4);
  padding: var(--sp-3) var(--sp-4);
  border-top: 1px solid var(--line-subtle);
}
.ds-table-footer__range {
  font-size: var(--fs-caption);
  color: var(--text-3);
  font-variant-numeric: tabular-nums;
}
</style>
