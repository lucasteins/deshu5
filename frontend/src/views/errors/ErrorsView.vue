<script setup lang="ts">
/**
 * 错题集（F2.5 · 设计稿 §4.5「4. 错题集」+ 设计落地参照 #10）
 *
 * - 列表：DsDataTable（统一表格：排序 / 列头漏斗筛选 / 分页 / 行展开）
 * - 左侧错误类型分布侧栏：类型 + 计数 + 条形，点击即筛选（§4.5「把归因从下拉变成结构」）
 * - 语义搜索：POST /api/error-search（RAG 相似度排序）
 * - 详情展开：业务问题 / 错误 SQL / 修正 SQL / 详细说明 + 元信息
 * - 对比视图：生成 SQL vs 修正 SQL 左右并排差异高亮（SqlCompare，--danger-soft / --success-soft 底）
 * - 标记已修复（confirm.l1）/ 删除（confirm.l2）/ 编辑（轻量弹窗）
 * 归因质量口径（留痕）：已归因 = error_type 为具体类型（非「其他」）；待归因 = 「其他」或空。
 */
import { computed, onMounted, ref } from 'vue'
import { ElButton, ElInput } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import { confirm, DsDataTable, toast, type DsColumn } from '@/components'
import {
  deleteError,
  ERROR_TYPES,
  fetchAllErrors,
  normalizeErrorTime,
  resolveError,
  searchErrors,
  type ErrorRecord,
  type ErrorSearchItem,
} from '@/api/errors'
import ErrorEditDialog from './ErrorEditDialog.vue'
import SqlCompare from './SqlCompare.vue'

/* ---------- 错误类型语义色映射（设计稿 §4.5：选表=琥珀 / 字段=红 / 关联=紫 / 条件=蓝 / 聚合=青 / 其他=灰） ---------- */
const TYPE_CLASS: Record<string, string> = {
  表选择错误: 'err-badge--table',
  字段选择错误: 'err-badge--field',
  关联条件错误: 'err-badge--relation',
  WHERE条件错误: 'err-badge--where',
  聚合方式错误: 'err-badge--agg',
  '排序/分组错误': 'err-badge--sort',
  其他: 'err-badge--other',
}
function typeClass(t: string): string {
  return TYPE_CLASS[t] ?? 'err-badge--other'
}
const TYPE_DOT: Record<string, string> = {
  表选择错误: 'err-dot--table',
  字段选择错误: 'err-dot--field',
  关联条件错误: 'err-dot--relation',
  WHERE条件错误: 'err-dot--where',
  聚合方式错误: 'err-dot--agg',
  '排序/分组错误': 'err-dot--sort',
  其他: 'err-dot--other',
}
function typeDot(t: string): string {
  return TYPE_DOT[t] ?? 'err-dot--other'
}

/* ---------- 数据 ---------- */
const allRows = ref<ErrorRecord[]>([])
const loading = ref(false)
const error = ref<unknown>(null)

async function load() {
  loading.value = true
  error.value = null
  try {
    const { items } = await fetchAllErrors()
    allRows.value = items
  } catch (e) {
    error.value = e
  } finally {
    loading.value = false
  }
}
onMounted(load)

/* ---------- 语义搜索 ---------- */
const searchQuery = ref('')
const searchActive = ref(false)
const searchItems = ref<ErrorSearchItem[]>([])
const searchLoading = ref(false)

async function runSearch() {
  const q = searchQuery.value.trim()
  if (!q) {
    resetSearch()
    return
  }
  searchLoading.value = true
  try {
    const res = await searchErrors(q, 50)
    /* 搜索结果与列表字段一致（error-search 已返回完整字段），直接采用 */
    searchItems.value = res.items ?? []
    searchActive.value = true
  } catch (e) {
    searchActive.value = false
    toast.danger({ title: '语义搜索失败', desc: e instanceof Error ? e.message : String(e) })
  } finally {
    searchLoading.value = false
  }
}

function resetSearch() {
  searchQuery.value = ''
  searchActive.value = false
  searchItems.value = []
}

/* ---------- 侧栏类型筛选 ---------- */
const typeFilter = ref('')
function toggleTypeFilter(t: string) {
  typeFilter.value = typeFilter.value === t ? '' : t
}

