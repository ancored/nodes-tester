import { ref, reactive } from 'vue'
const KEY = 'nt_admin_token'
function stored() { try { return localStorage.getItem(KEY) || '' } catch { return '' } }
const token = ref(stored())
export const auth = reactive({ verified: false, checking: false, error: '', capabilities: {}, epoch: 0, dirty: false })
export function useAdminToken() { return token }
export function getToken() { return token.value }
export function setToken(t) { token.value = t || ''; auth.verified = false; auth.epoch++ }
export function logout() { setToken(''); auth.error = ''; try { localStorage.removeItem(KEY) } catch {} }
export function requestLogout() {
  if (auth.dirty && !window.confirm('Есть несохранённые настройки. Выйти и отбросить их?')) return
  logout()
}
async function request(method, path, body, headers = {}) {
  const current = getToken()
  if (current) headers['X-Admin-Token'] = current
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  const controller = new AbortController(), timeout = setTimeout(() => controller.abort(), 12000)
  try {
    const resp = await fetch('/api' + path, { method, headers, cache: 'no-store', signal: controller.signal,
      body: body !== undefined ? JSON.stringify(body) : undefined })
    let data
    try { data = await resp.json() } catch { throw new Error('Непонятный ответ сервера') }
    if (!resp.ok) {
      if (resp.status === 401 && current === getToken()) auth.verified = false
      const err = new Error(data.error || 'HTTP ' + resp.status); err.status = resp.status; throw err
    }
    return data
  } catch (e) {
    if (e.name === 'AbortError') throw new Error('Сервер не ответил за 12 секунд. Обновите данные.')
    if (e instanceof TypeError) throw new Error('Нет связи с сервером админки. Проверьте сеть и обновите данные.')
    throw e
  } finally { clearTimeout(timeout) }
}
export const api = { get: p => request('GET', p), post: (p,b,h) => request('POST',p,b ?? {},h),
  put: (p,b,h) => request('PUT',p,b ?? {},h) }
export async function login(t, remember = false) {
  setToken(t.trim()); const epoch = auth.epoch; auth.checking = true; auth.error = ''
  try {
    const session = await api.get('/session')
    if (epoch !== auth.epoch) return
    auth.capabilities = session.capabilities; auth.verified = true
    try { remember ? localStorage.setItem(KEY,getToken()) : localStorage.removeItem(KEY) } catch {}
  } catch (e) {
    if (epoch !== auth.epoch) return
    // Only a rejected token is discarded; a timeout or server error keeps the remembered one.
    if (e.status === 401) logout()
    auth.error = e.message
  }
  finally { auth.checking = false }
}
export async function initSession() {
  try { auth.capabilities = await api.get('/capabilities') } catch(e) { auth.error = e.message }
  if (getToken()) await login(getToken(),true)
}
// Re-check a kept token after a transient failure without resetting the snapshot.
export async function retrySession() {
  if (!getToken() || auth.verified || auth.checking) return
  const epoch = auth.epoch; auth.checking = true
  try {
    const session = await api.get('/session')
    if (epoch !== auth.epoch) return
    auth.capabilities = session.capabilities; auth.verified = true; auth.error = ''
  } catch (e) {
    if (epoch !== auth.epoch) return
    if (e.status === 401) logout()
    auth.error = e.message
  } finally { auth.checking = false }
}
export function can(action) { return auth.verified && !!auth.capabilities[action] }
