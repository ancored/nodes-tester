<script setup>
import { computed } from 'vue'
import { useQuery } from '../query.js'
import { useSnapshot } from '../store.js'
import { bytes, dateTime, flag } from '../format.js'
import DataTable from '../components/DataTable.vue'
import FilterBar from '../components/FilterBar.vue'
import { useTableFilters } from '../filters.js'

const s = useSnapshot()

// Агрегаты считаются из строк трафика (traffic_rows), поэтому общие фильтры
// таблицы — страна, группа, протокол, провайдер — применяются к любому измерению.
const MEASURES = {
  'провайдеры': { dim: 'provider', title: 'провайдер', kinds: ['leaf', 'unspecified'] },
  'страны': { dim: 'cc', title: 'страна', kinds: ['leaf', 'unspecified'] },
  'протоколы': { dim: 'protocol', title: 'протокол', kinds: ['leaf'] },
  'ноды': { dim: 'node', title: 'нода', kinds: ['leaf', 'direct'] },
}
const requestedMeasure = useQuery('measure', 'провайдеры')
const measure = computed({get: () => Object.hasOwn(MEASURES, requestedMeasure.value) ? requestedMeasure.value : 'провайдеры', set: v => requestedMeasure.value = v})
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
    { key: 'up', title: 'исх', fmt: bytes },
    { key: 'down', title: 'вх', fmt: bytes },
    { key: 'total', title: 'всего', fmt: bytes },
  ]
})

const endpoints = computed(() => s.data.endpoints || [])
const endpCols = [
  { key: 'provider', title: 'провайдер', l: true },
  { key: 'source_ip', title: 'источник', l: true },
  { key: 'dest_host', title: 'назначение', l: true },
  { key: 'network', title: 'транспорт', l: true },
  { key: 'up', title: 'исх', fmt: bytes },
  { key: 'down', title: 'вх', fmt: bytes },
  { key: 'flows', title: 'соединений' },
]
</script>

<template>
  <div v-if="s.loading" class="empty">Загрузка…</div>
  <div v-else-if="s.error && !s.ready" class="empty bad">Ошибка: {{ s.error }}</div>
  <template v-else>
    <p class="hint">Пользовательский трафик за весь сохранённый период: {{ dateTime(s.data.traffic_range?.start) }} — {{ dateTime(s.data.traffic_range?.end) }}. Это не последние 24 часа. Трафик тестера исключён; история исчезнувших нод удаляется через {{ s.data.retention_days }} дней после последнего появления. Топ назначений ограничен 50 записями; назначения являются накопительными счётчиками, а не выборкой за указанный период.</p>
    <section class="panel">
      <h2>Пользовательский трафик</h2>
      <p v-if="s.data.source?.state !== 'ok'" class="mut">Нет доступной базы измерений.</p>
      <div v-else class="kpis"><div v-for="[key,label] in [['down','Входящий'],['up','Исходящий'],['total','Всего']]" :key="key" class="kpi"><div class="label">{{ label }}</div><div class="value">{{ bytes(s.data.traffic_user_totals?.[key] || 0) }}</div></div></div>
    </section>
    <section class="panel">
      <h2>Трафик тестера</h2>
      <p v-if="s.data.source?.state !== 'ok'" class="mut">Нет доступной базы измерений.</p>
      <template v-else>
        <p v-if="s.data.traffic_tester_range?.start" class="mut">За сохранённый период: {{ dateTime(s.data.traffic_tester_range.start) }} — {{ dateTime(s.data.traffic_tester_range.end) }}</p>
        <div class="kpis"><div v-for="[key,label] in [['down','Входящий'],['up','Исходящий'],['total','Всего']]" :key="key" class="kpi"><div class="label">{{ label }}</div><div class="value">{{ bytes(s.data.traffic_tester_totals?.[key] || 0) }}</div></div></div>
        <p>Скачано тестами download и heavy_download за 24 часа: <b>{{ bytes(s.data.traffic_test_download_24h?.down || 0) }}</b> за {{ s.data.traffic_test_download_24h?.tests || 0 }} замеров.</p>
      </template>
    </section>
    <section>
      <h2>Пользовательский трафик по измерениям</h2>
      <div class="toolbar"><label>Измерение<select v-model="measure"><option v-for="o in options" :key="o">{{ o }}</option></select></label><FilterBar :state="filterState" /></div>
      <DataTable :rows="trafRows" :columns="trafCols" :filters="[]" :page-size="15" />
    </section>
    <section>
      <h2>Топ назначений</h2>
      <DataTable :rows="endpoints" :columns="endpCols" :page-size="15" query-key="endpoints_" />
    </section>
  </template>
</template>
