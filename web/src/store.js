// Единый снимок данных: один поллинг /api/data, все вью читают из него (как текущий
// дашборд с одним /api/data). Избегаем N× collect() на каждый раздел.
import { reactive } from 'vue'
import { api } from './api.js'

const state = reactive({
  data: {},
  generated: '',
  loading: true,
  error: '',
  ready: false,
})

let timer = null
let started = false
let inFlight = null

export function refresh() {
  if (inFlight) return inFlight
  inFlight = (async () => {
    if (!state.ready) state.loading = true
    try {
      const d = await api.get('/data')
      state.data = d
      state.generated = d.generated || ''
      state.error = ''
      state.ready = true
    } catch (e) {
      state.error = e.message
    } finally {
      state.loading = false
      inFlight = null
    }
  })()
  return inFlight
}

export function useSnapshot(intervalMs = 15000) {
  if (!started) {
    started = true
    refresh()
    timer = setInterval(refresh, intervalMs)
  }
  return state
}

export function stopSnapshot() {
  if (timer) clearInterval(timer)
  timer = null
  started = false
}
