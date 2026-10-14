export const TEST_NAMES = { connectivity: 'Соединение', latency: 'Задержка', jitter: 'Джиттер / потери',
  download: 'Скорость / троттлинг', reachability: 'Доступность сайтов', heavy_download: 'Тяжёлая загрузка',
  gemini: 'Google AI', openai: 'OpenAI', anthropic: 'Claude AI', upstream: 'Прокси через ноду', whitelist: 'Белый список' }
// Единицы значений тестов: в ячейках только числа, единицы — в заголовке.
export const TEST_UNITS = { latency: 'мс', jitter: 'мс / %', download: 'Мбит/с', heavy_download: 'Мбит/с' }
// Проверки групп (config.json checks): названия и вердикт «1» / «0» / «» (неизвестно или устарел).
export const CHECK_NAMES = { country: 'Страна выхода', ip_type: 'Тип выходного IP', geo_consistent: 'Страна IP по базам',
  gemini: 'Google AI', openai: 'OpenAI', anthropic: 'Claude AI', upstream: 'Прокси через ноду', heavy: 'Тяжёлая загрузка', whitelist: 'Белые списки' }
export const checkNames = list => (list || []).map(c => CHECK_NAMES[c] || c).join(', ')
export function verdictMark(v) { return v === '1' ? '✓' : v === '0' ? '✗' : '?' }
export function verdictClass(v) { return v === '1' ? 'good' : v === '0' ? 'bad' : 'mut' }
export const testTitle = t => (TEST_NAMES[t] || t) + (TEST_UNITS[t] ? ', ' + TEST_UNITS[t] : '')
export const PHASES = { stopped: 'Остановлен', initializing: 'Подготовка', testing: 'Проверка ноды',
  waiting: 'Ожидание сроков тестов', apply_wait: 'Ожидание применения конфига' }
export function getPath(obj,path) { return path.split('.').reduce((v,k) => v?.[k],obj) }
export function setPath(obj,path,value) {
  const keys = path.split('.'), last = keys.pop(); let part = obj
  for (const [i,k] of keys.entries()) {
    if (part[k] == null) part[k] = /^\d+$/.test(keys[i+1] || last) ? [] : {}
    part = part[k]
  }
  part[last] = value
}
export function changedPaths(a,b,prefix = '') {
  if (JSON.stringify(a) === JSON.stringify(b)) return []
  if (a && b && typeof a === 'object' && typeof b === 'object') {
    return [...new Set([...Object.keys(a),...Object.keys(b)])].flatMap(k => changedPaths(a[k],b[k],prefix ? prefix+'.'+k : k))
  }
  return [prefix]
}
export function nodeFlags(n) {
  if (n.wh_only) return ['Только белые списки']      // клиентская ветвь: тестер ноду не ведёт
  return [n.present == null ? 'Присутствие неизвестно' : +n.present ? 'В списке тестера' : 'Нет в текущем списке',
    n.score == null ? 'Нет замеров рейтинга' : 'Рейтинг '+Number(n.score).toFixed(1),
    +n.banned ? 'Исключена вручную' : '', n.gstate === 'garbage' ? 'Карантин' : n.gstate === 'backoff' ? 'Пауза проверок' : '',
    +n.active ? 'Выбрана переключателем' : '', n.reserve ? 'Резерв: ' + n.reserve : ''].filter(Boolean)
}
// Ключ ноды в теге: [node_crc.cred_crc] — CRC настроек сервера и CRC учётных данных.
const NODE_KEY = /^(.*?)([0-9a-f]{8})\.([0-9a-f]{8})(\]?)\s*$/i
export function crcOf(row) { return row.crc || row.id || /\[([0-9a-f]{8}\.[0-9a-f]{8})\]/i.exec(row.node || '')?.[1] || '' }
// Тег или ключ → {head, node, cred, tail} для раскраски; не похоже на ключ — null.
export function splitNodeKey(text) {
  const m = NODE_KEY.exec(String(text ?? ''))
  return m ? { head: m[1], node: m[2], cred: m[3], tail: m[4] } : null
}
// Строки JSON outbound с пометкой, чей это CRC: 'cred' — учётные данные (credPaths),
// 'node' — остальные поля payload (payloadKeys), '' — поля вне CRC (tag, domain_resolver).
export function nodeJsonLines(fragment, payloadKeys = [], credPaths = []) {
  const creds = credPaths.map(p => p.join('.'))
  const keys = new Set(payloadKeys)
  const kindOf = path => !path.length || !keys.has(path[0]) ? ''
    : creds.some(c => path.join('.') === c || path.join('.').startsWith(c + '.')) ? 'cred' : 'node'
  const lines = []
  function walk(value, path, indent, prefix, comma) {
    const pad = '  '.repeat(indent), kind = kindOf(path)
    if (value && typeof value === 'object') {
      const entries = Array.isArray(value) ? value.map((v, i) => [i, v]) : Object.entries(value)
      const [open, close] = Array.isArray(value) ? ['[', ']'] : ['{', '}']
      if (!entries.length) { lines.push({ text: pad + prefix + open + close + comma, kind }); return }
      lines.push({ text: pad + prefix + open, kind })
      entries.forEach(([k, v], i) => walk(v, [...path, k], indent + 1,
        Array.isArray(value) ? '' : JSON.stringify(k) + ': ', i < entries.length - 1 ? ',' : ''))
      lines.push({ text: pad + close + comma, kind })
    } else {
      lines.push({ text: pad + prefix + JSON.stringify(value) + comma, kind })
    }
  }
  walk(fragment, [], 0, '', '')
  return lines
}
// Ключи строк таблицы для v-for: содержимое строки + номер повтора одинаковых строк.
// Уникальны по построению (повтор ключа ломает перерисовку Vue) и не зависят от того,
// какие поля у таблицы считаются «идентификатором».
export function rowKeys(rows) {
  const keys = new Map(), seen = new Map()
  for (const r of rows) {
    const base = JSON.stringify(r), n = seen.get(base) ?? 0
    seen.set(base, n + 1)
    keys.set(r, n ? base + '#' + n : base)
  }
  return keys
}
// Стандартные фильтры таблиц (DataTable): поля строк снимка /api/data.
// Первые столбцы таблиц нод — одинаково везде (образец — «Результаты»): провайдер, протокол
// столбиком, ключ в две строки (ссылка на карточку), флаг страны. crc и country — имена полей строки.
export function nodeColumns({ crc = 'crc', country = 'country' } = {}) {
  return [{ key: 'provider', title: 'Провайдер', l: true }, { key: 'protocol', title: 'Протокол', l: true },
    { key: crc, title: 'Нода', l: true, nowrap: true }, { key: country, title: 'Страна', head: '', l: true, narrow: true }]
}
export const STANDARD_FILTERS = [
  { key: 'country', title: 'Страна', get: r => (r.country ?? r.cc)?.toUpperCase?.() },
  { key: 'reserve', title: 'Резерв', get: r => String(r.reserve || '').split(',').filter(Boolean) },
  { key: 'protocol', title: 'Протокол' },
  { key: 'provider', title: 'Провайдер' },
]
