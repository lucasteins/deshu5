<script setup lang="ts">
/**
 * 问答对库（F1.1 试点 · 统一表格首秀）
 *
 * 设计依据：§4.4.2 表格组件 + §4.5「3. 问答对库」
 * 数据：GET /api/qa-pairs 全量 → 客户端筛选/排序/分页（useClientTable）；
 *      POST /api/qa-search 语义召回；编辑 /api/qa-update（弹窗）；删除 /api/qa-delete（L2 确认）
 * 交付范围（试点）：三态排序 / 列头漏斗筛选（难度·来源·状态）/ 分页 / 关键词·语义双模搜索 /
 *      行展开详情 / 编辑 / 删除 / 资产概览条。批量选择与列控制见交接日志「遗留」。
 * 设计偏差（记录于交接日志）：概览条不含「平均置信度」（库内无该数据列，不造假）。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { ElButton, ElInput, ElSegmented } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import { confirm, DsDataTable, toast, type DsColumn } from '@/components'
import {
  deleteQaPair,
  fetchAllQaPairs,
  normalizeQaTime,
  QA_DIFFICULTIES,
  searchQaPairs,
  type QaPair,
  type QaSearchItem,
} from '@/api/qa'
import QaEditDialog from './QaEditDialog.vue'

/* ---------- 数据 ---------- */
const allRows = ref<QaPair[]>([])
const loading = ref(false)
const error = ref<unknown>(null)

async function load() {
  loading.value = true
  error.value = null
  try {
    const { items } = await fetchAllQaPairs()
    allRows.value = items
  } catch (e) {
    error.value = e
  } finally {
    loading.value = false
  }
}
onMounted(load)

/* ---------- 搜索（关键词 / 语义 双模，设计稿 §4.5「3. 问答对库」） ---------- */
const MODE_OPTIONS = [
  { label: '关键词', value: 'keyword' },
  { label: '语义', value: 'semantic' },
]

const searchQuery = ref('')
const searchMode = ref<'keyword' | 'semantic'>('keyword')
const semanticActive = ref(false)
const semanticItems = ref<QaSearchItem[]>([])
const searchLoading = ref(false)

function setSearchMode(v: unknown) {
  if (v === 'keyword' || v === 'semantic') searchMode.value = v
}

watch(searchMode, () => {
  if (searchQuery.value.trim()) submitSearch()
})

const displayRows = computed<QaPair[]>(() => {
  const q = searchQuery.value.trim()
  if (!q) return allRows.value
  if (searchMode.value === 'semantic' && semanticActive.value) return semanticItems.value
  const lower = q.toLowerCase()
  return allRows.value.filter(
    (r) =>
      (r.question ?? '').toLowerCase().includes(lower) ||
      (r.standard_sql ?? '').toLowerCase().includes(lower),
  )
})

const resultHint = computed(() => {
  const q = searchQuery.value.trim()
  if (!q) return ''
  if (searchMode.value === 'semantic') {
    return semanticActive.value ? `语义召回 ${semanticItems.value.length} 条（按相似度排序）` : ''
  }
  return `关键词命中 ${displayRows.value.length} 条`
})

function submitSearch() {
  const q = searchQuery.value.trim()
  if (!q) {
    resetSearch()
    return
  }
  if (searchMode.value === 'semantic') {
    void runSemanticSearch(q)
  } else {
    semanticActive.value = false
    semanticItems.value = []
  }
}

async function runSemanticSearch(q: string) {
  searchLoading.value = true
  try {
    const res = await searchQaPairs(q, 50)
    /* /api/qa-search 响应不含 is_usable 等完整字段：按 id 与全量数据合并，避免状态列失真 */
    semanticItems.value = (res.items ?? []).map((it) => {
      const full = allRows.value.find((r) => r.id === it.id)
      return full ? { ...it, ...full } : it
    })
    semanticActive.value = true
  } catch (e) {
    semanticActive.value = false
    toast.danger({ title: '语义搜索失败', desc: e instanceof Error ? e.message : String(e) })
  } finally {
    searchLoading.value = false
  }
}

function resetSearch() {
  searchQuery.value = ''
  semanticActive.value = false
  semanticItems.value = []
}

/* ---------- 资产概览条（真实数据；无置信度列，故不展示） ---------- */
const stats = computed(() => {
  const cutoff = formatDay(Date.now() - 7 * 86400_000)
  let week = 0
  let usable = 0
  for (const r of allRows.value) {
    const t = normalizeQaTime(r.ingest_time)
    if (t && t >= cutoff) week += 1
    if (r.is_usable === 1) usable += 1
  }
  return { total: allRows.value.length, week, usable, pending: allRows.value.length - usable }
})

