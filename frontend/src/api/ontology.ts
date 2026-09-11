/**
 * 本体模型 API（F2.3，对应 modules/ontology/routes.py，前缀 /api/ontology）
 *
 * 端点清单（契约以 routes.py 为准）：
 *   GET  /summary                      总览计数 + 版本 + 漂移状态
 *   GET  /classes                      物理表（类）列表（?kind=dimension|fact|other）
 *   GET  /classes/<name>               类详情（属性 / 出入关系 / 落列码值）
 *   GET  /relations                    关系列表（?source=physical_fk|governance_doc|both）
 *   GET  /enumerations                 码值域列表（?code_name= 单域详情）
 *   GET  /concepts                     业务概念 + 同义词组
 *   GET  /entities                     实体列表（?layer=master|business|report）
 *   GET  /entities/<name>              实体详情（成员表 / 实体级关系 / 映射定义）
 *   GET  /entity-relations             实体间关系（表级关系聚合推导）
 *   GET  /entity-defs                  实体映射定义（可编辑规则）
 *   POST /entity-defs/<name>/describe  LLM 生成实体描述
 *   PUT  /entity-defs/<name>           新增 / 更新实体映射定义
 *   DELETE /entity-defs/<name>         逻辑删除实体映射定义
 *   GET  /export?format=...            导出本体（文件下载，走 window.open）
 *   GET  /drift                        跑一次漂移检测（可能自动生成提案）
 *   GET  /proposals                    提案列表
 *   GET  /proposals/<pid>              提案详情（含 diff 明细）
 *   POST /proposals/<pid>/approve      批准提案（快照落为新生效版本）
 *   POST /proposals/<pid>/reject       驳回提案
 *   POST /rebuild                      手动全量重建（生成提案，仍走审批）
 */
import { api } from './client'

/* ==================== 总览 ==================== */

export interface OntologyMeta {
  version: number
  base_fingerprint: string
  built_at: string
  summary?: Record<string, unknown>
}

export interface OntologySummaryInfo {
  version: number
  built_at: string
  base_fingerprint: string
  classes: number
  properties: number
  relations: number
  enumerations: number
  concepts: number
  entities: number
  entities_by_layer: Record<string, number>
  synonym_groups: number
}

export interface OntologySummaryResponse {
  success: boolean
  available: boolean
  meta: OntologyMeta | null
  summary?: OntologySummaryInfo
  drift?: boolean
  pending_proposal?: number | null
  drift_error?: string
  error?: string
}

/* ==================== 物理表（类）/ 属性 / 关系 ==================== */

export interface OntologyClass {
  name: string
  label: string
  kind: string
  comment: string
  property_count?: number
}

export interface OntologyClassesResponse {
  success: boolean
  items: OntologyClass[]
  total: number
  error?: string
}

export interface OntologyProperty {
  class_name: string
  name: string
  label: string
  data_type: string
  xsd_type: string
  is_pk: boolean
}

export interface OntologyRelation {
  from_class: string
  to_class: string
  join_conditions: string[]
  business_scenarios: string[]
  source: string
}

export interface ClassEnumerationRef {
  code_name: string
  cn_name: string
  column: string
  form?: string
}

export interface OntologyClassDetailResponse {
  success: boolean
  class: OntologyClass
  properties: OntologyProperty[]
  relations: OntologyRelation[]
  enumerations: ClassEnumerationRef[]
  error?: string
}

export interface OntologyRelationsResponse {
  success: boolean
  items: OntologyRelation[]
  total: number
  error?: string
}

/* ==================== 码值枚举 ==================== */

export interface EnumerationColumnRef {
  table: string
  column: string
  form?: string
}

export interface OntologyEnumerationSummary {
  code_name: string
  cn_name: string
  domain: string
  item_count: number
  column_refs: EnumerationColumnRef[]
}

export interface OntologyEnumerationsResponse {
  success: boolean
  items: OntologyEnumerationSummary[]
  total: number
  error?: string
}

export interface OntologyEnumeration {
  code_name: string
  cn_name: string
  domain_l1: string
  domain_l2: string
  domain_l3: string
  items: { code: string; name: string; sort?: number }[]
  column_refs: EnumerationColumnRef[]
}

export interface OntologyEnumerationDetailResponse {
  success: boolean
  item: OntologyEnumeration
  error?: string
}

