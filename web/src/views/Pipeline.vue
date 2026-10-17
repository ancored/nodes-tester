<script setup>
import { computed, onBeforeUnmount, onMounted, ref, toRaw } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { api, auth, can } from '../api.js'
import { t, locale } from '../i18n.js'
import Msg from '../components/Msg.vue'

const state = ref(null), runs = ref([]), schedule = ref(null), revision = ref('')
const error = ref(''), pollError = ref(''), logError = ref(''), notice = ref(''), log = ref(''), logOffset = ref(0), viewedRun = ref('')
const followLive = ref(true)
const saving = ref(false), busy = ref(false), dirty = ref(false), conflicted = ref(false)
const times = ref({ router: '', clients: '' })
const statusLabel = { running: t('running (run)'), ok: t('succeeded'), error: t('error'), busy: t('busy'), timeout: t('timeout'), interrupted: t('interrupted') }
const modeLabel = { router: t('router'), clients: t('clients'), fetch: t('subscription fetch'), apply: t('base and rules'), 'apply-clients': t('client rules') }
const triggerLabel = { schedule: t('scheduled'), manual: t('manually'), rebuild: t('rebuild on degradation') }
const when = ts => new Date(ts * 1000).toLocaleString(locale())
let timer = null, polling = false, selection = 0, disposed = false

const live = computed(() => state.value?.current)
const selectedRun = computed(() => runs.value.find(row => row.id === viewedRun.value) || (live.value?.id === viewedRun.value ? live.value : null))
function selectRun(id) {
  selection++; viewedRun.value = id; log.value = ''; logOffset.value = 0; logError.value = ''
}
function setEditor(data) {
  schedule.value = structuredClone(data)
  times.value = Object.fromEntries(['router', 'clients'].map(mode =>
    [mode, data.jobs[mode].times.join(', ')]))
  dirty.value = false; auth.dirty = false; conflicted.value = false
}
async function loadSchedule() {
  const response = await api.get('/pipeline/schedule')
  revision.value = response.revision; setEditor(response.data)
}
async function poll() {
  if (disposed || polling || !auth.verified || !can('pipeline')) return
  polling = true
  try {
    const [next, history] = await Promise.all([api.get('/pipeline'), api.get('/pipeline/runs?limit=20')])
    if (disposed) return
    state.value = next; runs.value = history.runs || []; pollError.value = ''
    if (!schedule.value) { revision.value = next.revision; setEditor(next.schedule) }
    if (followLive.value && next.current && viewedRun.value !== next.current.id) {
      selectRun(next.current.id)
    }
    if (viewedRun.value) await readLog()
  } catch (e) { pollError.value = e.message }
  finally { polling = false }
}
async function readLog() {
  const id = viewedRun.value, offset = logOffset.value, version = selection
  try {
    const chunk = await api.get(`/pipeline/runs/${id}/log?offset=${offset}`)
    if (disposed || version !== selection || offset !== logOffset.value) return
    log.value += chunk.text; logOffset.value = chunk.offset; logError.value = ''
  } catch (e) {
    if (!disposed && version === selection) logError.value = e.message
  }
}
async function launch(mode, dryRun) {
  if (!dryRun && !window.confirm(
    mode === 'router'
      ? t('Apply the router configuration? sing-box may restart and connections may drop.')
      : t('Build and publish the client configurations?'))) return
  busy.value = true; error.value = ''; notice.value = ''
  try {
    const result = await api.post('/pipeline/run', { mode, dry_run: dryRun })
    followLive.value = true; selectRun(result.run_id)
    notice.value = dryRun ? t('The check has started. The result will appear in the log.') : t('Applying has started. Watch the log.')
    await poll()
  } catch (e) { error.value = e.message }
  finally { busy.value = false }
}
function edit() { dirty.value = true; auth.dirty = true }
async function saveSchedule() {
  if (conflicted.value) return
  const next = structuredClone(toRaw(schedule.value))
  for (const mode of ['router', 'clients']) {
    const values = times.value[mode].split(',').map(x => x.trim()).filter(Boolean)
    if (values.some(x => !/^([01]\d|2[0-3]):[0-5]\d$/.test(x)) || new Set(values).size !== values.length) {
      error.value = t('Set the times for “{mode}” as HH:MM separated by commas, without repeats.', { mode: modeLabel[mode] }); return
    }
    next.jobs[mode].times = values
  }
  saving.value = true; error.value = ''; notice.value = ''
  try {
    const response = await api.put('/pipeline/schedule', next, { 'If-Match': revision.value })
    revision.value = response.revision; setEditor(next)
    notice.value = t('The schedule is saved. It takes effect immediately.')
    await poll()
  } catch (e) {
    error.value = e.message
    if (e.status === 409) conflicted.value = true
  } finally { saving.value = false }
}
async function reloadSchedule() {
  if (dirty.value && !window.confirm(t('Discard the unsaved schedule?'))) return
  try { await loadSchedule(); error.value = '' } catch (e) { error.value = e.message }
}
function showRun(row) {
  followLive.value = row.id === live.value?.id
  selectRun(row.id); readLog()
}
onMounted(async () => { await poll(); if (!disposed) timer = setInterval(poll, 2000) })
onBeforeUnmount(() => { disposed = true; clearInterval(timer); auth.dirty = false })
onBeforeRouteLeave(() => !dirty.value || window.confirm(t('Discard the unsaved schedule?')))
</script>