const typeFilteredRows = computed<ErrorRecord[]>(() => {
  if (!typeFilter.value) return allRows.value
  return allRows.value.filter((r) => (r.error_type || '其他') === typeFilter.value)
})

const displayRows = computed<ErrorRecord[]>(() => {
  return searchActive.value ? (searchItems.value as ErrorRecord[]) : typeFilteredRows.value
})

const searchHint = computed(() => {
  if (!searchActive.value) return ''
  return `搜索「${searchQuery.value.trim()}」找到 ${searchItems.value.length} 条相关错题（按语义相似度排序）`
})

/* ---------- 概览与分布 ---------- */
const stats = computed(() => {
  const total = allRows.value.length
  const resolved = allRows.value.filter((r) => r.is_resolved).length
  const attributed = allRows.value.filter(
    (r) => r.error_type && r.error_type !== '其他' && r.error_type !== '',
  ).length
  const unattributed = total - attributed
  const pct = total ? Math.round((attributed / total) * 100) : 0
  return { total, resolved, unresolved: total - resolved, attributed, unattributed, pct }
})

const typeDistribution = computed(() => {
  const map = new Map<string, number>()
  for (const r of allRows.value) {
    const t = r.error_type || '其他'
    map.set(t, (map.get(t) ?? 0) + 1)
  }
  return ERROR_TYPES.map((t) => ({ type: t, count: map.get(t) ?? 0 }))
    .filter((d) => d.count > 0)
    .sort((a, b) => b.count - a.count)
})

const maxTypeCount = computed(() =>
  typeDistribution.value.reduce((m, d) => Math.max(m, d.count), 0),
)

/* ---------- 列定义 ---------- */
const STATUS_OPTIONS = [
  { value: '待修复', label: '待修复' },
  { value: '已修复', label: '已修复' },
]

const columns = computed<DsColumn<ErrorRecord>[]>(() => [
  { key: 'business_question', label: '业务问题', minWidth: 300, sortable: true, slot: 'question' },
  { key: 'error_type', label: '错误类型', width: 132, slot: 'errorType' },
  {
    key: 'status',
    label: '状态',
    width: 96,
    align: 'center',
    slot: 'status',
    filter: { type: 'enum', options: STATUS_OPTIONS },
    value: (r) => (r.is_resolved ? '已修复' : '待修复'),
  },
  {
    key: 'frequency',
    label: '重复次数',
    width: 92,
    align: 'center',
    sortable: true,
    sortType: 'number',
    slot: 'frequency',
  },
  {
    key: 'created_at',
    label: '创建时间',
    width: 156,
    sortable: true,
    slot: 'time',
    value: (r) => normalizeErrorTime(r.created_at),
  },
  { key: 'actions', label: '操作', width: 158, align: 'right', slot: 'actions' },
])

/* ---------- 单元格与操作 ---------- */
function truncate(s: string, n: number): string {
  return s.length > n ? `${s.slice(0, n)}…` : s
}
function fmtTime(row: ErrorRecord): string {
  return normalizeErrorTime(row.created_at).slice(0, 16)
}

const editVisible = ref(false)
const editId = ref<number | null>(null)
function openEdit(row: ErrorRecord) {
  editId.value = row.id
  editVisible.value = true
}
function onSaved() {
  void load()
}

async function onResolve(row: ErrorRecord) {
  const ok = await confirm.l1({
    title: '标记已修复',
    message: `确定将错题 #${row.id}「${truncate(row.business_question, 24)}」标记为已修复吗？`,
    confirmText: '标记已修复',
  })
  if (!ok) return
  try {
    await resolveError(row.id, true)
    toast.success(`错题 #${row.id} 已标记为已修复`)
    if (searchActive.value && searchQuery.value.trim()) void runSearch()
    await load()
  } catch (e) {
    toast.danger({ title: '操作失败', desc: e instanceof Error ? e.message : String(e) })
  }
}

