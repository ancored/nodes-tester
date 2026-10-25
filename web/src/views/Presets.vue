<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api, auth, can } from '../api.js'
import { t, locale } from '../i18n.js'
import Msg from '../components/Msg.vue'
import HelpButton from '../components/HelpButton.vue'

// Две ветки с одинаковым устройством: роутер (presets/ + nodes.json → sing-box роутера)
// и клиенты (clients/presets/ + whnodes.json → конфиги клиентов из раздела clients config.json).
const BRANCHES = {
  router: {
    title: t('Router'), dir: 'presets/', nodes: 'nodes.json', mode: 'apply',
    check: t('Check the build'), apply: t('Apply'),
    confirm: t('Apply the base and enabled presets to sing-box? If the config changed, sing-box restarts; without connectivity the previous config comes back.'),
    toggled: t('the resulting config passed sing-box check. To change the running sing-box, press “Apply”.'),
    empty: t('Rules come only from base.json.'),
  },
  clients: {
    title: t('Clients'), dir: 'clients/presets/', nodes: 'whnodes.json', mode: 'apply-clients',
    check: t('Check the build'), apply: t('Build clients'),
    confirm: t('Build and publish client configs from the bases and enabled client presets? Subscriptions are not fetched; a client whose config fails sing-box check stays on the previous one.'),
    toggled: t('the configs of all clients passed sing-box check. To publish them, press “Build clients”.'),
    empty: t('Rules come only from the client bases (clients/base_<name>.json).'),
  },
}
const route = useRoute()
const branch = computed(() => route.query.branch === 'clients' ? 'clients' : 'router')
const b = computed(() => BRANCHES[branch.value])

const presets = ref([]), groups = ref(null), clients = ref([]), settings = ref(true)
const error = ref(''), notice = ref(''), busy = ref(false), loadError = ref('')
const run = ref(null), log = ref(''), logOffset = ref(0)
const statusLabel = { running: t('running (run)'), ok: t('succeeded'), error: t('error'), busy: t('busy'), timeout: t('timeout'), interrupted: t('interrupted') }
const fileUrl = p => '/singbox/files/' + encodeURIComponent(p)
let timer = null

async function load() {
  try {
    const response = await api.get('/singbox/presets?branch=' + branch.value)
    presets.value = response.presets || []; groups.value = response.groups
    clients.value = response.clients || []; settings.value = response.settings !== false
    loadError.value = response.error || ''
  } catch (e) { loadError.value = e.message }
}
async function toggle(item, enabled) {
  busy.value = true; error.value = ''; notice.value = ''
  try {
    const doc = await api.get(fileUrl(item.path))
    const data = doc.data
    data._preset = { ...(data._preset || {}), enabled }
    await api.put(fileUrl(item.path), data, { 'If-Match': doc.revision })
    notice.value = t(enabled ? '“{title}” is enabled in the file; {result}' : '“{title}” is disabled in the file; {result}', { title: item.title, result: b.value.toggled })
  } catch (e) { error.value = e.message }
  finally { await load(); busy.value = false }
}
async function launch(dryRun) {
  if (!dryRun && !window.confirm(b.value.confirm)) return
  busy.value = true; error.value = ''; notice.value = ''
  try {
    const result = await api.post('/pipeline/run', { mode: b.value.mode, dry_run: dryRun })
    run.value = { id: result.run_id, dry_run: dryRun, status: 'running' }; log.value = ''; logOffset.value = 0
    poll()
  } catch (e) { error.value = e.message }
  finally { busy.value = false }
}
async function poll() {
  clearTimeout(timer)
  if (!run.value) return
  const current = run.value
  let more = false
  try {
    const chunk = await api.get(`/pipeline/runs/${current.id}/log?offset=${logOffset.value}`)
    if (run.value !== current) return
    log.value += chunk.text; logOffset.value = chunk.offset; more = !!chunk.text
    const history = await api.get('/pipeline/runs?limit=20')
    const row = (history.runs || []).find(r => r.id === current.id)
    if (row) Object.assign(current, { status: row.status, started: row.started })
  } catch (e) { error.value = e.message }
  // Дочитываем журнал и после завершения; при сбое сети опрос продолжается.
  if (run.value === current && (current.status === 'running' || more))
    timer = setTimeout(poll, more && current.status !== 'running' ? 0 : 2000)
}
// Последний прогон ветки живёт на сервере: после возврата на страницу подтягиваем его статус и журнал.
async function restoreRun() {
  if (run.value) return
  const mode = b.value.mode
  try {
    const last = ((await api.get('/pipeline/runs?limit=20')).runs || []).find(r => r.mode === mode)
    if (!last || run.value || b.value.mode !== mode) return
    run.value = { id: last.id, dry_run: !!last.dry_run, status: last.status, started: last.started }; log.value = ''; logOffset.value = 0
    poll()
  } catch (e) { error.value = e.message }
}
watch([() => can('singbox_files'), branch], ([ready]) => {
  clearTimeout(timer); run.value = null; log.value = ''; error.value = ''; notice.value = ''
  presets.value = []; groups.value = null
  if (ready) { load(); restoreRun() }
}, { immediate: true })
onBeforeUnmount(() => clearTimeout(timer))
</script>

