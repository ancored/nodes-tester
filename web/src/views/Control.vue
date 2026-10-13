<script setup>
import { computed } from 'vue'
import { useSnapshot } from '../store.js'
import { nodeFlags, nodeColumns } from '../ux.js'
import { scoreClass } from '../format.js'
import DataTable from '../components/DataTable.vue'
const s = useSnapshot()
const all = computed(() => s.data.nodes || [])
// Состояния пересекаются: нода попадает в каждое подходящее.
const states = n => n.wh_only ? ['клиентские (WH)'] : [+n.active === 1 && 'активные', n.score == null && 'без замеров', n.gstate === 'backoff' && 'пауза',
  n.gstate === 'garbage' && 'карантин', +n.banned === 1 && 'исключены'].filter(Boolean)
const extraFilters = [{ key:'state', title:'Состояние', get:states }]
// Рейтинг и резерв — в своих столбцах, в «Состоянии» их не повторяем.
const stateFlags = n => nodeFlags(n).filter(f => !/^(Рейтинг|Нет замеров|Резерв)/.test(f)).join(' · ') || '—'
const columns = [
  ...nodeColumns(),
  { key:'title', title:'Имя в подписке', l:true, fmt:v => v || '—' },
  { key:'reserve', title:'Резерв', l:true, fmt:v => v || '—' },
  { key:'score', title:'Рейтинг',fmt:(v,n) => n.wh_only ? '—' : v == null ? 'Нет замеров' : Number(v).toFixed(1),cls:scoreClass },
  { key:'flags',title:'Состояние',l:true,fmt:(_,n) => stateFlags(n) },
]
</script>
<template>
  <p class="hint">Здесь ноды текущих подписок роутера; ушедшие из подписок — в «Архиве». Нажмите на ключ ноды, чтобы открыть карточку с результатами, историей и действиями. Ноды клиентской ветви (WH), которых нет у роутера, тестер не ведёт: у них только проверка белых списков и нет карточки.</p>
  <DataTable :rows="all" :extra-filters="extraFilters" :columns="columns" :page-size="20" empty="Нод с такими условиями нет. Сбросьте фильтры или проверьте состояние базы на обзоре." />
</template>
