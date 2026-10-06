<script setup>
import { onBeforeUnmount, ref, watch } from 'vue'
import { api, auth, can } from '../api.js'

const presets = ref([]), groups = ref(null), error = ref(''), notice = ref(''), busy = ref(false)
const loadError = ref('')
const run = ref(null), log = ref(''), logOffset = ref(0)
const statusLabel = { running: 'выполняется', ok: 'успешно', error: 'ошибка', busy: 'занято', timeout: 'тайм-аут', interrupted: 'прервано' }
const fileUrl = p => '/singbox/files/' + encodeURIComponent(p)
let timer = null

async function load() {
  try {
    const response = await api.get('/singbox/presets')
    presets.value = response.presets || []; groups.value = response.groups
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
    notice.value = `«${item.title}» ${enabled ? 'включён' : 'выключен'} в файле; итоговый конфиг прошёл sing-box check. Чтобы изменить работающий sing-box, нажмите «Применить».`
  } catch (e) { error.value = e.message }
  finally { await load(); busy.value = false }
}
async function launch(dryRun) {
  if (!dryRun && !window.confirm('Применить базу и включённые пресеты к sing-box? Если конфиг изменился, sing-box перезапустится; без связности вернётся прежний конфиг.')) return
  busy.value = true; error.value = ''; notice.value = ''
  try {
    const result = await api.post('/pipeline/run', { mode: 'apply', dry_run: dryRun })
    run.value = { id: result.run_id, dry_run: dryRun, status: 'running' }; log.value = ''; logOffset.value = 0
    poll()
  } catch (e) { error.value = e.message }
  finally { busy.value = false }
}
async function poll() {
  clearTimeout(timer)
  if (!run.value) return
  let more = false
  try {
    const chunk = await api.get(`/pipeline/runs/${run.value.id}/log?offset=${logOffset.value}`)
    log.value += chunk.text; logOffset.value = chunk.offset; more = !!chunk.text
    const history = await api.get('/pipeline/runs?limit=20')
    const row = (history.runs || []).find(r => r.id === run.value.id)
    if (row) Object.assign(run.value, { status: row.status, started: row.started })
  } catch (e) { error.value = e.message }
  // Дочитываем журнал и после завершения; при сбое сети опрос продолжается.
  if (run.value.status === 'running' || more) timer = setTimeout(poll, more && run.value.status !== 'running' ? 0 : 2000)
}
// Последний прогон apply живёт на сервере: после возврата на страницу подтягиваем его статус и журнал.
async function restoreRun() {
  if (run.value) return
  try {
    const last = ((await api.get('/pipeline/runs?limit=20')).runs || []).find(r => r.mode === 'apply')
    if (!last || run.value) return
    run.value = { id: last.id, dry_run: !!last.dry_run, status: last.status, started: last.started }; log.value = ''; logOffset.value = 0
    poll()
  } catch (e) { error.value = e.message }
}
watch(() => can('singbox_files'), ready => { if (ready) { load(); restoreRun() } }, { immediate: true })
onBeforeUnmount(() => clearTimeout(timer))
</script>

<template>
  <section class="panel">
    <h2>Правила sing-box</h2>
    <div class="help-text">
      <p>Правила маршрутизации и DNS собираются из пресетов, отдельных JSON-файлов в <code>singbox/presets/</code>. Итоговый конфиг: база + ноды + включённые пресеты по приоритету (меньше — выше). Пресет содержит всё, чего нет в базе: правила, DNS-серверы, наборы правил.</p>
      <p>Переключатель меняет файл пресета; сервер заранее проверяет итоговый конфиг через <code>sing-box check</code> и не сохраняет изменение, если проверка не прошла. Работающий sing-box меняет только «Применить».</p>
    </div>
    <p v-if="!auth.verified" class="notice">Войдите с токеном администратора.</p>
    <p v-else-if="!can('singbox_files')" class="notice">Раздел доступен только во встроенной админке с запущенным оркестратором.</p>
  </section>
  <template v-if="can('singbox_files')">
    <p v-if="error" class="notice bad" role="alert">{{ error }}</p>
    <p v-if="loadError" class="notice bad" role="alert">{{ loadError }}</p>
    <p v-if="notice" class="notice" role="status">{{ notice }}</p>
    <section class="panel">
      <h2>Пресеты</h2>
      <p v-if="!presets.length" class="mut">Пресетов нет. Правила берутся только из base.json. Создайте файл presets/&lt;имя&gt;.json в «Файлах sing-box».</p>
      <div v-if="presets.length" class="wrap">
        <table class="responsive-table presets">
          <thead><tr><th class="l">Вкл.</th><th class="l">Пресет</th><th>Приоритет</th><th class="l">Нужны группы</th><th class="l">Файл</th></tr></thead>
          <tbody>
            <tr v-for="item in presets" :key="item.name">
              <td class="l" data-label="Вкл."><input type="checkbox" :checked="item.enabled" :disabled="busy" :aria-label="'Включить ' + item.title" @change="toggle(item, $event.target.checked)" /></td>
              <td class="l" data-label="Пресет"><b>{{ item.title }}</b><small v-if="item.description" class="cell-note">{{ item.description }}</small></td>
              <td data-label="Приоритет">{{ item.priority }}<small v-for="(value, path) in item.priorities" :key="path" class="cell-note">{{ path }} {{ value }}</small></td>
              <td class="l" data-label="Нужны группы">
                <template v-if="item.requires.groups.length">{{ item.requires.groups.join(', ') }}<small v-if="item.missing_groups?.length" class="cell-note bad">нет в nodes.json: {{ item.missing_groups.join(', ') }}</small></template>
                <span v-else class="mut">—</span>
              </td>
              <td class="l" data-label="Файл"><RouterLink :to="{ path: '/singbox', query: { file: item.path } }">{{ item.name }}</RouterLink></td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-if="presets.some(p => p.missing_groups?.length)" class="notice bad">Недостающие группы включаются в «Подписки и сборка» → «Параметры сборки».</p>
      <p class="mut">Группы в текущем nodes.json: {{ groups ? groups.join(', ') : 'неизвестно' }}.</p>
    </section>
    <section class="panel">
      <h2>Применение</h2>
      <div class="actions">
        <button class="btn" :disabled="busy || run?.status === 'running'" @click="launch(true)">Проверить сборку</button>
        <button class="btn primary" :disabled="busy || run?.status === 'running'" @click="launch(false)">Применить</button>
      </div>
      <p class="help-text">Проверка собирает конфиг из редактируемой базы и пресетов с текущим nodes.json и прогоняет sing-box check, ничего не меняя. Применение копирует базу, собирает конфиг, перезапускает sing-box только при изменениях и проверяет связность.</p>
      <template v-if="run">
        <p>{{ run.dry_run ? 'Проверка' : 'Применение' }}<template v-if="run.started"> от {{ new Date(run.started * 1000).toLocaleString('ru-RU') }}</template>: <b>{{ statusLabel[run.status] || run.status }}</b> · <RouterLink to="/pipeline">история в «Конвейере»</RouterLink></p>
        <pre class="log">{{ log || 'Журнал пуст…' }}</pre>
      </template>
    </section>
  </template>
</template>

<style scoped>
.presets td { vertical-align: top }
.actions { display: flex; flex-wrap: wrap; gap: .5rem; margin: .7rem 0 }
.log { max-height: 26rem; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; font-size: .85rem }
</style>
