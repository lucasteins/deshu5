/**
 * SQL 轻量 token 级着色（设计稿 §4.5.1「SQL 块」）——零依赖，供 DsSqlViewer 使用。
 *
 * 颜色语义（映射到设计 token，见 DsSqlViewer 的样式表）：
 *   keyword  关键字（SELECT/FROM/WHERE/…）→ --accent
 *   string   字符串字面量（'…'）→ --ember
 *   comment  注释（-- / *…* ）→ --text-4（斜体）
 *   number   数字 → --c2
 *   function 函数名（后接 `(`）→ --c4
 *   table    表名（FROM/JOIN/UPDATE/INTO 后首个标识符）→ --c1
 *   field    字段 / 别名（其余标识符）→ --text-1
 *   operator/punct/plain → 默认
 *
 * 说明：tokenizer 为「着色可读性」设计，不追求 SQL 语法完备（表名识别为启发式——
 * 紧随 FROM/JOIN/UPDATE/INTO 之后的第一个非关键字标识符，含 `schema.table` 前半段）。
 * 复杂语句若误判表/字段，仅影响颜色、不影响内容——遵循「不造假」原则，不做过度猜测。
 */

export type SqlTokenType =
  | 'keyword'
  | 'string'
  | 'comment'
  | 'number'
  | 'function'
  | 'table'
  | 'field'
  | 'operator'
  | 'punct'
  | 'plain'

export interface SqlToken {
  type: SqlTokenType
  text: string
}

const KEYWORDS = new Set([
  'SELECT', 'FROM', 'WHERE', 'JOIN', 'LEFT', 'RIGHT', 'INNER', 'OUTER', 'FULL', 'CROSS',
  'ON', 'AS', 'AND', 'OR', 'NOT', 'IN', 'LIKE', 'BETWEEN', 'IS', 'NULL', 'GROUP', 'BY',
  'ORDER', 'LIMIT', 'OFFSET', 'HAVING', 'UNION', 'ALL', 'DISTINCT', 'CASE', 'WHEN',
  'THEN', 'ELSE', 'END', 'INSERT', 'INTO', 'VALUES', 'UPDATE', 'SET', 'DELETE', 'CREATE',
  'TABLE', 'DROP', 'ALTER', 'WITH', 'EXISTS', 'CAST', 'ASC', 'DESC', 'IF', 'PARTITION',
  'OVER', 'ROWS', 'RANGE', 'PRECEDING', 'FOLLOWING', 'UNBOUNDED', 'CURRENT', 'ROW',
  'TRUE', 'FALSE', 'DEFAULT', 'PRIMARY', 'KEY', 'FOREIGN', 'REFERENCES', 'UNIQUE', 'INDEX',
])

/** 紧随其后出现的标识符视为「表名」的关键字（启发式） */
const TABLE_PRECEDING = new Set(['FROM', 'JOIN', 'UPDATE', 'INTO'])

/** JOIN 修饰词（LEFT/RIGHT/… JOIN），跳过它们继续找表名 */
const JOIN_MODIFIERS = new Set(['LEFT', 'RIGHT', 'INNER', 'OUTER', 'FULL', 'CROSS'])

type RawToken = { type: 'word' | 'string' | 'comment' | 'number' | 'punct' | 'ws'; text: string }

/** 第一遍：切出原始 token（单词 / 字符串 / 注释 / 数字 / 标点 / 空白） */
function lex(sql: string): RawToken[] {
  const tokens: RawToken[] = []
  const re =
    /(?<ws>\s+)|(?<comment>--[^\n]*|\/\*[\s\S]*?\*\/)|(?<string>'(?:''|[^'])*')|(?<number>\b\d+(?:\.\d+)?\b)|(?<word>[A-Za-z_][A-Za-z0-9_]*)|(?<punct>[^\sA-Za-z0-9_])/g
  let m: RegExpExecArray | null
  while ((m = re.exec(sql)) !== null) {
    const g = m.groups!
    if (g.ws) tokens.push({ type: 'ws', text: g.ws })
    else if (g.comment) tokens.push({ type: 'comment', text: g.comment })
    else if (g.string) tokens.push({ type: 'string', text: g.string })
    else if (g.number) tokens.push({ type: 'number', text: g.number })
    else if (g.word) tokens.push({ type: 'word', text: g.word })
    else if (g.punct) tokens.push({ type: 'punct', text: g.punct })
  }
  return tokens
}

