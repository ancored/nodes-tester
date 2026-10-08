<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { onBeforeRouteLeave, useRoute } from 'vue-router'
import { api, auth, can } from '../api.js'

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
  } else if (JSON.stringify(left) !== JSON.stringify(right)) out.push(prefix || '(весь файл)')
  return out
}
function syncRules() {
  if (path.value !== 'rules.json') return
  try {
    const data = JSON.parse(text.value)
    if (data.version !== 1 || !Array.isArray(data.rules)) throw new Error('Нужны version=1 и массив rules')
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
  if (dirty.value && !window.confirm('Отбросить несохранённые изменения файла?')) return
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
  try { data = JSON.parse(text.value) } catch (e) { error.value = 'Ошибка JSON: ' + e.message; return }
  saving.value = true; error.value = ''; notice.value = ''
  try {
    const response = await api.put(url(path.value), data, { 'If-Match': revision.value })
    revision.value = response.revision; dirty.value = false; auth.dirty = false
    preview.value = null
    notice.value = savedNotice('Пресет сохранён', 'Файл сохранён')
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
  if (!window.confirm(`Восстановить ${path.value} из версии ${new Date(Number(ts) / 1e6).toLocaleString('ru-RU')}? Текущее содержимое сохранится в истории.`)) return
  try {
    await api.post(url(path.value) + '/restore', { ts }, { 'If-Match': revision.value })
    dirty.value = false; auth.dirty = false
    await select(path.value)
    notice.value = savedNotice('Версия восстановлена', 'Версия восстановлена')
  } catch (e) { error.value = e.message; if (e.status === 409) conflicted.value = true }
}
async function upload(event) {
  const file = event.target.files?.[0]
  if (!file) return
  try {
    const data = JSON.parse(await file.text())
    text.value = JSON.stringify(data, null, 2) + '\n'; changed()
    syncRules()
    notice.value = `Файл ${file.name} загружен в черновик. Нажмите «Сохранить».`
  } catch (e) { error.value = 'Не удалось прочитать JSON: ' + e.message }
  event.target.value = ''
}
async function uploadLocal(event, sourcePath) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file) return
  if (!/^rules\/[A-Za-z0-9._-]+\.json$/.test(sourcePath || '')) {
    error.value = 'Для локального источника укажите путь rules/<имя>.json'; return
  }
  try {
    const data = JSON.parse(await file.text())
    let current = 'missing'
    try { current = (await api.get(url(sourcePath))).revision }
    catch (e) { if (e.status !== 404) throw e }
    await api.put(url(sourcePath), data, { 'If-Match': current })
    notice.value = `${sourcePath} сохранён. Запустите конвейер для применения.`
    error.value = ''
    await refreshList()
  } catch (e) { error.value = 'Не удалось загрузить локальный источник: ' + e.message }
}
const isPreset = p => p.startsWith('presets/') || p.startsWith('clients/presets/')
function savedNotice(preset, other) {
  const what = isPreset(path.value) ? preset : other
  if (path.value.startsWith('presets/')) return `${what}. Чтобы изменить работающий sing-box, откройте «Правила» и нажмите «Применить».`
  if (path.value.startsWith('clients/')) return `${what}. Чтобы обновить конфиги клиентов, откройте «Правила» → «Клиенты» и нажмите «Собрать клиентов».`
  return `${what}. Для применения изменений запустите конвейер.`
}
async function removePreset() {
  const where = path.value.startsWith('clients/') ? 'Конфиги клиентов изменятся только после «Собрать клиентов»' : 'Работающий sing-box изменится только после «Применить»'
  if (!window.confirm(`Удалить ${path.value}? Файл сохранится в истории. ${where} в разделе «Правила».`)) return
  try {
    await api.del(url(path.value), { 'If-Match': revision.value })
    dirty.value = false; auth.dirty = false
    await refreshList(); await select('base.json')
    notice.value = 'Пресет удалён.'
  } catch (e) { error.value = e.message; if (e.status === 409) conflicted.value = true }
}
// Файлы по назначению: так пресет или клиентская база находятся без прокрутки полосы кнопок.
const FILE_GROUPS = [['Основные', p => known.includes(p)], ['Пресеты правил', p => p.startsWith('presets/')],
  ['Источники правил', p => p.startsWith('rules/')], ['Клиентские пресеты', p => p.startsWith('clients/presets/')],
  ['Клиенты', p => p.startsWith('clients/')], ['Прочее', () => true]]
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
onBeforeRouteLeave(() => !dirty.value || window.confirm('Отбросить несохранённые изменения файла?'))
</script>