async function onDelete(row: ErrorRecord) {
  const ok = await confirm.l2({
    title: '删除错题',
    message: `即将删除错题 #${row.id}，此操作不可撤销。`,
    impacts: [
      `从错题集永久删除「${truncate(row.business_question, 28)}」`,
      '错误归因分布与统计看板的错题计数将相应变化',
    ],
    confirmText: '删除',
    danger: true,
  })
  if (!ok) return
  try {
    await deleteError(row.id)
    toast.success(`已删除错题 #${row.id}`)
    if (searchActive.value && searchQuery.value.trim()) void runSearch()
    await load()
  } catch (e) {
    toast.danger({ title: '删除失败', desc: e instanceof Error ? e.message : String(e) })
  }
}
</script>

<template>
  <div class="errors">
    <!-- 左侧：错误类型分布侧栏（§4.5「4. 错题集」点击即筛选） -->
    <aside class="errors__side">
      <div class="errors__side-head">
        <span class="errors__side-title">错误类型</span>
        <span class="errors__side-total">{{ stats.total }}</span>
      </div>

      <button
        type="button"
        class="err-type-row err-type-row--all"
        :class="{ 'is-active': typeFilter === '' }"
        @click="typeFilter = ''"
      >
        <span class="err-type-row__label">全部错题</span>
        <span class="err-type-row__count">{{ stats.total }}</span>
      </button>

      <button
        v-for="d in typeDistribution"
        :key="d.type"
        type="button"
        class="err-type-row"
        :class="{ 'is-active': typeFilter === d.type }"
        @click="toggleTypeFilter(d.type)"
      >
        <span class="err-dot" :class="typeDot(d.type)" aria-hidden="true" />
        <span class="err-type-row__label">{{ d.type }}</span>
        <span class="err-type-row__bar">
          <span
            class="err-type-row__fill"
            :style="{ width: maxTypeCount ? `${(d.count / maxTypeCount) * 100}%` : '0%' }"
          />
        </span>
        <span class="err-type-row__count">{{ d.count }}</span>
      </button>

      <div class="errors__side-attribution">
        <span class="errors__side-title">归因质量</span>
        <div class="errors__attrib-row">
          <span>已归因</span>
          <b>{{ stats.pct }}%</b>
        </div>
        <div class="errors__attrib-row">
          <span>待归因</span>
          <b>{{ stats.unattributed }} 条</b>
        </div>
      </div>
    </aside>

    <!-- 主区：概览条 + 表格 -->
    <div class="errors__main">
      <div class="errors__overview">
        <span>共 <b>{{ stats.total }}</b> 条</span>
        <span class="errors__sep">·</span>
        <span>已修复 <b>{{ stats.resolved }}</b></span>
        <span class="errors__sep">·</span>
        <span>待修复 <b>{{ stats.unresolved }}</b></span>
      </div>

      <DsDataTable
        :columns="columns"
        :rows="displayRows"
        :loading="loading || searchLoading"
        :error="error"
        expandable
        table-id="errors"
        skeleton="table"
        @retry="load"
      >
        <template #toolbar>
          <ElInput
            v-model="searchQuery"
            class="errors__search"
            placeholder="语义搜索错题（如：供电单位过滤条件）"
            clearable
            :prefix-icon="Search"
            @keyup.enter="runSearch"
            @clear="resetSearch"
          />
          <ElButton type="primary" :loading="searchLoading" @click="runSearch">搜索</ElButton>
          <ElButton @click="resetSearch">重置</ElButton>
          <span v-if="searchHint" class="errors__hint">{{ searchHint }}</span>
        </template>

        <template #cell-question="{ row }">
          <span class="err-q" :title="row.business_question">{{ row.business_question }}</span>
        </template>

        <template #cell-errorType="{ row }">
          <span class="err-badge" :class="typeClass(row.error_type)">
            {{ row.error_type || '其他' }}
          </span>
        </template>

        <template #cell-status="{ row }">
          <span class="err-badge" :class="row.is_resolved ? 'err-badge--resolved' : 'err-badge--open'">
            {{ row.is_resolved ? '已修复' : '待修复' }}
          </span>
        </template>

        <template #cell-frequency="{ row }">
          <span v-if="row.frequency > 1" class="err-freq">×{{ row.frequency }}</span>
          <span v-else class="err-freq err-freq--one">1</span>
        </template>

        <template #cell-time="{ row }">
          <span class="err-time">{{ fmtTime(row) || '—' }}</span>
        </template>

        <template #cell-actions="{ row }">
          <span class="err-actions">
            <button type="button" class="err-link" @click="openEdit(row)">编辑</button>
            <button
              v-if="!row.is_resolved"
              type="button"
              class="err-link err-link--ok"
              @click="onResolve(row)"
            >
              标记已修复
            </button>
            <button type="button" class="err-link err-link--danger" @click="onDelete(row)">
              删除
            </button>
          </span>
        </template>

        <template #expanded="{ row }">
          <div class="err-detail">
            <div class="err-detail__meta">
              <span class="err-badge" :class="typeClass(row.error_type)">
                {{ row.error_type || '其他' }}
              </span>
              <span class="err-badge" :class="row.is_resolved ? 'err-badge--resolved' : 'err-badge--open'">
                {{ row.is_resolved ? '已修复' : '待修复' }}
              </span>
              <span v-if="row.frequency > 1" class="err-freq">重复 ×{{ row.frequency }}</span>
              <span class="err-detail__metaitem">创建：{{ fmtTime(row) || '—' }}</span>
              <span class="err-detail__metaitem">ID：{{ row.id }}</span>
            </div>

            <div v-if="row.error_detail" class="err-detail__section">
              <div class="err-detail__label">详细说明</div>
              <div class="err-detail__text">{{ row.error_detail }}</div>
            </div>

            <div class="err-detail__section">
              <div class="err-detail__label">对比视图 · 生成 SQL vs 修正 SQL</div>
              <SqlCompare :generated="row.generated_sql" :correct="row.correct_sql" />
            </div>
          </div>
        </template>
      </DsDataTable>

      <ErrorEditDialog v-model:visible="editVisible" :error-id="editId" @saved="onSaved" />
    </div>
  </div>
