<script setup>
import { computed } from 'vue'
import { useSnapshot } from '../store.js'
import { dateDM, dur, timeHMS, scoreClass, fixed, pct } from '../format.js'
import DataTable from '../components/DataTable.vue'
import StatusLegend from '../components/StatusLegend.vue'
import { STATUS } from '../status.js'

const s = useSnapshot()

// --- Качество провайдеров (по группам нод) ---
const qualityRows = computed(() => s.data.provider_quality || [])
const qualityFilters = [{ key: 'group', title: 'Группа', get: (r) => r.group }, { key: 'provider', title: 'Провайдер' }]
const score1 = (v) => fixed(v, 1)
const nextProbe = (v, r) =>
  r.state === 'backoff'
    ? r.passes_left > 0 ? `пропустит ещё ${r.passes_left}` : 'в ближайшем прогоне'
    : v ? `${dateDM(v)} ${timeHMS(v).slice(0, 5)}` : '–'
const qualCols = [
  { key: 'provider', title: 'провайдер', l: true },
  { key: 'group', title: 'группа', l: true },
  { key: 'total', title: 'всего нод' },
  { key: 'dead_pct', title: 'доля с рейтингом 0', fmt: pct, cls: (v) => (+v > 50 ? 'bad' : 'num'), strong: true },
  { key: 'avg', title: 'ср. рейтинг (без нулей)', fmt: score1, cls: scoreClass, strong: true },
]

// --- На паузе / в карантине; удалённые — в «Архиве» ---
const garbageRows = computed(() => s.data.garbage || [])
const stateLabel = (r) => (r.state === 'garbage' ? STATUS.quarantine.label : STATUS.pause.label)
const garbageFilters = [{ key: 'state', title: 'Статус', get: stateLabel }]
const garbCols = [
  { key: 'provider', title: 'провайдер', l: true },
  { key: 'protocol', title: 'протокол', l: true },
  { key: 'cc', title: 'cc', l: true },
  { key: 'crc', title: 'crc', l: true, nowrap: true, cls: () => 'mut' },
  { key: 'first_seen', title: 'в конфиге с', fmt: dateDM, cls: () => 'num' },
  { key: 'state', title: 'статус', slot: true },
  { key: 'streak', title: 'провалов подряд' },
  { key: 'garbage_count', title: '× в карантине', note: 'сколько раз за историю попадала в карантин' },
  { key: 'last_garbage', title: 'посл. карантин', fmt: dateDM, cls: () => 'num' },
  { key: 'in_garbage', title: 'в статусе', fmt: dur, cls: () => 'num', note: 'сколько длится текущий эпизод паузы/карантина' },
  { key: 'until', title: 'след. проба', fmt: nextProbe, note: 'пауза — сколько прогонов ещё пропустит; карантин — когда истечёт' },
]

// --- Долгожители / выпадающие ---
const longevity = computed(() => s.data.longevity || [])
const longCols = [
  { key: 'provider', title: 'провайдер', l: true },
  { key: 'protocol', title: 'протокол', l: true },
  { key: 'cc', title: 'cc', l: true },
  { key: 'crc', title: 'crc', l: true, nowrap: true, cls: () => 'mut' },
  { key: 'first_seen', title: 'в конфиге с', fmt: dateDM, cls: () => 'num' },
  { key: 'age', title: 'возраст', fmt: dur, cls: () => 'num' },
  { key: 'score', title: 'score', fmt: score1, cls: scoreClass, strong: true },
  { key: 'garbage_count', title: '× в карантине' },
  { key: 'fails', title: 'пауз+карантинов', note: 'сколько раз уходила в паузу или карантин' },
  { key: 'active', title: 'act', slot: true },
  { key: 'reserve', title: 'резерв', l: true, fmt: (v) => v || '—' },
]
const dropouts = computed(() => s.data.dropouts || [])
const dropCols = [
  { key: 'provider', title: 'провайдер', l: true },
  { key: 'protocol', title: 'протокол', l: true },
  { key: 'cc', title: 'cc', l: true },
  { key: 'crc', title: 'crc', l: true, nowrap: true, cls: () => 'mut' },
  { key: 'first_seen', title: 'в конфиге с', fmt: dateDM, cls: () => 'num' },
  { key: 'first_garbage', title: '1-й карантин', fmt: dateDM, cls: () => 'num' },
  { key: 'lifespan', title: 'до 1-го карантина', fmt: dur, cls: () => 'num' },
  { key: 'garbage_count', title: '× в карантине' },
]
</script>

<template>
  <div v-if="s.loading" class="empty">Загрузка…</div>
  <div v-else-if="s.error && !s.ready" class="empty bad">Ошибка: {{ s.error }}</div>
  <template v-else>
    <StatusLegend />
    <p class="mut" style="margin-top: 0">Только ноды, присутствующие в списке тестера. Отсутствующие — в <RouterLink to="/graveyard">архиве</RouterLink>. Присутствие в подписке не проверяется здесь напрямую.</p>

    <section>
      <h2>Качество провайдеров</h2>
      <DataTable :rows="qualityRows" :columns="qualCols" :filters="qualityFilters" :page-size="15" query-key="quality_" />
    </section>

    <section>
      <h2>На паузе и в карантине</h2>
      <DataTable :rows="garbageRows" :columns="garbCols" :extra-filters="garbageFilters" :page-size="15" query-key="limits_">
        <template #cell-state="{ row }">
          <b v-if="row.state === 'garbage'" :class="STATUS.quarantine.cls">{{ STATUS.quarantine.label }}</b>
          <b v-else :class="STATUS.pause.cls">{{ STATUS.pause.label }}</b>
        </template>
      </DataTable>
    </section>

    <section>
      <h2>Долгожители <small>(в строю, по возрасту ↓)</small></h2>
      <DataTable :rows="longevity" :columns="longCols" :page-size="12" query-key="long_">
        <template #cell-active="{ row }">
          <b v-if="+row.active === 1" class="good">Да</b>
          <span v-else class="mut">Нет</span>
        </template>
      </DataTable>
    </section>
    <section>
      <h2>Быстро выпадающие <small>(по сроку до 1-го карантина ↑)</small></h2>
      <DataTable :rows="dropouts" :columns="dropCols" :page-size="12" query-key="drops_" />
    </section>
  </template>
</template>
