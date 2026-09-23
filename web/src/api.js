// Тонкая обёртка над fetch: базовый префикс /api, токен из localStorage в заголовке
// X-Admin-Token для write/control-запросов, единый разбор ошибок {error}.

import { ref } from 'vue'

const TOKEN_KEY = 'nt_admin_token'

function storedToken() {
  try { return localStorage.getItem(TOKEN_KEY) || '' } catch { return '' }
}
const adminToken = ref(storedToken())

export function useAdminToken() { return adminToken }
export function getToken() { return adminToken.value }
export function setToken(t) {
  adminToken.value = t || ''
  try { t ? localStorage.setItem(TOKEN_KEY, t) : localStorage.removeItem(TOKEN_KEY) } catch { /* ignore */ }
}

async function request(method, path, body) {
  const headers = {}
  const token = getToken()
  if (token) headers['X-Admin-Token'] = token
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  const resp = await fetch('/api' + path, {
    method,
    headers,
    cache: 'no-store',
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  let data = null
  try { data = await resp.json() } catch { /* нет тела */ }
  if (!resp.ok) {
    const msg = (data && data.error) || `HTTP ${resp.status}`
    const err = new Error(msg)
    err.status = resp.status
    throw err
  }
  return data
}

export const api = {
  get: (p) => request('GET', p),
  post: (p, body) => request('POST', p, body ?? {}),
  put: (p, body) => request('PUT', p, body ?? {}),
  del: (p) => request('DELETE', p),
}
