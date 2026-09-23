<script setup>
import { ref, computed } from 'vue'
import { useSnapshot } from '../store.js'
import { dateDM, dur, timeHMS, scoreClass, fixed, pct } from '../format.js'
import DataTable from '../components/DataTable.vue'
import Chips from '../components/Chips.vue'
import AttritionChart from '../components/AttritionChart.vue'

const s = useSnapshot()

// --- Качество провайдеров (region tabs) ---
const qualRegion = ref('все')
const qualityRows = computed(() => s.data.provider_quality || [])
const qualRegions = computed(() => ['все', ...[...new Set(qualityRows.value.map((r) => r.region).filter(Boolean))].sort()])
const qualityFiltered = computed(() =>
  qualRegion.value === 'все' ? qualityRows.value : qualityRows.value.filter((r) => r.region === qualRegion.value)
)
const score1 = (v) => fixed(v, 1)
const qualCols = [
  { key: 'provider', title: 'провайдер', l: true },
  { key: 'region', title: 'регион', l: true },
  { key: 'total', title: 'всего нод' },
  { key: 'dead_pct', title: 'доля мёртвых', fmt: pct, cls: (v) => (+v > 50 ? 'bad' : 'num'), strong: true },
  { key: 'avg', title: 'ср. рейтинг живых', fmt: score1, cls: scoreClass, strong: true },
]

// --- Мусорные / деградирующие (фильтры provider/cc/protocol) ---
const garbageRows = computed(() => s.data.garbage || [])
const fProv = ref('все'), fCc = ref('все'), fProto = ref('все')
const uniq = (rows, k) => ['все', ...[...new Set(rows.map((r) => r[k]).filter((v) => v != null && v !== ''))].map(String).sort()]
const garbProv = computed(() => uniq(garbageRows.value, 'provider'))
const garbCc = computed(() => uniq(garbageRows.value, 'cc'))
const garbProto = computed(() => uniq(garbageRows.value, 'protocol'))
const garbageFiltered = computed(() =>
  garbageRows.value.filter(
    (r) =>
      (fProv.value === 'все' || String(r.provider) === fProv.value) &&
      (fCc.value === 'все' || String(r.cc) === fCc.value) &&
      (fProto.value === 'все' || String(r.protocol) === fProto.value)
  )
)
const garbCols = [
  { key: 'provider', title: 'провайдер', l: true },
  { key: 'protocol', title: 'протокол', l: true },
  { key: 'cc', title: 'cc', l: true },
  { key: 'crc', title: 'crc', l: true, cls: () => 'mut' },
  { key: 'first_seen', title: 'в конфиге с', fmt: dateDM, cls: () => 'num' },
  { key: 'state', title: 'статус', slot: true },
  { key: 'streak', title: 'провалов' },
  { key: 'garbage_count', title: '× в мусоре' },
  { key: 'last_garbage', title: 'посл. мусор', fmt: dateDM, cls: () => 'num' },
  { key: 'in_garbage', title: 'в мусоре', fmt: dur, cls: () => 'num' },
  { key: 'until', title: 'до', fmt: timeHMS },
  { key: 'deleted', title: 'удалена', slot: true },
]

// --- Долгожители / выпадающие ---
const longevity = computed(() => s.data.longevity || [])
const longCols = [
  { key: 'provider', title: 'провайдер', l: true },
  { key: 'protocol', title: 'протокол', l: true },
  { key: 'cc', title: 'cc', l: true },
  { key: 'crc', title: 'crc', l: true, cls: () => 'mut' },
  { key: 'first_seen', title: 'в конфиге с', fmt: dateDM, cls: () => 'num' },
  { key: 'age', title: 'возраст', fmt: dur, cls: () => 'num' },
  { key: 'score', title: 'score', fmt: score1, cls: scoreClass, strong: true },
  { key: 'garbage_count', title: '× в мусоре' },
  { key: 'fails', title: 'провалов' },
  { key: 'active', title: 'act', slot: true },
]
const dropouts = computed(() => s.data.dropouts || [])
const dropCols = [
  { key: 'provider', title: 'провайдер', l: true },
  { key: 'protocol', title: 'протокол', l: true },
  { key: 'cc', title: 'cc', l: true },
  { key: 'crc', title: 'crc', l: true, cls: () => 'mut' },
  { key: 'first_seen', title: 'в конфиге с', fmt: dateDM, cls: () => 'num' },
  { key: 'first_garbage', title: '1-й мусор', fmt: dateDM, cls: () => 'num' },
  { key: 'lifespan', title: 'прожила', fmt: dur, cls: () => 'num' },
  { key: 'garbage_count', title: '× в мусоре' },
  { key: 'deleted', title: 'удалена', slot: true },
]

const attrition = computed(() => s.data.attrition || [])
</script>

<template>
  <div v-if="s.loading" class="empty">Загрузка…</div>
  <div v-else-if="s.error && !s.ready" class="empty bad">Ошибка: {{ s.error }}</div>
  <template v-else>
    <section>
      <h2>Качество провайдеров</h2>
      <Chips v-model="qualRegion" :options="qualRegions" label="регион" />
      <DataTable :rows="qualityFiltered" :columns="qualCols" :page-size="15" />
    </section>

    <section>
      <h2>Мусорные / деградирующие ноды</h2>
      <Chips v-model="fProv" :options="garbProv" label="провайдер" />
      <Chips v-model="fCc" :options="garbCc" label="cc" />
      <Chips v-model="fProto" :options="garbProto" label="протокол" />
      <DataTable :rows="garbageFiltered" :columns="garbCols" :page-size="15">
        <template #cell-state="{ row }">
          <b v-if="row.state === 'garbage'" class="bad">карантин</b>
          <b v-else class="num">backoff</b>
        </template>
        <template #cell-deleted="{ row }">
          <b v-if="+row.deleted === 1" class="bad" title="ноды больше нет в подписке">● удалена</b>
          <span v-else class="mut">·</span>
        </template>
      </DataTable>
    </section>

    <section>
      <h2>Динамика выбытия</h2>
      <AttritionChart :rows="attrition" />
    </section>

    <div class="grid2">
      <section>
        <h2>Долгожители <small>(живые, по возрасту ↓)</small></h2>
        <DataTable :rows="longevity" :columns="longCols" :page-size="12">
          <template #cell-active="{ row }">
            <b v-if="+row.active === 1" class="good">●</b>
            <span v-else class="mut">·</span>
          </template>
        </DataTable>
      </section>
      <section>
        <h2>Быстро выпадающие <small>(по сроку жизни до 1-го мусора ↑)</small></h2>
        <DataTable :rows="dropouts" :columns="dropCols" :page-size="12">
          <template #cell-deleted="{ row }">
            <b v-if="+row.deleted === 1" class="bad" title="ноды больше нет в подписке">● удалена</b>
            <span v-else class="mut">·</span>
          </template>
        </DataTable>
      </section>
    </div>
  </template>
</template>
