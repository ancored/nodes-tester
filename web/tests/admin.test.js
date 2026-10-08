import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { parse, compileScript } from '@vue/compiler-sfc'
import * as vue from 'vue'

// Execute the real component setup with Vue lifecycle hooks and synthetic API responses.
// The renderer has no browser: these checks cover state and requests, not appearance.
const renderer = vue.createRenderer({
  createElement: () => ({}), createText: () => ({}), createComment: () => ({}),
  insert() {}, remove() {}, setText() {}, setElementText() {}, patchProp() {},
  parentNode: () => null, nextSibling: () => null,
})
const settle = () => new Promise(resolve => setImmediate(resolve))
function mount(view, api, query = {}) {
  const { descriptor } = parse(readFileSync(new URL(`../src/views/${view}.vue`, import.meta.url), 'utf8'))
  const script = compileScript(descriptor, { id: view, genDefaultAs: 'component' })
  const code = script.content.replace(/^import .+ from .+$/gm, '')
  const component = new Function('vue', 'router', 'apiModule', 'setInterval', 'clearInterval', 'setTimeout', 'clearTimeout', 'window',
    `const { computed, onBeforeUnmount, onMounted, ref, watch, toRaw } = vue;
     const { onBeforeRouteLeave, useRoute } = router;
     const { api, auth, can } = apiModule;
     ${code}
     return component`)(vue, { onBeforeRouteLeave() {}, useRoute: () => ({ query }) },
      { api, auth: { verified: true, dirty: false }, can: () => true },
      () => 0, () => {}, () => 0, () => {}, { confirm: () => true })
  const app = renderer.createApp({ ...component, render: () => vue.h('div') })
  const instance = app.mount({})
  return { state: instance.$.setupState, unmount: () => app.unmount() }
}
const schedule = { jobs: { router: { enabled: true, times: ['03:00'] }, clients: { enabled: false, times: [] } }, timeout: 60 }
const live = { id: 'live', mode: 'router', dry_run: true, status: 'running', started: 1 }
const old = { id: 'old', mode: 'clients', dry_run: false, status: 'ok', started: 1 }
function pipelineApi(readLog = async url => ({ text: url.includes('/old/') ? 'old log' : 'live log', offset: 8 })) {
  return {
    async get(url) {
      if (url === '/pipeline') return { current: live, revision: 'v1', schedule }
      if (url.startsWith('/pipeline/runs?')) return { runs: [live, old] }
      return readLog(url)
    },
  }
}

test('pipeline polling keeps the selected historical run; returning to live is explicit', async () => {
  const page = mount('Pipeline', pipelineApi())
  try {
    await settle()
    page.state.showRun(old); await settle()
    await page.state.poll()
    assert.equal(page.state.viewedRun, 'old')
    assert.equal(page.state.selectedRun.id, 'old')
    page.state.showRun(live); await settle()
    assert.equal(page.state.viewedRun, 'live')
    assert.equal(page.state.followLive, true)
  } finally { page.unmount() }
})

test('late and overlapping log responses cannot mix runs or duplicate text', async () => {
  const requests = []
  const page = mount('Pipeline', pipelineApi(url => new Promise(resolve => requests.push({ url, resolve }))))
  try {
    await settle()
    page.state.showRun(old)
    requests[0].resolve({ text: 'wrong run', offset: 9 }); await settle()
    assert.equal(page.state.log, '')
    const duplicate = page.state.readLog()
    requests[1].resolve({ text: 'old log', offset: 7 }); await settle()
    requests[2].resolve({ text: 'old log', offset: 7 }); await duplicate
    assert.equal(page.state.log, 'old log')
    assert.equal(page.state.logOffset, 7)
  } finally { page.unmount() }
})

test('schedule validation error survives successful background polling', async () => {
  const page = mount('Pipeline', pipelineApi())
  try {
    await settle()
    page.state.times.router = '25:00'
    await page.state.saveSchedule()
    const message = page.state.error
    assert.match(message, /HH:MM/)
    await page.state.poll()
    assert.equal(page.state.error, message)
    assert.equal(page.state.pollError, '')
  } finally { page.unmount() }
})

test('preset save rejection remains visible after reloading checkbox state', async () => {
  const item = { path: 'presets/demo.json', title: 'Пример', enabled: false }
  const page = mount('Presets', {
    async get(url) {
      if (url === '/singbox/presets?branch=router') return { presets: [item], groups: [] }
      if (url.startsWith('/pipeline/runs?')) return { runs: [] }
      return { data: { _preset: { enabled: false } }, revision: 'v1' }
    },
    async put() { throw new Error('Проверка конфигурации не прошла') },
  })
  try {
    await settle(); await page.state.toggle(item, true)
    assert.equal(page.state.error, 'Проверка конфигурации не прошла')
    assert.equal(page.state.presets[0].enabled, false)
    assert.equal(page.state.busy, false)
  } finally { page.unmount() }
})

test('clients tab lists client presets and builds clients without touching the router', async () => {
  const item = { path: 'clients/presets/ru.json', title: 'RU', enabled: true }
  const requests = [], posted = []
  const page = mount('Presets', {
    async get(url) {
      requests.push(url)
      if (url === '/singbox/presets?branch=clients') return { presets: [item], groups: ['global'], clients: ['a'], settings: false }
      if (url.startsWith('/pipeline/runs?')) return { runs: [{ id: 'r', mode: 'apply', status: 'ok', started: 1 }] }
      return { text: '', offset: 0 }
    },
    async post(url, data) { posted.push({ url, data }); return { run_id: 'c1' } },
  }, { branch: 'clients' })
  try {
    await settle()
    assert.deepEqual(page.state.presets, [item])
    assert.deepEqual(page.state.clients, ['a'])
    assert.equal(page.state.settings, false)
    assert.equal(page.state.run, null)                  // прогон apply роутера сюда не подтягивается
    await page.state.launch(false)
    assert.deepEqual(posted, [{ url: '/pipeline/run', data: { mode: 'apply-clients', dry_run: false } }])
    assert.ok(!requests.includes('/singbox/presets?branch=router'))
  } finally { page.unmount() }
})

test('schedule saves reactive form values with the revision and keeps unrelated fields', async () => {
  let saved
  const api = pipelineApi()
  api.put = async (url, data, headers) => { saved = { url, data, headers }; return { revision: 'v2' } }
  const page = mount('Pipeline', api)
  try {
    await settle()
    page.state.schedule.custom = { preserved: true }
    page.state.times.router = '04:00, 16:00'
    await page.state.saveSchedule()
    assert.equal(saved.url, '/pipeline/schedule')
    assert.deepEqual(saved.headers, { 'If-Match': 'v1' })
    assert.deepEqual(saved.data.jobs.router.times, ['04:00', '16:00'])
    assert.deepEqual(saved.data.custom, { preserved: true })
    assert.equal(page.state.revision, 'v2')
    assert.equal(page.state.error, '')
  } finally { page.unmount() }
})
