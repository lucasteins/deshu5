/**
 * 客户端表格管道（筛选 → 排序 → 分页），配合 DsDataTable 使用
 *
 * 适用于「全量数据量小（≤千行）、后端仅分页」的场景（如问答对库 117 条）：
 * 视图一次性取全量 rows，组件内完成 §4.4.2 的排序 / 筛选 / 分页交互。
 * 未来服务端分页表格（大表）另扩展 server 模式，不复用本管道。
 *
 * 规则（设计稿 §4.4.2）：
 * - 筛选：同列多个枚举值为 OR，跨列 AND；
 * - 排序：升 → 降 → 取消三态，取消后恢复数据源原始顺序（稳定排序）；
 * - 筛选 / 排序变化时回到第 1 页；数据收缩时页码自动收敛。
 */
import { computed, ref, toValue, watch, type MaybeRefOrGetter } from 'vue'
import type { DsColumn, DsEnumFilters, DsFilterChip, DsSort } from './types'

interface UseClientTableOptions<Row extends object> {
  rows: MaybeRefOrGetter<Row[]>
  columns: MaybeRefOrGetter<DsColumn<Row>[]>
  defaultSort?: DsSort | null
  defaultPageSize?: number
}

/** 取单元格原始值（排序/筛选共用；供 DsDataTable 默认渲染复用） */
export function cellRaw(row: object, col: DsColumn<never> | DsColumn<object>): string | number {
  const c = col as DsColumn<object>
  if (c.value) return c.value(row)
  const v = (row as Record<string, unknown>)[c.key]
  if (v === null || v === undefined) return ''
  return typeof v === 'number' ? v : String(v)
}

/** 取单元格展示文本 */
export function cellText(row: object, col: DsColumn<never> | DsColumn<object>): string {
  return String(cellRaw(row, col))
}

export function useClientTable<Row extends object>(options: UseClientTableOptions<Row>) {
  const sort = ref<DsSort | null>(options.defaultSort ?? null)
  const enumFilters = ref<DsEnumFilters>({})
  const page = ref(1)
  const pageSize = ref(options.defaultPageSize ?? 20)

  /** 筛选后（未排序）数据——排序在其上进行 */
  const filteredRows = computed(() => {
    let out = toValue(options.rows)
    for (const col of toValue(options.columns)) {
      const picked = enumFilters.value[col.key]
      if (picked?.length) {
        out = out.filter((r) => picked.includes(String(cellRaw(r, col))))
      }
    }
    return out
  })

  const sortedRows = computed(() => {
    const s = sort.value
    if (!s) return filteredRows.value
    const col = toValue(options.columns).find((c) => c.key === s.key)
    if (!col?.sortable) return filteredRows.value

    const dir = s.order === 'asc' ? 1 : -1
    const numeric = col.sortType === 'number'
    return [...filteredRows.value].sort((a, b) => {
      const va = cellRaw(a, col)
      const vb = cellRaw(b, col)
      const cmp = numeric ? Number(va) - Number(vb) : String(va).localeCompare(String(vb), 'zh-Hans-CN')
      return cmp * dir
    })
  })

  /** 筛选后总条数（表格脚注与分页以它为准） */
  const total = computed(() => filteredRows.value.length)

  const pageCount = computed(() => Math.max(1, Math.ceil(total.value / pageSize.value)))

  const pageRows = computed(() => {
    const start = (page.value - 1) * pageSize.value
    return sortedRows.value.slice(start, start + pageSize.value)
  })

  // 数据收缩（筛选/删除后）时页码收敛
  watch([total, pageSize], () => {
    if (page.value > pageCount.value) page.value = pageCount.value
  })

  /** 来自 ElTable 的 sort-change（'ascending' | 'descending' | null） */
  function applySort(key: string, order: 'ascending' | 'descending' | null) {
    sort.value = order ? { key, order: order === 'ascending' ? 'asc' : 'desc' } : null
    page.value = 1
  }

  function setEnumFilter(key: string, values: string[]) {
    if (values.length) {
      enumFilters.value = { ...enumFilters.value, [key]: values }
    } else {
      const { [key]: _removed, ...rest } = enumFilters.value
      enumFilters.value = rest
    }
    page.value = 1
  }

  function clearColumn(key: string) {
    const { [key]: _removed, ...rest } = enumFilters.value
    enumFilters.value = rest
    page.value = 1
  }

  function clearFilters() {
    enumFilters.value = {}
    page.value = 1
  }

  const filterChips = computed<DsFilterChip[]>(() => {
    const chips: DsFilterChip[] = []
    for (const col of toValue(options.columns)) {
      const picked = enumFilters.value[col.key]
      if (!picked?.length || !col.filter) continue
      const labels = picked.map(
        (v) => col.filter?.options.find((o) => o.value === v)?.label ?? v,
      )
      chips.push({ key: col.key, label: `${col.label} = ${labels.join('/')}` })
    }
    return chips
  })

  function setPage(p: number) {
    page.value = p
  }

  function setPageSize(size: number) {
    pageSize.value = size
    page.value = 1
  }

  return {
    sort,
    enumFilters,
    page,
    pageSize,
    pageRows,
    total,
    filteredRows,
    filterChips,
    applySort,
    setEnumFilter,
    clearColumn,
    clearFilters,
    setPage,
    setPageSize,
  }
}
