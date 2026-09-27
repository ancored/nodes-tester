<script setup>
import { computed } from 'vue'
import { useSnapshot } from '../store.js'
import { useQuery } from '../query.js'
import { dateTime } from '../format.js'
import DataTable from '../components/DataTable.vue'
const s=useSnapshot(), region=useQuery('region','все')
const rows=computed(()=>s.data.history || [])
const regions=computed(()=>['все',...new Set(rows.value.map(r=>r.region).filter(Boolean))])
const filtered=computed(()=>region.value==='все' ? rows.value : rows.value.filter(r=>r.region===region.value))
const reasons={manual:'Ручной выбор',rotation:'Плановая ротация',quality:'Смена по качеству',emergency:'Аварийная замена',
  'emergency-stuck':'Авария: замена не найдена',initial:'Первый выбор',init:'Первый выбор'}
const columns=[
  {key:'ts',title:'Дата и время',fmt:dateTime,l:true},{key:'region',title:'Регион',l:true},
  {key:'tag',title:'Выбранная нода',l:true},{key:'crc',title:'Карточка',l:true},
  {key:'prev',title:'Предыдущая нода',l:true},{key:'reason',title:'Причина',l:true,fmt:(v,r)=>r.stuck ? 'Авария: замена не найдена' : reasons[v] || v || 'Не записана'},
  {key:'active',title:'Последняя запись региона',fmt:v=>+v===1 ? 'Да' : 'Нет'},
]
</script>
<template>
  <p class="hint">Последние 200 записей. «Последняя запись региона» не означает текущую активную ноду. При аварии без замены новое переключение не произошло.</p>
  <div class="toolbar"><label>Регион<select v-model="region"><option v-for="r in regions" :key="r">{{ r }}</option></select></label></div>
  <DataTable :rows="filtered" :columns="columns" :page-size="20" />
</template>
