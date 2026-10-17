<script setup>
import { computed } from 'vue'
import { useSnapshot } from '../store.js'
import { dateTime } from '../format.js'
import { crcOf } from '../ux.js'
import { t } from '../i18n.js'
import NodeKey from '../components/NodeKey.vue'
import DataTable from '../components/DataTable.vue'
import ReasonIcon from '../components/ReasonIcon.vue'
import HelpButton from '../components/HelpButton.vue'
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
const reasons={manual:t('Manual choice'),rotation:t('Scheduled rotation'),quality:t('Change by quality'),emergency:t('Emergency replacement'),
  'emergency-stuck':t('Emergency: no replacement found'),failsafe:t('No candidates: fallback outbound'),initial:t('First choice'),init:t('First choice')}
const reasonText=v=>reasons[v] || v || t('Not recorded')
const reasonFilters=[{key:'region',title:t('Group')},{key:'reason',title:t('Reason'),get:r=>reasonText(r.reason_key)}]
const keyOf=tag=>crcOf({node:tag || ''})
const columns=[
  {key:'ts',title:t('Date and time'),fmt:dateTime,l:true},{key:'region',title:t('Group'),l:true},
  {key:'tag',title:t('Selected node'),l:true,slot:true},
  {key:'prev',title:t('Previous node'),l:true,slot:true},{key:'reason_key',title:t('Reason'),l:true,slot:true},
  {key:'current_choice',title:t('Active node'),fmt:v=>v ? '● ' + t('Active') : '—',cls:v=>v ? 'good' : ''},
]
</script>
<template>
  <section class="panel"><h2>{{ t('Active nodes by group') }}<HelpButton topic="switches#active" /></h2>
    <p v-for="r in activeRegions" :key="r.region" class="break"><b>{{ r.region }}</b> · <span class="badge good">{{ t('Active') }}</span> {{ r.active }}</p>
    <p v-if="!activeRegions.length">{{ t('No active nodes are selected or their state is unavailable.') }}</p>
    <p v-if="!s.data.runner" class="mut">{{ t('Live group choice is unavailable in standalone mode; the history below is still available.') }}</p>
  </section>
  <h2>{{ t('History') }}<HelpButton topic="switches#history" /></h2>
  <DataTable :rows="rows" :columns="columns" :extra-filters="reasonFilters" :page-size="20" :row-class="r=>r.current_choice ? 'active' : ''">
    <template v-for="k in ['tag','prev']" :key="k" #[`cell-${k}`]="{row}">
      <RouterLink v-if="keyOf(row[k])" :to="'/nodes/'+keyOf(row[k])" class="break"><NodeKey :text="row[k]" /></RouterLink>
      <span v-else class="break">{{ row[k] || '—' }}</span>
    </template>
    <template #cell-reason_key="{row}"><span class="reason"><ReasonIcon :reason="row.reason_key" />{{ reasonText(row.reason_key) }}</span></template>
  </DataTable>
</template>
