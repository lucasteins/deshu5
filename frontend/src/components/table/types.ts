/**
 * 统一表格组件类型（设计稿 §4.4.2）
 *
 * 能力范围（F1.1 首落）：三态排序（升 → 降 → 取消）/ 列头漏斗筛选（枚举，
 * 同列多值为 OR、跨列 AND）/ 分页 / 筛选 chips / 密度切换 / 行展开。
 * 暂未实现（标注于交接日志，F2 按需补）：列控制抽屉 / 行选择与批量 / 键盘导航 / URL 状态同步。
 */

/** 排序状态（key = 列键，order = 方向） */
export interface DsSort {
  key: string
  order: 'asc' | 'desc'
}

/** 枚举筛选可选项 */
export interface DsFilterOption {
  value: string
  label: string
}

/** 列头漏斗筛选定义（v1 仅枚举勾选；数值区间/日期预设等按需扩展） */
export interface DsEnumFilter {
  type: 'enum'
  options: DsFilterOption[]
}

export type DsFilterDef = DsEnumFilter

/** 列定义 */
export interface DsColumn<Row = Record<string, unknown>> {
  /** 列键：缺省渲染 row[key]，排序/筛选也以它关联 */
  key: string
  label: string
  width?: number
  minWidth?: number
  align?: 'left' | 'center' | 'right'
  /** 参与排序（三态：升 → 降 → 取消） */
  sortable?: boolean
  /** 排序取值：'number' 按数字；缺省按字符串（中文 localeCompare 拼音序） */
  sortType?: 'string' | 'number'
  /** 列头漏斗筛选 */
  filter?: DsFilterDef
  /** 固定列 */
  fixed?: 'left' | 'right'
  /** 单元格自定义插槽名（`#cell-<slot>`）；缺省直接渲染取值文本 */
  slot?: string
  /** 取值访问器（排序/筛选/默认渲染共用；缺省取 row[key]） */
  value?: (row: Row) => string | number
}

/** 已应用的枚举筛选（列键 → 选中值数组） */
export type DsEnumFilters = Record<string, string[]>

/** 筛选条 chip（每列一个，label 如「难度 = 基础题/进阶题」） */
export interface DsFilterChip {
  key: string
  label: string
}