/** 第二遍：对 word token 做关键字 / 函数 / 表名 / 字段分类 */
export function tokenizeSql(sql: string): SqlToken[] {
  const raw = lex(sql)
  const out: SqlToken[] = []
  let prevWord = '' // 上一个显著 word（大写），用于表名启发式
  let prevRawType = ''

  for (let i = 0; i < raw.length; i++) {
    const t = raw[i]
    if (t.type === 'ws') {
      // 空白合并进前一个 plain（若存在），否则单独保留以保持原文
      if (out.length && out[out.length - 1].type === 'plain') {
        out[out.length - 1] = { type: 'plain', text: out[out.length - 1].text + t.text }
      } else {
        out.push({ type: 'plain', text: t.text })
      }
      continue
    }
    if (t.type === 'comment') {
      out.push({ type: 'comment', text: t.text })
      prevRawType = 'comment'
      continue
    }
    if (t.type === 'string') {
      out.push({ type: 'string', text: t.text })
      prevRawType = 'string'
      continue
    }
    if (t.type === 'number') {
      out.push({ type: 'number', text: t.text })
      prevRawType = 'number'
      continue
    }
    if (t.type === 'punct') {
      out.push({ type: 'punct', text: t.text })
      if (t.text !== '.') prevRawType = 'punct'
      continue
    }
    // word
    const upper = t.text.toUpperCase()
    let type: SqlTokenType
    if (KEYWORDS.has(upper)) {
      type = 'keyword'
      if (!JOIN_MODIFIERS.has(upper)) prevWord = upper
    } else if (i + 1 < raw.length && raw[i + 1].type === 'punct' && raw[i + 1].text === '(') {
      type = 'function'
    } else if (TABLE_PRECEDING.has(prevWord) && prevRawType !== '.') {
      type = 'table'
      prevWord = '' // 只取 FROM/JOIN 后第一个标识符为表名，后续为字段/别名
    } else {
      type = 'field'
    }
    out.push({ type, text: t.text })
    prevRawType = 'word'
  }
  return out
}

/* ================= 轻量 SQL 美化（目验修复：单行 SQL → 子句换行，提升可读性） =================
 * 启发式：覆盖 SELECT / FROM / JOIN / WHERE / GROUP BY / ORDER BY 等常见子句；
 * CASE / 窗口函数等保持内联；字符串、注释原样保留；解析失败原样返回（保守兜底）。
 */

/** 子句关键字：换行顶格（随括号深度缩进） */
const CLAUSE_WORDS = new Set([
  'SELECT', 'FROM', 'WHERE', 'HAVING', 'LIMIT', 'OFFSET', 'UNION', 'EXCEPT', 'INTERSECT',
  'VALUES', 'SET', 'INSERT', 'UPDATE', 'DELETE',
])

/** 续行缩进关键字 */
const INDENT_WORDS = new Set(['AND', 'OR', 'ON'])

/** JOIN 词组起始词（LEFT [OUTER] JOIN / INNER JOIN / …） */
const JOIN_LEADS = new Set(['JOIN', 'LEFT', 'RIGHT', 'INNER', 'OUTER', 'FULL', 'CROSS'])

const upperOf = (t: RawToken | undefined): string =>
  t && t.type === 'word' ? t.text.toUpperCase() : ''

/** 非 ASCII 连写字符（CJK 等）：标识符内不插空格（如中文别名「客户编号」） */
const CJK_CHAR = /[⺀-鿿豈-﫿぀-ヿ가-힯！-｠]/

/** i 处是否 JOIN 词组起点；是则返回词组结束下标（含 JOIN），否则 -1 */
function joinEnd(sig: RawToken[], i: number): number {
  if (!JOIN_LEADS.has(upperOf(sig[i]))) return -1
  if (upperOf(sig[i]) === 'JOIN') return i
  if (upperOf(sig[i + 1]) === 'JOIN') return i + 1
  if (upperOf(sig[i + 1]) === 'OUTER' && upperOf(sig[i + 2]) === 'JOIN') return i + 2
  return -1
}

