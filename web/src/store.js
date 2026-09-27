import { reactive, watch } from 'vue'
import { api, auth, retrySession } from './api.js'
const state = reactive({ data: {}, generated: '', received: null, loading: true, error: '', ready: false })
let timer = null, started = false, inFlight = null
watch(() => auth.epoch, () => { state.data = {}; state.ready = false; state.generated = ''; state.received = null; refresh() })
watch(() => auth.verified, () => { if (auth.verified) refresh() })
export function refresh() {
  const epoch = auth.epoch
  if (inFlight?.epoch === epoch) return inFlight.promise
  const promise = (async () => {
    if (!state.ready) state.loading = true
    try {
      const data = await api.get('/data')
      if (epoch !== auth.epoch) return
      state.data = data; state.generated = data.generated || ''; state.received = Date.now(); state.error = ''; state.ready = true
      retrySession()
    } catch(e) { if (epoch === auth.epoch) state.error = e.message }
    finally { if (epoch === auth.epoch) { state.loading = false; inFlight = null } }
  })()
  inFlight = { epoch, promise }; return promise
}
export function useSnapshot(intervalMs = 15000) {
  if (!started) { started = true; refresh(); timer = setInterval(refresh,intervalMs) }
  return state
}
export function stopSnapshot() { clearInterval(timer); timer = null; started = false }
