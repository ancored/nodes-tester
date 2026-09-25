<script setup>
import { ref, computed } from 'vue'
import { useSnapshot } from '../store.js'
import { dateDM, dur, scoreClass, fixed } from '../format.js'
import { CAUSES } from '../status.js'
import DataTable from '../components/DataTable.vue'
import Chips from '../components/Chips.vue'
import Sparkline from '../components/Sparkline.vue'

const s = useSnapshot()
const rows = computed(() => s.data.graveyard || [])

const fProv = ref('все'), fCause = ref('все')
const provs = computed(() => ['все', ...[...new Set(rows.value.map((r) => r.provider).filter(Boolean))].sort()])
const causeOpts = computed(() => ['все', ...Object.keys(CAUSES).filter((k) => rows.value.some((r) => r.cause === k)).map((k) => CAUSES[k].label)])
const filtered = computed(() =>
  rows.value.filter(
    (r) =>
      (fProv.value === 'все' || r.provider === fProv.value) &&
      (fCause.value === 'все' || CAUSES[r.cause]?.label === fCause.value)
  )
)

const med = (arr) => {
  if (!arr.length) return 0
  const a = [...arr].sort((x, y) => x - y)
  return a[Math.floor(a.length / 2)]
}
const kpis = computed(() => {
  const r = rows.value
  const week = Date.now() / 1000 - 7 * 86400
  return [
    { label: 'На кладбище', value: r.length },
    { label: 'Ушло за 7 дней', value: r.filter((x) => x.removed_at >= week).length },
    { label: 'Медиана жизни', value: dur(med(r.map((x) => x.lifespan))) },
    { label: 'Ушли рабочими', value: r.filter((x) => x.cause === 'alive').length },
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
  { key: 'provider', title: 'провайдер', l: true },
  { key: 'n', title: 'удалено нод', strong: true },
  { key: 'life', title: 'медиана жизни', fmt: dur, cls: () => 'num' },
  { key: 'peak', title: 'ср. пик рейтинга', fmt: (v) => fixed(v, 1), cls: scoreClass },
  { key: 'alive', title: 'ушли рабочими', note: 'провайдер убрал ноду, которая ещё проходила тесты' },
]

const cols = [
  { key: 'provider', title: 'провайдер', l: true },
  { key: 'protocol', title: 'протокол', l: true },
  { key: 'cc', title: 'cc', l: true },
  { key: 'crc', title: 'crc', l: true, cls: () => 'mut', cellTitle: (r) => r.tag || '' },
  { key: 'first_seen', title: 'появилась', fmt: dateDM, cls: () => 'num' },
  { key: 'removed_at', title: 'удалена', fmt: dateDM, cls: () => 'num' },
  { key: 'lifespan', title: 'прожила', fmt: dur, cls: () => 'num', strong: true },
  { key: 'spark', title: 'рейтинг', slot: true, note: 'траектория рейтинга за всю жизнь (score_history)' },
  { key: 'peak', title: 'пик', fmt: (v) => fixed(v, 1), cls: scoreClass },
  { key: 'avg', title: 'средн.', fmt: (v) => fixed(v, 1), cls: scoreClass },
  { key: 'last', title: 'в конце', fmt: (v) => fixed(v, 1), cls: scoreClass, strong: true },
  { key: 'garbage_count', title: '× в карантине' },
  { key: 'cause', title: 'как ушла', l: true, slot: true },
  { key: 'purge_in', title: 'сотрётся через', fmt: dur, cls: () => 'mut', note: 'через столько запись удалится из БД (retention_days)' },
]
</script>

<template>
  <div v-if="s.loading" class="empty">Загрузка…</div>
  <div v-else-if="s.error && !s.ready" class="empty bad">Ошибка: {{ s.error }}</div>
  <template v-else>
    <p class="mut" style="margin-top: 0">
      Ноды, которые провайдер убрал из подписки. Их история хранится в БД ещё
      <code>retention_days</code> (по умолчанию 30 дн.) после ухода, затем стирается —
      см. колонку «сотрётся через».
    </p>
    <div class="kpis">
      <div v-for="k in kpis" :key="k.label" class="kpi">
        <div class="label">{{ k.label }}</div>
        <div class="value">{{ k.value }}</div>
      </div>
    </div>

    <section>
      <h2>Потери по провайдерам</h2>
      <DataTable :rows="byProvider" :columns="provCols" :page-size="10" />
    </section>

    <section>
      <h2>Надгробия <small>(свежие потери сверху)</small></h2>
      <Chips v-model="fProv" :options="provs" label="провайдер" />
      <Chips v-model="fCause" :options="causeOpts" label="как ушла" />
      <DataTable :rows="filtered" :columns="cols" :page-size="20" empty="кладбище пусто — ни одна нода не уходила">
        <template #cell-spark="{ row }"><Sparkline :vals="row.spark" /></template>
        <template #cell-cause="{ row }">
          <b :class="CAUSES[row.cause]?.cls" :title="CAUSES[row.cause]?.hint">{{ CAUSES[row.cause]?.label || row.cause }}</b>
        </template>
      </DataTable>
    </section>
  </template>
</template>