function formatDay(ts: number): string {
  const d = new Date(ts)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} 00:00:00`
}

/* ---------- 列定义 ---------- */
const DIFFICULTY_OPTIONS = QA_DIFFICULTIES.map((v) => ({ value: v, label: v }))
const STATUS_OPTIONS = [
  { value: '可用', label: '可用' },
  { value: '待评价', label: '待评价' },
]

const sourceOptions = computed(() => {
  const uniq = [...new Set(allRows.value.map((r) => r.source).filter(Boolean))]
  return uniq.map((v) => ({ value: v, label: v }))
})

const columns = computed<DsColumn<QaPair>[]>(() => [
  { key: 'id', label: 'ID', width: 72, sortable: true, sortType: 'number', align: 'right' },
  { key: 'question', label: '业务问题', minWidth: 320, sortable: true, slot: 'question' },
  { key: 'standard_sql', label: '标准 SQL', minWidth: 280, slot: 'sql' },
  {
    key: 'difficulty',
    label: '难度',
    width: 104,
    align: 'center',
    slot: 'difficulty',
    filter: { type: 'enum', options: DIFFICULTY_OPTIONS },
  },
  { key: 'source', label: '来源', width: 116, filter: { type: 'enum', options: sourceOptions.value } },
  {
    key: 'status',
    label: '状态',
    width: 96,
    align: 'center',
    slot: 'status',
    filter: { type: 'enum', options: STATUS_OPTIONS },
    value: (r) => (r.is_usable === 1 ? '可用' : '待评价'),
  },
  {
    key: 'ingest_time',
    label: '录入时间',
    width: 156,
    sortable: true,
    slot: 'time',
    value: (r) => normalizeQaTime(r.ingest_time),
  },
  { key: 'actions', label: '操作', width: 132, align: 'right', slot: 'actions' },
])

/* ---------- 单元格与操作 ---------- */
const DIFF_CLASS: Record<string, string> = {
  基础题: 'qa-badge--success',
  进阶题: 'qa-badge--info',
  挑战题: 'qa-badge--warn',
}
function diffClass(d: string): string {
  return DIFF_CLASS[d] ?? 'qa-badge--info'
}
function fmtTime(row: QaPair): string {
  return normalizeQaTime(row.ingest_time).slice(0, 16)
}
function truncate(s: string, n: number): string {
  return s.length > n ? `${s.slice(0, n)}…` : s
}

const editVisible = ref(false)
const editId = ref<number | null>(null)
function openEdit(row: QaPair) {
  editId.value = row.id
  editVisible.value = true
}
function onSaved() {
  void load()
}

async function onDelete(row: QaPair) {
  const ok = await confirm.l2({
    title: '删除问答对',
    message: `即将删除问答对 #${row.id}，此操作不可撤销。`,
    impacts: [
      `从问答对库永久删除「${truncate(row.question, 28)}」`,
      '智能问数与训练出题的检索将不再命中该问答对',
    ],
    confirmText: '删除',
    danger: true,
  })
  if (!ok) return
  try {
    await deleteQaPair(row.id)
    toast.success(`已删除问答对 #${row.id}`)
    if (semanticActive.value && searchQuery.value.trim()) {
      void runSemanticSearch(searchQuery.value.trim())
    }
    await load()
  } catch (e) {
    toast.danger({ title: '删除失败', desc: e instanceof Error ? e.message : String(e) })
  }
}
</script>

<template>
  <div class="qa-lib">
    <div class="qa-lib__overview">
      <span>共 <b>{{ stats.total }}</b> 条</span>
      <span class="qa-lib__sep">·</span>
      <span>近 7 天新增 <b>{{ stats.week }}</b></span>
      <span class="qa-lib__sep">·</span>
      <span>可用 <b>{{ stats.usable }}</b> / 待评价 <b>{{ stats.pending }}</b></span>
    </div>

    <DsDataTable
      :columns="columns"
      :rows="displayRows"
      :loading="loading || searchLoading"
      :error="error"
      expandable
      table-id="qa-lib"
      skeleton="table"
      @retry="load"
    >
      <template #toolbar="{ filtered }">
        <ElSegmented
          :model-value="searchMode"
          :options="MODE_OPTIONS"
          size="small"
          @update:model-value="setSearchMode"
        />
        <ElInput
          v-model="searchQuery"
          class="qa-lib__search"
          placeholder="搜索业务问题 / SQL，Enter 检索"
          clearable
          :prefix-icon="Search"
          @keyup.enter="submitSearch"
          @clear="resetSearch"
        />
        <ElButton type="primary" @click="submitSearch">搜索</ElButton>
        <ElButton @click="resetSearch">重置</ElButton>
        <span v-if="resultHint" class="qa-lib__hint">
          {{ resultHint }}<template v-if="filtered !== displayRows.length">
            · 列筛选后 {{ filtered }} 条</template>
        </span>
      </template>

      <template #cell-question="{ row }">
        <span class="qa-q" :title="row.question">{{ row.question }}</span>
      </template>

      <template #cell-sql="{ row }">
        <code class="qa-sql" :title="row.standard_sql || '（未生成）'">
          {{ row.standard_sql || '（未生成）' }}
        </code>
      </template>

      <template #cell-difficulty="{ row }">
        <span class="qa-badge" :class="diffClass(row.difficulty)">
          {{ row.difficulty || '进阶题' }}
        </span>
      </template>

      <template #cell-status="{ row }">
        <span
          class="qa-badge"
          :class="row.is_usable === 1 ? 'qa-badge--success' : 'qa-badge--muted'"
        >
          {{ row.is_usable === 1 ? '可用' : '待评价' }}
        </span>
      </template>

      <template #cell-time="{ row }">
        <span class="qa-time">{{ fmtTime(row) || '—' }}</span>
      </template>

      <template #cell-actions="{ row }">
        <span class="qa-actions">
          <button type="button" class="qa-link" @click="openEdit(row)">编辑</button>
          <button type="button" class="qa-link qa-link--danger" @click="onDelete(row)">删除</button>
        </span>
      </template>

      <template #expanded="{ row }">
        <div class="qa-detail">
          <div class="qa-detail__section">
            <div class="qa-detail__label">业务问题</div>
            <div class="qa-detail__text">{{ row.question }}</div>
          </div>
          <div class="qa-detail__section">
            <div class="qa-detail__label">标准 SQL</div>
            <pre class="qa-detail__sql">{{ row.standard_sql || '（未生成）' }}</pre>
          </div>
          <div class="qa-detail__meta">
            <span class="qa-badge" :class="diffClass(row.difficulty)">
              {{ row.difficulty || '进阶题' }}
            </span>
            <span
              class="qa-badge"
              :class="row.is_usable === 1 ? 'qa-badge--success' : 'qa-badge--muted'"
            >
              {{ row.is_usable === 1 ? '可用' : '待评价' }}
            </span>
            <span class="qa-detail__metaitem">来源：{{ row.source || '—' }}</span>
            <span class="qa-detail__metaitem">录入：{{ fmtTime(row) || '—' }}</span>
            <span class="qa-detail__metaitem">ID：{{ row.id }}</span>
          </div>
        </div>
      </template>
    </DsDataTable>

    <QaEditDialog v-model="editVisible" :pair-id="editId" @saved="onSaved" />
  </div>
