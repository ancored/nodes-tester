<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { api, auth, can } from '../api.js'

const state = ref(null), runs = ref([]), schedule = ref(null), revision = ref('')
const error = ref(''), notice = ref(''), log = ref(''), logOffset = ref(0), viewedRun = ref('')
const saving = ref(false), busy = ref(false), dirty = ref(false), conflicted = ref(false)
const times = ref({ router: '', clients: '' })
const statusLabel = { running: 'выполняется', ok: 'успешно', error: 'ошибка', busy: 'занято', timeout: 'тайм-аут', interrupted: 'прервано' }
let timer = null, polling = false

const live = computed(() => state.value?.current)
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
  if (polling || !auth.verified || !can('pipeline')) return
  polling = true
  try {
    const [next, history] = await Promise.all([api.get('/pipeline'), api.get('/pipeline/runs?limit=20')])
    state.value = next; runs.value = history.runs || []; error.value = ''
    if (!schedule.value) { revision.value = next.revision; setEditor(next.schedule) }
    if (next.current && viewedRun.value !== next.current.id) {
      viewedRun.value = next.current.id; log.value = ''; logOffset.value = 0
    }
    if (viewedRun.value) await readLog()
  } catch (e) { error.value = e.message }
  finally { polling = false }
}
async function readLog() {
  const chunk = await api.get(`/pipeline/runs/${viewedRun.value}/log?offset=${logOffset.value}`)
  log.value += chunk.text; logOffset.value = chunk.offset
}
async function launch(mode, dryRun) {
  if (!dryRun && !window.confirm(
    mode === 'router'
      ? 'Применить конфигурацию роутера? sing-box может перезапуститься, а соединения — оборваться.'
      : 'Собрать и опубликовать клиентские конфигурации?')) return
  busy.value = true; error.value = ''; notice.value = ''
  try {
    const result = await api.post('/pipeline/run', { mode, dry_run: dryRun })
    viewedRun.value = result.run_id; log.value = ''; logOffset.value = 0
    notice.value = dryRun ? 'Проверка запущена. Результат появится в журнале.' : 'Применение запущено. Следите за журналом.'
    await poll()
  } catch (e) { error.value = e.message }
  finally { busy.value = false }
}
function edit() { dirty.value = true; auth.dirty = true }
async function saveSchedule() {
  if (conflicted.value) return
  const next = structuredClone(schedule.value)
  for (const mode of ['router', 'clients']) {
    const values = times.value[mode].split(',').map(x => x.trim()).filter(Boolean)
    if (values.some(x => !/^([01]\d|2[0-3]):[0-5]\d$/.test(x)) || new Set(values).size !== values.length) {
      error.value = `Укажите для ${mode} уникальное время HH:MM через запятую`; return
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
function showRun(row) { viewedRun.value = row.id; log.value = ''; logOffset.value = 0; readLog().catch(e => { error.value = e.message }) }
onMounted(async () => { await poll(); timer = setInterval(poll, 2000) })
onBeforeUnmount(() => { clearInterval(timer); auth.dirty = false })
onBeforeRouteLeave(() => !dirty.value || window.confirm('Отбросить несохранённое расписание?'))
</script>

<template>
  <section class="panel">
    <h2>Конвейер обновления</h2>
    <p>Router загружает подписки, собирает ноды и правила, затем применяет конфигурацию к роутеру. Clients собирает отдельные клиентские конфигурации. Проверка создаёт промежуточные файлы и может скачивать подписки, но не применяет их.</p>
    <p v-if="!auth.verified" class="notice">Войдите с токеном администратора, чтобы видеть журнал и управлять конвейером.</p>
    <p v-else-if="!can('pipeline')" class="notice">Управление недоступно: нужен работающий тестер с pipeline.json и команда nodes-tester в PATH.</p>
    <template v-if="can('pipeline')">
      <p v-if="error" class="notice bad" role="alert">{{ error }}. Опрос повторится автоматически.</p>
      <p v-if="notice" class="notice" role="status">{{ notice }}</p>
      <div v-for="mode in ['router', 'clients']" :key="mode" class="panel">
        <h3>{{ mode === 'router' ? 'Роутер' : 'Клиенты' }}</h3>
        <p>Следующий запуск: {{ state?.next_run?.[mode] ? new Date(state.next_run[mode] * 1000).toLocaleString('ru-RU') : 'не запланирован' }}</p>
        <div class="actions">
          <button class="btn" :disabled="busy || !!live" @click="launch(mode, true)">Проверить (dry-run)</button>
          <button class="btn" :disabled="busy || !!live" @click="launch(mode, false)">Применить</button>
        </div>
      </div>
      <p role="status">{{ live ? `Идёт ${live.mode}${live.dry_run ? ' · проверка' : ' · применение'}` : 'Сейчас конвейер не выполняется' }}</p>
      <h3>Журнал {{ viewedRun ? viewedRun.slice(0, 8) : '' }}</h3>
      <pre class="pipeline-log" aria-live="polite">{{ log || 'Выберите прогон из истории.' }}</pre>
      <h3>История</h3>
      <div class="pipeline-history">
        <button v-for="row in runs" :key="row.id" class="btn" @click="showRun(row)">
          {{ new Date(row.started * 1000).toLocaleString('ru-RU') }} · {{ row.mode === 'router' ? 'роутер' : 'клиенты' }} · {{ row.dry_run ? 'проверка' : 'применение' }} · {{ statusLabel[row.status] || row.status }}
        </button>
      </div>
      <p v-if="!runs.length">Прогонов пока нет.</p>
    </template>
  </section>
  <section v-if="can('pipeline') && schedule" class="panel">
    <h2>Ежедневное расписание</h2>
    <p>Время роутера. Новое расписание начинает действовать после сохранения; пропущенные после перезапуска задания выполняются один раз.</p>
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
.pipeline-log { max-height: 22rem; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; background: #111827; color: #e5e7eb; padding: 1rem; border-radius: .5rem }
.pipeline-history { display: flex; flex-direction: column; align-items: flex-start; gap: .4rem; max-height: 16rem; overflow: auto }
.actions { display: flex; flex-wrap: wrap; gap: .5rem }
</style>
