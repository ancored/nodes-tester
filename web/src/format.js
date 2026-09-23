// Форматтеры значений (перенос логики из dashboard/server.py: B/T/DUR/D/score-цвета).

export function bytes(n) {
  n = +n || 0
  const u = ['B', 'K', 'M', 'G', 'T']
  let i = 0
  while (n >= 1024 && i < 4) { n /= 1024; i++ }
  return n.toFixed(i ? 1 : 0) + u[i]
}

export function timeHMS(e) {
  if (!e) return ''
  return new Date(e * 1000).toLocaleTimeString('ru-RU', { hour12: false })
}

// Длительность в человекочитаемом виде (д/ч/м) — порт DUR.
export function dur(s) {
  s = +s || 0
  const d = Math.floor(s / 86400)
  const h = Math.floor((s % 86400) / 3600)
  const m = Math.floor((s % 3600) / 60)
  return d ? `${d}д ${h}ч` : h ? `${h}ч ${m}м` : `${m}м`
}

// Короткая дата DD/MM (или '–') — порт D.
export function dateDM(e) {
  if (!e) return '–'
  const d = new Date(e * 1000)
  return `${String(d.getDate()).padStart(2, '0')}/${String(d.getMonth() + 1).padStart(2, '0')}`
}

export function scoreClass(v) {
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
