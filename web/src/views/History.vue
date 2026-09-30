<script setup>
import { computed } from 'vue'
import { useSnapshot } from '../store.js'
import { useQuery } from '../query.js'
import { dateTime } from '../format.js'
import DataTable from '../components/DataTable.vue'
const s=useSnapshot(), region=useQuery('region','все')
const activeRegions=computed(()=>s.data.runner ? s.data.runner.regions || [] :
  (s.data.nodes || []).filter(n=>+n.active===1).map(n=>({region:n.region,active:n.node})))
const rows=computed(()=>{
  const active = new Map(activeRegions.value.map(r=>[r.region,r.active])), marked=new Set()
  return (s.data.history || []).map(r=>{
    const current = !marked.has(r.region) && !!active.get(r.region) && active.get(r.region)===r.tag
    if(current) marked.add(r.region)
    return {...r,current_choice:current}
  })
})
const regions=computed(()=>['все',...new Set(rows.value.map(r=>r.region).filter(Boolean))])
const filtered=computed(()=>region.value==='все' ? rows.value : rows.value.filter(r=>r.region===region.value))
const reasons={manual:'Ручной выбор',rotation:'Плановая ротация',quality:'Смена по качеству',emergency:'Аварийная замена',
  'emergency-stuck':'Авария: замена не найдена',failsafe:'Никто не прошёл обязательный тест — failsafe',initial:'Первый выбор',init:'Первый выбор'}
const columns=[
  {key:'ts',title:'Дата и время',fmt:dateTime,l:true},{key:'region',title:'Регион',l:true},
  {key:'tag',title:'Выбранная нода',l:true},{key:'crc',title:'Карточка',l:true},
  {key:'prev',title:'Предыдущая нода',l:true},{key:'reason',title:'Причина',l:true,fmt:(v,r)=>r.stuck ? 'Авария: замена не найдена' : reasons[v] || v || 'Не записана'},
  {key:'current_choice',title:'Активная нода',fmt:v=>v ? '● Активна' : '—',cls:v=>v ? 'good' : ''},
]
</script>
<template>
  <section class="panel"><h2>Активные ноды по регионам</h2>
    <p v-for="r in activeRegions" :key="r.region" class="break"><b>{{ r.region }}</b> · <span class="badge good">Активна</span> {{ r.active }}</p>
    <p v-if="!activeRegions.length">Активные ноды не выбраны или их состояние недоступно.</p>
    <p class="mut">{{ s.data.runner ? 'Текущий выбор подключённого переключателя.' : 'Последний сохранённый выбор из базы; живое состояние недоступно.' }} В таблице выделена последняя запись для каждой активной ноды.</p>
  </section>
  <p class="hint">Последние 200 записей. Зелёным выделена последняя запись для текущей активной ноды каждого региона. При аварии без замены новое переключение не произошло.</p>
  <div class="toolbar"><label>Регион<select v-model="region"><option v-for="r in regions" :key="r">{{ r }}</option></select></label></div>
  <DataTable :rows="filtered" :columns="columns" :page-size="20" :row-class="r=>r.current_choice ? 'active' : ''" />
</template>
