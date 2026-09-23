<script setup>
import { ref, computed } from 'vue'
import { useSnapshot } from '../store.js'
import { scoreClass, fixed } from '../format.js'
import DataTable from '../components/DataTable.vue'
import Chips from '../components/Chips.vue'
import Sparkline from '../components/Sparkline.vue'

const s = useSnapshot()
const region = ref('все')

const rows = computed(() => s.data.rating || [])
const spark = computed(() => s.data.score_spark || {})
const regions = computed(() => ['все', ...[...new Set(rows.value.map((r) => r.region).filter(Boolean))].sort()])
const filtered = computed(() =>
  region.value === 'все' ? rows.value : rows.value.filter((r) => r.region === region.value)
)

const c01 = (v) => fixed(v, 2)
const columns = [
  { key: 'provider', title: 'провайдер', l: true },
  { key: 'protocol', title: 'протокол', l: true },
  { key: 'country', title: 'cc', l: true },
  { key: 'id', title: 'crc', l: true, cls: () => 'mut' },
  { key: 'score', title: 'score', slot: true },
  { key: 'active', title: 'act', slot: true },
  { key: 'reliability', title: 'rel', fmt: c01 },
  { key: 'consistency', title: 'cons', fmt: c01 },
  { key: 'throttle', title: 'thr', fmt: c01 },
  { key: 'jitter', title: 'jit', fmt: c01 },
  { key: 'latency', title: 'lat', fmt: c01 },
  { key: 'throughput', title: 'dl', fmt: c01 },
  { key: 'spark', title: 'история', l: true, slot: true },
  { key: 'samples', title: 'n' },
]
const rowClass = (r) => (+r.active === 1 ? 'active' : '')
</script>

<template>
  <div v-if="s.loading" class="empty">Загрузка…</div>
  <div v-else-if="s.error && !s.ready" class="empty bad">Ошибка: {{ s.error }}</div>
  <template v-else>
    <Chips v-model="region" :options="regions" label="регион" />
    <DataTable :rows="filtered" :columns="columns" :row-class="rowClass" :page-size="20">
      <template #cell-score="{ row }">
        <b :class="scoreClass(row.score)">{{ (+row.score).toFixed(1) }}</b>
      </template>
      <template #cell-active="{ row }">
        <b v-if="+row.active === 1" class="good">●</b>
        <span v-else class="mut">·</span>
      </template>
      <template #cell-spark="{ row }">
        <Sparkline :vals="spark[row.id] || []" />
      </template>
    </DataTable>
  </template>
</template>
