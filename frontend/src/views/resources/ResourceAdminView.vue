<script setup lang="ts">
/**
 * 资源管理页签（F2.2，原 static/js/resource.js 资源管理编辑器）
 *
 * 通用资源 CRUD：类型切换 → 条目表格（动态列，依 entry_schema）→ 新建/编辑/删除/
 * 启用开关；模板下载与文件导入（JSON/CSV/xlsx）。
 *
 * 复用 DsDataTable（客户端分页管道）：条目一次性全量取回（内部工具量级 ≤千行，
 * 后端 list 默认 limit 200，这里取 1000），q 子串过滤仍在后端完成。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElButton, ElInput, ElOption, ElSelect, ElSwitch } from 'element-plus'
import { Search, Upload } from '@element-plus/icons-vue'
import { ApiError } from '@/api'
import { confirm, DsDataTable, toast, type DsColumn } from '@/components'
import {
  deleteResourceItem,
  fetchResourceItems,
  fetchResourcesSummary,
  fetchResourceTemplate,
  importResourceItems,
  updateResourceItem,
  type EntrySchemaField,
  type ImportResponse,
  type ResourceItem,
  type ResourceSummary,
} from '@/api/resources'
import ResourceEditDialog from './ResourceEditDialog.vue'

/** 只读资源（写按钮禁用 + 注明，原 RES_ADMIN_READONLY） */
const READONLY: Record<string, string> = {
  schema_catalog: '表结构目录由 DDL 预加载管道生产（SchemaPreloader 每次启动重建），本期只读。',
  schema_graph: '表关系图由 DDL 预加载管道生产（主外键关系），本期只读。',
}
/** 条目标识字段优先级（各类型主键形态不一） */
const ID_FIELDS = ['id', 'code_name', 'table_name']

const types = ref<ResourceSummary[]>([])
const rtype = ref('')
const schema = ref<EntrySchemaField[]>([])
const items = ref<ResourceItem[]>([])
const loading = ref(false)
const error = ref<unknown>(null)
const kw = ref('')

const editVisible = ref(false)
const editingId = ref<string | null>(null)
const importResult = ref<ImportResponse | null>(null)
const importing = ref(false)

const fileInput = ref<HTMLInputElement>()

const readonlyNote = computed(() => READONLY[rtype.value] ?? '')
const readonly = computed(() => Boolean(READONLY[rtype.value]))

/* ---------- 数据加载 ---------- */

async function loadTypes() {
  try {
    const res = await fetchResourcesSummary()
    types.value = res.items ?? []
    if (!rtype.value && types.value.length) {
      const preferred = types.value.find((t) => t.name === 'keyword_table_map')
      rtype.value = preferred ? preferred.name : types.value[0].name
    }
  } catch (e) {
    error.value = e
  }
}

async function loadSchema() {
  if (!rtype.value) return
  try {
    const res = await fetchResourceTemplate(rtype.value)
    schema.value = res.template?.entry_schema ?? []
  } catch {
    // 模板缺失不阻断条目表格展示（schema 仅用于动态列/表单）
  }
}

async function loadItems() {
  if (!rtype.value) return
  loading.value = true
  error.value = null
  try {
    const res = await fetchResourceItems(rtype.value, kw.value.trim())
    items.value = res.items ?? []
  } catch (e) {
    error.value = e
  } finally {
    loading.value = false
  }
}

async function refresh() {
  await Promise.all([loadSchema(), loadItems()])
}

onMounted(async () => {
  await loadTypes()
  await refresh()
})

function onTypeChange() {
  kw.value = ''
  importResult.value = null
  void refresh()
}

let debounceTimer: ReturnType<typeof setTimeout> | undefined
function onSearchInput() {
  clearTimeout(debounceTimer)
  debounceTimer = setTimeout(() => void loadItems(), 300)
}
onBeforeUnmount(() => clearTimeout(debounceTimer))

/* ---------- 动态列 ---------- */

function itemId(item: ResourceItem): string {
  for (const f of ID_FIELDS) {
    const v = item[f]
    if (v !== undefined && v !== null && v !== '') return String(v)
  }
  return ''
}

function cellText(v: unknown): string {
  if (v === null || v === undefined) return ''
  let s = typeof v === 'object' ? JSON.stringify(v) : String(v)
  return s.length > 60 ? `${s.slice(0, 60)}…` : s
}

