export const TEST_NAMES = { connectivity: 'Соединение', latency: 'Задержка', jitter: 'Джиттер / потери',
  download: 'Скорость / троттлинг', reachability: 'Доступность сайтов', heavy_download: 'Тяжёлая загрузка', gemini: 'Gemini / страна Google' }
export const PHASES = { stopped: 'Остановлен', initializing: 'Подготовка', enumerating: 'Получение списка нод',
  testing: 'Основные проверки', required_testing: 'Обязательные тесты групп (gemini)', heavy_testing: 'Тяжёлая проверка кандидатов', switching: 'Выбор нод регионов',
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