<template>
  <section class="panel">
    <h2>{{ t('Update pipeline') }}</h2>
    <p class="help-text">{{ t('The “Router” mode fetches subscriptions, builds nodes and rules, then applies the configuration to the router. The “Clients” mode builds separate client configurations. A check creates intermediate files and may download subscriptions, but does not apply them.') }}</p>
    <p v-if="!auth.verified" class="notice">{{ t('Log in with the administrator token to see the log and control the pipeline.') }}</p>
    <p v-else-if="!can('pipeline')" class="notice">{{ t('Control is unavailable: it needs a running tester with an admin token and the nodes-tester command in PATH.') }}</p>
    <template v-if="can('pipeline')">
      <p v-if="error" class="notice bad" role="alert">{{ error }}</p>
      <p v-if="pollError" class="notice bad" role="alert">{{ pollError }}. {{ t('Polling will be retried automatically.') }}</p>
      <p v-if="notice" class="notice" role="status">{{ notice }}</p>
      <div v-for="mode in ['router', 'clients']" :key="mode" class="panel">
        <h3>{{ mode === 'router' ? t('Router') : t('Clients') }}</h3>
        <p>{{ t('Next run: {time}', { time: state?.next_run?.[mode] ? when(state.next_run[mode]) : t('not scheduled') }) }}</p>
        <div class="actions">
          <button class="btn" :disabled="busy || !!live" @click="launch(mode, true)">{{ t('Check (dry run)') }}</button>
          <button class="btn" :disabled="busy || !!live" @click="launch(mode, false)">{{ t('Apply') }}</button>
        </div>
      </div>
      <p role="status">{{ live ? t('Running: {mode} · {action}', { mode: modeLabel[live.mode] || live.mode, action: live.dry_run ? t('check') : t('apply') }) : t('The pipeline is not running now') }}</p>
      <div class="toolbar">
        <h3>{{ t('Log') }}</h3>
        <button v-if="live && viewedRun !== live.id" class="btn" @click="showRun(live)">{{ t('To the current run') }}</button>
      </div>
      <p v-if="selectedRun" class="field-note">{{ when(selectedRun.started) }} · {{ modeLabel[selectedRun.mode] || selectedRun.mode }} · {{ selectedRun.dry_run ? t('check') : t('apply') }} · {{ statusLabel[selectedRun.status] || selectedRun.status }}</p>
      <p v-if="logError" class="notice bad" role="alert">{{ logError }}. {{ t('Loading the log will be retried automatically.') }}</p>
      <pre class="log pipeline-log" aria-live="polite">{{ log || (viewedRun ? t('The log is empty so far.') : t('Choose a run from the history.')) }}</pre>
      <h3>{{ t('History') }}</h3>
      <div v-if="runs.length" class="wrap pipeline-history">
        <table class="responsive-table" :aria-label="t('The last 20 pipeline runs')">
          <thead><tr><th class="l">{{ t('Start') }}</th><th class="l">{{ t('Mode') }}</th><th class="l">{{ t('Action') }}</th><th class="l">{{ t('Trigger') }}</th><th class="l">{{ t('Status') }}</th><th class="l">{{ t('Log') }}</th></tr></thead>
          <tbody>
            <tr v-for="row in runs" :key="row.id" :class="{ selected: row.id === viewedRun }">
              <td class="l" :data-label="t('Start')">{{ when(row.started) }}</td>
              <td class="l" :data-label="t('Mode')">{{ modeLabel[row.mode] || row.mode }}</td>
              <td class="l" :data-label="t('Action')">{{ row.dry_run ? t('check') : t('apply') }}</td>
              <td class="l" :data-label="t('Trigger')">{{ triggerLabel[row.trigger] || row.trigger || '—' }}</td>
              <td class="l" :data-label="t('Status')"><span :class="{ good: row.status === 'ok', bad: ['error', 'timeout', 'interrupted'].includes(row.status) }">{{ statusLabel[row.status] || row.status }}</span></td>
              <td class="l" :data-label="t('Log')"><button class="btn sm" :aria-pressed="row.id === viewedRun" :aria-label="t('Log of the run at {time}', { time: when(row.started) })" @click="showRun(row)">{{ row.id === viewedRun ? t('Opened') : t('Open') }}</button></td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-if="!runs.length">{{ t('No runs yet.') }}</p>
    </template>
  </section>
  <section v-if="can('pipeline') && schedule" class="panel">
    <h2>{{ t('Daily schedule') }}</h2>
    <p class="help-text">{{ t('Router time. A new schedule takes effect after saving; jobs missed during a restart run once.') }}</p>
    <p class="notice"><Msg :text="t('If cron already runs this pipeline, remove the {line} line from crontab after enabling the schedule here. Otherwise one of the coinciding runs gets the “busy” status.')"><template #line><code>nodes-tester pipeline</code></template></Msg></p>
    <div v-for="mode in ['router', 'clients']" :key="mode" class="panel">
      <label class="check"><input v-model="schedule.jobs[mode].enabled" type="checkbox" @change="edit" /> {{ mode === 'router' ? t('Router') : t('Clients') }}</label>
      <label>{{ t('Times HH:MM, comma-separated') }} <input v-model="times[mode]" type="text" placeholder="03:00, 15:00" @input="edit" /></label>
    </div>
    <label>{{ t('Run timeout, s') }} <input v-model.number="schedule.timeout" type="number" min="1" max="86400" @input="edit" /></label>
    <p v-if="conflicted" class="notice bad" role="alert">{{ t('The file changed in another window. Reload the schedule before saving.') }}</p>
    <div class="actions">
      <button class="btn" :disabled="!dirty || saving || conflicted" @click="saveSchedule">{{ t('Save schedule') }}</button>
      <button class="btn" @click="reloadSchedule">{{ t('Reload file') }}</button>
    </div>
  </section>
</template>

<style scoped>
.pipeline-log { height: 22rem; overflow: auto; overflow-wrap: anywhere }
.pipeline-history { max-height: 24rem; overflow: auto }
.pipeline-history tr.selected { background: var(--hover); box-shadow: inset 3px 0 var(--accent) }
.pipeline-history button[aria-pressed="true"] { border-color: var(--accent); color: var(--accent) }
.toolbar h3 { margin: 0 }
.actions { display: flex; flex-wrap: wrap; gap: .5rem }
</style>