const columns = computed<DsColumn<ResourceItem>[]>(() => {
  const cols: DsColumn<ResourceItem>[] = []
  const first = items.value[0]
  const idField = (first && ID_FIELDS.find((f) => f in first)) || 'id'

  cols.push({
    key: idField,
    label: idField,
    minWidth: 120,
    sortable: true,
    value: (r) => cellText(r[idField]),
  })

  for (const spec of schema.value) {
    if (spec.field === idField || spec.field === 'enabled') continue
    cols.push({
      key: spec.field,
      label: spec.field,
      minWidth: 120,
      sortable: spec.type === 'str' || spec.type === 'int',
      sortType: spec.type === 'int' ? 'number' : 'string',
      value: (r) => cellText(r[spec.field]),
    })
  }

  if (first && 'enabled' in first) {
    cols.push({
      key: 'enabled',
      label: 'enabled',
      width: 96,
      align: 'center',
      slot: 'enabled',
      value: (r) => (r.enabled ? '启用' : '停用'),
    })
  }

  // 补充条目里 schema 未覆盖的字段（如 ingest_time），最多 8 列，避免超宽
  if (first) {
    for (const k of Object.keys(first)) {
      if (cols.length >= 8) break
      if (cols.some((c) => c.key === k)) continue
      if (k === 'updated_at') continue
      cols.push({ key: k, label: k, minWidth: 120, value: (r) => cellText(r[k]) })
    }
  }

  cols.push({ key: 'actions', label: '操作', width: 132, align: 'right', slot: 'actions' })
  return cols
})

/* ---------- 操作 ---------- */

function openNew() {
  editingId.value = null
  editVisible.value = true
}
function openEdit(row: ResourceItem) {
  editingId.value = itemId(row)
  editVisible.value = true
}
function onSaved() {
  void loadItems()
}

async function onDelete(row: ResourceItem) {
  const id = itemId(row)
  const ok = await confirm.l2({
    title: '删除资源条目',
    message: `即将删除 ${rtype.value} 条目 ${id}，此操作不可撤销。`,
    impacts: [`从「${rtype.value}」中永久删除条目 ${id}`, '依赖该条目的生成 / 检索链路可能受到影响'],
    confirmText: '删除',
    danger: true,
  })
  if (!ok) return
  try {
    await deleteResourceItem(rtype.value, id)
    toast.success(`已删除条目 ${id}`)
    await loadItems()
  } catch (e) {
    toast.danger({ title: '删除失败', desc: describeError(e) })
  }
}

async function toggleEnabled(row: ResourceItem, val: boolean) {
  const id = itemId(row)
  try {
    await updateResourceItem(rtype.value, id, { enabled: val ? 1 : 0 })
    row.enabled = val ? 1 : 0
  } catch (e) {
    toast.danger({ title: '更新失败', desc: describeError(e) })
    void loadItems()
  }
}

