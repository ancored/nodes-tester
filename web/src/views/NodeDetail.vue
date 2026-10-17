<script setup>
import { computed, ref, watch, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import { useSnapshot } from '../store.js'
import { nodeFlags, nodeJsonLines, testTitle, CHECK_NAMES, verdictMark, verdictClass } from '../ux.js'
import { dateTime, bytes, dur } from '../format.js'
import { useQuery } from '../query.js'
import NodeActions from '../components/NodeActions.vue'
import NodeKey from '../components/NodeKey.vue'
import Sparkline from '../components/Sparkline.vue'
import DataTable from '../components/DataTable.vue'
import { api, auth, can } from '../api.js'
import { t } from '../i18n.js'
import HelpButton from '../components/HelpButton.vue'
const s = useSnapshot(), route = useRoute(), tab = useQuery('tab','state')
const crc = computed(() => String(route.params.crc).toLowerCase())
// Ушедшая из подписок нода есть только в архиве: карточка строится по его строке.
const node = computed(() => (s.data.nodes || []).find(n => n.crc.toLowerCase() === crc.value) || archivedNode())
function archivedNode(){
  const g = (s.data.graveyard || []).find(n => n.crc.toLowerCase() === crc.value)
  return g && { ...g, node: g.tag, country: g.cc, present: 0, score: g.last }
}
const result = computed(() => s.data.results?.rows?.find(r => r.crc.toLowerCase() === crc.value))
const events = computed(() => (s.data.node_events || []).filter(e => e.crc.toLowerCase() === crc.value))
const switches = computed(() => (s.data.history || []).filter(e => e.crc?.toLowerCase() === crc.value))
const traffic = computed(() => (s.data.node_traffic || []).find(n => n.crc === crc.value))
const detail=ref(null), history=ref([]), detailError=ref(''), historyError=ref('')
const historyFrom=ref(''), historyTo=ref('')
const filteredHistory=computed(()=>history.value.filter(item=>{
  const from=historyFrom.value ? new Date(historyFrom.value).getTime()/1000 : -Infinity
  const to=historyTo.value ? new Date(historyTo.value).getTime()/1000 + 60 : Infinity
  return item.ts >= from && item.ts < to
}))
function clearHistoryRange(){historyFrom.value='';historyTo.value=''}
// Проверки групп для ноды: входит ли, кандидат ли, вердикты с возрастом результата.
const nodeGroups=ref(null), nodeGroupsError=ref('')
async function loadNodeGroups(){
  const selected=crc.value
  try{ const data=await api.get('/nodes/'+selected+'/groups'); if(selected===crc.value){ nodeGroups.value=data; nodeGroupsError.value='' } }
  catch(e){ nodeGroupsError.value=e.message }
}
watch([crc,tab],()=>{ if(tab.value==='groups') loadNodeGroups() },{immediate:true})
function groupStatus(g){ return g.active ? t('active (group)') : g.reserve ? t('in reserve') : g.candidate ? t('candidate') : g.member ? t('not a candidate') : t('not a member') }
function age(ts){ return ts ? t('{time} ago', { time: dur(Date.now()/1000-ts) }) : t('no result') }
let requestId=0
async function loadDetails() {
  const id=++requestId, epoch=auth.epoch, selected=crc.value
  detail.value=null;history.value=[];detailError.value='';historyError.value='';clearHistoryRange()
  await Promise.all([
    api.get('/nodes/'+selected+'/score-history').then(data=>{if(id===requestId && epoch===auth.epoch)history.value=data.score_history || []}).catch(e=>{if(id===requestId && epoch===auth.epoch)historyError.value=e.message}),
    can('edit_config') ? api.get('/nodes/'+selected+'/details').then(data=>{if(id===requestId && epoch===auth.epoch)detail.value=data}).catch(e=>{if(id===requestId && epoch===auth.epoch)detailError.value=e.message}) : Promise.resolve(),
  ])
}
watch([crc,()=>auth.epoch,()=>auth.verified],loadDetails,{immediate:true})
onUnmounted(()=>{requestId++})
watch(()=>s.data.generated,async()=>{
  const id=requestId, epoch=auth.epoch, selected=crc.value
  try {const data=await api.get('/nodes/'+selected+'/score-history');if(id===requestId && epoch===auth.epoch){history.value=data.score_history || [];historyError.value=''}}
  catch(e){if(id===requestId && epoch===auth.epoch)historyError.value=e.message}
})
// Порядок полей — как в nodes.json; цвет строки — чей это CRC: настроек сервера или учётных данных.
const jsonLines=computed(()=>detail.value?.fragment ? nodeJsonLines(detail.value.fragment,detail.value.crc_fields,detail.value.cred_paths) : [])
const historyColumns=[{key:'ts',title:t('Date / time'),fmt:dateTime,l:true},{key:'score',title:t('Rating'),fmt:v=>v == null ? '—' : Number(v).toFixed(1)}]
const eventNames = { added:t('Added'), removed:t('Disappeared from the list'), backoff:t('Pause'), garbage:t('Quarantined'), recovered:t('Recovered'), ban:t('Excluded'), unban:t('Exclusion removed'), restriction_cleared:t('Restriction lifted') }
const TABS = [['state',t('State')],['groups',t('Groups')],['results',t('Results')],['traffic',t('Traffic')],['events',t('Events')],['actions',t('Actions')]]
</script>
<template>
  <p><RouterLink :to="{ path:'/nodes', query:{ q:crc } }">← {{ t('To the node list') }}</RouterLink></p>
  <p v-if="!node" class="notice">{{ t('The node is not found in the current snapshot. Check the database or refresh the data.') }}</p>
  <template v-else>
    <section class="panel">
      <h2 class="break"><NodeKey :text="node.node" /><HelpButton topic="node-card#header" /></h2>
      <p class="break">{{ t('Name in the subscription') }}: {{ node.title || '—' }}</p>
      <div class="chips"><span v-if="node.groups?.length" class="badge">{{ t('Groups') }}: {{ node.groups.join(', ') }}</span><span v-for="flag in nodeFlags(node)" :key="flag" class="badge">{{ flag }}</span></div>
      <p v-if="node.guntil">{{ t(node.gstate === 'backoff' ? 'Paused until {time}, then the node is checked again.' : 'Quarantined until {time}, then the node is checked again.', { time: dateTime(node.guntil) }) }}</p>
    </section>
    <nav class="chips" :aria-label="t('Node card')"><button v-for="[v,label] in TABS" class="chip" :class="{on:tab === v}" @click="tab = v" :key="v">{{ label }}</button></nav>
    <section v-if="tab === 'state'" class="panel">
      <h2>{{ t('Rating change') }}<HelpButton topic="node-card#state" /></h2><Sparkline :vals="s.data.score_spark?.[crc] || []" />
      <p v-if="historyError" class="notice bad">{{ historyError }} <button class="btn" @click="loadDetails">{{ t('Retry loading') }}</button></p>
      <details class="rating-history"><summary>{{ t('Rating measurements') }} · {{ history.length }}</summary>
        <div class="toolbar"><label>{{ t('From date and time') }}<input v-model="historyFrom" type="datetime-local" :max="historyTo || undefined" /></label><label>{{ t('To date and time') }}<input v-model="historyTo" type="datetime-local" :min="historyFrom || undefined" /></label><button v-if="historyFrom || historyTo" class="btn" @click="clearHistoryRange">{{ t('Reset period') }}</button></div>
        <DataTable :rows="filteredHistory" :columns="historyColumns" :page-size="15" query-key="score_" :show-search="false" :show-sort="false" :empty="t('No measurements for the selected period.')" />
      </details>
      <details><summary>{{ t('Technical details') }}</summary>
        <p v-if="!can('edit_config')">{{ t('Log in with the administrator token to see the node JSON.') }}</p>
        <p v-if="detailError" class="notice bad">{{ detailError }} <button class="btn" @click="loadDetails">{{ t('Retry loading') }}</button></p>
        <template v-if="detail?.fragment">
            <pre class="node-json"><code><span v-for="(line,i) in jsonLines" :key="i" class="json-line" :class="line.kind ? 'crc-'+line.kind : ''">{{ line.text }}{{ '\n' }}</span></code></pre>
            <p v-if="detail.fragment_source!=='nodes_file'" class="mut">{{ t('The fragment is restored from SQLite; fields excluded from the CRC, except tag, are not stored in the database.') }}</p>
            <p>{{ t('Key check') }}: <b :class="detail.calculated_crc===crc ? 'good' : 'bad'">{{ detail.calculated_crc===crc ? '✓ OK' : '✕ ' + t('Does not match') }}</b><span v-if="detail.calculated_crc!==crc"> — {{ t('recalculated') }} <NodeKey :text="detail.calculated_crc" /></span></p>
            <details><summary>{{ t('CRC32 input strings: exactly what is hashed') }}</summary>
              <p class="crc-node">{{ t('Server settings') }}</p><pre class="node-json crc-node">{{ detail.node_payload }}</pre>
              <p class="crc-cred">{{ t('Credentials') }}</p><pre class="node-json crc-cred">{{ detail.cred_payload }}</pre>
            </details>
            <p class="mut break">{{ t('Restriction reason: {reason}; consecutive failures: {n}.', { reason: node.gstate || t('none'), n: node.gstreak || 0 }) }}</p>
        </template>
        <p v-else-if="detail">{{ t('The JSON of this node is not saved.') }}</p>
      </details>
    </section>
    <section v-if="tab === 'results'" class="panel">
      <HelpButton topic="node-card#results" />
      <p v-if="!result">{{ t('No saved results. This does not mean a failed check.') }}</p>
      <template v-else>
        <dl class="facts"><template v-for="(cell,test) in result.cells" :key="test"><dt>{{ testTitle(test) }}</dt><dd>{{ cell.v }}<small class="cell-note">{{ dateTime(cell.ts) }}</small><details v-if="!cell.ok && cell.title" class="failure-detail"><summary>{{ t('Details') }}</summary><pre>{{ cell.title }}</pre></details></dd></template></dl>
      </template>
    </section>
    <section v-if="tab === 'traffic'" class="panel">
      <HelpButton topic="node-card#traffic" />
      <p v-if="traffic">{{ t('Incoming {down}, outgoing {up} over the stored period {start} — {end}.', { down: bytes(traffic.down), up: bytes(traffic.up), start: dateTime(s.data.traffic_range?.start), end: dateTime(s.data.traffic_range?.end) }) }}</p>
      <p v-else>{{ t('There is no saved user traffic for this node. This does not prove it was not used: check the collection and the retention period.') }}</p><RouterLink to="/traffic">{{ t('All traffic aggregates and the retention period') }}</RouterLink>
    </section>
    <section v-if="tab === 'events'" class="panel">
      <h2>{{ t('Restrictions and presence') }}<HelpButton topic="node-card#events" /></h2>
      <p v-for="(e,i) in events" :key="e.ts+':'+i">{{ dateTime(e.ts) }} · {{ eventNames[e.event] || e.event }} · {{ e.reason === 'manual' ? t('manually by the administrator') : e.reason || t('automatically') }}</p><p v-if="!events.length">{{ t('No events in the available window.') }}</p>
      <h2>{{ t('Switches') }}</h2><p v-for="(e,i) in switches" :key="e.ts+':'+i">{{ dateTime(e.ts) }} · {{ e.region }} · {{ e.reason }}</p><p v-if="!switches.length">{{ t('No records in the last 200 switches.') }}</p>
    </section>
    <section v-if="tab === 'groups'" class="panel">
      <h2>{{ t('Group checks') }}<HelpButton topic="node-card#groups" /></h2>
      <p v-if="nodeGroupsError" class="notice bad">{{ nodeGroupsError }}</p>
      <p v-else-if="!nodeGroups">{{ t('Loading…') }}</p>
      <p v-else-if="!nodeGroups.live">{{ t('No tester connected: the group state is unavailable.') }}</p>
      <p v-else-if="!nodeGroups.groups.length">{{ t('The tester does not know this node yet: it appears after the first measurement.') }}</p>
      <table v-else class="node-checks">
        <thead><tr><th class="l">{{ t('Group') }}</th><th class="l">{{ t('State') }}</th><th class="l">{{ t('Checks: ✓ passed, ✗ failed, ? no fresh result') }}</th></tr></thead>
        <tbody><tr v-for="g in nodeGroups.groups" :key="g.group">
          <td class="l"><b>{{ g.group }}</b></td>
          <td class="l" :class="g.active || g.candidate ? 'good' : 'mut'">{{ groupStatus(g) }}</td>
          <td class="l"><span v-if="!g.checks.length" class="mut">{{ t('no checks: any working node fits') }}</span>
            <span v-for="c in g.checks" :key="c.check" class="check-chip" :title="age(c.ts)"><b :class="verdictClass(c.verdict)">{{ verdictMark(c.verdict) }}</b> {{ CHECK_NAMES[c.check] || c.check }}<span class="mut">{{ c.part === 'prefer' ? ' (' + t('preferred check') + ')' : '' }} · {{ age(c.ts) }}</span></span></td>
        </tr></tbody>
      </table>
    </section>
    <section v-if="tab === 'actions'" class="panel"><HelpButton topic="node-card#actions" /><NodeActions :node="node" /></section>
  </template>
</template>

<style scoped>
.node-checks { width: 100%; border-collapse: collapse; }
.node-checks td, .node-checks th { padding: 6px 8px 6px 0; vertical-align: top; border-top: 1px solid var(--line); }
.node-checks td:last-child, .node-checks th:last-child { width: 100%; white-space: normal; }
.check-chip { display: inline-block; margin: 0 12px 4px 0; white-space: nowrap; }
</style>
