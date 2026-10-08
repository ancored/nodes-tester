<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api, auth, can } from '../api.js'

// Две ветки с одинаковым устройством: роутер (presets/ + nodes.json → sing-box роутера)
// и клиенты (clients/presets/ + whnodes.json → конфиги из clients.list).
const BRANCHES = {
  router: {
    title: 'Роутер', dir: 'presets/', nodes: 'nodes.json', mode: 'apply',
    check: 'Проверить сборку', apply: 'Применить',
    confirm: 'Применить базу и включённые пресеты к sing-box? Если конфиг изменился, sing-box перезапустится; без связности вернётся прежний конфиг.',
    toggled: 'итоговый конфиг прошёл sing-box check. Чтобы изменить работающий sing-box, нажмите «Применить».',
    empty: 'Правила берутся только из base.json.',
    applyHelp: 'Проверка собирает конфиг из редактируемой базы и пресетов с текущим nodes.json и прогоняет sing-box check, ничего не меняя. Применение копирует базу, собирает конфиг, перезапускает sing-box только при изменениях и проверяет связность.',
  },
  clients: {
    title: 'Клиенты', dir: 'clients/presets/', nodes: 'whnodes.json', mode: 'apply-clients',
    check: 'Проверить сборку', apply: 'Собрать клиентов',
    confirm: 'Собрать и опубликовать конфиги клиентов из баз и включённых клиентских пресетов? Подписки не загружаются; клиент, чей конфиг не прошёл sing-box check, остаётся на прежнем.',
    toggled: 'конфиги всех клиентов прошли sing-box check. Чтобы опубликовать их, нажмите «Собрать клиентов».',
    empty: 'Правила берутся только из баз клиентов (clients/base_<имя>.json).',
    applyHelp: 'Проверка собирает конфиги клиентов из баз и включённых пресетов с текущим whnodes.json в отдельный каталог, ничего не публикуя. Сборка публикует их и файлы clients/publish/; конфиг, не прошедший sing-box check, не публикуется.',
  },
}
const route = useRoute()
const branch = computed(() => route.query.branch === 'clients' ? 'clients' : 'router')
const b = computed(() => BRANCHES[branch.value])

