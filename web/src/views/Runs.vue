<script setup>
import { ref, computed, onMounted, onUnmounted, nextTick } from 'vue'
import { api } from '../api.js'
import { timeHMS } from '../format.js'

const status = ref(null)
const statusErr = ref('')
const lines = ref([])
const seq = ref(0)
const logErr = ref('')
const busy = ref(false)
const msg = ref('')
const logBox = ref(null)
const readOnly = ref(false)

let stTimer = null
let logTimer = null

async function pollStatus() {
  try {
    status.value = await api.get('/status')
    statusErr.value = ''
    return true
  } catch (e) {
    statusErr.value = e.message
    if (e.status === 409) readOnly.value = true
    return false
  }
}

async function pollLogs() {
  try {
    const d = await api.get('/logs?seq=' + seq.value + '&tail=300')
    if (d.lines && d.lines.length) {
      for (const l of d.lines) {
        if (l.seq > seq.value) lines.value.push(l)
      }
      seq.value = d.seq || seq.value
      if (lines.value.length > 1500) lines.value = lines.value.slice(-1500)
      nextTick(() => {
        if (logBox.value) logBox.value.scrollTop = logBox.value.scrollHeight
      })
    }
    logErr.value = ''
  } catch (e) {
    logErr.value = e.message
  }
}

async function runPass() {
  busy.value = true
  msg.value = ''
  try {
    await api.post('/run/pass')
    msg.value = 'запрос отправлен — прогон начнётся'
  } catch (e) {
    msg.value = e.message
  } finally {
    busy.value = false
  }
}

const nextRotation = computed(() => {
  const t = status.value && status.value.next_rotation
  return t ? timeHMS(t) : '–'
})
const threads = computed(() => {
  const st = status.value || {}
  const m = st.monitor ? '✓' : '✗'
  const tr = st.traffic ? '✓' : '✗'
  return `монитор ${m} · трафик ${tr}`
})

onMounted(async () => {
  if (!await pollStatus()) return
  await pollLogs()
  stTimer = setInterval(pollStatus, 5000)
  logTimer = setInterval(pollLogs, 1500)
})
onUnmounted(() => {
  clearInterval(stTimer)
  clearInterval(logTimer)
})
</script>

<template>
  <div>
    <div class="kpis">
      <div class="kpi">
        <div class="label">тестер</div>
        <div class="value" :class="status && status.running ? 'good' : 'bad'">
          {{ status ? (status.running ? 'работает' : 'остановлен') : '…' }}
        </div>
      </div>
      <div class="kpi"><div class="label">прогон</div><div class="value">{{ (status && status.pass) || '–' }}</div></div>
      <div class="kpi"><div class="label">след. ротация</div><div class="value" style="font-size: 16px">{{ nextRotation }}</div></div>
      <div class="kpi"><div class="label">потоки</div><div class="value" style="font-size: 14px">{{ threads }}</div></div>
    </div>

    <div v-if="statusErr" class="empty bad">{{ statusErr }}</div>

    <section>
      <h2>Активные ноды по регионам</h2>
      <div class="wrap">
        <table>
          <thead><tr><th class="l">регион</th><th class="l">активная нода</th></tr></thead>
          <tbody>
            <tr v-if="!status || !status.regions || !status.regions.length">
              <td class="l empty" colspan="2">нет активных регионов (первый прогон ещё не прошёл?)</td>
            </tr>
            <tr v-for="r in (status && status.regions) || []" :key="r.region">
              <td class="l"><b>{{ r.region }}</b></td>
              <td class="l mut">{{ r.active }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section>
      <h2>Управление</h2>
      <button class="btn" :disabled="busy || !(status && status.running)" @click="runPass">
        Запустить прогон сейчас
      </button>
      <span class="mut" style="margin-left: 12px">{{ msg }}</span>
    </section>

    <section>
      <h2>Живой лог</h2>
      <div v-if="logErr" class="empty bad">{{ logErr }}</div>
      <div ref="logBox" class="log">
        <div v-if="!lines.length" class="mut">журнал пуст…</div>
        <div v-for="(l, i) in lines" :key="l.seq" class="log-line">
          <span class="mut">{{ timeHMS(l.ts) }}</span>
          <span>{{ l.line }}</span>
        </div>
      </div>
    </section>
  </div>
</template>