/* ==================== 业务概念 ==================== */

export interface OntologyConcept {
  concept: string
  maps_to: string[]
  alt_labels: string[]
}

export interface OntologyConceptsResponse {
  success: boolean
  items: OntologyConcept[]
  total: number
  synonym_groups: Record<string, string[]>
  error?: string
}

/* ==================== 实体层（精炼设计） ==================== */

export type OntologyLayer = 'master' | 'business' | 'report'

export interface OntologyEntity {
  name: string
  label: string
  layer: OntologyLayer | string
  member_tables: string[]
  parent: string
  comment: string
  member_count?: number
}

export interface OntologyEntitiesResponse {
  success: boolean
  items: OntologyEntity[]
  total: number
  error?: string
}

export interface EntityMember {
  table: string
  label: string
  exists: boolean
}

export interface EntityRelation {
  from_entity: string
  to_entity: string
  member_relations: string[]
  scenarios: string[]
  sources: string[]
}

export interface OntologyEntityRelationsResponse {
  success: boolean
  items: EntityRelation[]
  total: number
  error?: string
}

export interface OntologyEntityDetailResponse {
  success: boolean
  entity: OntologyEntity
  def: OntologyEntityDef | null
  members: EntityMember[]
  entity_relations: EntityRelation[]
  error?: string
}

export interface OntologyEntityDef {
  name: string
  label: string
  layer: OntologyLayer | string
  parent: string
  member_tables: string[]
  comment: string
}

export interface OntologyEntityDefsResponse {
  success: boolean
  items: OntologyEntityDef[]
  total: number
  error?: string
}

export interface DescribeEntityResponse {
  success: boolean
  name: string
  comment: string
  applied: boolean
  note: string
  error?: string
}

export interface EntityDefMutationResponse {
  success: boolean
  note?: string
  error?: string
}

/* ==================== 提案 / 漂移 / 重建 ==================== */

export interface DiffCounts {
  classes_added?: number
  classes_removed?: number
  classes_changed?: number
  properties_added?: number
  properties_removed?: number
  properties_changed?: number
  relations_added?: number
  relations_removed?: number
  relations_changed?: number
  enumerations_added?: number
  enumerations_removed?: number
  enumerations_items_changed?: number
  concepts_added?: number
  concepts_removed?: number
  concepts_changed?: number
  entities_added?: number
  entities_removed?: number
  entities_changed?: number
}

export interface DiffPropertyChange {
  name: string
  old: Record<string, unknown>
  new: Record<string, unknown>
}

export interface DiffRelationChange {
  key: string
  old: Record<string, unknown>
  new: Record<string, unknown>
}

export interface DiffEntityChange {
  name: string
  old: Record<string, unknown>
  new: Record<string, unknown>
}

export interface OntologyDiff {
  counts?: DiffCounts
  classes?: { added?: string[]; removed?: string[]; changed?: string[] }
  properties?: {
    added?: string[]
    removed?: string[]
    changed?: DiffPropertyChange[]
  }
  relations?: {
    added?: string[]
    removed?: string[]
    changed?: DiffRelationChange[]
  }
  enumerations?: {
    added?: string[]
    removed?: string[]
    items_changed?: number
  }
  concepts?: { added?: string[]; removed?: string[]; changed?: string[] }
  entities?: {
    added?: string[]
    removed?: string[]
    changed?: DiffEntityChange[]
  }
}

export interface Proposal {
  id: number
  base_fingerprint: string
  status: 'pending' | 'approved' | 'rejected' | string
  counts: DiffCounts
  created_at: string
  decided_at?: string
}

export interface ProposalsResponse {
  success: boolean
  items: Proposal[]
  error?: string
}

export interface ProposalDetailResponse extends Proposal {
  success: boolean
  diff: OntologyDiff
  error?: string
}

export interface DriftResponse {
  success?: boolean
  drift: boolean
  fingerprint?: string
  active_version?: number | null
  proposal_id?: number
  diff?: OntologyDiff
  note?: string
  error?: string
}

export interface RebuildResponse {
  success?: boolean
  proposal_id: number
  diff: OntologyDiff
  fingerprint: string
  error?: string
}

export interface ApproveResponse {
  success: boolean
  version: number
  summary: OntologySummaryInfo
  error?: string
}

export interface RejectResponse {
  success: boolean
  error?: string
}