/** 轻量 SQL 美化（仅调整空白层，不改变语义） */
export function formatSql(sql: string): string {
  if (!sql || !sql.trim()) return sql
  try {
    const sig = lex(sql).filter((t) => t.type !== 'ws')
    if (!sig.length) return sql
    const lines: string[] = []
    let line = ''
    let depth = 0
    let between = false // BETWEEN … AND 的 AND 不换行
    let afterDot = false // '.' 后不补空格（schema.table）
    let windowDepth = -1 // OVER (…) 的括号深度：窗口内保持内联
    const parenOpenLines: number[] = [] // '(' 时行数快照：跨行括号的 ')' 另起一行

    const pad = (n: number) => '  '.repeat(Math.min(n, 3))
    function flush() {
      const l = line.replace(/\s+$/, '')
      if (l) lines.push(l)
      line = ''
      afterDot = false
    }
    function start(text: string, extra = 0) {
      flush()
      between = false
      line = pad(depth + extra) + text
    }
    function push(text: string) {
      if (!line) {
        line = pad(depth) + text
      } else if (afterDot) {
        line += text
        afterDot = false
      } else {
        const last = line[line.length - 1]
        if (CJK_CHAR.test(last) && CJK_CHAR.test(text[0])) line += text
        else line += last === ' ' || last === '(' ? text : ' ' + text
      }
    }

    for (let i = 0; i < sig.length; i++) {
      const t = sig[i]
      if (t.type === 'comment') {
        flush()
        lines.push(pad(depth) + t.text)
        continue
      }
      if (t.type === 'string' || t.type === 'number') {
        push(t.text)
        continue
      }
      if (t.type === 'word') {
        const w = upperOf(t)
        // GROUP BY / ORDER BY 作一个整体（窗口函数内保持内联）
        if ((w === 'GROUP' || w === 'ORDER') && upperOf(sig[i + 1]) === 'BY') {
          if (!(windowDepth !== -1 && depth >= windowDepth)) {
            start(`${t.text} ${sig[i + 1].text}`)
            i++
            continue
          }
        }
        const je = joinEnd(sig, i)
        if (je >= 0) {
          start(sig.slice(i, je + 1).map((x) => x.text).join(' '))
          i = je
          continue
        }
        if (CLAUSE_WORDS.has(w) && !afterDot) {
          start(t.text)
          continue
        }
        if (INDENT_WORDS.has(w)) {
          if (w === 'AND' && between) {
            between = false
            push(t.text)
            continue
          }
          start(t.text, 1)
          continue
        }
        if (w === 'BETWEEN') between = true
        push(t.text)
        continue
      }
      // punct
      const p = t.text
      if (CJK_CHAR.test(p)) {
        push(p)
        continue
      }
      if (p === ',') {
        line = line.replace(/\s+$/, '') + ','
        continue
      }
      if (p === '(') {
        const prev = sig[i - 1]
        const prevKw =
          !!prev && prev.type === 'word' && KEYWORDS.has(upperOf(prev)) && !JOIN_MODIFIERS.has(upperOf(prev))
        line = line.replace(/\s+$/, '')
        if (line && prevKw) line += ' '
        line += '('
        parenOpenLines.push(lines.length)
        if (upperOf(prev) === 'OVER') windowDepth = depth + 1
        depth++
        continue
      }
      if (p === ')') {
        const spanned = lines.length > (parenOpenLines.pop() ?? lines.length)
        depth = Math.max(0, depth - 1)
        if (windowDepth !== -1 && depth < windowDepth) windowDepth = -1
        if (spanned) start(')')
        else line = line.replace(/\s+$/, '') + ')'
        continue
      }
      if (p === '.') {
        line = line.replace(/\s+$/, '') + '.'
        afterDot = true
        continue
      }
      if (p === ';') {
        line = line.replace(/\s+$/, '') + ';'
        flush()
        continue
      }
      // 运算符：两侧留白（'(' / '.' 旁、复合运算符除外）
      line = line.replace(/\s+$/, '')
      const last = line[line.length - 1]
      if (line && /[<>=!]$/.test(last) && /[<>=!]/.test(p)) {
        line += p
        continue
      }
      if (line && last === p && (p === '|' || p === '&')) {
        line += p
        continue
      }
      if (line && last !== '(' && last !== '.') line += ' '
      line += p
    }
    flush()
    return lines.join('\n') || sql
  } catch {
    return sql // 保守兜底：解析失败原样展示
  }
}

/** 把一整段 SQL 渲染成 HTML（转义 + 着色）；供 v-html 使用 */
export function highlightSql(sql: string): string {
  const tokens = tokenizeSql(sql)
  let html = ''
  for (const t of tokens) {
    const esc = escapeHtml(t.text)
    if (t.type === 'plain') {
      html += esc
    } else {
      html += `<span class="sq-${t.type}">${esc}</span>`
    }
  }
  return html
}

/** 转义 HTML 特殊字符（不转义空白） */
export function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}
