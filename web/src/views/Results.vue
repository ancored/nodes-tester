<script setup>
import { computed } from 'vue'
import { useSnapshot } from '../store.js'
import { TEST_NAMES } from '../ux.js'
import { dateTime } from '../format.js'
import DataTable from '../components/DataTable.vue'
const s=useSnapshot()
const tests=computed(()=>s.data.results?.tests || [])
const rows=computed(()=>s.data.results?.rows || [])
const columns=computed(()=>[
  {key:'provider',title:'Провайдер',l:true},{key:'protocol',title:'Протокол',l:true},{key:'country',title:'Страна',l:true},
  {key:'crc',title:'Нода',l:true},{key:'ts',title:'Последний проход',fmt:dateTime,l:true},
  ...tests.value.map(t=>({key:t,title:TEST_NAMES[t] || t,slot:true,l:true})),
])
</script>
<template>
  <p class="hint">Скорость — Мбит/с, задержка и джиттер — мс, потери — %. Множитель скорости показывает троттлинг. Для каждого результата указано время замера, а не обновления страницы. Тяжёлая загрузка может относиться к другому проходу.</p>
  <DataTable :rows="rows" :columns="columns" empty="Сохранённых результатов нет. Проверьте журнал и настройку storage.store_results.">
    <template v-for="test in tests" :key="test" #[`cell-${test}`]="{row}">
      <template v-if="row.cells[test]"><b :class="row.cells[test].ok ? 'good' : 'bad'">{{ row.cells[test].v }}</b>
        <small class="cell-note">{{ dateTime(row.cells[test].ts) }}<span v-if="row.cells[test].off_pass"> · другой проход</span></small>
        <details v-if="row.cells[test].title"><summary>Подробности</summary>{{ row.cells[test].title }}</details>
      </template><span v-else class="mut">Не измерено в этом проходе</span>
    </template>
  </DataTable>
</template>
