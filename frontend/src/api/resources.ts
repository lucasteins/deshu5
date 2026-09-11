/**
 * 数据资源 API（F2.2，对应 modules/resources/routes.py 端点）
 *
 * - GET  /api/schema-graph         → 表节点 + 主外键关系边（启动预加载，零额外查询）
 * - GET  /api/table-columns        → 单表字段 + 关联关系（?table=）
 * - GET  /api/code-value-domains   → 码值域清单（含明细数与落列映射）
 * - GET  /api/code-value-items     → 单域码值明细（?code_name=）
 * - /api/resources                 → 资源类型摘要（name/label/count/description）
 * - /api/resources/<rtype>/items   → 条目分页列表（?limit=&offset=&q=）
 * - /api/resources/<rtype>/items/<id> → 单条 / PUT 改 / DELETE 删
 * - /api/resources/<rtype>/items   → POST 新建（body 为条目 JSON）
 * - /api/resources/<rtype>/import  → POST 批量导入（multipart 文件 或 JSON {rows}）
 * - /api/resources/<rtype>/template → 下载导入模板（entry_schema + example_rows）
 *
 * 说明：GET /api/schema 返回全库表/列/主外键/行数，旧实现（static/js/resource.js）的
 * 目录模式只消费 schema-graph + table-columns，未用该端点；此处按「不重造」仅实现
 * 实际消费方，F2.3 本体模型若需全量 schema 可再补 fetchSchema()。
 */
import { api } from './client'

/* ==================== Schema 图谱 / 表详情 ==================== */

/** 表节点（来自 /api/schema-graph） */
export interface SchemaNode {
  name: string
  comment: string
  /** dim（维表）/ dwd（事实表）/ other */
  layer: string
  column_count: number
  pk: string[]
  rel_count: number
}

/** 主外键关系边（join_conditions 为关系条件列表） */
export interface SchemaEdge {
  from: string
  to: string
  join_conditions: string[]
}

export interface SchemaGraphResponse {
  success: boolean
  nodes: SchemaNode[]
  edges: SchemaEdge[]
  error?: string
}

/** 单表字段 */
export interface TableColumn {
  name: string
  type: string
  pk?: boolean
  comment?: string
}

/** 单表关联关系（other = 相邻表） */
export interface TableRelationship {
  table: string
  comment: string
  join_conditions: string[]
}

export interface TableColumnsResponse {
  success: boolean
  table: string
  comment: string
  columns: TableColumn[]
  relationships: TableRelationship[]
  error?: string
}

/* ==================== 码值库 ==================== */

/** 码值域（含明细数与落列映射） */
export interface CodeDomain {
  code_name: string
  cn_name: string
  domain: string
  item_count: number
  columns: string[]
}

export interface CodeDomainsResponse {
  success: boolean
  total: number
  items: CodeDomain[]
  error?: string
}

/** 单域码值明细项 */
export interface CodeItem {
  code: string
  name: string
  sort_order: number
}

export interface CodeItemsResponse {
  success: boolean
  code_name: string
  items: CodeItem[]
  error?: string
}

/* ==================== 资源管理（通用资源 REST） ==================== */

/** 资源类型摘要（/api/resources） */
export interface ResourceSummary {
  name: string
  label: string
  description: string
  count: number | null
}

export interface ResourcesSummaryResponse {
  success: boolean
  items: ResourceSummary[]
  error?: string
}

/** entry_schema 字段类型（modules/resources/base.py#_FIELD_TYPES） */
export type EntryFieldType = 'str' | 'int' | 'bool' | 'list' | 'dict'

/** 录入模板字段说明 */
export interface EntrySchemaField {
  field: string
  type: EntryFieldType
  required?: boolean
}

/** 导入模板（/api/resources/<rtype>/template） */
export interface ResourceTemplate {
  resource: string
  label: string
  description: string
  entry_schema: EntrySchemaField[]
  example_rows: Record<string, unknown>[]
}

