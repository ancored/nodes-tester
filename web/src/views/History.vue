<script setup>
import { computed } from 'vue'
import { useSnapshot } from '../store.js'
import { dateTime } from '../format.js'
import DataTable from '../components/DataTable.vue'
import ReasonIcon from '../components/ReasonIcon.vue'
const s=useSnapshot()
const activeRegions=computed(()=>s.data.runner?.regions || [])
const rows=computed(()=>{
  const active = new Map(activeRegions.value.map(r=>[r.region,r.active])), marked=new Set()
  return (s.data.history || []).map(r=>{
    const current = !marked.has(r.region) && !!active.get(r.region) && active.get(r.region)===r.tag
    if(current) marked.add(r.region)
    return {...r,groups:[r.region],reason_key:r.stuck ? 'emergency-stuck' : r.reason,current_choice:current}
  })
})
const reasons={manual:'Ручной выбор',rotation:'Плановая ротация',quality:'Смена по качеству',emergency:'Аварийная замена',
  'emergency-stuck':'Авария: замена не найдена',failsafe:'Никто не прошёл обязательный тест — failsafe',initial:'Первый выбор',init:'Первый выбор'}
const reasonText=v=>reasons[v] || v || 'Не записана'
const reasonFilters=[{key:'reason',title:'Причина',get:r=>reasonText(r.reason_key)}]
const columns=[
  {key:'ts',title:'Дата и время',fmt:dateTime,l:true},{key:'region',title:'Группа',l:true},
  {key:'tag',title:'Выбранная нода',l:true},{key:'crc',title:'Карточка',l:true,nowrap:true},
  {key:'prev',title:'Предыдущая нода',l:true},{key:'reason_key',title:'Причина',l:true,slot:true},
  {key:'current_choice',title:'Активная нода',fmt:v=>v ? '● Активна' : '—',cls:v=>v ? 'good' : ''},
]
</script>
<template>
  <section class="panel"><h2>Активные ноды по группам</h2>
    <p v-for="r in activeRegions" :key="r.region" class="break"><b>{{ r.region }}</b> · <span class="badge good">Активна</span> {{ r.active }}</p>
    <p v-if="!activeRegions.length">Активные ноды не выбраны или их состояние недоступно.</p>
    <p class="mut">{{ s.data.runner ? 'Текущий выбор подключённого переключателя. В таблице выделена последняя запись для каждой активной ноды.' : 'В отдельном режиме живой выбор групп недоступен; история ниже остаётся доступной.' }}</p>
  </section>
  <p class="hint">Последние 200 записей. При работающем тестере зелёным выделена последняя запись для текущей активной ноды каждой группы. При аварии без замены новое переключение не произошло.</p>
  <DataTable :rows="rows" :columns="columns" :extra-filters="reasonFilters" :page-size="20" :row-class="r=>r.current_choice ? 'active' : ''">
    <template #cell-reason_key="{row}"><span class="reason"><ReasonIcon :reason="row.reason_key" />{{ reasonText(row.reason_key) }}</span></template>
  </DataTable>
</template>