<template>
  <section class="panel">
    <h2>Исходные файлы sing-box</h2>
    <p class="help-text">Здесь хранятся база роутера, источники правил, пресеты и клиентские базы. Сохранение меняет исходный файл, но не работающий sing-box. Базу и пресеты можно применить в <RouterLink to="/presets">«Правилах»</RouterLink> без загрузки подписок; полный цикл запускается в <RouterLink to="/pipeline">«Конвейере»</RouterLink>.</p>
    <p v-if="!auth.verified" class="notice">Войдите с токеном администратора.</p>
    <p v-else-if="!can('singbox_files')" class="notice">Редактор доступен только во встроенной админке с запущенным оркестратором.</p>
    <template v-else>
      <p v-if="error" class="notice bad" role="alert">{{ error }}</p>
      <p v-if="notice" class="notice" role="status">{{ notice }}</p>
      <div class="toolbar file-picker">
        <label>Файл<select :value="path" @change="pick($event)">
          <optgroup v-for="[title, items] in fileGroups" :key="title" :label="title">
            <option v-for="item in items" :key="item" :value="item">{{ item }}</option>
          </optgroup>
        </select></label>
        <label>Новый или другой путь<input v-model="newPath" placeholder="presets/my-rules.json" @keydown.enter="addPath" /></label>
        <button class="btn" :disabled="!newPath.trim()" @click="addPath">Открыть или создать</button>
      </div>
    </template>
  </section>
  <section v-if="can('singbox_files')" class="panel">
    <h2>{{ path }}</h2>
    <p class="help-text">Редактируйте JSON или загрузите локальный файл. При сохранении base.json и пресетов (presets/*.json) сервер проверит итоговую конфигурацию через sing-box, при сохранении клиентских баз, пресетов (clients/presets/*.json) и clients/settings.json — конфиг каждого клиента.</p>
    <template v-if="path === 'rules.json'">
      <h3>Источники правил</h3>
      <p class="help-text">Назначение — имя файла в /etc/sing-box/rules/. URL скачивается при запуске; локальный JSON загружается здесь.</p>
      <p v-if="!rulesValid" class="notice bad">Исправьте JSON ниже, чтобы редактировать список правил.</p>
      <template v-else>
        <div v-for="(item, index) in ruleRows" :key="index" class="rule-row">
          <label>Назначение <input :value="item.dest" placeholder="category-ru.srs" @input="editRule(index, 'dest', $event.target.value)" /></label>
          <label>Источник <select :value="item.url !== undefined ? 'url' : 'file'" @change="editRule(index, 'source', $event.target.value)"><option value="url">URL</option><option value="file">Локальный файл</option></select></label>
          <label v-if="item.url !== undefined">URL <input :value="item.url" type="url" placeholder="https://example.org/rule.srs" @input="editRule(index, 'url', $event.target.value)" /></label>
          <template v-else>
            <label>Путь <input :value="item.file" placeholder="rules/my-domains.json" @input="editRule(index, 'file', $event.target.value)" /></label>
            <label>Загрузить локальный JSON <input type="file" accept=".json,application/json" @change="uploadLocal($event, item.file)" /></label>
          </template>
          <button class="btn" @click="removeRule(index)">Удалить источник</button>
        </div>
        <button class="btn" @click="addRule">Добавить источник</button>
        <p class="help-text">После изменений списка нажмите «Сохранить файл».</p>
      </template>
    </template>
    <label>Загрузить JSON в черновик <input type="file" accept=".json,application/json" @change="upload" /></label>
    <label class="json-editor-label">Содержимое JSON <textarea v-model="text" class="cfg-editor" spellcheck="false" rows="20" @input="changed(); syncRules()"></textarea></label>
    <p v-if="conflicted" class="notice bad" role="alert">Файл изменился в другом окне. Перечитайте его перед сохранением.</p>
    <div class="actions">
      <button class="btn" :disabled="!dirty || saving || conflicted" @click="save">Сохранить файл</button>
      <button class="btn" @click="select(path)">Перечитать файл</button>
      <button v-if="isPreset(path) && revision !== 'missing'" class="btn" :disabled="saving" @click="removePreset">Удалить пресет</button>
    </div>
    <h3>Предыдущие версии</h3>
    <p v-if="!versions.length">Предыдущих версий нет.</p>
    <div class="versions">
      <div v-for="ts in versions" :key="ts" class="version-row">
        <span>{{ new Date(Number(ts) / 1e6).toLocaleString('ru-RU') }}</span>
        <button class="btn" :disabled="dirty || conflicted" @click="showVersion(ts)">Посмотреть</button>
        <button class="btn" :disabled="dirty || conflicted || preview?.ts !== ts" @click="restore(ts)">Восстановить</button>
      </div>
    </div>
    <div v-if="preview" class="version-preview">
      <h4>Версия {{ new Date(Number(preview.ts) / 1e6).toLocaleString('ru-RU') }}</h4>
      <p>{{ preview.paths.length ? `Отличаются поля (${preview.paths.length}): ${preview.paths.slice(0, 30).join(', ')}${preview.paths.length > 30 ? '…' : ''}` : 'Содержимое совпадает с текущим файлом.' }}</p>
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
