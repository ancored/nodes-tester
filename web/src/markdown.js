// Markdown справки → HTML. Подмножество, которым написаны docs/help: заголовки, абзацы, списки
// (с вложением), таблицы, цитаты, код, схемы mermaid (готовым SVG), ссылки, якоря <a id="…">.
// Весь текст экранируется; из HTML допускается только якорь <a id>.

const esc = s => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')

// Якорь заголовка, как его строит GitHub (и tests/test_docs.py).
export const slug = text => text.toLowerCase().replace(/[^\p{L}\p{N}_\- ]/gu, '').replace(/ /g, '-')

// Ключ схемы: хэш её текста (cyrb53). Тем же ключом скрипт scripts/diagrams.mjs называет SVG.
export function diagramKey(code) {
  let h1 = 0xdeadbeef, h2 = 0x41c6ce57
  for (const ch of code.trim()) {
    const c = ch.codePointAt(0)
    h1 = Math.imul(h1 ^ c, 2654435761); h2 = Math.imul(h2 ^ c, 1597334677)
  }
  h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909)
  h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909)
  return (h2 >>> 0).toString(16).padStart(8, '0') + (h1 >>> 0).toString(16).padStart(8, '0')
}

// Схемы документа: [{key, code}].
export function diagrams(text) {
  return [...text.matchAll(/^```mermaid\n([\s\S]*?)^```/gm)].map(m => ({ key: diagramKey(m[1]), code: m[1] }))
}

const LANG_SWITCH = /^(\*\*English\*\* \| \[Русский\]|\[English\]\(.*\) \| \*\*Русский\*\*)/  // i18n-skip: строка перехода между языками

// link(href) → {href, attrs} — куда ведёт ссылка (страница справки, GitHub, внешний сайт).
function inline(text, link) {
  const codes = []
  let s = esc(text).replace(/`([^`]+)`/g, (_, c) => `\u0000${codes.push(c) - 1}\u0000`)
  s = s.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_, label, href) => {
    const target = link(href.replace(/&amp;/g, '&'))
    return `<a href="${esc(target.href)}"${target.attrs || ''}>${label}</a>`
  })
  s = s.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^\w*])\*([^*\s][^*]*?)\*(?![\w*])/g, '$1<em>$2</em>')
    .replace(/(^|[^\w])_([^_\s][^_]*?)_(?!\w)/g, '$1<em>$2</em>')
    .replace(/&lt;br&gt;/g, '<br>')
  return s.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${codes[i]}</code>`)
}

const cells = line => line.trim().replace(/^\||\|$/g, '').split(/(?<!\\)\|/).map(c => c.trim().replace(/\\\|/g, '|'))
const isTableRule = line => /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$/.test(line)
const LIST = /^(\s*)([-*]|\d+\.)\s+(.*)$/

// Список с вложением по отступу.
function list(lines, start, render) {
  const items = [], base = LIST.exec(lines[start])[1].length, ordered = /\d/.test(LIST.exec(lines[start])[2])
  let i = start
  while (i < lines.length) {
    const m = LIST.exec(lines[i])
    if (m && m[1].length === base) { items.push([m[3]]); i++; continue }
    if (lines[i].trim() && /^\s+/.test(lines[i]) && (!m || m[1].length > base) && items.length) {
      items[items.length - 1].push(lines[i].slice(Math.min(base + 2, lines[i].search(/\S/)))); i++; continue
    }
    break
  }
  const tag = ordered ? 'ol' : 'ul'
  const body = items.map(([first, ...rest]) => {
    const nested = rest.findIndex(l => LIST.test(l))
    const text = [first, ...(nested < 0 ? rest : rest.slice(0, nested))].map(l => l.trim()).join(' ')
    return `<li>${render.inline(text)}${nested < 0 ? '' : render.blocks(rest.slice(nested))}</li>`
  }).join('')
  return [`<${tag}>${body}</${tag}>`, i]
}

