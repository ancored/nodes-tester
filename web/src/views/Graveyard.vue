<script setup>
import { computed } from 'vue'
import { useSnapshot } from '../store.js'
import { dateDM, dur, scoreClass, fixed } from '../format.js'
import { CAUSES } from '../status.js'
import DataTable from '../components/DataTable.vue'
import { nodeColumns } from '../ux.js'
import Sparkline from '../components/Sparkline.vue'
import { t } from '../i18n.js'
import HelpButton from '../components/HelpButton.vue'

const s = useSnapshot()
const rows = computed(() => s.data.graveyard || [])

const causeFilters = [{ key: 'cause', title: t('How it left'), get: (r) => CAUSES[r.cause]?.label || r.cause }]

const med = (arr) => {
  if (!arr.length) return 0
  const a = [...arr].sort((x, y) => x - y)
  return a[Math.floor(a.length / 2)]
}
const kpis = computed(() => {
  const r = rows.value
  const week = Date.now() / 1000 - 7 * 86400
  return [
    { label: t('In the archive'), value: r.length },
    { label: t('Gone in 7 days'), value: r.filter((x) => x.removed_at >= week).length },
    { label: t('Median lifetime'), value: dur(med(r.map((x) => x.lifespan))) },
    { label: t('Left while working'), value: r.filter((x) => x.cause === 'alive').length },
  ]
})

// Сводка по провайдерам: сколько потеряли и сколько в среднем жили их ноды.
const byProvider = computed(() => {
  const acc = {}
  for (const r of rows.value) {
    const a = (acc[r.provider || '?'] ??= { provider: r.provider || '?', n: 0, life: [], peak: [], alive: 0 })
    a.n++
    a.life.push(r.lifespan)
    if (r.peak != null) a.peak.push(+r.peak)
    if (r.cause === 'alive') a.alive++
  }
  return Object.values(acc)
    .map((a) => ({
      provider: a.provider, n: a.n, alive: a.alive,
      life: med(a.life),
      peak: a.peak.length ? a.peak.reduce((x, y) => x + y, 0) / a.peak.length : null,
    }))
    .sort((x, y) => y.n - x.n)
})
const provCols = [
  { key: 'provider', title: t('provider'), l: true },
  { key: 'n', title: t('nodes removed'), strong: true },
  { key: 'life', title: t('median lifetime'), fmt: dur, cls: () => 'num' },
  { key: 'peak', title: t('avg. rating peak'), fmt: (v) => fixed(v, 1), cls: scoreClass },
  { key: 'alive', title: t('left while working') },
]

const cols = [
  ...nodeColumns({ country: 'cc' }),
  { key: 'first_seen', title: t('first seen'), fmt: dateDM, cls: () => 'num' },
  { key: 'removed_at', title: t('removed on'), fmt: dateDM, cls: () => 'num' },
  { key: 'lifespan', title: t('lived'), fmt: dur, cls: () => 'num', strong: true },
  { key: 'spark', title: t('rating'), slot: true },
  { key: 'peak', title: t('peak'), fmt: (v) => fixed(v, 1), cls: scoreClass },
  { key: 'avg', title: t('avg.'), fmt: (v) => fixed(v, 1), cls: scoreClass },
  { key: 'last', title: t('at the end'), fmt: (v) => fixed(v, 1), cls: scoreClass, strong: true },
  { key: 'garbage_count', title: t('× quarantined') },
  { key: 'cause', title: t('how it left'), l: true, slot: true },
  { key: 'purge_in', title: t('erased in'), fmt: dur, cls: () => 'mut' },
]
</script>

<template>
  <div v-if="s.loading" class="empty">{{ t('Loading…') }}</div>
  <div v-else-if="s.error && !s.ready" class="empty bad">{{ t('Error: {error}', { error: s.error }) }}</div>
  <template v-else>
    <div class="kpis">
      <div v-for="k in kpis" :key="k.label" class="kpi">
        <div class="label">{{ k.label }}</div>
        <div class="value">{{ k.value }}</div>
      </div>
    </div>

    <section>
      <h2>{{ t('Losses by provider') }}<HelpButton topic="nodes#losses" /></h2>
      <DataTable :rows="byProvider" :columns="provCols" :page-size="10" query-key="providers_" />
    </section>

    <section>
      <h2>{{ t('History of missing nodes') }} <small>{{ t('(recent losses first)') }}</small><HelpButton topic="nodes#history" /></h2>
      <DataTable :rows="rows" :extra-filters="causeFilters" :columns="cols" :page-size="20" :empty="t('There are no archived nodes in this snapshot.')">
        <template #cell-spark="{ row }"><Sparkline :vals="row.spark" /></template>
        <template #cell-cause="{ row }">
          <b :class="CAUSES[row.cause]?.cls" :title="CAUSES[row.cause]?.hint">{{ CAUSES[row.cause]?.label || row.cause }}</b>
        </template>
      </DataTable>
    </section>
  </template>
</template>
