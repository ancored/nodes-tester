import { reactive } from 'vue'
import { render, diagrams } from './markdown.js'

// Справка — docs/help/*.md (английский) и *.ru.md (русский), плюс справочник полей docs/CONFIG.md.
// Тексты и схемы входят в сборку отдельными кусками и грузятся по требованию.
const FILES = import.meta.glob(['../../docs/help/*.md', '../../docs/CONFIG.md', '../../docs/CONFIG.ru.md'], { query: '?raw', import: 'default' })
const SVGS = import.meta.glob('./diagrams/*.svg', { query: '?raw', import: 'default' })
const REPO = 'https://github.com/ancored/nodes-tester/blob/master/'

// Путь в репозитории → страница справки: docs/help/groups.md → groups, docs/CONFIG.md → CONFIG.
const pageOf = path => /^docs\/help\/([\w-]+?)(\.ru)?\.md$/.exec(path)?.[1] || (/^docs\/CONFIG(\.ru)?\.md$/.test(path) ? 'CONFIG' : null)
const fileOf = (page, lang) => (page === 'CONFIG' ? 'docs/CONFIG' : 'docs/help/' + page) + (lang === 'ru' ? '.ru.md' : '.md')
const loaders = Object.fromEntries(Object.entries(FILES).map(([k, v]) => [k.replace(/^(\.\.\/)+/, ''), v]))

// Порядок страниц в оглавлении: сначала устройство, потом страницы админки.
export const HELP_PAGES = ['README', 'concepts', 'checks', 'rating', 'groups', 'switching', 'restrictions',
  'access', 'overview', 'nodes', 'node-card', 'subscriptions', 'pipeline', 'rules', 'singbox-files',
  'runs', 'switches', 'traffic', 'settings', 'troubleshooting', 'CONFIG']
export const hasPage = page => HELP_PAGES.includes(page)

function normalize(path) {
  const out = []
  for (const part of path.split('/')) {
    if (part === '..') out.pop(); else if (part && part !== '.') out.push(part)
  }
  return out.join('/')
}

// Ссылка из документа: страница справки (data-help), остальное — GitHub или внешний сайт.
function linker(page, lang) {
  const dir = fileOf(page, lang).replace(/[^/]+$/, '')
  return href => {
    if (/^[a-z]+:/i.test(href)) return { href, attrs: ' target="_blank" rel="noopener noreferrer"' }
    const [path, anchor = ''] = href.split('#')
    const target = path ? normalize(dir + path) : fileOf(page, lang)
    const help = pageOf(target)
    if (help) return { href: helpHref(help, anchor), attrs: ` data-help="${help}#${anchor}"` }
    return { href: REPO + target + (anchor ? '#' + anchor : ''), attrs: ' target="_blank" rel="noopener noreferrer"' }
  }
}

export const helpHref = (page, anchor) => '#/help?page=' + encodeURIComponent(page) + (anchor ? '&s=' + encodeURIComponent(anchor) : '')

const cache = new Map()
// Страница справки на языке: {title, html, headings, text}. Нет перевода — английская.
export function loadHelp(page, lang) {
  const key = page + ':' + lang
  if (!cache.has(key)) cache.set(key, (async () => {
    const load = loaders[fileOf(page, lang)] || loaders[fileOf(page, 'en')]
    if (!load) throw new Error('no help page ' + page)
    const text = await load()
    const svgs = Object.fromEntries(await Promise.all(diagrams(text).map(async ({ key }) =>
      [key, await SVGS['./diagrams/' + key + '.svg']?.()])))
    return { ...render(text, { link: linker(page, lang), diagram: k => svgs[k] || null }), text }
  })())
  return cache.get(key)
}

// Боковая панель справки: открывается иконкой «?» на нужном разделе.
export const helpPanel = reactive({ open: false, page: '', anchor: '' })
// topic — «страница#якорь».
export function openHelp(topic) {
  const [page, anchor = ''] = topic.split('#')
  Object.assign(helpPanel, { open: true, page, anchor })
}
export function closeHelp() { helpPanel.open = false }
