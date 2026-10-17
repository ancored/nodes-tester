<script setup>
import { computed } from 'vue'
import { useSnapshot } from '../store.js'
import { dateDM, dur, timeHMS, scoreClass, fixed, pct } from '../format.js'
import DataTable from '../components/DataTable.vue'
import { nodeColumns } from '../ux.js'
import StatusLegend from '../components/StatusLegend.vue'
import { STATUS } from '../status.js'
import { t } from '../i18n.js'
import Msg from '../components/Msg.vue'

const s = useSnapshot()

// --- Качество провайдеров ---
const qualityRows = computed(() => s.data.provider_quality || [])
const qualityFilters = [{ key: 'provider', title: t('Provider') }]
const score1 = (v) => fixed(v, 1)
const nextProbe = (v) => v ? `${dateDM(v)} ${timeHMS(v).slice(0, 5)}` : '–'
const qualCols = [
  { key: 'provider', title: t('provider'), l: true },
  { key: 'total', title: t('total nodes') },
  { key: 'dead_pct', title: t('share with rating 0'), fmt: pct, cls: (v) => (+v > 50 ? 'bad' : 'num'), strong: true },
  { key: 'avg', title: t('avg. rating (without zeros)'), fmt: score1, cls: scoreClass, strong: true },
]

// --- На паузе / в карантине; удалённые — в «Архиве» ---
const garbageRows = computed(() => s.data.garbage || [])
const stateLabel = (r) => (r.state === 'garbage' ? STATUS.quarantine.label : STATUS.pause.label)
const garbageFilters = [{ key: 'state', title: t('Status'), get: stateLabel }]
const garbCols = [
  ...nodeColumns({ country: 'cc' }),
  { key: 'first_seen', title: t('in config since'), fmt: dateDM, cls: () => 'num' },
  { key: 'state', title: t('status'), slot: true },
  { key: 'streak', title: t('failures in a row') },
  { key: 'garbage_count', title: t('× quarantined'), note: t('how many times it was quarantined over its history') },
  { key: 'last_garbage', title: t('last quarantine'), fmt: dateDM, cls: () => 'num' },
  { key: 'in_garbage', title: t('in status'), fmt: dur, cls: () => 'num', note: t('how long the current pause/quarantine episode lasts') },
  { key: 'until', title: t('next probe'), fmt: nextProbe, note: t('when the pause or quarantine expires') },
]

// --- Долгожители / выпадающие ---
const longevity = computed(() => s.data.longevity || [])
const longCols = [
  ...nodeColumns({ country: 'cc' }),
  { key: 'first_seen', title: t('in config since'), fmt: dateDM, cls: () => 'num' },
  { key: 'age', title: t('age'), fmt: dur, cls: () => 'num' },
  { key: 'score', title: 'score', fmt: score1, cls: scoreClass, strong: true },
  { key: 'garbage_count', title: t('× quarantined') },
  { key: 'fails', title: t('pauses+quarantines'), note: t('how many times it went into a pause or quarantine') },
  { key: 'active', title: 'act', slot: true },
  { key: 'reserve', title: t('reserve'), l: true, fmt: (v) => v || '—' },
]
const dropouts = computed(() => s.data.dropouts || [])
const dropCols = [
  ...nodeColumns({ country: 'cc' }),
  { key: 'first_seen', title: t('in config since'), fmt: dateDM, cls: () => 'num' },
  { key: 'first_garbage', title: t('1st quarantine'), fmt: dateDM, cls: () => 'num' },
  { key: 'lifespan', title: t('until 1st quarantine'), fmt: dur, cls: () => 'num' },
  { key: 'garbage_count', title: t('× quarantined') },
]
</script>

<template>
  <div v-if="s.loading" class="empty">{{ t('Loading…') }}</div>
  <div v-else-if="s.error && !s.ready" class="empty bad">{{ t('Error: {error}', { error: s.error }) }}</div>
  <template v-else>
    <StatusLegend />
    <p class="mut" style="margin-top: 0"><Msg :text="t('Only nodes present in the tester list. Missing ones are in the {archive}. Presence in the subscription is not checked here directly.')"><template #archive><RouterLink to="/graveyard">{{ t('archive') }}</RouterLink></template></Msg></p>

    <section>
      <h2>{{ t('Provider quality') }}</h2>
      <DataTable :rows="qualityRows" :columns="qualCols" :filters="qualityFilters" :page-size="15" query-key="quality_" />
    </section>

    <section>
      <h2>{{ t('Paused and quarantined') }}</h2>
      <DataTable :rows="garbageRows" :columns="garbCols" :extra-filters="garbageFilters" :page-size="15" query-key="limits_">
        <template #cell-state="{ row }">
          <b v-if="row.state === 'garbage'" :class="STATUS.quarantine.cls">{{ STATUS.quarantine.label }}</b>
          <b v-else :class="STATUS.pause.cls">{{ STATUS.pause.label }}</b>
        </template>
      </DataTable>
    </section>

    <section>
      <h2>{{ t('Long-lived') }} <small>{{ t('(in service, by age ↓)') }}</small></h2>
      <DataTable :rows="longevity" :columns="longCols" :page-size="12" query-key="long_">
        <template #cell-active="{ row }">
          <b v-if="+row.active === 1" class="good">{{ t('Yes') }}</b>
          <span v-else class="mut">{{ t('No') }}</span>
        </template>
      </DataTable>
    </section>
    <section>
      <h2>{{ t('Quick dropouts') }} <small>{{ t('(by time until the 1st quarantine ↑)') }}</small></h2>
      <DataTable :rows="dropouts" :columns="dropCols" :page-size="12" query-key="drops_" />
    </section>
  </template>
</template>
