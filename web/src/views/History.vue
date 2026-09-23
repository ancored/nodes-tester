<script setup>
import { ref, computed } from 'vue'
import { useSnapshot } from '../store.js'
import { timeHMS } from '../format.js'
import DataTable from '../components/DataTable.vue'
import Chips from '../components/Chips.vue'

const s = useSnapshot()
const region = ref('все')

const rows = computed(() => s.data.history || [])
const regions = computed(() => ['все', ...[...new Set(rows.value.map((r) => r.region).filter(Boolean))].sort()])
const filtered = computed(() =>
  region.value === 'все' ? rows.value : rows.value.filter((r) => r.region === region.value)
)

const columns = [
  { key: 'provider', title: 'провайдер', l: true },
  { key: 'protocol', title: 'протокол', l: true },
  { key: 'cc', title: 'cc', l: true },
  { key: 'crc', title: 'crc', l: true, cls: () => 'mut' },
  { key: 'ts', title: 'переключено', fmt: timeHMS },
  { key: 'reason', title: 'причина', l: true, slot: true },
  { key: 'active', title: 'активная', slot: true },
]
const rowClass = (r) => (+r.active === 1 ? 'active' : '')
</script>

<template>
  <div v-if="s.loading" class="empty">Загрузка…</div>
  <div v-else-if="s.error && !s.ready" class="empty bad">Ошибка: {{ s.error }}</div>
  <template v-else>
    <Chips v-model="region" :options="regions" label="регион" />
    <DataTable :rows="filtered" :columns="columns" :row-class="rowClass" :page-size="20">
      <template #cell-reason="{ row }">
        <b v-if="row.stuck" class="bad" title="EMERGENCY без замены — активная заблокирована, здоровых кандидатов нет">⚠ EMERGENCY</b>
        <b v-else-if="row.emergency" class="bad" title="аварийное переключение (активная заблокирована)">EMERGENCY</b>
        <span v-else class="mut">{{ row.reason || '' }}</span>
      </template>
      <template #cell-active="{ row }">
        <b v-if="+row.active === 1" class="good">●</b>
        <span v-else class="mut">·</span>
      </template>
    </DataTable>
  </template>
</template>
