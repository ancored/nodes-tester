<script setup>
import { computed } from 'vue'
import { useQuery } from '../query.js'
import { useSnapshot } from '../store.js'
import { bytes, dateTime } from '../format.js'
import DataTable from '../components/DataTable.vue'
import Chips from '../components/Chips.vue'

const s = useSnapshot()

// Измерение трафика: ключ снимка + колонки + подпись.
const MEASURES = {
  'провайдеры': { key: 'traffic_providers', dim: { key: 'provider', title: 'провайдер' } },
  'страны': { key: 'traffic_countries', dim: { key: 'cc', title: 'cc' } },
  'протоколы': { key: 'traffic_protocols', dim: { key: 'protocol', title: 'протокол' } },
  'ноды (топ-10)': { key: 'traffic_nodes', dim: { key: 'node', title: 'нода' } },
}
const requestedMeasure = useQuery('measure', 'провайдеры')
const measure = computed({get: () => Object.hasOwn(MEASURES, requestedMeasure.value) ? requestedMeasure.value : 'провайдеры', set: v => requestedMeasure.value = v})
const options = Object.keys(MEASURES)

const trafRows = computed(() => s.data[MEASURES[measure.value].key] || [])
const trafCols = computed(() => {
  const dim = MEASURES[measure.value].dim
  return [
    { key: dim.key, title: dim.title, l: true, cls: dim.key === 'node' ? () => 'tag' : null },
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
    <p class="hint">Пользовательский трафик за весь сохранённый период: {{ dateTime(s.data.traffic_range?.start) }} — {{ dateTime(s.data.traffic_range?.end) }}. Это не последние 24 часа. Трафик тестера исключён; хранение — {{ s.data.retention_days }} дней. Топ нод ограничен 10 записями, назначений — 50; назначения являются накопительными счётчиками, а не выборкой за указанный период.</p>
    <section>
      <h2>Трафик</h2>
      <Chips v-model="measure" :options="options" />
      <DataTable :rows="trafRows" :columns="trafCols" :page-size="15" />
    </section>
    <section>
      <h2>Топ назначений</h2>
      <DataTable :rows="endpoints" :columns="endpCols" :page-size="15" query-key="endpoints_" />
    </section>
  </template>
</template>
