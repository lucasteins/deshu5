/**
 * 思考事件渲染映射（设计稿 §4.5.1「思考过程」）——对齐旧实现 static/js/app.js#_renderThinkingEvent。
 *
 * 把后端 SSE 的 `thinking` 事件（QaThinking）映射为「思考窗口」的一条可渲染条目。
 * 纯函数，无副作用；`llm_stream`（逐字流）不在此映射，由消费方按 phase 累积后作为
 * `ThinkingStream` 传入 DsThinkingPanel 的 stream 槽。
 */
import type { QaThinking } from '@/types'

export type ThinkingTone = 'normal' | 'ok' | 'warn' | 'err' | 'dim'

export interface ThinkingEntry {
  /** 左侧标签，如「意图 / 定位 / 字段 / 码值 / LLM / 审计 / 修复 / 模板 / 草稿 / 审查 / 执行」 */
  tag: string
  /** 正文（纯文本） */
  body: string
  tone?: ThinkingTone
  /** 附加 chips（如定位到的表名） */
  chips?: string[]
  /** 附加子行（如「来源：…」） */
  sub?: string[]
}

/** LLM 逐字流（phase 相同连续追加；phase 切换 = 新起一行） */
export interface ThinkingStream {
  phase: 'reasoning' | 'output'
  text: string
}

function fmtMs(ms: number | undefined): string {
  if (ms == null) return ''
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`
}

const AUDIT_VERDICT: Record<string, string> = {
  pass: '审计通过',
  fixed: '采纳审计修正',
  unavailable: '审计不可用（按草稿放行）',
  fail_nosql: '审计不通过（未给修正）',
  fix_still_broken: '审计修正版仍不可执行',
  fix_invalid: '审计修正版未过校验',
}

const AUDIT_OK = new Set(['pass', 'fixed'])

export function thinkingToEntry(evt: QaThinking): ThinkingEntry | null {
  switch (evt.kind) {
    case 'intent': {
      const parts = [`类型=${evt.question_type || '查询'}`]
      if ((evt.tables ?? []).length) parts.push(`规则表=${evt.tables!.join('、')}`)
      const fields = (evt.fields ?? [])
        .map((f) => (f.agg ? `${f.agg}(${f.name || '*'})` : f.name || ''))
        .filter(Boolean)
      if (fields.length) parts.push(`字段=${fields.join('、')}`)
      const filters = (evt.filters ?? []).map((f) => `${f.field}${f.op}${f.value}`)
      if (filters.length) parts.push(`条件=${filters.join('、')}`)
      return { tag: '意图', body: parts.join(' · ') }
    }
    case 'tables': {
      const tables = evt.tables ?? []
      const src = evt.sources ?? {}
      const srcNames: Record<string, string> = { rule: '规则', llm: 'LLM', draft: '草稿' }
      const srcParts = Object.keys(srcNames)
        .filter((k) => ((src as Record<string, string[]>)[k] ?? []).length)
        .map((k) => `${srcNames[k]}: ${(src as Record<string, string[]>)[k].join('、')}`)
      const entry: ThinkingEntry = {
        tag: '定位',
        body: `合并定位 ${tables.length} 张表`,
        chips: tables,
      }
      if (srcParts.length) entry.sub = [`来源：${srcParts.join(' · ')}`]
      return entry
    }
    case 'columns': {
      const summary = evt.summary ?? []
      if (!summary.length) return { tag: '字段', body: '未注入字段上下文', tone: 'dim' }
      return { tag: '字段', body: summary.map((s) => `${s.table} 注入 ${s.shown}/${s.total} 列`).join(' · ') }
    }
    case 'code_values': {
      const items = evt.items ?? []
      if (!items.length) return { tag: '码值', body: '未命中码值域', tone: 'dim' }
      const body = items
        .map((d) => {
          const parts = [`${d.cn_name || ''}(${d.code_name || ''})`]
          if (d.form) parts.push(`形态=${d.form}`)
          if ((d.columns ?? []).length) parts.push(`落列=${d.columns!.join('、')}`)
          if ((d.matched ?? []).length) parts.push(`★命中=${d.matched!.join('、')}`)
          return parts.join(' · ')
        })
        .join(' ｜ ')
      return { tag: '码值', body }
    }
    case 'llm': {
      if (evt.phase === 'assemble')
        return { tag: 'LLM', body: `组装 prompt ${evt.prompt_chars} 字符 · 模型 ${evt.model || '-'} · 调用中…` }
      if (evt.phase === 'done')
        return { tag: 'LLM', body: `返回完成 · 耗时 ${fmtMs(evt.ms)} · SQL ${evt.sql_len} 字符`, tone: 'ok' }
      if (evt.phase === 'error') return { tag: 'LLM', body: `调用失败: ${evt.error || ''}`, tone: 'err' }
      return null
    }
    case 'audit_llm': {
      if (evt.phase === 'start')
        return {
          tag: '审计',
          body: `草稿试执行${evt.exec_ok ? `成功（${evt.row_count ?? 0} 行）` : `报错: ${evt.error || ''}`} · low 档合理性审计中…`,
        }
      const ok = AUDIT_OK.has(evt.verdict ?? '')
      const body =
        `${AUDIT_VERDICT[evt.verdict ?? ''] || evt.verdict || '-'}` +
        `${evt.reason ? ' · ' + evt.reason : ''}${evt.ms != null ? ' · ' + fmtMs(evt.ms) : ''}`
      return { tag: '审计', body, tone: ok ? 'ok' : 'err' }
    }
    case 'repair':
      return { tag: '修复', body: `第 ${evt.attempt} 轮定点修复 · 原因: ${evt.reason || ''}`, tone: 'err' }
    case 'template':
      return { tag: '模板', body: `命中签名模板 ${evt.name || ''}（${evt.via || ''}）`, tone: 'ok' }
    case 'draft':
      return {
        tag: '草稿',
        body:
          evt.mode === 'direct'
            ? `草稿直出 · SQL ${evt.sql_len} 字符`
            : `LLM 未过校验，草稿兜底 · SQL ${evt.sql_len} 字符`,
        tone: 'ok',
      }
    case 'review':
      return {
        tag: '审查',
        body:
          `第 ${evt.attempt} 次生成未通过（${evt.action || ''}）` +
          `${evt.message ? ': ' + evt.message : ''} · 重新生成`,
        tone: 'err',
      }
    case 'exec':
      return evt.status === 'success'
        ? { tag: '执行', body: `成功 · 返回 ${evt.row_count} 行 · ${fmtMs(evt.ms)}`, tone: 'ok' }
        : { tag: '执行', body: `失败: ${evt.error || ''}`, tone: 'err' }
    case 'audit':
      return {
        tag: '审计',
        body: `审查结论 ${evt.status || ''}${evt.post_audit ? ' · 已触发错题深度审计' : ''}`,
      }
    case 'llm_stream':
      // 逐字流单独处理（见 ThinkingStream），此处不产生离散条目
      return null
    default:
      return { tag: '?', body: '', tone: 'dim' }
  }
}