</template>

<style scoped>
.qa-lib {
  display: flex;
  flex-direction: column;
  gap: var(--sp-3);
}

/* 资产概览条（§4.5「3. 问答对库」） */
.qa-lib__overview {
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
.qa-lib__overview b {
  font-family: var(--font-mono);
  font-weight: 600;
  color: var(--text-1);
  font-variant-numeric: tabular-nums;
}
.qa-lib__sep {
  color: var(--text-4);
}
.qa-lib__hint {
  margin-left: auto;
  font-size: var(--fs-caption);
  color: var(--text-3);
  white-space: nowrap;
}

.qa-lib__search {
  width: 300px;
}

/* 单元格 */
.qa-q {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text-1);
}
.qa-sql {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text-2);
}
.qa-time {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text-3);
  font-variant-numeric: tabular-nums;
}

/* 徽标 */
.qa-badge {
  display: inline-flex;
  align-items: center;
  height: 22px;
  padding: 0 10px;
  border-radius: var(--r-full);
  font-size: var(--fs-caption);
  font-weight: 500;
  white-space: nowrap;
}
.qa-badge--success {
  background: var(--success-soft);
  color: var(--success);
}
.qa-badge--info {
  background: var(--info-soft);
  color: var(--info);
}
.qa-badge--warn {
  background: var(--warning-soft);
  color: var(--warning);
}
.qa-badge--muted {
  background: var(--sunken);
  color: var(--text-3);
}

/* 行内操作：hover / 键盘 focus 才显示（§4.4.2 行内操作） */
.qa-actions {
  display: inline-flex;
  gap: var(--sp-3);
  opacity: 0;
  transition: opacity var(--dur-fast) var(--ease-out);
}
.qa-lib :deep(.el-table__row:hover) .qa-actions,
.qa-lib :deep(.el-table__row:focus-within) .qa-actions {
  opacity: 1;
}
.qa-link {
  padding: 0;
  border: none;
  background: none;
  font-size: var(--fs-body-sm);
  color: var(--accent);
  cursor: pointer;
}
.qa-link:hover {
  text-decoration: underline;
}
.qa-link--danger {
  color: var(--danger);
}

/* 行展开详情 */
.qa-detail {
  display: flex;
  flex-direction: column;
  gap: var(--sp-3);
  padding: var(--sp-3) var(--sp-4);
  background: var(--surface-2);
  border-radius: var(--r-md);
}
.qa-detail__label {
  margin-bottom: 4px;
  font-size: var(--fs-caption);
  font-weight: 600;
  color: var(--text-3);
}
.qa-detail__text {
  font-size: var(--fs-body-sm);
  color: var(--text-1);
  line-height: 1.7;
}
.qa-detail__sql {
  margin: 0;
  padding: var(--sp-3);
  background: var(--sunken);
  border: 1px solid var(--line-subtle);
  border-radius: var(--r-sm);
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.7;
  color: var(--text-1);
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 260px;
  overflow: auto;
}
.qa-detail__meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--sp-3);
}
.qa-detail__metaitem {
  font-size: var(--fs-caption);
  color: var(--text-3);
}
</style>
