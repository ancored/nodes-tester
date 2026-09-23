<script setup>
import { ref, computed } from 'vue'
import { useSnapshot, refresh } from '../store.js'
import { api, useAdminToken } from '../api.js'
import { scoreClass } from '../format.js'
import DataTable from '../components/DataTable.vue'
import Chips from '../components/Chips.vue'

const s = useSnapshot()
const msg = ref('')
const busy = ref(false)
const filter = ref('все')
const adminToken = useAdminToken()
const hasToken = computed(() => !!adminToken.value)

const rows = computed(() => s.data.nodes || [])
const filters = ['все', 'активные', 'в карантине', 'забанены', 'ушли']
const filtered = computed(() => {
  const r = rows.value
  if (filter.value === 'все') return r
  if (filter.value === 'активные') return r.filter((n) => +n.active === 1)
  if (filter.value === 'в карантине') return r.filter((n) => n.gstate === 'garbage')
  if (filter.value === 'забанены') return r.filter((n) => +n.banned === 1)
  if (filter.value === 'ушли') return r.filter((n) => +n.present === 0)
  return r
})

async function act(path, okMsg = 'готово') {
  busy.value = true
  msg.value = ''
  try {
    await api.post(path)
    msg.value = okMsg
    await refresh()
  } catch (e) {
    msg.value = e.message
  } finally {
    busy.value = false
  }
}

async function doActivate(node) {
  if (!node.region) { msg.value = 'нода не в рейтинге — регион неизвестен'; return }
  busy.value = true
  msg.value = ''
  try {
    await api.post('/regions/' + node.region + '/switch', { node: node.node })
    msg.value = 'переключено'
    await refresh()
  } catch (e) {
    msg.value = e.message
  } finally {
    busy.value = false
  }
}

const columns = [
  { key: 'provider', title: 'провайдер', l: true },
  { key: 'protocol', title: 'протокол', l: true },
  { key: 'country', title: 'cc', l: true },
  { key: 'crc', title: 'crc', l: true, cls: () => 'mut' },
  { key: 'region', title: 'регион', l: true },
  { key: 'score', title: 'score', slot: true },
  { key: 'state', title: 'статус', l: true, slot: true },
  { key: 'actions', title: 'действия', l: true, slot: true },
]
const rowClass = (r) => (+r.active === 1 ? 'active' : '')

function stateOf(n) {
  if (+n.banned === 1) return { t: 'бан', c: 'bad' }
  if (n.gstate === 'garbage') return { t: 'карантин', c: 'bad' }
  if (n.gstate === 'backoff') return { t: 'backoff', c: 'heavy' }
  if (+n.present === 0) return { t: 'ушла', c: 'mut' }
  if (+n.active === 1) return { t: 'активная', c: 'good' }
  return { t: '·', c: 'mut' }
}
</script>

<template>
  <div v-if="s.loading" class="empty">Загрузка…</div>
  <div v-else-if="s.error && !s.ready" class="empty bad">Ошибка: {{ s.error }}</div>
  <template v-else>
    <div v-if="!hasToken" class="hint">
      Токен не задан — write-действия недоступны. Введите <code>X-Admin-Token</code> в шапке.
    </div>
    <div class="chips" style="justify-content: space-between">
      <Chips v-model="filter" :options="filters" label="фильтр" />
      <span class="mut" style="font-size: 12px">{{ msg || '' }}</span>
    </div>
    <DataTable :rows="filtered" :columns="columns" :row-class="rowClass" :page-size="20">
      <template #cell-score="{ row }">
        <b :class="scoreClass(row.score)">{{ row.score != null ? (+row.score).toFixed(1) : '–' }}</b>
      </template>
      <template #cell-state="{ row }">
        <b :class="stateOf(row).c">{{ stateOf(row).t }}</b>
      </template>
      <template #cell-actions="{ row }">
        <span class="actions">
          <button
            class="btn sm"
            :disabled="busy"
            :title="+row.banned === 1 ? 'снять бан' : 'забанить (пропускать в тестах)'"
            @click="+row.banned === 1 ? act('/nodes/' + row.crc + '/unban', 'разбанено') : act('/nodes/' + row.crc + '/ban', 'забанено')"
          >{{ +row.banned === 1 ? 'разбанить' : 'бан' }}</button>
          <button
            class="btn sm"
            :disabled="busy"
            :title="row.gstate ? 'снять карантин/backoff' : 'отправить в карантин'"
            @click="row.gstate ? act('/nodes/' + row.crc + '/unquarantine', 'карантин снят') : act('/nodes/' + row.crc + '/quarantine', 'в карантин')"
          >{{ row.gstate ? 'снять карантин' : 'карантин' }}</button>
          <button
            class="btn sm"
            :disabled="busy || !row.region || +row.score <= 0"
            title="сделать активной нодой региона"
            @click="doActivate(row)"
          >активировать</button>
        </span>
      </template>
    </DataTable>
  </template>
</template>
