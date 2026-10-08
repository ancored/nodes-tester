<script setup>
import { computed, onBeforeUnmount, onMounted, ref, toRaw } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { api, auth, can } from '../api.js'

const state = ref(null), runs = ref([]), schedule = ref(null), revision = ref('')
const error = ref(''), pollError = ref(''), logError = ref(''), notice = ref(''), log = ref(''), logOffset = ref(0), viewedRun = ref('')
const followLive = ref(true)
const saving = ref(false), busy = ref(false), dirty = ref(false), conflicted = ref(false)
const times = ref({ router: '', clients: '' })
const statusLabel = { running: 'выполняется', ok: 'успешно', error: 'ошибка', busy: 'занято', timeout: 'тайм-аут', interrupted: 'прервано' }
const modeLabel = { router: 'роутер', clients: 'клиенты', apply: 'база и правила', 'apply-clients': 'клиентские правила' }
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
      ? 'Применить конфигурацию роутера? sing-box может перезапуститься, а соединения — оборваться.'
      : 'Собрать и опубликовать клиентские конфигурации?')) return
  busy.value = true; error.value = ''; notice.value = ''
  try {
    const result = await api.post('/pipeline/run', { mode, dry_run: dryRun })
    followLive.value = true; selectRun(result.run_id)
    notice.value = dryRun ? 'Проверка запущена. Результат появится в журнале.' : 'Применение запущено. Следите за журналом.'
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
      error.value = `Укажите для раздела «${modeLabel[mode]}» время в формате HH:MM через запятую, без повторов.`; return
    }
    next.jobs[mode].times = values
  }
  saving.value = true; error.value = ''; notice.value = ''
  try {
    const response = await api.put('/pipeline/schedule', next, { 'If-Match': revision.value })
    revision.value = response.revision; setEditor(next)
    notice.value = 'Расписание сохранено. Оно действует сразу.'
    await poll()
  } catch (e) {
    error.value = e.message
    if (e.status === 409) conflicted.value = true
  } finally { saving.value = false }
}
async function reloadSchedule() {
  if (dirty.value && !window.confirm('Отбросить несохранённое расписание?')) return
  try { await loadSchedule(); error.value = '' } catch (e) { error.value = e.message }
}
function showRun(row) {
  followLive.value = row.id === live.value?.id
  selectRun(row.id); readLog()
}
onMounted(async () => { await poll(); if (!disposed) timer = setInterval(poll, 2000) })
onBeforeUnmount(() => { disposed = true; clearInterval(timer); auth.dirty = false })
onBeforeRouteLeave(() => !dirty.value || window.confirm('Отбросить несохранённое расписание?'))
</script>

