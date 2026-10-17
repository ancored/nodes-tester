// Схемы справки: блоки ```mermaid из docs/help/*.md → SVG в src/diagrams/<ключ>.svg.
// Админка не тянет mermaid (несколько мегабайт): берёт готовый SVG, цвета — из своей темы
// (свой <style> mermaid вырезается). Запуск после правки схем: npm run diagrams.
// Chromium: CHROMIUM_PATH, иначе /opt/pw-browsers/chromium, иначе установленный Chrome.
import { existsSync, readdirSync, readFileSync, unlinkSync, writeFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { chromium } from 'playwright-core'
import { diagrams } from '../src/markdown.js'

const HELP = new URL('../../docs/help/', import.meta.url).pathname
const OUT = new URL('../src/diagrams/', import.meta.url).pathname
const MERMAID = createRequire(import.meta.url).resolve('mermaid/dist/mermaid.min.js')

const wanted = new Map()
for (const file of readdirSync(HELP).filter(f => f.endsWith('.md')).sort())
  for (const d of diagrams(readFileSync(HELP + file, 'utf8'))) wanted.set(d.key, { ...d, file })
const present = new Set(readdirSync(OUT).filter(f => f.endsWith('.svg')).map(f => f.slice(0, -4)))
for (const key of present) if (!wanted.has(key)) { unlinkSync(OUT + key + '.svg'); console.log('removed', key) }
const todo = [...wanted.values()].filter(d => process.argv.includes('--force') || !present.has(d.key))
if (!todo.length) { console.log('diagrams are up to date'); process.exit(0) }

const executablePath = process.env.CHROMIUM_PATH || (existsSync('/opt/pw-browsers/chromium') ? '/opt/pw-browsers/chromium' : undefined)
const browser = await chromium.launch(executablePath ? { executablePath } : { channel: 'chrome' })
try {
  const page = await browser.newPage()
  await page.setContent('<!doctype html><meta charset="utf-8"><body></body>')
  await page.addScriptTag({ path: MERMAID })
  await page.evaluate(() => window.mermaid.initialize({ startOnLoad: false, theme: 'base', securityLevel: 'strict',
    fontFamily: 'Arial, Helvetica, sans-serif', flowchart: { htmlLabels: true, useMaxWidth: false, padding: 14, nodeSpacing: 40, rankSpacing: 46 } }))
  for (const d of todo) {
    let svg
    try { svg = await page.evaluate(([id, code]) => window.mermaid.render(id, code).then(r => r.svg), ['d' + d.key, d.code]) }
    catch (e) { throw new Error(`${d.file}: ${e.message}\n${d.code}`) }
    // Свои цвета mermaid не нужны: оформление — классы в styles.css админки.
    svg = svg.replace(/<style>[\s\S]*?<\/style>/g, '').replace(/ style="max-width:[^"]*"/, '')
      .replace(/(\d+\.\d)\d+/g, '$1')                    // десятых пикселя хватает: файл вдвое меньше
    writeFileSync(OUT + d.key + '.svg', svg + '\n')
    console.log('rendered', d.key, d.file)
  }
} finally { await browser.close() }
