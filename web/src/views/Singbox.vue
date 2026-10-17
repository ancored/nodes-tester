<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { onBeforeRouteLeave, useRoute } from 'vue-router'
import { api, auth, can } from '../api.js'
import { t, locale } from '../i18n.js'
import Msg from '../components/Msg.vue'

const files = ref([]), path = ref('base.json'), text = ref(''), revision = ref('missing')
const versions = ref([]), error = ref(''), notice = ref(''), dirty = ref(false), conflicted = ref(false), saving = ref(false)
const preview = ref(null)
const newPath = ref(''), ruleRows = ref([]), rulesValid = ref(true)
const known = ['base.json', 'rules.json']
const route = useRoute()
const url = p => '/singbox/files/' + encodeURIComponent(p)
function changed() { dirty.value = true; auth.dirty = true }
function changedPaths(left, right, prefix = '', out = []) {
  const objects = value => value && typeof value === 'object' && !Array.isArray(value)
  if (objects(left) && objects(right)) {
    for (const key of new Set([...Object.keys(left), ...Object.keys(right)]))
      changedPaths(left[key], right[key], prefix ? `${prefix}.${key}` : key, out)
  } else if (JSON.stringify(left) !== JSON.stringify(right)) out.push(prefix || t('(whole file)'))
  return out
}
function syncRules() {
  if (path.value !== 'rules.json') return
  try {
    const data = JSON.parse(text.value)
    if (data.version !== 1 || !Array.isArray(data.rules)) throw new Error(t('version=1 and a rules array are required'))
    ruleRows.value = data.rules.map(item => ({ ...item }))
    rulesValid.value = true
  } catch { rulesValid.value = false }
}
function writeRules() {
  text.value = JSON.stringify({ version: 1, rules: ruleRows.value }, null, 2) + '\n'
  rulesValid.value = true
  changed()
}
function addRule() { ruleRows.value.push({ dest: '', url: '' }); writeRules() }
function removeRule(index) { ruleRows.value.splice(index, 1); writeRules() }
function editRule(index, key, value) {
  const item = ruleRows.value[index]
  if (key === 'source') {
    delete item.url; delete item.file
    item[value] = ''
  } else item[key] = value
  writeRules()
}
async function refreshList() {
  const response = await api.get('/singbox/files')
  files.value = response.files || []
}
async function select(next) {
  if (dirty.value && !window.confirm(t('Discard unsaved file changes?'))) return
  try {
    const response = await api.get(url(next))
    path.value = next; text.value = JSON.stringify(response.data, null, 2) + '\n'; revision.value = response.revision
  } catch (e) {
    if (e.status !== 404) { error.value = e.message; return }
    path.value = next; text.value = '{}\n'; revision.value = 'missing'
  }
  dirty.value = false; auth.dirty = false; conflicted.value = false; error.value = ''; notice.value = ''
  preview.value = null
  syncRules()
  try { versions.value = (await api.get(url(next) + '/history')).versions || [] }
  catch (e) { error.value = e.message }
}
async function save() {
  if (conflicted.value) return
  let data
  try { data = JSON.parse(text.value) } catch (e) { error.value = t('JSON error: {error}', { error: e.message }); return }
  saving.value = true; error.value = ''; notice.value = ''
  try {
    const response = await api.put(url(path.value), data, { 'If-Match': revision.value })
    revision.value = response.revision; dirty.value = false; auth.dirty = false
    preview.value = null
    notice.value = savedNotice(t('Preset saved'), t('File saved'))
    await refreshList()
    versions.value = (await api.get(url(path.value) + '/history')).versions || []
  } catch (e) { error.value = e.message; if (e.status === 409) conflicted.value = true }
  finally { saving.value = false }
}
async function showVersion(ts) {
  try {
    const response = await api.get(url(path.value) + '/history?ts=' + encodeURIComponent(ts))
    const current = JSON.parse(text.value)
    preview.value = { ts, data: response.data, paths: changedPaths(current, response.data) }
    error.value = ''
  } catch (e) { error.value = e.message }
}
async function restore(ts) {
  if (preview.value?.ts !== ts) return
  if (!window.confirm(t('Restore {path} from the version of {time}? The current content will be kept in the history.', { path: path.value, time: versionTime(ts) }))) return
  try {
    await api.post(url(path.value) + '/restore', { ts }, { 'If-Match': revision.value })
    dirty.value = false; auth.dirty = false
    await select(path.value)
    notice.value = savedNotice(t('Version restored'), t('Version restored'))
  } catch (e) { error.value = e.message; if (e.status === 409) conflicted.value = true }
}
async function upload(event) {
  const file = event.target.files?.[0]
  if (!file) return
  try {
    const data = JSON.parse(await file.text())
    text.value = JSON.stringify(data, null, 2) + '\n'; changed()
    syncRules()
    notice.value = t('File {name} is loaded into the draft. Press “Save file”.', { name: file.name })
  } catch (e) { error.value = t('Could not read JSON: {error}', { error: e.message }) }
  event.target.value = ''
}
async function uploadLocal(event, sourcePath) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file) return
  if (!/^rules\/[A-Za-z0-9._-]+\.json$/.test(sourcePath || '')) {
    error.value = t('Set the path rules/<name>.json for a local source'); return
  }
  try {
    const data = JSON.parse(await file.text())
    let current = 'missing'
    try { current = (await api.get(url(sourcePath))).revision }
    catch (e) { if (e.status !== 404) throw e }
    await api.put(url(sourcePath), data, { 'If-Match': current })
    notice.value = t('{path} saved. Run the pipeline to apply it.', { path: sourcePath })
    error.value = ''
    await refreshList()
  } catch (e) { error.value = t('Could not upload the local source: {error}', { error: e.message }) }
}
const isPreset = p => p.startsWith('presets/') || p.startsWith('clients/presets/')
function savedNotice(preset, other) {
  const what = isPreset(path.value) ? preset : other
  if (path.value.startsWith('presets/')) return what + '. ' + t('To change the running sing-box, open “Rules” and press “Apply”.')
  if (path.value.startsWith('clients/')) return what + '. ' + t('To update the client configs, open “Rules” → “Clients” and press “Build clients”.')
  return what + '. ' + t('Run the pipeline to apply the changes.')
}
async function removePreset() {
  const ask = path.value.startsWith('clients/')
    ? t('Delete {path}? The file will be kept in the history. The client configs change only after “Build clients” in the “Rules” section.', { path: path.value })
    : t('Delete {path}? The file will be kept in the history. The running sing-box changes only after “Apply” in the “Rules” section.', { path: path.value })
  if (!window.confirm(ask)) return
  try {
    await api.del(url(path.value), { 'If-Match': revision.value })
    dirty.value = false; auth.dirty = false
    await refreshList(); await select('base.json')
    notice.value = t('Preset deleted.')
  } catch (e) { error.value = e.message; if (e.status === 409) conflicted.value = true }
}
// Файлы по назначению: так пресет или клиентская база находятся без прокрутки полосы кнопок.
const FILE_GROUPS = [[t('Main'), p => known.includes(p)], [t('Rule presets'), p => p.startsWith('presets/')],
  [t('Rule sources'), p => p.startsWith('rules/')], [t('Client presets'), p => p.startsWith('clients/presets/')],
  [t('Clients'), p => p.startsWith('clients/')], [t('Other'), () => true]]
