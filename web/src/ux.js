import { t } from './i18n.js'
// Словари названий: значения — переводы, берутся при каждом обращении (смена языка видна сразу).
const translated = table => Object.defineProperties({}, Object.fromEntries(Object.entries(table)
  .map(([k, text]) => [k, { get: text, enumerable: true }])))
export const TEST_NAMES = translated({ connectivity: () => t('Connection'), latency: () => t('Latency'), jitter: () => t('Jitter / loss'),
  download: () => t('Speed / throttling'), reachability: () => t('Site reachability'), heavy_download: () => t('Heavy download'),
  gemini: () => 'Google AI', openai: () => 'OpenAI', anthropic: () => 'Claude AI', upstream: () => t('Proxy through the node'), whitelist: () => t('Whitelist'),
  google_country: () => t('Country seen by Google') })
// Единицы значений тестов: в ячейках только числа, единицы — в заголовке.
export const TEST_UNITS = translated({ latency: () => t('ms'), jitter: () => t('ms / %'), download: () => t('Mbit/s'), heavy_download: () => t('Mbit/s') })
// Проверки групп (config.json checks): названия и вердикт «1» / «0» / «» (неизвестно или устарел).
export const CHECK_NAMES = translated({ country: () => t('Exit country'), ip_type: () => t('Exit IP type'), geo_consistent: () => t('IP country by databases'),
  gemini: () => 'Google AI', openai: () => 'OpenAI', anthropic: () => 'Claude AI', upstream: () => t('Proxy through the node'), heavy: () => t('Heavy download'), whitelist: () => t('Whitelists') })
// Причина провала проверки (nodes_tester/diagnose.py CAUSES): код из ячейки результата.
export const FAILURE_CAUSES = translated({ dns: () => t('DNS: the name of the node server does not resolve'),
  tls: () => t('TLS: the certificate or Reality check failed'), tcp: () => t('TCP: no connection to the node server'),
  transport: () => t('Transport: the server answered with an HTTP error'),
  handshake: () => t('The handshake hung after connecting: blocking or a dead server'), reset: () => t('The connection was reset'),
  freeze: () => t('The transfer froze midway'), slow: () => t('Too slow'), service: () => t('The service refused: region or limit'),
  target: () => t('An error of the check target, not of the node'), timeout: () => t('No answer in time'), other: () => t('Other cause') })
export const checkNames = list => (list || []).map(c => CHECK_NAMES[c] || c).join(', ')
export function verdictMark(v) { return v === '1' ? '✓' : v === '0' ? '✗' : '?' }
export function verdictClass(v) { return v === '1' ? 'good' : v === '0' ? 'bad' : 'mut' }
export const testTitle = name => (TEST_NAMES[name] || name) + (TEST_UNITS[name] ? ', ' + TEST_UNITS[name] : '')
export const PHASES = translated({ stopped: () => t('Stopped'), initializing: () => t('Preparing'), testing: () => t('Checking a node'),
  waiting: () => t('Waiting for test deadlines'), apply_wait: () => t('Waiting for the config to be applied') })
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
// state: только состояние — без рейтинга и резерва (у них в таблице свои столбцы).
export function nodeFlags(n, { state = false } = {}) {
  return [n.present == null ? t('Presence unknown') : +n.present ? t('In the tester list') : t('Not in the current list'),
    state ? '' : n.score == null ? t('No rating measurements') : t('Rating {score}', { score: Number(n.score).toFixed(1) }),
    +n.banned ? t('Excluded manually') : '', n.gstate === 'garbage' ? t('Quarantined') : n.gstate === 'backoff' ? t('Checks paused') : '',
    +n.active ? t('Selected by the switcher') : '', !state && n.reserve ? t('Reserve: {groups}', { groups: n.reserve }) : ''].filter(Boolean)
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
// столбиком, ключ в две строки (ссылка на карточку), флаг страны. crc и country — имена полей строки;
// route — вместо флага страны цепочка «вход → выход» (ячейка — ChainCell), у прямой ноды — флаг страны выхода.
// Поля строки — от /api/data: chain («FI→NL», только у цепочки), route (она же или страна выхода), entry_*/exit_*, warp.
export function nodeColumns({ crc = 'crc', country = 'country', route = false } = {}) {
  return [{ key: 'provider', title: t('Provider'), l: true }, { key: 'protocol', title: t('Protocol'), l: true },
    { key: crc, title: t('Node'), l: true, nowrap: true },
    route ? { key: 'route', title: t('Country or chain'), head: '', l: true, chain: true, cellTitle: chainTitle }
      : { key: country, title: t('Country'), head: '', l: true, narrow: true }]
}
export function chainTitle(r) {
  if (r.chain) return t('Entry {entry} → exit {exit}', { entry: `${r.entry_cc || '?'} ${r.entry_ip}`, exit: `${r.exit_cc} ${r.exit_ip}` })
    + (r.warp ? '\n' + t('Exit through Cloudflare WARP: the country follows the Cloudflare point of presence') : '')
  if (r.entry_ip) return t('Entry and exit: {country} {ip}', { country: r.exit_cc, ip: r.exit_ip })
  return r.country ? String(r.country).toUpperCase() : null
}
export const STANDARD_FILTERS = [
  { key: 'country', get title() { return t('Country') }, get: r => (r.country ?? r.cc)?.toUpperCase?.() },
  { key: 'reserve', get title() { return t('Reserve') }, get: r => String(r.reserve || '').split(',').filter(Boolean) },
  { key: 'protocol', get title() { return t('Protocol') } },
  { key: 'provider', get title() { return t('Provider') } },
]
// Значения по умолчанию из config.schema.json: путь поля → default. Элементы массивов — «путь[]»
// (subscriptions[].enabled), $ref на определения раскрываются. Единственный источник умолчаний для
// подсказок в формах: схема ставится рядом с config.json и совпадает с загрузчиком (тест).
export function schemaDefaults(schema) {
  const out = {}, defs = schema?.definitions || {}
  const resolve = node => node?.$ref ? { ...defs[node.$ref.split('/').pop()], ...node } : node || {}
  function walk(node, path) {
    node = resolve(node)
    if (path && 'default' in node) out[path] = node.default
    for (const [key, sub] of Object.entries(node.properties || {})) walk(sub, path ? path + '.' + key : key)
    if (node.items && typeof node.items === 'object') walk(node.items, path + '[]')
  }
  walk(schema, '')
  return out
}
// Подсказка поля: действующее по умолчанию значение строкой (пусто — подсказки нет).
export function defaultText(value) {
  if (value == null || value === '') return ''
  return Array.isArray(value) ? value.join(', ') : String(value)
}