async function downloadTemplate() {
  try {
    const res = await fetchResourceTemplate(rtype.value)
    const blob = new Blob([JSON.stringify(res.template, null, 2)], { type: 'application/json' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `${rtype.value}_template.json`
    a.click()
    URL.revokeObjectURL(a.href)
  } catch (e) {
    toast.danger({ title: '获取模板失败', desc: describeError(e) })
  }
}

function onPickFile() {
  fileInput.value?.click()
}

async function onImport(ev: Event) {
  const input = ev.target as HTMLInputElement
  const file = input.files && input.files[0]
  input.value = ''
  if (!file) return
  importing.value = true
  importResult.value = null
  try {
    importResult.value = await importResourceItems(rtype.value, file)
    toast.success('导入完成')
    await loadItems()
  } catch (e) {
    toast.danger({ title: '导入失败', desc: describeError(e) })
  } finally {
    importing.value = false
  }
}

function describeError(e: unknown): string {
  if (e instanceof ApiError) {
    const errors = e.payload && Array.isArray(e.payload.errors) ? (e.payload.errors as string[]) : null
    if (errors && errors.length) return errors.join('；')
    return e.message
  }
  return e instanceof Error ? e.message : String(e)
}
</script>

<template>
  <div class="res-admin">
    <DsDataTable
      :columns="columns"
      :rows="items"
      :loading="loading"
      :error="error"
      table-id="res-admin"
      skeleton="table"
      @retry="loadItems"
    >
      <template #toolbar>
        <ElSelect v-model="rtype" class="res-type" placeholder="资源类型" @change="onTypeChange">
          <ElOption v-for="t in types" :key="t.name" :value="t.name" :label="`${t.label}（${t.name}，${t.count ?? '?'}）`" />
        </ElSelect>
        <ElInput
          v-model="kw"
          class="res-search"
          placeholder="搜索（名称 / 内容子串）"
          clearable
          :prefix-icon="Search"
          @input="onSearchInput"
        />
        <template v-if="!readonly">
          <ElButton type="primary" @click="openNew">新建</ElButton>
          <ElButton @click="downloadTemplate">下载模板</ElButton>
          <ElButton :icon="Upload" :loading="importing" @click="onPickFile">导入</ElButton>
        </template>
      </template>

      <template #cell-enabled="{ row }">
        <ElSwitch
          v-if="!readonly"
          :model-value="!!row.enabled"
          @change="(v: string | number | boolean) => toggleEnabled(row, Boolean(v))"
        />
        <span v-else class="muted">{{ row.enabled ? '启用' : '停用' }}</span>
      </template>

      <template #cell-actions="{ row }">
        <template v-if="!readonly">
          <span class="row-actions">
            <button type="button" class="link" @click="openEdit(row)">编辑</button>
            <button type="button" class="link link--danger" @click="onDelete(row)">删除</button>
          </span>
        </template>
        <span v-else class="muted">只读</span>
      </template>
    </DsDataTable>

    <p v-if="readonlyNote" class="res-note">{{ readonlyNote }}</p>

    <div v-if="importResult" class="import-result">
      <p class="import-summary">
        导入完成：接收 {{ importResult.accepted }} 条，拒绝 {{ importResult.rejected.length }} 条
      </p>
      <table v-if="importResult.rejected.length" class="reject-table">
        <thead>
          <tr>
            <th style="width: 50%">行内容</th>
            <th>拒绝原因</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(rj, i) in importResult.rejected.slice(0, 10)" :key="i">
            <td class="mono">{{ JSON.stringify(rj.row).slice(0, 120) }}</td>
            <td>{{ rj.reason }}</td>
          </tr>
        </tbody>
      </table>
      <p v-if="importResult.rejected.length > 10" class="import-more">
        … 其余 {{ importResult.rejected.length - 10 }} 条从略
      </p>
    </div>

    <input ref="fileInput" type="file" accept=".json,.csv,.xlsx" hidden @change="onImport" />

    <ResourceEditDialog
      v-model="editVisible"
      :rtype="rtype"
      :schema="schema"
      :item-id="editingId"
      @saved="onSaved"
    />
  </div>
</template>

<style scoped>
.res-admin {
  display: flex;
  flex-direction: column;
  gap: var(--sp-3);
}
.res-type {
  width: 260px;
}
.res-search {
  width: 240px;
}
.res-note {
  margin: 0;
  font-size: var(--fs-caption);
  color: var(--text-3);
}
.muted {
  color: var(--text-4);
  font-size: var(--fs-caption);
}

.row-actions {
  display: inline-flex;
  gap: var(--sp-3);
  opacity: 0;
  transition: opacity var(--dur-fast) var(--ease-out);
}
.res-admin :deep(.el-table__row:hover) .row-actions,
.res-admin :deep(.el-table__row:focus-within) .row-actions {
  opacity: 1;
}
.link {
  padding: 0;
  border: none;
  background: none;
  font-size: var(--fs-body-sm);
  color: var(--accent);
  cursor: pointer;
}
.link:hover {
  text-decoration: underline;
}
.link--danger {
  color: var(--danger);
}

.import-result {
  padding: var(--sp-3) var(--sp-4);
  background: var(--surface-1);
  border: 1px solid var(--line-subtle);
  border-radius: var(--r-md);
}
.import-summary {
  margin: 0 0 var(--sp-2);
  font-size: var(--fs-body-sm);
  color: var(--text-2);
}
.reject-table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--fs-body-sm);
}
.reject-table th {
  text-align: left;
  padding: 6px 10px;
  font-size: var(--fs-caption);
  font-weight: 600;
  color: var(--text-3);
  background: var(--sunken);
  border-bottom: 1px solid var(--line);
}
.reject-table td {
  padding: 6px 10px;
  border-bottom: 1px solid var(--line-subtle);
  color: var(--text-1);
  vertical-align: top;
}
.mono {
  font-family: var(--font-mono);
  font-size: 11px;
}
.import-more {
  margin: var(--sp-2) 0 0;
  font-size: var(--fs-caption);
  color: var(--text-3);
}
</style>
