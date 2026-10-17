// Форматтеры значений (перенос логики из dashboard/server.py: B/T/DUR/D/score-цвета).
import { t, locale } from './i18n.js'

export function bytes(n) {
  n = +n || 0
  const u = ['B', 'K', 'M', 'G', 'T']
  let i = 0
  while (n >= 1024 && i < 4) { n /= 1024; i++ }
  return n.toFixed(i ? 1 : 0) + u[i]
}

export function timeHMS(e) {
  if (!e) return ''
  return new Date(e * 1000).toLocaleTimeString(locale(), { hour12: false })
}

export function dateTime(e) {
  return e ? new Date(e * 1000).toLocaleString(locale()) : t('No data')
}

// Длительность в человекочитаемом виде (д/ч/м) — порт DUR.
export function dur(s) {
  s = +s || 0
  const d = Math.floor(s / 86400)
  const h = Math.floor((s % 86400) / 3600)
  const m = Math.floor((s % 3600) / 60)
  return d ? t('{d}d {h}h', { d, h }) : h ? t('{h}h {m}m', { h, m }) : t('{m}m', { m })
}

// Короткая дата DD/MM (или '–') — порт D.
export function dateDM(e) {
  if (!e) return '–'
  const d = new Date(e * 1000)
  return `${String(d.getDate()).padStart(2, '0')}/${String(d.getMonth() + 1).padStart(2, '0')}`
}

export function scoreClass(v) {
  if (v == null || v === '') return 'mut'
  const n = +v
  return n >= 70 ? 'good' : n <= 20 ? 'bad' : 'num'
}

export function fixed(v, d = 2) {
  if (v === '' || v == null) return '–'
  return (+v).toFixed(d)
}

export function pct(v) {
  return (+v || 0) + '%'
}

// Флаг страны по ISO-коду (NL → 🇳🇱). Не код страны (группа, регион, «other») — как есть.
export function flag(cc) {
  let c = String(cc ?? '').trim().toUpperCase()
  if (c === 'UK') c = 'GB'
  if (!/^[A-Z]{2}$/.test(c)) return cc ?? '—'
  return String.fromCodePoint(...[...c].map(ch => 0x1F1E6 + ch.charCodeAt(0) - 65))
}
