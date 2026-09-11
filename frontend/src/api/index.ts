// 类型化 API 层出口：axios 统一实例 + 动词糖（03-迁移方案 §3.1）
// 按模块的端点函数（training / resources / provision / ontology / report / settings）
// 自 F1 起随各模块实现补入（api/<module>.ts）。
export * from './client'
export * from './qa'
export * from './stats'
export * from './resources'
export * from './chat'
export * from './training'
export * from './errors'
export * from './settings'
export * from './provision'
export * from './report'
export * from './ontology'
