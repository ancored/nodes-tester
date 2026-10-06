export const TEST_NAMES = { connectivity: 'Соединение', latency: 'Задержка', jitter: 'Джиттер / потери',
  download: 'Скорость / троттлинг', reachability: 'Доступность сайтов', heavy_download: 'Тяжёлая загрузка',
  gemini: 'Google AI', openai: 'OpenAI', anthropic: 'Claude AI' }
// Единицы значений тестов: в ячейках только числа, единицы — в заголовке.
export const TEST_UNITS = { latency: 'мс', jitter: 'мс / %', download: 'Мбит/с', heavy_download: 'Мбит/с' }
export const testTitle = t => (TEST_NAMES[t] || t) + (TEST_UNITS[t] ? ', ' + TEST_UNITS[t] : '')
export const PHASES = { stopped: 'Остановлен', initializing: 'Подготовка', enumerating: 'Получение списка нод',
  testing: 'Основные проверки', required_testing: 'Обязательные тесты групп (AI)', heavy_testing: 'Тяжёлая проверка кандидатов', switching: 'Выбор нод регионов',
  waiting: 'Ожидание следующего прохода' }
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
  return [n.present == null ? 'Присутствие неизвестно' : +n.present ? 'В списке тестера' : 'Нет в текущем списке',
    n.score == null ? 'Нет замеров рейтинга' : 'Рейтинг '+Number(n.score).toFixed(1),
    +n.banned ? 'Исключена вручную' : '', n.gstate === 'garbage' ? 'Карантин' : n.gstate === 'backoff' ? 'Пауза проверок' : '',
    +n.active ? 'Выбрана переключателем' : ''].filter(Boolean)
}
export function crcOf(row) { return row.crc || row.id || /\[([0-9a-f]{8})\]/i.exec(row.node || '')?.[1] || '' }
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
export const STANDARD_FILTERS = [
  { key: 'country', title: 'Страна', get: r => (r.country ?? r.cc)?.toUpperCase?.() },
  { key: 'group', title: 'Группа', get: r => r.groups },
  { key: 'protocol', title: 'Протокол' },
  { key: 'provider', title: 'Провайдер' },
]