<template>
  <section class="panel">
    <h2>{{ t('sing-box rules') }}<HelpButton topic="rules#presets" /></h2>
    <p v-if="!auth.verified" class="notice">{{ t('Log in with the administrator token.') }}</p>
    <p v-else-if="!can('singbox_files')" class="notice">{{ t('The section is available only in the embedded admin panel with a running orchestrator.') }}</p>
  </section>
  <template v-if="can('singbox_files')">
    <nav class="chips" :aria-label="t('Rules branch')">
      <RouterLink v-for="(item, key) in BRANCHES" :key="key" class="chip" :class="{ on: branch === key }"
                  :to="{ path: '/presets', query: key === 'router' ? {} : { branch: key } }">{{ item.title }}</RouterLink>
    </nav>
    <p v-if="error" class="notice bad" role="alert">{{ error }}</p>
    <p v-if="loadError" class="notice bad" role="alert">{{ loadError }}</p>
    <p v-if="notice" class="notice" role="status">{{ notice }}</p>
    <p v-if="branch === 'clients' && !settings" class="notice bad"><Msg :text="t('There is no {file} with the rule set distribution address ({example}): a preset with local rule sets cannot be built for clients.')"><template #file><RouterLink :to="{ path: '/singbox', query: { file: 'clients/settings.json' } }">clients/settings.json</RouterLink></template><template #example><code>{"rules_url": "https://…/"}</code></template></Msg></p>
    <section class="panel">
      <h2>{{ t('Presets') }} · {{ b.title }}<HelpButton topic="rules#list" /></h2>
      <p v-if="!presets.length" class="mut">{{ t('No presets.') }} {{ b.empty }} {{ t('Create a file {file} in “sing-box files”.', { file: b.dir + '<name>.json' }) }}</p>
      <div v-if="presets.length" class="wrap">
        <table class="responsive-table presets">
          <thead><tr><th class="l">{{ t('On') }}</th><th class="l">{{ t('Preset') }}</th><th>{{ t('Priority') }}</th><th class="l">{{ t('Required groups') }}</th><th class="l">{{ t('File') }}</th></tr></thead>
          <tbody>
            <tr v-for="item in presets" :key="item.name">
              <td class="l" :data-label="t('On')"><input type="checkbox" :checked="item.enabled" :disabled="busy" :aria-label="t('Enable {title}', { title: item.title })" @change="toggle(item, $event.target.checked)" /></td>
              <td class="l" :data-label="t('Preset')"><b>{{ item.title }}</b><small v-if="item.description" class="cell-note">{{ item.description }}</small></td>
              <td :data-label="t('Priority')">{{ item.priority }}<small v-for="(value, path) in item.priorities" :key="path" class="cell-note">{{ path }} {{ value }}</small></td>
              <td class="l" :data-label="t('Required groups')">
                <template v-if="item.requires.groups.length">{{ item.requires.groups.join(', ') }}<small v-if="item.missing_groups?.length" class="cell-note bad">{{ t('missing in {file}', { file: b.nodes }) }}: {{ item.missing_groups.join(', ') }}</small></template>
                <span v-else class="mut">—</span>
              </td>
              <td class="l" :data-label="t('File')"><RouterLink :to="{ path: '/singbox', query: { file: item.path } }">{{ item.name }}</RouterLink></td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-if="presets.some(p => p.missing_groups?.length)" class="notice bad">{{ t('Missing groups are enabled in “Subscriptions and build” → “Build parameters”.') }}</p>
      <p class="mut">{{ t('Groups in the current {file}: {groups}.', { file: b.nodes, groups: groups ? groups.join(', ') : t('unknown') }) }}</p>
      <p v-if="branch === 'clients'" class="mut">{{ t('Clients: {clients}.', { clients: clients.length ? clients.join(', ') : t('none') }) }} <RouterLink to="/subscriptions">{{ t('The list and links are in “Subscriptions and build” → “Clients”.') }}</RouterLink></p>
    </section>
    <section class="panel">
      <h2>{{ branch === 'clients' ? t('Client build') : t('Applying') }}<HelpButton :topic="branch === 'clients' ? 'rules#clients' : 'rules#apply'" /></h2>
      <div class="actions">
        <button class="btn" :disabled="busy || run?.status === 'running'" @click="launch(true)">{{ b.check }}</button>
        <button class="btn primary" :disabled="busy || run?.status === 'running'" @click="launch(false)">{{ b.apply }}</button>
      </div>
      <template v-if="run">
        <p>{{ run.dry_run ? t('Check') : (branch === 'clients' ? t('Build') : t('Applying')) }}<template v-if="run.started"> · {{ new Date(run.started * 1000).toLocaleString(locale()) }}</template>: <b>{{ statusLabel[run.status] || run.status }}</b> · <RouterLink to="/pipeline">{{ t('history in the “Pipeline”') }}</RouterLink></p>
        <pre class="log">{{ log || t('The log is empty…') }}</pre>
      </template>
    </section>
  </template>
</template>

<style scoped>
.presets td { vertical-align: top }
.actions { display: flex; flex-wrap: wrap; gap: .5rem; margin: .7rem 0 }
.log { max-height: 26rem; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; font-size: .85rem }
</style>
