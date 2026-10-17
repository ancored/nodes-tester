<script setup>
import { computed } from 'vue'
import { useSnapshot } from '../store.js'
import { nodeFlags, nodeColumns } from '../ux.js'
import { scoreClass } from '../format.js'
import { t } from '../i18n.js'
import DataTable from '../components/DataTable.vue'
const s = useSnapshot()
const all = computed(() => s.data.nodes || [])
// Состояния пересекаются: нода попадает в каждое подходящее.
const states = n => n.wh_only ? [t('client (WH)')] : [+n.active === 1 && t('active'), n.score == null && t('no measurements'), n.gstate === 'backoff' && t('paused'),
  n.gstate === 'garbage' && t('quarantine'), +n.banned === 1 && t('excluded')].filter(Boolean)
const extraFilters = [{ key:'state', title:t('State'), get:states }]
// Рейтинг и резерв — в своих столбцах, в «Состоянии» их не повторяем.
const stateFlags = n => nodeFlags(n, { state: true }).join(' · ') || '—'
const columns = [
  ...nodeColumns(),
  { key:'reserve', title:t('Reserve'), l:true, fmt:v => v || '—' },
  { key:'score', title:t('Rating'),fmt:(v,n) => n.wh_only ? '—' : v == null ? t('No measurements') : Number(v).toFixed(1),cls:scoreClass },
  { key:'flags',title:t('State'),l:true,fmt:(_,n) => stateFlags(n) },
  { key:'title', title:t('Name in the subscription'), l:true, fmt:v => v || '—' },
]
</script>
<template>
  <DataTable :rows="all" :extra-filters="extraFilters" :columns="columns" :page-size="20" :empty="t('No nodes match these conditions. Reset the filters or check the database state on the overview.')" />
</template>
