<script setup>
import { onBeforeUnmount, ref, watch } from 'vue'
import { api, auth, can } from '../api.js'

const presets = ref([]), groups = ref(null), error = ref(''), notice = ref(''), busy = ref(false)
const run = ref(null), log = ref(''), logOffset = ref(0)
const statusLabel = { running: 'выполняется', ok: 'успешно', error: 'ошибка', busy: 'занято', timeout: 'тайм-аут', interrupted: 'прервано' }
const fileUrl = p => '/singbox/files/' + encodeURIComponent(p)
let timer = null

async function load() {
  try {
    const response = await api.get('/singbox/presets')
    presets.value = response.presets || []; groups.value = response.groups
    error.value = response.error || ''
  } catch (e) { error.value = e.message }
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
  finally { busy.value = false; await load() }
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
  try {
    const chunk = await api.get(`/pipeline/runs/${run.value.id}/log?offset=${logOffset.value}`)
    log.value += chunk.text; logOffset.value = chunk.offset
    const history = await api.get('/pipeline/runs?limit=20')
    const row = (history.runs || []).find(r => r.id === run.value.id)
    if (row) run.value.status = row.status
  } catch (e) { error.value = e.message }
  if (run.value.status === 'running') timer = setTimeout(poll, 2000)
}
watch(() => can('singbox_files'), ready => { if (ready) load() }, { immediate: true })
onBeforeUnmount(() => clearTimeout(timer))
</script>

<template>
  <section class="panel">
    <h2>Правила sing-box</h2>
    <p>Правила маршрутизации и DNS собираются из пресетов — отдельных JSON-файлов в <code>singbox/presets/</code>. Итоговый конфиг: база + ноды + включённые пресеты по приоритету (меньше — выше). Пресет содержит всё, чего нет в базе: правила, DNS-серверы, наборы правил.</p>
    <p>Переключатель меняет файл пресета; сервер заранее проверяет итоговый конфиг через <code>sing-box check</code> и не сохраняет изменение, если проверка не прошла. Работающий sing-box меняет только «Применить».</p>
    <p v-if="!auth.verified" class="notice">Войдите с токеном администратора.</p>
    <p v-else-if="!can('singbox_files')" class="notice">Раздел доступен только во встроенной админке с запущенным оркестратором.</p>
  </section>
  <template v-if="can('singbox_files')">
    <p v-if="error" class="notice bad" role="alert">{{ error }}</p>
    <p v-if="notice" class="notice" role="status">{{ notice }}</p>
    <section class="panel">
      <h2>Пресеты</h2>
      <p v-if="!presets.length" class="mut">Пресетов нет. Правила берутся только из base.json. Создайте файл presets/&lt;имя&gt;.json в «Файлах sing-box».</p>
      <div v-for="item in presets" :key="item.name" class="preset-row">
        <label class="check"><input type="checkbox" :checked="item.enabled" :disabled="busy" @change="toggle(item, $event.target.checked)" /> <b>{{ item.title }}</b></label>
        <span class="mut">{{ item.name }} · приоритет {{ item.priority }}<template v-if="item.dns_priority !== item.priority"> (DNS {{ item.dns_priority }})</template></span>
        <p class="desc">{{ item.description }}</p>
        <p v-if="item.requires.groups.length" :class="item.missing_groups?.length ? 'notice bad' : 'mut'">
          Нужны группы: {{ item.requires.groups.join(', ') }}<template v-if="item.missing_groups?.length"> · нет в текущем nodes.json: {{ item.missing_groups.join(', ') }} — включите их в «Подписки и сборка» → «Параметры сборки»</template>
        </p>
        <RouterLink :to="{ path: '/singbox', query: { file: item.path } }">Открыть JSON</RouterLink>
      </div>
      <p class="mut">Группы в текущем nodes.json: {{ groups ? groups.join(', ') : 'неизвестно' }}.</p>
    </section>
    <section class="panel">
      <h2>Применение</h2>
      <div class="actions">
        <button class="btn" :disabled="busy || run?.status === 'running'" @click="launch(true)">Проверить сборку</button>
        <button class="btn primary" :disabled="busy || run?.status === 'running'" @click="launch(false)">Применить</button>
      </div>
      <p class="mut">Проверка собирает конфиг из редактируемой базы и пресетов с текущим nodes.json и прогоняет sing-box check, ничего не меняя. Применение копирует базу, собирает конфиг, перезапускает sing-box только при изменениях и проверяет связность.</p>
      <template v-if="run">
        <p>{{ run.dry_run ? 'Проверка' : 'Применение' }}: <b>{{ statusLabel[run.status] || run.status }}</b></p>
        <pre class="log">{{ log || 'Журнал пуст…' }}</pre>
      </template>
    </section>
  </template>
</template>

<style scoped>
.preset-row { display: grid; gap: .3rem; padding: .7rem 0; border-top: 1px solid var(--line) }
.preset-row:first-of-type { border-top: 0 }
.desc { margin: 0 }
.actions { display: flex; flex-wrap: wrap; gap: .5rem; margin: .7rem 0 }
.log { max-height: 26rem; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; font-size: .85rem }
</style>
