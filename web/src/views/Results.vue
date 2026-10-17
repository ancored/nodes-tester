<script setup>
import { computed } from 'vue'
import { useSnapshot } from '../store.js'
import { testTitle, nodeColumns } from '../ux.js'
import { flag } from '../format.js'
import { t, locale } from '../i18n.js'
// Компактное время: ячейки тестов должны помещаться в ширину страницы.
const short = ts => ts ? new Date(ts*1000).toLocaleString(locale(),{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}) : '—'
import DataTable from '../components/DataTable.vue'
import IpTypeIcon from '../components/IpTypeIcon.vue'
const s=useSnapshot()
const tests=computed(()=>s.data.results?.tests || [])
const rows=computed(()=>(s.data.results?.rows || []).filter(r => !r.removed))
// AI-тесты — узкие колонки: галка, крест или «?» (сеть, вердикт прежний); у Google AI — флаг страны.
const AI_NOTE=t('Google AI: the country according to Google (flag) and Gemini availability; OpenAI and Claude AI: service availability. ✓ available, ✗ not available, ? network failure, the previous result stays. Only candidates of groups with required tests are checked; hover to see the time and the reason.')
// Узкие колонки-отметки: AI, соединение (страна — во флаге строки), белый список.
const AI={gemini:1,openai:1,anthropic:1,upstream:1,connectivity:1,whitelist:1}
// Короткие заголовки, чтобы таблица помещалась; полное название — в пояснениях и сортировке.
const HEADS={connectivity:t('Link'),whitelist:t('Whitelist'),reachability:t('Sites'),download:t('Speed, Mbit/s')}
// Последнее соединение не прошло — флаг из прошлого успешного замера: бледный, с временем замера.
function staleCountry(cols) {
  const country = cols[3]
  country.cls = (_, r) => r.country_stale ? 'stale-flag' : ''
  country.cellTitle = r => !r.country ? null : String(r.country).toUpperCase()
    + (r.country_stale ? (r.country_ts ? ' — ' + t('measured {time}', { time: short(r.country_ts) }) : '') + '; ' + t('the last connection failed') : '')
  return cols
}
const NOTES={gemini:AI_NOTE,whitelist:t('Whether the mobile internet whitelist mode lets the node through: the server IP is in the IP list and the SNI in the domain list (without TLS the IP is enough). Checked locally once a day; hover to see the addresses and SNI.')}
const aiMark = c => c.unk ? '?' : c.ok ? '✓' : '✗'
const aiTitle = c => [c.v, c.title].filter(Boolean).join('\n')
const columns=computed(()=>[
  ...staleCountry(nodeColumns()),
  {key:'ip_type',title:t('IP type'),head:'IP',slot:true,narrow:true,note:t('Exit IP by the ip-api.com database: a building is a data centre, a phone a mobile network, a house a residential one; a mask means the IP is known as a VPN/proxy; a flag below means the database puts the IP in a different country than the connection test showed. Red is worse for AI services. Hover for the IP and network (ASN).')},
  ...tests.value.map(k=>k in AI ? {key:k,title:testTitle(k),head:HEADS[k],slot:true,narrow:true,note:NOTES[k] || null} : {key:k,title:testTitle(k),head:HEADS[k],slot:true,l:true}),
])
</script>
<template>
  <p class="hint">{{ t('The latest result of each test; measurement times are in the node card. The speed multiplier shows throttling; “—” means the test was not measured. A pale flag is the country from a previous measurement: the last connection failed. Click a failure to see the details.') }}</p>
  <DataTable :rows="rows" :columns="columns" :empty="t('No saved results. Check the tester log.')">
    <template #cell-ip_type="{row}"><span v-if="row.ip_kind" class="ip-type" :title="row.ip_title || null"><span><IpTypeIcon :kind="row.ip_kind" /><IpTypeIcon v-if="row.ip_proxy" kind="proxy" /></span><span v-if="row.ip_db_cc" class="bad">{{ flag(row.ip_db_cc) }}</span></span><span v-else class="mut">—</span></template>
    <template v-for="test in tests" :key="test" #[`cell-${test}`]="{row}">
      <template v-if="row.cells[test]?.ai">
        <span class="ai-cell" :title="aiTitle(row.cells[test])"><b :class="row.cells[test].unk ? 'mut' : row.cells[test].ok ? 'good' : 'bad'">{{ aiMark(row.cells[test]) }}</b><span v-if="row.cells[test].cc">{{ flag(row.cells[test].cc) }}</span></span>
      </template>
      <template v-else-if="row.cells[test]">
        <details v-if="!row.cells[test].ok && row.cells[test].title" class="failure-detail"><summary><span class="bad">{{ row.cells[test].v }}</span></summary><pre>{{ row.cells[test].title }}</pre></details>
        <span v-else :class="row.cells[test].ok ? 'good' : 'bad'" :title="row.cells[test].title || null">{{ row.cells[test].v }}</span>
      </template><span v-else class="mut" :title="t('Not measured')">—</span>
    </template>
  </DataTable>
</template>
<style scoped>
.ip-type, .ai-cell { display: inline-flex; flex-direction: column; align-items: center; gap: 2px; line-height: 1.2; }
.ip-type > span:first-child { display: inline-flex; gap: 2px; }
:deep(td.stale-flag) { opacity: .35; }
</style>
