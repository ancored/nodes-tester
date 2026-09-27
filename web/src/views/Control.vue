<script setup>
import { computed } from 'vue'
import { useSnapshot } from '../store.js'
import { useQuery } from '../query.js'
import { nodeFlags } from '../ux.js'
import { scoreClass } from '../format.js'
import DataTable from '../components/DataTable.vue'
const s = useSnapshot(), filter = useQuery('filter','все'), region = useQuery('region','все')
const all = computed(() => s.data.nodes || [])
const regions = computed(() => ['все', ...new Set(all.value.map(n => n.region).filter(Boolean))])
const rows = computed(() => all.value.filter(n => (region.value === 'все' || n.region === region.value) &&
  (filter.value === 'все' || filter.value === 'активные' && +n.active === 1 ||
  filter.value === 'без замеров' && n.score == null || filter.value === 'пауза' && n.gstate === 'backoff' ||
  filter.value === 'карантин' && n.gstate === 'garbage' || filter.value === 'исключены' && +n.banned === 1 ||
  filter.value === 'отсутствуют' && +n.present === 0)))
const columns = [
  { key:'node', title:'Нода', l:true },
  { key:'provider', title:'Провайдер', l:true }, { key:'country',title:'Страна',l:true },
  { key:'region', title:'Регион',l:true }, { key:'score', title:'Рейтинг',fmt:v => v == null ? 'Нет замеров' : Number(v).toFixed(1),cls:scoreClass },
  { key:'flags',title:'Состояние',l:true,fmt:(_,n) => nodeFlags(n).join(' · ') },
  { key:'detail', title:'Подробности', l:true,slot:true },
]
</script>
<template>
  <p class="hint">Здесь ноды, известные тестеру, а не содержимое всех подписок. Откройте карточку для результатов, истории и действий.</p>
  <div class="toolbar">
    <label>Состояние <select v-model="filter"><option v-for="v in ['все','активные','без замеров','пауза','карантин','исключены','отсутствуют']" :key="v">{{ v }}</option></select></label>
    <label>Регион <select v-model="region"><option v-for="v in regions" :key="v">{{ v }}</option></select></label>
  </div>
  <DataTable :rows="rows" :total="all.length" :columns="columns" :page-size="20" empty="Нод с такими условиями нет. Сбросьте фильтры или проверьте состояние базы на обзоре.">
    <template #cell-detail="{row}"><RouterLink :to="'/nodes/'+row.crc">Открыть карточку</RouterLink></template>
  </DataTable>
</template>
