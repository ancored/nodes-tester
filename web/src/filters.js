import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { STANDARD_FILTERS } from './ux.js'

// Единый блок фильтров таблиц: страна, группа, протокол, провайдер (+ свои поля).
// Фильтр: {key, title, get?(row) → значение или массив, label?(value)}. Выбор хранится
// в query (<prefix>f_<key>), фильтр показывается, если в строках больше одного значения.
const list = v => (Array.isArray(v) ? v : [v]).filter(x => x != null && x !== '').map(String)

export function useTableFilters(rows, specs, prefix = '') {
  const route = useRoute(), router = useRouter()
  const name = f => prefix + 'f_' + f.key
  const value = f => String(route.query[name(f)] ?? '')
  const filters = computed(() => (specs.value ?? STANDARD_FILTERS)
    .map(f => ({ ...f, get: f.get || (r => r[f.key]) }))
    .map(f => ({ ...f, options: [...new Set(rows.value.flatMap(r => list(f.get(r))))]
      .sort((a, b) => a.localeCompare(b, 'ru', { numeric: true })) }))
    .filter(f => f.options.length > 1 || value(f)))
  const any = computed(() => filters.value.some(value))
  const go = patch => router.replace({ query: { ...route.query, ...patch, [prefix + 'page']: undefined } })
  const set = (f, v) => go({ [name(f)]: v || undefined })
  const reset = () => go(Object.fromEntries(filters.value.map(f => [name(f), undefined])))
  const apply = all => {
    const chosen = filters.value.filter(value)
    return chosen.length ? all.filter(r => chosen.every(f => list(f.get(r)).includes(value(f)))) : all
  }
  return { filters, value, any, set, reset, apply }
}
