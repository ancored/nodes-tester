import { api } from './api.js'
// Официальная Dashboard sing-box хранит серверы в localStorage своего origin; админка отдаёт её
// с того же origin (/sing-box-dashboard/), поэтому после входа записываем туда адрес и секрет.
const KEY = 'sing-box-dashboard.servers'
export const BOX_DASHBOARD = '/sing-box-dashboard/'
const norm = url => String(url || '').trim().replace(/\/+$/, '').replace(/^http:\/\//i, '')
export async function provisionBoxDashboard() {
  const { url, secret } = await api.get('/singbox/dashboard')
  let state = { servers: [], activeId: null }
  try { state = JSON.parse(localStorage.getItem(KEY)) || state } catch {}
  const servers = Array.isArray(state.servers) ? state.servers : []
  let server = servers.find(s => norm(s.url) === norm(url))
  if (server) server.secret = secret
  else servers.push(server = { id: crypto.randomUUID?.() || String(Date.now()), name: 'router', url: norm(url), secret })
  try { localStorage.setItem(KEY, JSON.stringify({ servers, activeId: server.id })) } catch {}
}