/* ==================== 函数 ==================== */

export function fetchOntologySummary(): Promise<OntologySummaryResponse> {
  return api.get<OntologySummaryResponse>('/ontology/summary')
}

export function fetchOntologyClasses(kind?: string): Promise<OntologyClassesResponse> {
  return api.get<OntologyClassesResponse>('/ontology/classes', kind ? { kind } : undefined)
}

export function fetchOntologyClass(name: string): Promise<OntologyClassDetailResponse> {
  return api.get<OntologyClassDetailResponse>(
    `/ontology/classes/${encodeURIComponent(name)}`,
  )
}

export function fetchOntologyRelations(source?: string): Promise<OntologyRelationsResponse> {
  return api.get<OntologyRelationsResponse>(
    '/ontology/relations',
    source ? { source } : undefined,
  )
}

export function fetchOntologyEnumerations(): Promise<OntologyEnumerationsResponse> {
  return api.get<OntologyEnumerationsResponse>('/ontology/enumerations')
}

export function fetchOntologyEnumeration(
  codeName: string,
): Promise<OntologyEnumerationDetailResponse> {
  return api.get<OntologyEnumerationDetailResponse>('/ontology/enumerations', {
    code_name: codeName,
  })
}

export function fetchOntologyConcepts(): Promise<OntologyConceptsResponse> {
  return api.get<OntologyConceptsResponse>('/ontology/concepts')
}

export function fetchOntologyEntities(layer?: string): Promise<OntologyEntitiesResponse> {
  return api.get<OntologyEntitiesResponse>(
    '/ontology/entities',
    layer ? { layer } : undefined,
  )
}

export function fetchOntologyEntity(name: string): Promise<OntologyEntityDetailResponse> {
  return api.get<OntologyEntityDetailResponse>(
    `/ontology/entities/${encodeURIComponent(name)}`,
  )
}

export function fetchOntologyEntityRelations(): Promise<OntologyEntityRelationsResponse> {
  return api.get<OntologyEntityRelationsResponse>('/ontology/entity-relations')
}

export function fetchOntologyEntityDefs(): Promise<OntologyEntityDefsResponse> {
  return api.get<OntologyEntityDefsResponse>('/ontology/entity-defs')
}

export function describeEntity(
  name: string,
  apply = false,
): Promise<DescribeEntityResponse> {
  return api.post<DescribeEntityResponse>(
    `/ontology/entity-defs/${encodeURIComponent(name)}/describe`,
    { apply },
  )
}

export interface EntityDefPayload {
  label?: string
  layer?: string
  parent?: string
  member_tables?: string[]
  comment?: string
  enabled?: number
}

export function upsertEntityDef(
  name: string,
  payload: EntityDefPayload,
): Promise<EntityDefMutationResponse> {
  return api.put<EntityDefMutationResponse>(
    `/ontology/entity-defs/${encodeURIComponent(name)}`,
    payload,
  )
}

export function deleteEntityDef(name: string): Promise<EntityDefMutationResponse> {
  return api.delete<EntityDefMutationResponse>(
    `/ontology/entity-defs/${encodeURIComponent(name)}`,
  )
}

/** 导出走浏览器新窗口（文件下载），不需要 axios 处理。 */
export function ontologyExportUrl(format: string): string {
  // 与 axios baseURL 一致走同源 /api（dev 由 Vite 代理到 5050；生产 Flask 同源）。
  return `/api/ontology/export?format=${encodeURIComponent(format)}`
}

export function checkDrift(): Promise<DriftResponse> {
  return api.get<DriftResponse>('/ontology/drift')
}

export function fetchProposals(): Promise<ProposalsResponse> {
  return api.get<ProposalsResponse>('/ontology/proposals')
}

export function fetchProposal(pid: number): Promise<ProposalDetailResponse> {
  return api.get<ProposalDetailResponse>(`/ontology/proposals/${pid}`)
}

export function approveProposal(pid: number): Promise<ApproveResponse> {
  return api.post<ApproveResponse>(`/ontology/proposals/${pid}/approve`)
}

export function rejectProposal(pid: number): Promise<RejectResponse> {
  return api.post<RejectResponse>(`/ontology/proposals/${pid}/reject`)
}

export function rebuildOntology(): Promise<RebuildResponse> {
  return api.post<RebuildResponse>('/ontology/rebuild')
}
