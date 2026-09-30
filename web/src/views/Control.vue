<script setup>
import { computed } from 'vue'
import { useSnapshot } from '../store.js'
import { nodeFlags } from '../ux.js'
import { scoreClass } from '../format.js'
import DataTable from '../components/DataTable.vue'
const s = useSnapshot()
const all = computed(() => s.data.nodes || [])
// Состояния пересекаются: нода попадает в каждое подходящее.
const states = n => [+n.active === 1 && 'активные', n.score == null && 'без замеров', n.gstate === 'backoff' && 'пауза',
  n.gstate === 'garbage' && 'карантин', +n.banned === 1 && 'исключены', +n.present === 0 && 'отсутствуют'].filter(Boolean)
const extraFilters = [{ key:'state', title:'Состояние', get:states }]
const columns = [
  { key:'node', title:'Нода', l:true },
  { key:'provider', title:'Провайдер', l:true }, { key:'country',title:'Страна',l:true },
  { key:'groups', title:'Группы',l:true,fmt:v => (v || []).join(', ') || '—' }, { key:'score', title:'Рейтинг',fmt:v => v == null ? 'Нет замеров' : Number(v).toFixed(1),cls:scoreClass },
  { key:'flags',title:'Состояние',l:true,fmt:(_,n) => nodeFlags(n).join(' · ') },
  { key:'detail', title:'Подробности', l:true,slot:true },
]
</script>
<template>
  <p class="hint">Здесь ноды, известные тестеру, а не содержимое всех подписок. Откройте карточку для результатов, истории и действий.</p>
  <DataTable :rows="all" :extra-filters="extraFilters" :columns="columns" :page-size="20" empty="Нод с такими условиями нет. Сбросьте фильтры или проверьте состояние базы на обзоре.">
    <template #cell-detail="{row}"><RouterLink :to="'/nodes/'+row.crc">Открыть карточку</RouterLink></template>
  </DataTable>
</template>
