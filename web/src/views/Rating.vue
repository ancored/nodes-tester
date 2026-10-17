<script setup>
import { computed } from 'vue'
import { useSnapshot } from '../store.js'
import { scoreClass, fixed } from '../format.js'
import DataTable from '../components/DataTable.vue'
import { nodeColumns } from '../ux.js'
import Sparkline from '../components/Sparkline.vue'
import { t } from '../i18n.js'

const s = useSnapshot()

const rows = computed(() => s.data.rating || [])
const spark = computed(() => s.data.score_spark || {})

const c01 = (v) => fixed(v, 2)
const columns = [
  ...nodeColumns({ crc: 'id' }),
  { key: 'score', title: t('rating'), slot: true },
  { key: 'active', title: t('selected'), slot: true },
  { key: 'reserve', title: t('reserve'), l: true, fmt: (v) => v || '—' },
  { key: 'reliability', title: t('reliability'), fmt: c01 },
  { key: 'consistency', title: t('consistency'), fmt: c01 },
  { key: 'throttle', title: t('throttling'), fmt: c01 },
  { key: 'jitter', title: t('jitter'), fmt: c01 },
  { key: 'latency', title: t('latency'), fmt: c01 },
  { key: 'throughput', title: t('speed'), fmt: c01 },
  { key: 'spark', title: t('history'), l: true, slot: true },
  { key: 'samples', title: t('samples') },
]
const rowClass = (r) => (+r.active === 1 ? 'active' : '')
</script>

<template>
  <div v-if="s.loading" class="empty">{{ t('Loading…') }}</div>
  <div v-else-if="s.error && !s.ready" class="empty bad">{{ t('Error: {error}', { error: s.error }) }}</div>
  <template v-else>
    <p class="hint">{{ t('Rating: 0–100. The reliability, consistency, throttling, jitter, latency and speed factors are normalised from 0 to 1: higher is better. These are not raw ms or Mbit/s; those are in “Results”. The rating colour is a rough guide (≤20 / ≥70), not a switching rule.') }}</p>
    <DataTable :rows="rows" :columns="columns" :row-class="rowClass" :page-size="20">
      <template #cell-score="{ row }">
        <b :class="scoreClass(row.score)">{{ (+row.score).toFixed(1) }}</b>
      </template>
      <template #cell-active="{ row }">
        <b v-if="+row.active === 1" class="good">{{ t('Yes') }}</b>
        <span v-else class="mut">{{ t('No') }}</span>
      </template>
      <template #cell-spark="{ row }">
        <Sparkline :vals="spark[row.id] || []" />
      </template>
    </DataTable>
  </template>
</template>
