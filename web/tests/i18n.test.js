import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'

const stored = new Map()
globalThis.localStorage = { getItem: k => stored.get(k), setItem: (k, v) => stored.set(k, v), removeItem: k => stored.delete(k) }
const { t, tn, setLang, lang, browserLang } = await import('../src/i18n.js')
const { default: ru } = await import('../src/locales/ru.js')

const SRC = new URL('../src/', import.meta.url).pathname
const files = dir => readdirSync(dir).flatMap(f => statSync(join(dir, f)).isDirectory() ? files(join(dir, f)) : [join(dir, f)])
const sources = files(SRC).filter(f => /\.(vue|js)$/.test(f) && !f.includes('/locales/'))
  .map(f => ({ file: f.slice(SRC.length), text: readFileSync(f, 'utf8') }))
const QUOTED = String.raw`'((?:[^'\\]|\\.)*)'`
const unescape = s => s.replace(/\\(.)/g, '$1')

// Строки, которые код передаёт в t()/tn(): литерал первым аргументом или обе ветви тернарного оператора.
function used() {
  const keys = new Map()
  const add = (key, file) => keys.set(unescape(key), file)
  for (const { file, text: raw } of sources) {
    const text = raw.split('\n').map(stripComment).join('\n')
    for (const m of text.matchAll(new RegExp(String.raw`\btn?\(\s*(?:[\w.]+,\s*)?` + QUOTED, 'g'))) add(m[1], file)
    for (const m of text.matchAll(new RegExp(String.raw`\btn\(\s*[^,]+,\s*` + QUOTED, 'g'))) add(m[1], file)
    for (const m of text.matchAll(new RegExp(String.raw`\bt\([^()?]*\?\s*` + QUOTED + String.raw`\s*:\s*` + QUOTED, 'g'))) { add(m[1], file); add(m[2], file) }
  }
  return keys
}
// Комментарий до конца строки; «://» в адресах комментарием не считается.
const stripComment = line => line.replace(/(^|[^:'"`])\/\/.*$/, '$1')
const params = s => [...String(s).matchAll(/\{(\w+)\}/g)].map(m => m[1]).sort()

test('every translated string has a Russian entry', () => {
  const missing = [...used()].filter(([key]) => !(key in ru)).map(([key, file]) => `${file}: ${key}`)
  assert.deepEqual(missing, [])
})

test('the catalog has no stale keys', () => {
  const keys = used(), all = sources.map(s => s.text).join('\n')
  // Имена маршрутов переводятся через t(item.name): ключи — значения name в router.js.
  const stale = Object.keys(ru).filter(k => !keys.has(k) && !all.includes(`name: '${k}'`))
  assert.deepEqual(stale, [])
})

test('the catalog has no duplicate keys and keeps the same parameters', () => {
  const text = readFileSync(new URL('../src/locales/ru.js', import.meta.url), 'utf8')
  const keys = [...text.matchAll(new RegExp(String.raw`^\s*` + QUOTED + ':', 'gm'))].map(m => unescape(m[1]))
  assert.deepEqual(keys.filter((k, i) => keys.indexOf(k) !== i), [])
  for (const [key, value] of Object.entries(ru)) {
    for (const form of [value].flat()) assert.deepEqual(params(form), params(key).filter(p => p !== 'n' || params(form).includes('n')), key)
  }
})

test('no Cyrillic outside the catalog, except comments and marked lines', () => {
  const found = []
  for (const { file, text } of sources) {
    text.replace(/<!--[\s\S]*?-->/g, '').split('\n').forEach((line, i) => {
      const code = stripComment(line)
      if (/[А-Яа-яЁё]/.test(code) && !line.includes('i18n-skip')) found.push(`${file}:${i + 1}: ${line.trim()}`)
    })
  }
  assert.deepEqual(found, [])
})

test('language: browser default, manual choice is remembered, English fallback', () => {
  assert.equal(browserLang(['ru-RU', 'en']), 'ru')
  assert.equal(browserLang(['de-DE', 'en-US']), 'en')
  assert.equal(browserLang(['de']), 'en')
  setLang('ru')
  assert.equal(lang.value, 'ru')
  assert.equal(stored.get('nt_lang'), 'ru')
  assert.equal(t('Refresh'), 'Обновить')
  assert.equal(t('Snapshot {time}', { time: '12:00' }), 'Снимок 12:00')
  assert.equal(t('Not in the catalog {x}', { x: 1 }), 'Not in the catalog 1')
  setLang('en')
  assert.equal(t('Refresh'), 'Refresh')
  setLang('xx')
  assert.equal(lang.value, 'en')
})

test('plural forms follow the language rules', () => {
  ru['{n} node'] = ['{n} нода', '{n} ноды', '{n} нод']
  setLang('ru')
  assert.deepEqual([1, 2, 5, 11, 21, 22, 112].map(n => tn(n, '{n} node', '{n} nodes')),
    ['1 нода', '2 ноды', '5 нод', '11 нод', '21 нода', '22 ноды', '112 нод'])
  setLang('en')
  assert.deepEqual([1, 2].map(n => tn(n, '{n} node', '{n} nodes')), ['1 node', '2 nodes'])
  delete ru['{n} node']
})
