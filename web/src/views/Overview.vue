<script setup>
import { computed, ref, watch, onMounted, onUnmounted } from 'vue'
import { useSnapshot } from '../store.js'
import { api, auth } from '../api.js'
import { PHASES, checkNames } from '../ux.js'
import { bytes, dateTime } from '../format.js'
import { t } from '../i18n.js'
import HelpButton from '../components/HelpButton.vue'
// Ноды тестера: клиентские (WH) только проверяются на белые списки и в счётчики не входят.
const s=useSnapshot(), nodes=computed(()=>(s.data.nodes || []).filter(n=>!n.wh_only)), runner=computed(()=>s.data.runner)
// sing-box: остановка/запуск и killswitch (только встроенная админка на OpenWrt).
const box=ref(null), boxBusy=ref(false), boxError=ref('')
const boxAvailable=computed(()=>!!auth.capabilities.singbox_control)
async function loadBox(){ if(!boxAvailable.value) return; try{ box.value=await api.get('/singbox/control'); boxError.value='' }catch(e){ boxError.value=e.message } }
// Группы из config.json и их живое состояние (активная нода или запасной выход, кандидаты, резерв).
const groups=ref([]), groupsError=ref('')
async function loadGroups(){ try{ groups.value=(await api.get('/groups')).groups; groupsError.value='' }catch(e){ groupsError.value=e.message } }
let boxTimer
onMounted(()=>{ loadBox(); loadGroups(); boxTimer=setInterval(()=>{ loadBox(); loadGroups() },10000) })
onUnmounted(()=>clearInterval(boxTimer))
watch(boxAvailable,v=>{ if(v) loadBox() })
async function boxAction(kind){
  const ks=box.value?.killswitch?.active
  const ask = kind==='stop'
    ? (ks ? t('Stop sing-box? The killswitch is loaded: the devices it protects stay without internet until sing-box is started again. Applying a config by the pipeline will be postponed. The admin panel and SSH stay available.')
      : t('Stop sing-box? The killswitch is not loaded: LAN devices go to the internet directly through the provider, without a proxy, until sing-box is started again. Applying a config by the pipeline will be postponed. The admin panel and SSH stay available.'))
    : t('Start sing-box?')
  if(!window.confirm(ask)) return
  boxBusy.value=true
  try{ box.value=await api.post('/singbox/'+kind,{}); boxError.value='' }catch(e){ boxError.value=e.message }finally{ boxBusy.value=false }
}
async function setKillswitch(on){
  boxBusy.value=true
  try{ box.value=await api.put('/singbox/killswitch',{enabled:on}); boxError.value='' }catch(e){ boxError.value=e.message; await loadBox() }finally{ boxBusy.value=false }
}
const boxState=computed(()=>!box.value ? '—' : box.value.running ? t('running') : box.value.stopped_by_admin ? t('stopped from the admin panel') : t('not running'))
const kpis=computed(()=>[
  [t('In the tester list'),nodes.value.filter(n=>+n.present === 1).length],
  [t('With rating measurements'),nodes.value.filter(n=>n.score != null && +n.present === 1).length],
  [t('Group candidates'),nodes.value.filter(n=>n.can_activate).length],
  [t('With restrictions'),nodes.value.filter(n=>n.banned || n.gstate).length],
])
const directions=computed(()=>[['down',t('Incoming')],['up',t('Outgoing')],['total',t('Total')]])
</script>
<template>
  <section class="panel">
    <h2>{{ t('System state') }}<HelpButton topic="overview#system-state" /></h2>
    <dl class="facts">
      <dt>{{ t('Tester stage') }}</dt><dd>{{ runner ? (runner.running ? PHASES[runner.progress?.phase] || t('Process running') : t('Stopped')) : t('Live state is unavailable in the standalone admin panel') }}</dd>
      <template v-if="runner?.running && runner.progress?.node"><dt>{{ t('Current node') }}</dt><dd>{{ runner.progress.node }}</dd></template>
      <dt>{{ t('Last measurement in the database') }}</dt><dd>{{ dateTime(s.data.source?.last_measurement) }}</dd>
      <dt>{{ t('Settings and control') }}</dt><dd>{{ auth.verified ? t('Token verified') : t('Viewing is available; log in for actions') }}</dd>
    </dl>
    <p v-if="s.error || s.data.source?.state !== 'ok'" class="notice bad">{{ s.error || s.data.source?.message || t('Fetching data…') }}</p>
    <p v-if="runner?.request_queued" class="notice">{{ t('On-demand testing is running: {n} nodes left.', { n: runner.forced_left }) }}</p>
  </section>
  <section v-if="boxAvailable" class="panel">
    <h2>sing-box<HelpButton topic="overview#singbox" /></h2>
    <dl class="facts">
      <dt>{{ t('State') }}</dt><dd :class="box && !box.running ? 'bad' : ''">{{ boxState }}</dd>
      <dt>Killswitch</dt><dd>
        <template v-if="!box?.killswitch?.installed">{{ t('the service is not installed: without sing-box LAN traffic goes directly') }}</template>
        <template v-else-if="box.killswitch.active">{{ t('rules loaded · packets blocked: {n}', { n: box.killswitch.blocked ?? '—' }) }}<template v-if="!box.killswitch.enabled"> · <span class="bad">{{ t('autostart disabled') }}</span></template></template>
        <template v-else><b class="bad">{{ t('rules not loaded') }}</b> — {{ t('without sing-box LAN traffic goes directly') }}</template>
      </dd>
    </dl>
    <div v-if="auth.verified" class="actions">
      <button v-if="box?.running" class="btn" :disabled="boxBusy" @click="boxAction('stop')">{{ t('Stop sing-box') }}</button>
      <button v-else class="btn" :disabled="boxBusy" @click="boxAction('start')">{{ t('Start sing-box') }}</button>
      <label v-if="box?.killswitch?.installed" class="check"><input type="checkbox" :checked="box.killswitch.active" :disabled="boxBusy" @change="setKillswitch($event.target.checked)" /> {{ t('Killswitch (router service): do not let LAN bypass sing-box') }}</label>
    </div>
    <p v-else class="mut">{{ t('Log in with the token to control sing-box.') }}</p>
    <p v-if="boxError || box?.error" class="notice bad">{{ boxError || box.error }}</p>
  </section>
  <h2>{{ t('Nodes: count and state') }}<HelpButton topic="overview#nodes" /></h2>
  <div class="kpis"><div v-for="[label,value] in kpis" :key="label" class="kpi"><div class="label">{{ label }}</div><div class="value">{{ s.data.source?.state === 'ok' ? value : '—' }}</div></div></div>
  <section class="panel">
    <h2>{{ t('Traffic over the stored period') }}<HelpButton topic="overview#traffic" /></h2>
    <p v-if="s.data.source?.state !== 'ok'" class="mut">{{ t('Traffic data is unavailable.') }}</p>
    <p v-else-if="s.data.traffic_range?.start" class="mut">{{ t('User measurements: {start} — {end}', { start: dateTime(s.data.traffic_range.start), end: dateTime(s.data.traffic_range.end) }) }}</p>
    <p v-else class="mut">{{ t('There are no user measurements for the stored period.') }}</p>
    <h3>{{ t('User traffic') }}</h3>
    <div class="kpis"><div v-for="[key,label] in directions" :key="key" class="kpi"><div class="label">{{ label }}</div><div class="value">{{ s.data.source?.state === 'ok' ? bytes(s.data.traffic_user_totals?.[key] || 0) : '—' }}</div></div></div>
    <p>{{ t('Downloaded by tests in 24 hours:') }} <b>{{ s.data.source?.state === 'ok' ? bytes(s.data.traffic_test_download_24h?.down || 0) : '—' }}</b> <span class="mut">({{ t('{n} measurements; response bodies without network overhead', { n: s.data.traffic_test_download_24h?.tests || 0 }) }})</span></p>
    <h3>{{ t('Tester traffic') }}</h3>
    <div class="kpis"><div v-for="[key,label] in directions" :key="key" class="kpi"><div class="label">{{ label }}</div><div class="value">{{ s.data.source?.state === 'ok' ? bytes(s.data.traffic_tester_totals?.[key] || 0) : '—' }}</div></div></div>
    <RouterLink to="/traffic">{{ t('User traffic details') }}</RouterLink>
  </section>
  <section class="panel">
    <h2>{{ t('Groups') }}<HelpButton topic="overview#groups" /></h2>
    <p v-if="groupsError" class="notice bad">{{ groupsError }}</p>
    <p v-else-if="!runner">{{ t('Unavailable: no Runner connected. History and rating may contain old values.') }}</p>
    <div v-for="g in groups" :key="g.tag" class="group-state">
      <p><b>{{ g.tag }}</b> <span class="mut">· {{ g.strict ? t('strict') : t('non-strict') }}{{ g.direct_fallback ? ', ' + t('direct-out allowed') : '' }}{{ g.in_global ? '' : ', ' + t('outside global-auto-out') }}</span></p>
      <template v-if="g.live">
        <p class="break"><template v-if="g.active">{{ t('Active') }}: <RouterLink :to="'/nodes/'+g.active.slice(g.active.lastIndexOf('[')+1,-1)">{{ g.active }}</RouterLink></template><span v-else-if="g.fallback" :class="g.fallback==='failsafe-out' ? '' : 'bad'">{{ t('No candidates: the group is on {outbound}', { outbound: g.fallback }) }}</span><template v-else>{{ t('No node selected') }}</template></p>
        <p class="mut">{{ t('Nodes {members} · candidates {candidates} · reserve {reserve}', { members: g.members, candidates: g.candidates, reserve: g.reserve.length }) }}<template v-if="g.require.length"> · {{ t('required') }}: {{ checkNames(g.require) }}</template><template v-if="g.prefer.length"> · {{ t('preferred') }}: {{ checkNames(g.prefer) }}</template></p>
      </template>
    </div>
  </section>
</template>

<style scoped>
.group-state { border-top: 1px solid var(--line); padding: 6px 0; }
.group-state p { margin: 2px 0; }
</style>
