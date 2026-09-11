/**
 * 错题集「对比视图」—— 生成 SQL vs 修正 SQL 的行级差异（设计稿 §4.5「4. 错题集」）
 *
 * 做法：先经 sql-formatter 美化（best-effort，失败回退原文），再做**行级 LCS 差异**，
 * 产出左右对齐的差异行（新增行 / 删除行 / 相同行），供 SqlCompare.vue 渲染
 * 「差异行高亮（--danger-soft / --success-soft 底）+ 新增/删除行标记」。
 *
 * 不引入新依赖：sql-formatter 已在 package.json；LCS 差异自研（零依赖）。
 */
import { format } from 'sql-formatter'

/** 单元格：一行在某一侧的表现 */
export interface DiffCell {
  /** 行内容；null 表示该侧无内容（对齐占位） */
  text: string | null
  /** same=两侧相同；del=仅存在于左侧（生成 SQL）；add=仅存在于右侧（修正 SQL） */
  kind: 'same' | 'del' | 'add'
  /** 该行在自身文本中的行号（1 起）；占位为 null */
  no: number | null
}

/** 左右对齐的一行 */
export interface DiffRow {
  left: DiffCell
  right: DiffCell
}

const SQL_FORMAT_OPTS = {
  language: 'mysql' as const,
  tabWidth: 2,
  keywordCase: 'upper' as const,
  linesBetweenQueries: 1,
}

function formatSql(sql: string): string {
  const trimmed = (sql || '').trim()
  if (!trimmed) return ''
  try {
    return format(trimmed, SQL_FORMAT_OPTS)
  } catch {
    return trimmed
  }
}

function splitLines(s: string): string[] {
  if (s === '') return []
  return s.split('\n')
}

/** LCS 动态规划矩阵（行级） */
function lcsMatrix(a: string[], b: string[]): number[][] {
  const n = a.length
  const m = b.length
  const dp: number[][] = Array.from({ length: n + 1 }, () => new Array<number>(m + 1).fill(0))
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1])
    }
  }
  return dp
}

/** 计算左右对齐的差异行 */
export function diffSqlLines(oldSql: string, newSql: string): DiffRow[] {
  const a = splitLines(formatSql(oldSql))
  const b = splitLines(formatSql(newSql))
  const dp = lcsMatrix(a, b)
  const rows: DiffRow[] = []
  let i = 0
  let j = 0
  while (i < a.length || j < b.length) {
    if (i < a.length && j < b.length && a[i] === b[j]) {
      rows.push({
        left: { text: a[i], kind: 'same', no: i + 1 },
        right: { text: b[j], kind: 'same', no: j + 1 },
      })
      i++
      j++
    } else if (j < b.length && (i >= a.length || dp[i][j + 1] >= dp[i + 1][j])) {
      // 右侧新增（修正 SQL 有、生成 SQL 无）
      rows.push({
        left: { text: null, kind: 'add', no: null },
        right: { text: b[j], kind: 'add', no: j + 1 },
      })
      j++
    } else if (i < a.length) {
      // 左侧删除（生成 SQL 有、修正 SQL 无）
      rows.push({
        left: { text: a[i], kind: 'del', no: i + 1 },
        right: { text: null, kind: 'del', no: null },
      })
      i++
    } else {
      break
    }
  }
  return rows
}

/** 差异统计：删除行数 / 新增行数 / 相同行数 */
export function diffStats(rows: DiffRow[]): { removed: number; added: number; same: number } {
  let removed = 0
  let added = 0
  let same = 0
  for (const r of rows) {
    if (r.left.kind === 'del') removed += 1
    if (r.right.kind === 'add') added += 1
    if (r.left.kind === 'same') same += 1
  }
  return { removed, added, same }
}
