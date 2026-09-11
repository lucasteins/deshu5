/**
 * 统计看板时间聚合（F1.2，纯函数）
 *
 * 数据源：问答对 `ingest_time`（「Thu, 10 Sep 2026 15:35:20 GMT」，实为本地时间）。
 * 用途：/api/stats 为快照、不含历史序列，看板「入库趋势」与「入库活跃度」两图由此聚合。
 * 所有函数不依赖 DOM，便于单测。
 */
import { normalizeQaTime, type QaPair } from '@/api/qa'

const WEEK_MS = 7 * 86400_000

/**
 * 解析后端时间串为本地 Date（不做事区换算，与 normalizeQaTime 口径一致）。
 * 非法串返回 null。
 */
export function parseLocalDate(raw: string | null | undefined): Date | null {
  const s = normalizeQaTime(raw)
  const m = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})$/.exec(s)
  if (!m) return null
  const d = new Date(
    Number(m[1]),
    Number(m[2]) - 1,
    Number(m[3]),
    Number(m[4]),
    Number(m[5]),
    Number(m[6]),
  )
  return Number.isNaN(d.getTime()) ? null : d
}

/** ISO 周编号（周一为一周之始） */
export function isoWeek(d: Date): { year: number; week: number } {
  const date = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()))
  const dayNum = (date.getUTCDay() + 6) % 7 // 周一 = 0
  date.setUTCDate(date.getUTCDate() - dayNum + 3) // 本周四
  const firstThursday = new Date(Date.UTC(date.getUTCFullYear(), 0, 4))
  const firstDayNum = (firstThursday.getUTCDay() + 6) % 7
  firstThursday.setUTCDate(firstThursday.getUTCDate() - firstDayNum + 3)
  const week =
    1 + Math.round((date.getTime() - firstThursday.getTime()) / WEEK_MS)
  return { year: date.getUTCFullYear(), week }
}

function isoKey(d: Date): string {
  const { year, week } = isoWeek(d)
  return `${year}-W${String(week).padStart(2, '0')}`
}

/** 某日期所在周的周一 00:00 */
function startOfWeek(d: Date): Date {
  const x = new Date(d.getFullYear(), d.getMonth(), d.getDate())
  const dayNum = (x.getDay() + 6) % 7 // 周一 = 0
  x.setDate(x.getDate() - dayNum)
  return x
}

export interface WeekBucket {
  key: string
  /** x 轴标签：ISO 周（如 W36） */
  label: string
  /** 该周新增问答对 */
  total: number
  /** 其中可用（is_usable = 1） */
  usable: number
}

/**
 * 近 `weeks` 周（含本周）逐周聚合。
 * 无数据的周补 0，保证 x 轴连续、趋势可读。
 */
export function weeklyBuckets(rows: QaPair[], weeks = 12, now = new Date()): WeekBucket[] {
  const thisMonday = startOfWeek(now)
  const buckets: WeekBucket[] = []
  const index = new Map<string, WeekBucket>()

  for (let i = weeks - 1; i >= 0; i -= 1) {
    const monday = new Date(thisMonday.getTime() - i * WEEK_MS)
    const key = isoKey(monday)
    const bucket: WeekBucket = { key, label: `W${isoWeek(monday).week}`, total: 0, usable: 0 }
    buckets.push(bucket)
    index.set(key, bucket)
  }

  for (const row of rows) {
    const d = parseLocalDate(row.ingest_time)
    if (!d) continue
    const bucket = index.get(isoKey(d))
    if (!bucket) continue
    bucket.total += 1
    if (row.is_usable === 1) bucket.usable += 1
  }

  return buckets
}

export const WEEKDAYS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'] as const

export interface HeatResult {
  /** 7×24 计数矩阵（行 = 周一..周日） */
  matrix: number[][]
  max: number
  peak: { day: number; hour: number; count: number } | null
}

/** 按「星期 × 小时」聚合（周一为第 0 行） */
export function heatMatrix(rows: QaPair[]): HeatResult {
  const matrix: number[][] = Array.from({ length: 7 }, () => Array.from({ length: 24 }, () => 0))
  let max = 0
  let peak: HeatResult['peak'] = null

  for (const row of rows) {
    const d = parseLocalDate(row.ingest_time)
    if (!d) continue
    const day = (d.getDay() + 6) % 7
    const hour = d.getHours()
    const next = matrix[day][hour] + 1
    matrix[day][hour] = next
    if (next > max) {
      max = next
      peak = { day, hour, count: next }
    }
  }

  return { matrix, max, peak }
}