// render(text, {link, diagram}) → {html, title, headings}. diagram(key) — SVG схемы или null.
export function render(text, { link = href => ({ href }), diagram = () => null } = {}) {
  const lines = text.replace(/\r\n/g, '\n').split('\n')
  if (LANG_SWITCH.test(lines[0] || '')) lines.shift()
  const headings = [], seen = {}
  let title = '', pendingId = ''
  const r = { inline: t => inline(t, link) }
  r.blocks = function blocks(lines) {
    const out = []
    let i = 0
    while (i < lines.length) {
      const line = lines[i]
      if (!line.trim()) { i++; continue }
      const anchor = /^<a id="([\w-]+)"><\/a>\s*$/.exec(line.trim())
      if (anchor) { pendingId = anchor[1]; i++; continue }
      const fence = /^```(\S*)/.exec(line)
      if (fence) {
        let j = i + 1
        while (j < lines.length && !lines[j].startsWith('```')) j++
        const code = lines.slice(i + 1, j).join('\n') + '\n'
        const svg = fence[1] === 'mermaid' ? diagram(diagramKey(code)) : null
        out.push(svg ? `<figure class="diagram">${svg}</figure>` : `<pre><code>${esc(code)}</code></pre>`)
        i = j + 1; continue
      }
      const h = /^(#{1,6})\s+(.*?)\s*$/.exec(line)
      if (h) {
        const level = h[1].length, plain = h[2]
        let id = slug(plain.replace(/`/g, ''))
        seen[id] = (seen[id] || 0) + 1
        if (seen[id] > 1) id += '-' + (seen[id] - 1)
        if (level === 1 && !title) title = plain.replace(/`/g, '')
        else headings.push({ level, text: plain.replace(/`/g, ''), id: pendingId || id })
        const extra = pendingId ? `<span class="help-anchor" id="${pendingId}"></span>` : ''
        out.push(`<h${level} id="${id}">${extra}${r.inline(plain)}</h${level}>`)
        pendingId = ''; i++; continue
      }
      if (pendingId) { out.push(`<span class="help-anchor" id="${pendingId}"></span>`); pendingId = '' }
      if (/^\s*(-{3,}|\*{3,})\s*$/.test(line)) { out.push('<hr>'); i++; continue }
      if (line.includes('|') && isTableRule(lines[i + 1] || '')) {
        const head = cells(line), align = cells(lines[i + 1]).map(c => c.endsWith(':') ? ' class="r"' : '')
        let j = i + 2
        const rows = []
        while (j < lines.length && lines[j].includes('|') && lines[j].trim()) rows.push(cells(lines[j++]))
        out.push('<div class="table-wrap"><table><thead><tr>' + head.map((c, k) => `<th${align[k]}>${r.inline(c)}</th>`).join('')
          + '</tr></thead><tbody>' + rows.map(row => '<tr>' + head.map((_, k) => `<td${align[k]}>${r.inline(row[k] ?? '')}</td>`).join('') + '</tr>').join('')
          + '</tbody></table></div>')
        i = j; continue
      }
      if (line.startsWith('>')) {
        let j = i
        while (j < lines.length && lines[j].startsWith('>')) j++
        out.push(`<blockquote>${blocks(lines.slice(i, j).map(l => l.replace(/^>\s?/, '')))}</blockquote>`)
        i = j; continue
      }
      if (LIST.test(line)) { const [html, next] = list(lines, i, r); out.push(html); i = next; continue }
      let j = i
      while (j < lines.length && lines[j].trim() && !/^(#{1,6}\s|```|>|<a id=)/.test(lines[j]) && !LIST.test(lines[j])
             && !(lines[j].includes('|') && isTableRule(lines[j + 1] || ''))) j++
      out.push(`<p>${r.inline(lines.slice(i, j).map(l => l.trim()).join(' '))}</p>`)
      i = j
    }
    return out.join('\n')
  }
  const html = r.blocks(lines)
  return { html, title, headings }
}
