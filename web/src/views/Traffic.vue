<script setup>
import { computed } from 'vue'
import { useQuery } from '../query.js'
import { useSnapshot } from '../store.js'
import { bytes, dateTime, flag } from '../format.js'
import DataTable from '../components/DataTable.vue'
import FilterBar from '../components/FilterBar.vue'
import Msg from '../components/Msg.vue'
import { useTableFilters } from '../filters.js'
import { t } from '../i18n.js'
import HelpButton from '../components/HelpButton.vue'

const s = useSnapshot()

// Агрегаты считаются из строк трафика (traffic_rows), поэтому общие фильтры
// таблицы — страна, группа, протокол, провайдер — применяются к любому измерению.
const MEASURES = {
  providers: { dim: 'provider', label: t('providers'), title: t('provider'), kinds: ['leaf'] },
  countries: { dim: 'cc', label: t('countries'), title: t('country'), kinds: ['leaf'] },
  protocols: { dim: 'protocol', label: t('protocols'), title: t('protocol'), kinds: ['leaf'] },
  nodes: { dim: 'node', label: t('nodes'), title: t('node'), kinds: ['leaf', 'direct'] },
}
const requestedMeasure = useQuery('measure', 'providers')
const measure = computed({get: () => Object.hasOwn(MEASURES, requestedMeasure.value) ? requestedMeasure.value : 'providers', set: v => requestedMeasure.value = v})
const options = Object.keys(MEASURES)
const allRows = computed(() => s.data.traffic_rows || [])
// Опции фильтров — по leaf-нодам; фильтр применяется к строкам до свёртки.
const filterState = useTableFilters(computed(() => allRows.value.filter(r => r.kind === 'leaf')), computed(() => null))
const trafRows = computed(() => {
  const m = MEASURES[measure.value], acc = {}
  for (const r of filterState.apply(allRows.value)) {
    if (!m.kinds.includes(r.kind) || r[m.dim] == null) continue
    const a = (acc[r[m.dim]] ??= { [m.dim]: r[m.dim], crc: m.dim === 'node' && r.kind === 'leaf' ? r.crc : undefined, up: 0, down: 0, total: 0 })
    a.up += r.up; a.down += r.down; a.total += r.total
  }
  return Object.values(acc).sort((x, y) => y.total - x.total)
})
const trafCols = computed(() => {
  const m = MEASURES[measure.value]
  return [
    { key: m.dim, title: m.title, l: true, fmt: m.dim === 'cc' ? v => flag(v) + ' ' + String(v).toUpperCase() : null },
    { key: 'up', title: t('out'), fmt: bytes },
    { key: 'down', title: t('in'), fmt: bytes },
    { key: 'total', title: t('total'), fmt: bytes },
  ]
})

const endpoints = computed(() => s.data.endpoints || [])
const endpCols = [
  { key: 'provider', title: t('provider'), l: true },
  { key: 'source_ip', title: t('source'), l: true },
  { key: 'dest_host', title: t('destination'), l: true },
  { key: 'network', title: t('transport'), l: true },
  { key: 'up', title: t('out'), fmt: bytes },
  { key: 'down', title: t('in'), fmt: bytes },
  { key: 'flows', title: t('connections') },
]
</script>

<template>
  <div v-if="s.loading" class="empty">{{ t('Loading…') }}</div>
  <div v-else-if="s.error && !s.ready" class="empty bad">{{ t('Error: {error}', { error: s.error }) }}</div>
  <template v-else>
    <p class="mut">{{ t('Stored period: {start} — {end}', { start: dateTime(s.data.traffic_range?.start), end: dateTime(s.data.traffic_range?.end) }) }}</p>
    <section class="panel">
      <h2>{{ t('User traffic') }}<HelpButton topic="traffic#user" /></h2>
      <p v-if="s.data.source?.state !== 'ok'" class="mut">{{ t('No measurement database is available.') }}</p>
      <div v-else class="kpis"><div v-for="[key,label] in [['down',t('Incoming')],['up',t('Outgoing')],['total',t('Total')]]" :key="key" class="kpi"><div class="label">{{ label }}</div><div class="value">{{ bytes(s.data.traffic_user_totals?.[key] || 0) }}</div></div></div>
    </section>
    <section class="panel">
      <h2>{{ t('Tester traffic') }}<HelpButton topic="traffic#tester" /></h2>
      <p v-if="s.data.source?.state !== 'ok'" class="mut">{{ t('No measurement database is available.') }}</p>
      <template v-else>
        <p v-if="s.data.traffic_tester_range?.start" class="mut">{{ t('Over the stored period: {start} — {end}', { start: dateTime(s.data.traffic_tester_range.start), end: dateTime(s.data.traffic_tester_range.end) }) }}</p>
        <div class="kpis"><div v-for="[key,label] in [['down',t('Incoming')],['up',t('Outgoing')],['total',t('Total')]]" :key="key" class="kpi"><div class="label">{{ label }}</div><div class="value">{{ bytes(s.data.traffic_tester_totals?.[key] || 0) }}</div></div></div>
        <p><Msg :text="t('Downloaded by the download and heavy_download tests in 24 hours: {bytes} in {n} measurements.', { n: s.data.traffic_test_download_24h?.tests || 0 })"><template #bytes><b>{{ bytes(s.data.traffic_test_download_24h?.down || 0) }}</b></template></Msg></p>
      </template>
    </section>
    <section>
      <h2>{{ t('User traffic by dimension') }}<HelpButton topic="traffic#dimensions" /></h2>
      <div class="toolbar"><label>{{ t('Dimension') }}<select v-model="measure"><option v-for="o in options" :key="o" :value="o">{{ MEASURES[o].label }}</option></select></label><FilterBar :state="filterState" /></div>
      <DataTable :rows="trafRows" :columns="trafCols" :filters="[]" :page-size="15" />
    </section>
    <section>
      <h2>{{ t('Top destinations') }}<HelpButton topic="traffic#destinations" /></h2>
      <DataTable :rows="endpoints" :columns="endpCols" :page-size="15" query-key="endpoints_" />
    </section>
  </template>
</template>
