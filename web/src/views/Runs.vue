<script setup>
import { ref, computed, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { api, auth, can } from '../api.js'
import { PHASES } from '../ux.js'
import { dateTime } from '../format.js'
const status=ref(null), statusErr=ref(''), lines=ref([]), seq=ref(0), logErr=ref(''), busy=ref(false), msg=ref('')
const paused=ref(false), follow=ref(true), query=ref(''), level=ref('все'), logBox=ref(null)
let stTimer, logTimer, statusBusy=false, logsBusy=false, disposed=false
async function pollStatus() {
  if(statusBusy || auth.capabilities.mode === 'standalone') return
  statusBusy=true; const epoch=auth.epoch
  try { const data=await api.get('/status'); if(epoch===auth.epoch && !disposed){status.value=data;statusErr.value=''} }
  catch(e){if(epoch===auth.epoch && !disposed)statusErr.value=e.message}
  finally{statusBusy=false}
}
async function pollLogs() {
  if(logsBusy || paused.value || auth.capabilities.mode === 'standalone') return
  logsBusy=true; const epoch=auth.epoch
  try {
    const data=await api.get('/logs?seq='+seq.value+'&tail=300')
    if(epoch !== auth.epoch || disposed)return
    if(data.seq < seq.value){lines.value=[];seq.value=0}
    for(const line of data.lines || []){if(line.seq>seq.value)lines.value.push(line)}
    seq.value=data.seq || seq.value
    if(lines.value.length>1500)lines.value=lines.value.slice(-1500)
    if(follow.value)nextTick(()=>{if(logBox.value)logBox.value.scrollTop=logBox.value.scrollHeight})
    logErr.value=''
  }catch(e){if(epoch===auth.epoch && !disposed)logErr.value=e.message}
  finally{logsBusy=false}
}
async function runPass() {
  busy.value=true; msg.value=''
  try {const r=await api.post('/run/pass');msg.value=r.already_queued ? 'Запрос уже находится в очереди.' : 'Проверка запрошена. Текущий проход не прерывается; следующий начнётся после него.';await pollStatus()}
  catch(e){msg.value=e.message}
  finally{busy.value=false}
}
watch(()=>auth.epoch,()=>{status.value=null;lines.value=[];seq.value=0;statusErr.value='';logErr.value=''})
watch(()=>auth.verified,()=>{pollStatus();pollLogs()})
const visible=computed(()=>lines.value.filter(l=>(!query.value || l.line.toLowerCase().includes(query.value.toLowerCase())) &&
  (level.value === 'все' || (level.value === 'ошибки' ? /ошиб|error|fail|veto|\[!\]/i : /switch|переключ|rotation|ротац/i).test(l.line))))
const outcome={completed:'Завершён',empty:'Нет тестируемых нод',error:'Прерван ошибкой'}
onMounted(()=>{pollStatus();pollLogs();stTimer=setInterval(pollStatus,5000);logTimer=setInterval(pollLogs,2000)})
onUnmounted(()=>{disposed=true;clearInterval(stTimer);clearInterval(logTimer)})
</script>
<template>
  <section class="panel">
    <h2>Проверки текущих нод sing-box</h2>
    <p>Внеплановый проход проверяет уже доступные ноды. Он не загружает подписки и не применяет настройки.</p>
    <p v-if="statusErr" class="notice bad" role="alert">{{ statusErr }}. Опрос продолжится автоматически.</p>
    <template v-if="status">
      <p><b>{{ PHASES[status.progress?.phase] || (status.running ? 'Процесс запущен' : 'Остановлен') }}</b> · проход {{ status.day }} / {{ status.pass }}</p>
      <p v-if="status.progress?.total">Обработано {{ status.progress.processed }} из {{ status.progress.total }} в текущем этапе (включая пропуски).</p>
      <p class="break" v-if="status.progress?.node">Текущая нода: {{ status.progress.node }}</p>
      <p v-if="status.request_queued" class="notice">Внеплановая проверка ожидает начала.</p>
      <p v-if="status.last_pass_result">Последний проход: {{ outcome[status.last_pass_result.outcome] }} · {{ dateTime(status.last_pass_result.finished) }}<span v-if="status.last_pass_result.error"> · {{ status.last_pass_result.error }}</span></p>
      <p v-if="status.wait_until">Текущее ожидание до {{ dateTime(status.wait_until) }}. Срок может измениться.</p>
      <p>Следующая ротация: {{ dateTime(status.next_rotation) }}. Это не время завершения проверки.</p>
      <p>Монитор активных нод: {{ status.monitor ? 'работает' : 'не работает' }} · сбор трафика: {{ status.traffic ? 'работает' : 'не работает' }}</p>
    </template>
    <button class="btn primary" :disabled="busy || !can('run_pass') || !status?.running || !!statusErr || status.request_queued" @click="runPass">Запросить внеплановую проверку</button>
    <p role="status">{{ msg }}</p>
  </section>
  <section>
    <h2>Журнал тестера</h2>
    <p class="hint">Показаны последние доступные строки (на странице до 1500). После длительной паузы часть записей может исчезнуть из буфера сервера. Фильтры определяют тип строки по тексту, это не структурированный уровень журнала.</p>
    <div class="toolbar">
      <label>Поиск<input type="search" v-model="query" /></label>
      <label>Строки<select v-model="level"><option>все</option><option>ошибки</option><option>переключения</option></select></label>
      <button class="btn" @click="paused = !paused">{{ paused ? 'Продолжить чтение' : 'Приостановить чтение' }}</button>
      <label class="check"><input type="checkbox" v-model="follow" /> Следовать за новыми строками</label>
    </div>
    <p v-if="logErr" class="notice bad">{{ logErr }}</p>
    <div ref="logBox" class="log" @wheel="follow=false">
      <p v-if="!visible.length" class="mut">Доступных строк с такими условиями нет.</p>
      <div v-for="l in visible" :key="l.seq" class="log-line"><span class="mut">{{ dateTime(l.ts) }}</span><span>{{ l.line }}</span></div>
    </div>
  </section>
</template>