const versionTime = ts => new Date(Number(ts) / 1e6).toLocaleString(locale())
const fileGroups = computed(() => {
  const all = [...new Set([...known, ...files.value.map(f => f.path), path.value])], seen = new Set()
  return FILE_GROUPS.map(([title, test]) => [title, all.filter(p => !seen.has(p) && test(p) && seen.add(p))]).filter(([, items]) => items.length)
})
async function pick(event) {
  const next = event.target.value
  await select(next)
  event.target.value = path.value                 // отмена при несохранённых правках — вернуть выбор
}
function addPath() {
  const next = newPath.value.trim()
  if (!next) return
  select(next)
}
watch(() => can('singbox_files'), async ready => {
  if (!ready || files.value.length) return
  try { await refreshList(); await select(typeof route.query.file === 'string' ? route.query.file : 'base.json') } catch (e) { error.value = e.message }
}, { immediate: true })
onBeforeUnmount(() => { auth.dirty = false })
onBeforeRouteLeave(() => !dirty.value || window.confirm(t('Discard unsaved file changes?')))
</script>

<template>
  <section class="panel">
    <h2>{{ t('sing-box source files') }}</h2>
    <p class="help-text"><Msg :text="t('The router base, rule sources, presets and client bases live here. Saving changes the source file, not the running sing-box. The base and presets can be applied in {rules} without fetching subscriptions; the full cycle runs in the {pipeline}.')"><template #rules><RouterLink to="/presets">{{ t('“Rules”') }}</RouterLink></template><template #pipeline><RouterLink to="/pipeline">{{ t('“Pipeline”') }}</RouterLink></template></Msg></p>
    <p v-if="!auth.verified" class="notice">{{ t('Log in with the administrator token.') }}</p>
    <p v-else-if="!can('singbox_files')" class="notice">{{ t('The editor is available only in the embedded admin panel with a running orchestrator.') }}</p>
    <template v-else>
      <p v-if="error" class="notice bad" role="alert">{{ error }}</p>
      <p v-if="notice" class="notice" role="status">{{ notice }}</p>
      <div class="toolbar file-picker">
        <label>{{ t('File') }}<select :value="path" @change="pick($event)">
          <optgroup v-for="[title, items] in fileGroups" :key="title" :label="title">
            <option v-for="item in items" :key="item" :value="item">{{ item }}</option>
          </optgroup>
        </select></label>
        <label>{{ t('New or another path') }}<input v-model="newPath" placeholder="presets/my-rules.json" @keydown.enter="addPath" /></label>
        <button class="btn" :disabled="!newPath.trim()" @click="addPath">{{ t('Open or create') }}</button>
      </div>
    </template>
  </section>
  <section v-if="can('singbox_files')" class="panel">
    <h2>{{ path }}</h2>
    <p class="help-text">{{ t('Edit the JSON or upload a local file. When base.json and presets (presets/*.json) are saved, the server checks the resulting configuration with sing-box; when client bases, presets (clients/presets/*.json) and clients/settings.json are saved, it checks the config of each client.') }}</p>
    <template v-if="path === 'rules.json'">
      <h3>{{ t('Rule sources') }}</h3>
      <p class="help-text">{{ t('The destination is a file name in /etc/sing-box/rules/. A URL is downloaded at run time; a local JSON is uploaded here.') }}</p>
      <p v-if="!rulesValid" class="notice bad">{{ t('Fix the JSON below to edit the rule list.') }}</p>
      <template v-else>
        <div v-for="(item, index) in ruleRows" :key="index" class="rule-row">
          <label>{{ t('Destination') }} <input :value="item.dest" placeholder="category-ru.srs" @input="editRule(index, 'dest', $event.target.value)" /></label>
          <label>{{ t('Source') }} <select :value="item.url !== undefined ? 'url' : 'file'" @change="editRule(index, 'source', $event.target.value)"><option value="url">URL</option><option value="file">{{ t('Local file') }}</option></select></label>
          <label v-if="item.url !== undefined">URL <input :value="item.url" type="url" placeholder="https://example.org/rule.srs" @input="editRule(index, 'url', $event.target.value)" /></label>
          <template v-else>
            <label>{{ t('Path') }} <input :value="item.file" placeholder="rules/my-domains.json" @input="editRule(index, 'file', $event.target.value)" /></label>
            <label>{{ t('Upload a local JSON') }} <input type="file" accept=".json,application/json" @change="uploadLocal($event, item.file)" /></label>
          </template>
          <button class="btn" @click="removeRule(index)">{{ t('Delete source') }}</button>
        </div>
        <button class="btn" @click="addRule">{{ t('Add source') }}</button>
        <p class="help-text">{{ t('After changing the list press “Save file”.') }}</p>
      </template>
    </template>
    <label>{{ t('Upload JSON into the draft') }} <input type="file" accept=".json,application/json" @change="upload" /></label>
    <label class="json-editor-label">{{ t('JSON content') }} <textarea v-model="text" class="cfg-editor" spellcheck="false" rows="20" @input="changed(); syncRules()"></textarea></label>
    <p v-if="conflicted" class="notice bad" role="alert">{{ t('The file changed in another window. Reload it before saving.') }}</p>
    <div class="actions">
      <button class="btn" :disabled="!dirty || saving || conflicted" @click="save">{{ t('Save file') }}</button>
      <button class="btn" @click="select(path)">{{ t('Reload file') }}</button>
      <button v-if="isPreset(path) && revision !== 'missing'" class="btn" :disabled="saving" @click="removePreset">{{ t('Delete preset') }}</button>
    </div>
    <h3>{{ t('Previous versions') }}</h3>
    <p v-if="!versions.length">{{ t('No previous versions.') }}</p>
    <div class="versions">
      <div v-for="ts in versions" :key="ts" class="version-row">
        <span>{{ versionTime(ts) }}</span>
        <button class="btn" :disabled="dirty || conflicted" @click="showVersion(ts)">{{ t('View') }}</button>
        <button class="btn" :disabled="dirty || conflicted || preview?.ts !== ts" @click="restore(ts)">{{ t('Restore') }}</button>
      </div>
    </div>
    <div v-if="preview" class="version-preview">
      <h4>{{ t('Version {time}', { time: versionTime(preview.ts) }) }}</h4>
      <p>{{ preview.paths.length ? t('Differing fields ({n}): {list}', { n: preview.paths.length, list: preview.paths.slice(0, 30).join(', ') + (preview.paths.length > 30 ? '…' : '') }) : t('The content matches the current file.') }}</p>
      <pre>{{ JSON.stringify(preview.data, null, 2) }}</pre>
    </div>
  </section>
</template>

<style scoped>
.actions, .versions { display: flex; flex-wrap: wrap; gap: .5rem; margin: .7rem 0 }
.file-picker { align-items: end }
.file-picker label { min-width: 0; max-width: 100% }
.file-picker select { width: 18rem }
.json-editor-label { display: block; margin: 1rem 0 }
.cfg-editor { display: block; margin-top: 6px }
.rule-row { display: flex; flex-wrap: wrap; align-items: end; gap: .5rem; margin: .7rem 0; padding: .7rem; border: 1px solid var(--line); border-radius: .5rem }
.rule-row label { display: flex; flex-direction: column; gap: .2rem; min-width: 0; max-width: 100% }
.rule-row input { max-width: 20rem }
.version-row { display: flex; align-items: center; flex-wrap: wrap; gap: .5rem }
.version-preview pre { max-height: 22rem; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere }
</style>