export interface ResourceTemplateResponse {
  success: boolean
  template: ResourceTemplate
  error?: string
}

/** 资源条目（结构随 entry_schema 变化，用宽松类型承载） */
export type ResourceItem = Record<string, unknown>

export interface ResourceItemsResponse {
  success: boolean
  total: number
  limit: number
  offset: number
  items: ResourceItem[]
  error?: string
}

export interface ResourceItemResponse {
  success: boolean
  item: ResourceItem
  error?: string
}

export interface ResourceMutationResponse {
  success: boolean
  item?: ResourceItem
  message?: string
  error?: string
  errors?: string[]
}

/** 批量导入报告（import_rows 返回） */
export interface ImportRejectedRow {
  row: Record<string, unknown>
  reason: string
}

export interface ImportResponse {
  success: boolean
  accepted: number
  rejected: ImportRejectedRow[]
  error?: string
}

/* ==================== 函数 ==================== */

export function fetchSchemaGraph(): Promise<SchemaGraphResponse> {
  return api.get<SchemaGraphResponse>('/schema-graph')
}

export function fetchTableColumns(table: string): Promise<TableColumnsResponse> {
  return api.get<TableColumnsResponse>('/table-columns', { table })
}

export function fetchCodeDomains(): Promise<CodeDomainsResponse> {
  return api.get<CodeDomainsResponse>('/code-value-domains')
}

export function fetchCodeItems(codeName: string): Promise<CodeItemsResponse> {
  return api.get<CodeItemsResponse>('/code-value-items', { code_name: codeName })
}

export function fetchResourcesSummary(): Promise<ResourcesSummaryResponse> {
  return api.get<ResourcesSummaryResponse>('/resources')
}

/**
 * 资源条目列表（服务端分页 + q 子串过滤）。
 * F2.2 复用 DsDataTable 的客户端分页管道，故取「全量」一次拉回（内部工具量级 ≤千行，
 * 后端 list 默认 limit 200；此处按 1000 取全量以交给客户端分页）。
 */
export function fetchResourceItems(
  rtype: string,
  q = '',
  limit = 1000,
  offset = 0,
): Promise<ResourceItemsResponse> {
  return api.get<ResourceItemsResponse>(`/resources/${rtype}/items`, { limit, offset, q })
}

export function fetchResourceItem(
  rtype: string,
  itemId: string,
): Promise<ResourceItemResponse> {
  return api.get<ResourceItemResponse>(
    `/resources/${rtype}/items/${encodeURIComponent(itemId)}`,
  )
}

export function createResourceItem(
  rtype: string,
  payload: ResourceItem,
): Promise<ResourceMutationResponse> {
  return api.post<ResourceMutationResponse>(`/resources/${rtype}/items`, payload)
}

export function updateResourceItem(
  rtype: string,
  itemId: string,
  payload: ResourceItem,
): Promise<ResourceMutationResponse> {
  return api.put<ResourceMutationResponse>(
    `/resources/${rtype}/items/${encodeURIComponent(itemId)}`,
    payload,
  )
}

export function deleteResourceItem(
  rtype: string,
  itemId: string,
): Promise<ResourceMutationResponse> {
  return api.delete<ResourceMutationResponse>(
    `/resources/${rtype}/items/${encodeURIComponent(itemId)}`,
  )
}

export function fetchResourceTemplate(rtype: string): Promise<ResourceTemplateResponse> {
  return api.get<ResourceTemplateResponse>(`/resources/${rtype}/template`)
}

/** 批量导入（multipart 文件，.json / .csv / .xlsx）；axios 对 FormData 自动设边界头 */
export function importResourceItems(rtype: string, file: File): Promise<ImportResponse> {
  const fd = new FormData()
  fd.append('file', file)
  return api.post<ImportResponse>(`/resources/${rtype}/import`, fd)
}