</template>

<style scoped>
.errors {
  display: grid;
  grid-template-columns: 220px 1fr;
  gap: var(--sp-4);
  align-items: start;
}

/* ---------- 左侧侧栏 ---------- */
.errors__side {
  position: sticky;
  top: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: var(--sp-4);
  background: var(--surface-1);
  border: 1px solid var(--line-subtle);
  border-radius: var(--r-lg);
}
.errors__side-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: var(--sp-2);
}
.errors__side-title {
  font-size: var(--fs-caption);
  font-weight: var(--fw-h3);
  letter-spacing: 0.02em;
  color: var(--text-3);
}
.errors__side-total {
  font-family: var(--font-mono);
  font-size: var(--fs-caption);
  color: var(--text-2);
}

.err-type-row {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  padding: 7px 10px;
  border: none;
  background: none;
  border-radius: var(--r-sm);
  cursor: pointer;
  text-align: left;
  transition: background-color var(--dur-fast) var(--ease-standard);
}
.err-type-row:hover {
  background: var(--surface-2);
}
.err-type-row.is-active {
  background: var(--accent-soft);
}
.err-type-row--all {
  font-weight: var(--fw-h3);
}
.err-type-row__label {
  flex: none;
  max-width: 104px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--fs-body-sm);
  color: var(--text-1);
}
.err-type-row__bar {
  flex: 1;
  min-width: 0;
  height: 4px;
  background: var(--sunken);
  border-radius: var(--r-full);
  overflow: hidden;
}
.err-type-row__fill {
  display: block;
  height: 100%;
  background: var(--text-4);
  border-radius: var(--r-full);
}
.err-type-row.is-active .err-type-row__fill {
  background: var(--accent);
}
.err-type-row__count {
  flex: none;
  min-width: 20px;
  text-align: right;
  font-family: var(--font-mono);
  font-size: var(--fs-caption);
  color: var(--text-3);
  font-variant-numeric: tabular-nums;
}

.errors__side-attribution {
  margin-top: var(--sp-4);
  padding-top: var(--sp-3);
  border-top: 1px solid var(--line-subtle);
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.errors__attrib-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: var(--fs-body-sm);
  color: var(--text-2);
}
.errors__attrib-row b {
  font-family: var(--font-mono);
  font-weight: 600;
  color: var(--text-1);
  font-variant-numeric: tabular-nums;
}

