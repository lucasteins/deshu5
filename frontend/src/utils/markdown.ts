/**
 * 轻量 Markdown 渲染（无外部依赖）——自 ReportView.vue 抽出，供报告页 / 技能对话复用。
 *
 * 支持：h1-h4 / 管道表格 / 无序·有序列表 / hr / 段落 / 行内 **加粗** 与 `代码`；
 * 全文先 escapeHtml 再做行内替换，无注入面。表格输出 class="rp-table"，样式由消费方提供。
 */

function escapeHtml(s: string): string {
  return String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

function renderInline(s: string): string {
  return escapeHtml(s)
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
}

export function renderMarkdown(md: string): string {
  if (!md) return ''
  const lines = md.replace(/\r\n/g, '\n').split('\n')
  let html = ''
  let i = 0
  while (i < lines.length) {
    const line = lines[i]
    if (/^\s*\|.*\|\s*$/.test(line) && i + 1 < lines.length && /^\s*\|[\s\-:|]+\|\s*$/.test(lines[i + 1])) {
      const headers = line.split('|').slice(1, -1).map((c) => c.trim())
      i += 2
      const rowCells: string[][] = []
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) {
        rowCells.push(lines[i].split('|').slice(1, -1).map((c) => c.trim()))
        i++
      }
      html += `<table class="rp-table"><thead><tr><th>${headers.map(renderInline).join('</th><th>')}</th></tr></thead><tbody>${rowCells
        .map((cells) => `<tr>${cells.map((c) => `<td>${renderInline(c)}</td>`).join('')}</tr>`)
        .join('')}</tbody></table>`
      continue
    }
    const h = line.match(/^(#{1,4})\s+(.*)$/)
    if (h) {
      const lv = h[1].length
      html += `<h${lv}>${renderInline(h[2])}</h${lv}>`
      i++
      continue
    }
    if (/^\s*([-*+]|\d+\.)\s+/.test(line)) {
      const ordered = /^\s*\d+\.\s+/.test(line)
      html += ordered ? '<ol>' : '<ul>'
      while (i < lines.length && /^\s*([-*+]|\d+\.)\s+/.test(lines[i])) {
        html += `<li>${renderInline(lines[i].replace(/^\s*([-*+]|\d+\.)\s+/, ''))}</li>`
        i++
      }
      html += ordered ? '</ol>' : '</ul>'
      continue
    }
    if (/^\s*---+\s*$/.test(line)) {
      html += '<hr>'
      i++
      continue
    }
    if (line.trim() === '') {
      i++
      continue
    }
    html += `<p>${renderInline(line)}</p>`
    i++
  }
  return html
}