const presets = ref([]), groups = ref(null), clients = ref([]), settings = ref(true)
const error = ref(''), notice = ref(''), busy = ref(false), loadError = ref('')
const run = ref(null), log = ref(''), logOffset = ref(0)
const statusLabel = { running: 'выполняется', ok: 'успешно', error: 'ошибка', busy: 'занято', timeout: 'тайм-аут', interrupted: 'прервано' }
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
    notice.value = `«${item.title}» ${enabled ? 'включён' : 'выключен'} в файле; ${b.value.toggled}`
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
    <h2>Правила sing-box</h2>
    <div class="help-text">
      <p>Правила маршрутизации и DNS собираются из пресетов, отдельных JSON-файлов. Итоговый конфиг: база + ноды + включённые пресеты по приоритету (меньше — выше). Пресет содержит всё, чего нет в базе: правила, DNS-серверы, наборы правил.</p>
      <p>У роутера и клиентов свои пресеты: <code>singbox/presets/</code> склеиваются с base.json и nodes.json, <code>singbox/clients/presets/</code> — с базой каждого клиента и whnodes.json. Локальные наборы правил роутера клиент получает по адресу раздачи из <code>clients/settings.json</code>.</p>
      <p>Переключатель меняет файл пресета; сервер заранее проверяет итоговые конфиги через <code>sing-box check</code> и не сохраняет изменение, если проверка не прошла.</p>
    </div>
    <p v-if="!auth.verified" class="notice">Войдите с токеном администратора.</p>
    <p v-else-if="!can('singbox_files')" class="notice">Раздел доступен только во встроенной админке с запущенным оркестратором.</p>
  </section>
  <template v-if="can('singbox_files')">
    <nav class="chips" aria-label="Ветка правил">
      <RouterLink v-for="(item, key) in BRANCHES" :key="key" class="chip" :class="{ on: branch === key }"
                  :to="{ path: '/presets', query: key === 'router' ? {} : { branch: key } }">{{ item.title }}</RouterLink>
    </nav>
    <p v-if="error" class="notice bad" role="alert">{{ error }}</p>
    <p v-if="loadError" class="notice bad" role="alert">{{ loadError }}</p>
    <p v-if="notice" class="notice" role="status">{{ notice }}</p>
    <p v-if="branch === 'clients' && !settings" class="notice bad">Нет <RouterLink :to="{ path: '/singbox', query: { file: 'clients/settings.json' } }">clients/settings.json</RouterLink> с адресом раздачи наборов правил (<code>{"rules_url": "https://…/"}</code>): пресет с локальными наборами правил клиентам не собрать.</p>
    <section class="panel">
      <h2>Пресеты · {{ b.title }}</h2>
      <p v-if="!presets.length" class="mut">Пресетов нет. {{ b.empty }} Создайте файл {{ b.dir }}&lt;имя&gt;.json в «Файлах sing-box».</p>
      <div v-if="presets.length" class="wrap">
        <table class="responsive-table presets">
          <thead><tr><th class="l">Вкл.</th><th class="l">Пресет</th><th>Приоритет</th><th class="l">Нужны группы</th><th class="l">Файл</th></tr></thead>
          <tbody>
            <tr v-for="item in presets" :key="item.name">
              <td class="l" data-label="Вкл."><input type="checkbox" :checked="item.enabled" :disabled="busy" :aria-label="'Включить ' + item.title" @change="toggle(item, $event.target.checked)" /></td>
              <td class="l" data-label="Пресет"><b>{{ item.title }}</b><small v-if="item.description" class="cell-note">{{ item.description }}</small></td>
              <td data-label="Приоритет">{{ item.priority }}<small v-for="(value, path) in item.priorities" :key="path" class="cell-note">{{ path }} {{ value }}</small></td>
              <td class="l" data-label="Нужны группы">
                <template v-if="item.requires.groups.length">{{ item.requires.groups.join(', ') }}<small v-if="item.missing_groups?.length" class="cell-note bad">нет в {{ b.nodes }}: {{ item.missing_groups.join(', ') }}</small></template>
                <span v-else class="mut">—</span>
              </td>
              <td class="l" data-label="Файл"><RouterLink :to="{ path: '/singbox', query: { file: item.path } }">{{ item.name }}</RouterLink></td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-if="presets.some(p => p.missing_groups?.length)" class="notice bad">Недостающие группы включаются в «Подписки и сборка» → «Параметры сборки».</p>
      <p class="mut">Группы в текущем {{ b.nodes }}: {{ groups ? groups.join(', ') : 'неизвестно' }}.</p>
      <p v-if="branch === 'clients'" class="mut">Клиенты из clients.list: {{ clients.length ? clients.join(', ') : 'нет' }}.</p>
    </section>
    <section class="panel">
      <h2>{{ branch === 'clients' ? 'Сборка клиентов' : 'Применение' }}</h2>
      <div class="actions">
        <button class="btn" :disabled="busy || run?.status === 'running'" @click="launch(true)">{{ b.check }}</button>
        <button class="btn primary" :disabled="busy || run?.status === 'running'" @click="launch(false)">{{ b.apply }}</button>
      </div>
      <p class="help-text">{{ b.applyHelp }}</p>
      <template v-if="run">
        <p>{{ run.dry_run ? 'Проверка' : (branch === 'clients' ? 'Сборка' : 'Применение') }}<template v-if="run.started"> от {{ new Date(run.started * 1000).toLocaleString('ru-RU') }}</template>: <b>{{ statusLabel[run.status] || run.status }}</b> · <RouterLink to="/pipeline">история в «Конвейере»</RouterLink></p>
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
