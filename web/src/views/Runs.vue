<script setup>
import { ref, computed, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { api, auth, can } from '../api.js'
import { PHASES } from '../ux.js'
import { dateTime } from '../format.js'
import { t } from '../i18n.js'
const status=ref(null), statusErr=ref(''), lines=ref([]), seq=ref(0), logErr=ref(''), busy=ref(false), msg=ref('')
const paused=ref(false), follow=ref(true), query=ref(''), level=ref('all'), logBox=ref(null)
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
async function runTests() {
  busy.value=true; msg.value=''
  try {const r=await api.post('/run/tests');msg.value=r.already_queued ? t('On-demand testing is already running.') : t('Testing started: all nodes are checked without waiting for the test deadlines.');await pollStatus()}
  catch(e){msg.value=e.message}
  finally{busy.value=false}
}
watch(()=>auth.epoch,()=>{status.value=null;lines.value=[];seq.value=0;statusErr.value='';logErr.value=''})
watch(()=>auth.verified,()=>{pollStatus();pollLogs()})
const visible=computed(()=>lines.value.filter(l=>(!query.value || l.line.toLowerCase().includes(query.value.toLowerCase())) &&
  (level.value === 'all' || (level.value === 'errors' ? /ошиб|error|fail|veto|\[!\]/i : /switch|переключ|rotation|ротац/i).test(l.line))))  // i18n-skip: журнал тестера на русском
onMounted(()=>{pollStatus();pollLogs();stTimer=setInterval(pollStatus,5000);logTimer=setInterval(pollLogs,2000)})
onUnmounted(()=>{disposed=true;clearInterval(stTimer);clearInterval(logTimer)})
</script>
<template>
  <section class="panel">
    <h2>{{ t('Checks of the current sing-box nodes') }}</h2>
    <p class="help-text">{{ t('The tester checks nodes whose test results have expired. The button checks all available nodes now; it does not fetch subscriptions or apply settings.') }}</p>
    <p v-if="statusErr" class="notice bad" role="alert">{{ statusErr }}. {{ t('Polling will continue automatically.') }}</p>
    <template v-if="status">
      <p><b>{{ PHASES[status.progress?.phase] || (status.running ? t('Process running') : t('Stopped')) }}</b></p>
      <p class="break" v-if="status.progress?.node">{{ t('Current node: {node}', { node: status.progress.node }) }}</p>
      <p v-if="status.request_queued" class="notice">{{ t('On-demand testing is running: {n} nodes left.', { n: status.forced_left }) }}</p>
      <p v-if="status.wait_until">{{ t('Current wait until {time}. The deadline may change.', { time: dateTime(status.wait_until) }) }}</p>
      <p>{{ t('Next rotation: {time}.', { time: dateTime(status.next_rotation) }) }}</p>
      <p>{{ t('Active node monitor: {monitor} · traffic collection: {traffic}', { monitor: status.monitor ? t('running') : t('not running'), traffic: status.traffic ? t('running') : t('not running') }) }}</p>
    </template>
    <button class="btn primary" :disabled="busy || !can('run_tests') || !status?.running || !!statusErr || status.request_queued" @click="runTests">{{ t('Run testing now') }}</button>
    <p role="status">{{ msg }}</p>
  </section>
  <section>
    <h2>{{ t('Tester log') }}</h2>
    <p class="hint">{{ t('The latest available lines are shown (up to 1500 on the page). After a long pause some records may drop out of the server buffer. Filters detect the line type by its text; this is not a structured log level.') }}</p>
    <div class="toolbar">
      <label>{{ t('Search') }}<input type="search" v-model="query" /></label>
      <label>{{ t('Lines') }}<select v-model="level"><option value="all">{{ t('all') }}</option><option value="errors">{{ t('errors') }}</option><option value="switches">{{ t('switches') }}</option></select></label>
      <button class="btn" @click="paused = !paused">{{ paused ? t('Resume reading') : t('Pause reading') }}</button>
      <label class="check"><input type="checkbox" v-model="follow" /> {{ t('Follow new lines') }}</label>
    </div>
    <p v-if="logErr" class="notice bad">{{ logErr }}</p>
    <div ref="logBox" class="log" @wheel="follow=false">
      <p v-if="!visible.length" class="mut">{{ t('No available lines match these conditions.') }}</p>
      <div v-for="l in visible" :key="l.seq" class="log-line"><span class="mut">{{ dateTime(l.ts) }}</span><span>{{ l.line }}</span></div>
    </div>
  </section>
</template>