/* 语义色圆点（选表=琥珀 / 字段=红 / 关联=紫 / 条件=蓝 / 聚合=青 / 其他=灰） */
.err-dot {
  flex: none;
  width: 8px;
  height: 8px;
  border-radius: var(--r-full);
  background: var(--text-4);
}
.err-dot--table { background: var(--warning-fill); }
.err-dot--field { background: var(--danger-fill); }
.err-dot--relation { background: #8b5cf6; }
.err-dot--where { background: var(--info-fill); }
.err-dot--agg { background: var(--accent); }
.err-dot--sort { background: var(--ember-fill); }
.err-dot--other { background: var(--text-4); }

/* ---------- 主区 ---------- */
.errors__main {
  display: flex;
  flex-direction: column;
  gap: var(--sp-3);
  min-width: 0;
}
.errors__overview {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  padding: var(--sp-2) var(--sp-4);
  background: var(--sunken);
  border: 1px solid var(--line-subtle);
  border-radius: var(--r-md);
  font-size: var(--fs-body-sm);
  color: var(--text-2);
}
.errors__overview b {
  font-family: var(--font-mono);
  font-weight: 600;
  color: var(--text-1);
  font-variant-numeric: tabular-nums;
}
.errors__sep {
  color: var(--text-4);
}
.errors__search {
  width: 320px;
}
.errors__hint {
  margin-left: auto;
  font-size: var(--fs-caption);
  color: var(--text-3);
  white-space: nowrap;
}

/* ---------- 单元格 ---------- */
.err-q {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text-1);
}
.err-freq {
  font-family: var(--font-mono);
  font-size: var(--fs-caption);
  font-weight: 600;
  color: var(--warning);
}
.err-freq--one {
  color: var(--text-4);
  font-weight: 400;
}
.err-time {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text-3);
  font-variant-numeric: tabular-nums;
}

/* 徽标：语义色编码（§4.5） */
.err-badge {
  display: inline-flex;
  align-items: center;
  height: 22px;
  padding: 0 10px;
  border-radius: var(--r-full);
  font-size: var(--fs-caption);
  font-weight: 500;
  white-space: nowrap;
}
.err-badge--table { background: var(--warning-soft); color: var(--warning); }
.err-badge--field { background: var(--danger-soft); color: var(--danger); }
.err-badge--relation { background: rgba(139, 92, 246, 0.14); color: #8b5cf6; }
.err-badge--where { background: var(--info-soft); color: var(--info); }
.err-badge--agg { background: var(--accent-soft); color: var(--accent); }
.err-badge--sort { background: var(--ember-soft); color: var(--ember); }
.err-badge--other { background: var(--sunken); color: var(--text-3); }
.err-badge--resolved { background: var(--success-soft); color: var(--success); }
.err-badge--open { background: var(--sunken); color: var(--text-3); }

/* 行内操作：hover / focus 才显示（§4.4.2） */
.err-actions {
  display: inline-flex;
  gap: var(--sp-3);
  opacity: 0;
  transition: opacity var(--dur-fast) var(--ease-out);
}
.errors :deep(.el-table__row:hover) .err-actions,
.errors :deep(.el-table__row:focus-within) .err-actions {
  opacity: 1;
}
.err-link {
  padding: 0;
  border: none;
  background: none;
  font-size: var(--fs-body-sm);
  color: var(--accent);
  cursor: pointer;
  white-space: nowrap;
}
.err-link:hover {
  text-decoration: underline;
}
.err-link--ok {
  color: var(--success);
}
.err-link--danger {
  color: var(--danger);
}

/* ---------- 行展开详情 ---------- */
.err-detail {
  display: flex;
  flex-direction: column;
  gap: var(--sp-3);
  padding: var(--sp-3) var(--sp-4);
  background: var(--surface-2);
  border-radius: var(--r-md);
}
.err-detail__meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--sp-3);
}
.err-detail__metaitem {
  font-size: var(--fs-caption);
  color: var(--text-3);
}
.err-detail__section {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.err-detail__label {
  font-size: var(--fs-caption);
  font-weight: var(--fw-h3);
  color: var(--text-3);
}
.err-detail__text {
  font-size: var(--fs-body-sm);
  color: var(--text-1);
  line-height: 1.7;
  white-space: pre-wrap;
}
</style>
