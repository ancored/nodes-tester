import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { render, diagrams, slug } from '../src/markdown.js'
import { fileURLToPath } from 'node:url'

// Справка: Markdown docs/help/*.md встроен в админку; иконки «?» ссылаются на «страница#якорь».
const SRC = fileURLToPath(new URL('../src/', import.meta.url))
const HELP = fileURLToPath(new URL('../../docs/help/', import.meta.url))
const files = dir => readdirSync(dir).flatMap(f => statSync(join(dir, f)).isDirectory() ? files(join(dir, f)) : [join(dir, f)])
const sources = files(SRC).filter(f => /\.(vue|js)$/.test(f) && !f.includes('/locales/')).map(f => readFileSync(f, 'utf8')).join('\n')
const helpFiles = readdirSync(HELP).filter(f => f.endsWith('.md'))
const anchors = text => new Set([...text.matchAll(/^<a id="([\w-]+)"><\/a>/gm)].map(m => m[1]))

test('every help icon and page topic points to an existing section in both languages', () => {
  const topics = new Set([
    ...[...sources.matchAll(/topic="([\w-]+(?:#[\w-]+)?)"/g)].map(m => m[1]),
    ...[...sources.matchAll(/'([a-z-]+#[a-z0-9-]+)'/g)].map(m => m[1]),
    ...[...sources.matchAll(/help: '([\w-]+(?:#[\w-]+)?)'/g)].map(m => m[1]),
  ])
  assert(topics.size > 40, 'topics found: ' + topics.size)
  const missing = []
  for (const topic of topics) {
    const [page, anchor] = topic.split('#')
    for (const name of [page + '.md', page + '.ru.md']) {
      if (!helpFiles.includes(name)) { missing.push(`${topic}: no ${name}`); continue }
      if (anchor && !anchors(readFileSync(HELP + name, 'utf8')).has(anchor)) missing.push(`${topic}: no anchor in ${name}`)
    }
  }
  assert.deepEqual(missing, [])
})

test('the help contents list every help page', async () => {
  const text = readFileSync(SRC + 'help.js', 'utf8')
  const listed = JSON.parse(/HELP_PAGES = (\[[^\]]+\])/.exec(text)[1].replace(/'/g, '"').replace(/\s+/g, ''))
  const pages = helpFiles.filter(f => !f.endsWith('.ru.md')).map(f => f.slice(0, -3))
  assert.deepEqual([...pages, 'CONFIG'].sort(), [...listed].sort())
})

test('every diagram has its rendered SVG and there are no stale ones', () => {
  const wanted = new Set(helpFiles.flatMap(f => diagrams(readFileSync(HELP + f, 'utf8')).map(d => d.key)))
  const present = new Set(readdirSync(SRC + 'diagrams').map(f => f.replace(/\.svg$/, '')))
  assert.deepEqual([...wanted].filter(k => !present.has(k)), [], 'run npm run diagrams')
  assert.deepEqual([...present].filter(k => !wanted.has(k)), [], 'run npm run diagrams')
  for (const key of present) assert(!readFileSync(SRC + 'diagrams/' + key + '.svg', 'utf8').includes('<style'), key + ': own styles')
})

test('markdown: escaping, headings, anchors, tables, lists, links and diagrams', () => {
  const text = [
    '**English** | [Русский](x.ru.md)', '', '# Title', '', '<a id="part"></a>', '## Part one', '',  // i18n-skip: строка перехода
    'Text with `code <b>`, **bold**, _italic_ and <script>alert(1)</script>.', '',
    '| A | B |', '|---|---:|', '| 1 | [link](other.md#x) |', '',
    '- item', '  - nested', '- second', '', '1. first', '2. second', '',
    '> note', '', '```mermaid', 'flowchart TD', '  a --> b', '```', '', '```sh', 'echo <hi>', '```',
  ].join('\n')
  const key = diagrams(text)[0].key
  const links = []
  const doc = render(text, { link: href => { links.push(href); return { href: '#' + href, attrs: ' data-help="x"' } }, diagram: k => k === key ? '<svg/>' : null })
  assert.equal(doc.title, 'Title')
  assert.deepEqual(doc.headings, [{ level: 2, text: 'Part one', id: 'part' }])
  assert(!doc.html.includes('Русский'))
  assert(doc.html.includes('<h2 id="part-one"><span class="help-anchor" id="part"></span>Part one</h2>'))
  assert(doc.html.includes('<code>code &lt;b&gt;</code>') && doc.html.includes('<strong>bold</strong>') && doc.html.includes('<em>italic</em>'))
  assert(doc.html.includes('&lt;script&gt;') && !doc.html.includes('<script>'))
  assert(doc.html.includes('<th class="r">B</th>') && doc.html.includes('<a href="#other.md#x" data-help="x">link</a>'))
  assert.deepEqual(links, ['other.md#x'])
  assert(doc.html.includes('<ul><li>item<ul><li>nested</li></ul></li><li>second</li></ul>'))
  assert(doc.html.includes('<ol><li>first</li><li>second</li></ol>'))
  assert(doc.html.includes('<blockquote><p>note</p></blockquote>'))
  assert(doc.html.includes('<figure class="diagram"><svg/></figure>'))
  assert(doc.html.includes('<pre><code>echo &lt;hi&gt;\n</code></pre>'))
  assert.equal(slug('Пресеты клиентов'), 'пресеты-клиентов')
  assert.equal(slug('6. Update the package'), '6-update-the-package')
})
