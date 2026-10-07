<script setup>
import { computed } from 'vue'
import { useSnapshot } from '../store.js'
import { scoreClass, fixed } from '../format.js'
import DataTable from '../components/DataTable.vue'
import Sparkline from '../components/Sparkline.vue'

const s = useSnapshot()

const rows = computed(() => s.data.rating || [])
const spark = computed(() => s.data.score_spark || {})

const c01 = (v) => fixed(v, 2)
const columns = [
  { key: 'provider', title: 'провайдер', l: true },
  { key: 'protocol', title: 'протокол', l: true },
  { key: 'country', title: 'страна', l: true },
  { key: 'id', title: 'crc', l: true, nowrap: true, cls: () => 'mut' },
  { key: 'score', title: 'рейтинг', slot: true },
  { key: 'active', title: 'выбрана', slot: true },
  { key: 'reserve', title: 'резерв', l: true, fmt: (v) => v || '—' },
  { key: 'reliability', title: 'надёжность', fmt: c01 },
  { key: 'consistency', title: 'стабильность', fmt: c01 },
  { key: 'throttle', title: 'троттлинг', fmt: c01 },
  { key: 'jitter', title: 'джиттер', fmt: c01 },
  { key: 'latency', title: 'задержка', fmt: c01 },
  { key: 'throughput', title: 'скорость', fmt: c01 },
  { key: 'spark', title: 'история', l: true, slot: true },
  { key: 'samples', title: 'замеров' },
]
const rowClass = (r) => (+r.active === 1 ? 'active' : '')
</script>

<template>
  <div v-if="s.loading" class="empty">Загрузка…</div>
  <div v-else-if="s.error && !s.ready" class="empty bad">Ошибка: {{ s.error }}</div>
  <template v-else>
    <p class="hint">Рейтинг: 0–100. Факторы надёжности, стабильности, троттлинга, джиттера, задержки и скорости нормированы от 0 до 1: больше — лучше. Это не сырые мс или Мбит/с; они в «Результатах». Цвет рейтинга — условный ориентир (≤20 / ≥70), не правило переключения.</p>
    <DataTable :rows="rows" :columns="columns" :row-class="rowClass" :page-size="20">
      <template #cell-score="{ row }">
        <b :class="scoreClass(row.score)">{{ (+row.score).toFixed(1) }}</b>
      </template>
      <template #cell-active="{ row }">
        <b v-if="+row.active === 1" class="good">Да</b>
        <span v-else class="mut">Нет</span>
      </template>
      <template #cell-spark="{ row }">
        <Sparkline :vals="spark[row.id] || []" />
      </template>
    </DataTable>
  </template>
</template>