<template>
  <section class="panel">
    <h2>Конвейер обновления</h2>
    <p class="help-text">Режим «Роутер» загружает подписки, собирает ноды и правила, затем применяет конфигурацию к роутеру. Режим «Клиенты» собирает отдельные клиентские конфигурации. Проверка создаёт промежуточные файлы и может скачивать подписки, но не применяет их.</p>
    <p v-if="!auth.verified" class="notice">Войдите с токеном администратора, чтобы видеть журнал и управлять конвейером.</p>
    <p v-else-if="!can('pipeline')" class="notice">Управление недоступно: нужен работающий тестер с pipeline.json и команда nodes-tester в PATH.</p>
    <template v-if="can('pipeline')">
      <p v-if="error" class="notice bad" role="alert">{{ error }}</p>
      <p v-if="pollError" class="notice bad" role="alert">{{ pollError }}. Опрос повторится автоматически.</p>
      <p v-if="notice" class="notice" role="status">{{ notice }}</p>
      <div v-for="mode in ['router', 'clients']" :key="mode" class="panel">
        <h3>{{ mode === 'router' ? 'Роутер' : 'Клиенты' }}</h3>
        <p>Следующий запуск: {{ state?.next_run?.[mode] ? new Date(state.next_run[mode] * 1000).toLocaleString('ru-RU') : 'не запланирован' }}</p>
        <div class="actions">
          <button class="btn" :disabled="busy || !!live" @click="launch(mode, true)">Проверить (dry-run)</button>
          <button class="btn" :disabled="busy || !!live" @click="launch(mode, false)">Применить</button>
        </div>
      </div>
      <p role="status">{{ live ? `Идёт ${modeLabel[live.mode] || live.mode}${live.dry_run ? ' · проверка' : ' · применение'}` : 'Сейчас конвейер не выполняется' }}</p>
      <div class="toolbar">
        <h3>Журнал</h3>
        <button v-if="live && viewedRun !== live.id" class="btn" @click="showRun(live)">К текущему прогону</button>
      </div>
      <p v-if="selectedRun" class="field-note">{{ new Date(selectedRun.started * 1000).toLocaleString('ru-RU') }} · {{ modeLabel[selectedRun.mode] || selectedRun.mode }} · {{ selectedRun.dry_run ? 'проверка' : 'применение' }} · {{ statusLabel[selectedRun.status] || selectedRun.status }}</p>
      <p v-if="logError" class="notice bad" role="alert">{{ logError }}. Загрузка журнала повторится автоматически.</p>
      <pre class="log pipeline-log" aria-live="polite">{{ log || (viewedRun ? 'Журнал пока пуст.' : 'Выберите прогон из истории.') }}</pre>
      <h3>История</h3>
      <div v-if="runs.length" class="wrap pipeline-history">
        <table class="responsive-table" aria-label="Последние 20 прогонов конвейера">
          <thead><tr><th class="l">Начало</th><th class="l">Режим</th><th class="l">Действие</th><th class="l">Запуск</th><th class="l">Статус</th><th class="l">Журнал</th></tr></thead>
          <tbody>
            <tr v-for="row in runs" :key="row.id" :class="{ selected: row.id === viewedRun }">
              <td class="l" data-label="Начало">{{ new Date(row.started * 1000).toLocaleString('ru-RU') }}</td>
              <td class="l" data-label="Режим">{{ modeLabel[row.mode] || row.mode }}</td>
              <td class="l" data-label="Действие">{{ row.dry_run ? 'проверка' : 'применение' }}</td>
              <td class="l" data-label="Запуск">{{ row.trigger === 'schedule' ? 'по расписанию' : row.trigger === 'manual' ? 'вручную' : row.trigger || '—' }}</td>
              <td class="l" data-label="Статус"><span :class="{ good: row.status === 'ok', bad: ['error', 'timeout', 'interrupted'].includes(row.status) }">{{ statusLabel[row.status] || row.status }}</span></td>
              <td class="l" data-label="Журнал"><button class="btn sm" :aria-pressed="row.id === viewedRun" :aria-label="'Журнал прогона от ' + new Date(row.started * 1000).toLocaleString('ru-RU')" @click="showRun(row)">{{ row.id === viewedRun ? 'Открыт' : 'Открыть' }}</button></td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-if="!runs.length">Прогонов пока нет.</p>
    </template>
  </section>
  <section v-if="can('pipeline') && schedule" class="panel">
    <h2>Ежедневное расписание</h2>
    <p class="help-text">Время роутера. Новое расписание начинает действовать после сохранения; пропущенные после перезапуска задания выполняются один раз.</p>
    <p class="notice">Если этот конвейер уже запускает cron, удалите строку <code>nodes-tester pipeline</code> из crontab после включения расписания здесь. Иначе один из совпавших запусков получит статус «занято».</p>
    <div v-for="mode in ['router', 'clients']" :key="mode" class="panel">
      <label class="check"><input v-model="schedule.jobs[mode].enabled" type="checkbox" @change="edit" /> {{ mode === 'router' ? 'Роутер' : 'Клиенты' }}</label>
      <label>Время HH:MM через запятую <input v-model="times[mode]" type="text" placeholder="03:00, 15:00" @input="edit" /></label>
    </div>
    <label>Тайм-аут прогона, с <input v-model.number="schedule.timeout" type="number" min="1" max="86400" @input="edit" /></label>
    <p v-if="conflicted" class="notice bad" role="alert">Файл изменился в другом окне. Перечитайте расписание перед сохранением.</p>
    <div class="actions">
      <button class="btn" :disabled="!dirty || saving || conflicted" @click="saveSchedule">Сохранить расписание</button>
      <button class="btn" @click="reloadSchedule">Перечитать файл</button>
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
