<script setup>
import { computed } from 'vue'
import { useSnapshot } from '../store.js'
import { TEST_NAMES } from '../ux.js'
// Компактное время: ячейки тестов должны помещаться в ширину страницы.
const short = ts => ts ? new Date(ts*1000).toLocaleString('ru-RU',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}) : '—'
import DataTable from '../components/DataTable.vue'
const s=useSnapshot()
const tests=computed(()=>s.data.results?.tests || [])
const rows=computed(()=>s.data.results?.rows || [])
const columns=computed(()=>[
  {key:'provider',title:'Провайдер',l:true},{key:'protocol',title:'Протокол',l:true},{key:'country',title:'Страна',l:true},
  {key:'crc',title:'Нода',l:true,nowrap:true},{key:'ts',title:'Проход',fmt:short,l:true,nowrap:true},
  ...tests.value.map(t=>({key:t,title:TEST_NAMES[t] || t,slot:true,l:true})),
])
</script>
<template>
  <p class="hint">Скорость — Мбит/с, задержка и джиттер — мс, потери — %. Множитель скорости показывает троттлинг. Время обычных тестов указано в колонке «Проход»; «—» — тест не измерялся. Тяжёлая загрузка может относиться к другому проходу. Нажмите на провал, чтобы увидеть подробности.</p>
  <DataTable :rows="rows" :columns="columns" empty="Сохранённых результатов нет. Проверьте журнал и настройку storage.store_results.">
    <template v-for="test in tests" :key="test" #[`cell-${test}`]="{row}">
      <template v-if="row.cells[test]">
        <details v-if="!row.cells[test].ok && row.cells[test].title" class="failure-detail"><summary><b class="bad">{{ row.cells[test].v }}</b></summary><pre>{{ row.cells[test].title }}</pre></details>
        <b v-else :class="row.cells[test].ok ? 'good' : 'bad'">{{ row.cells[test].v }}</b>
        <small v-if="row.cells[test].heavy" class="cell-note">{{ short(row.cells[test].ts) }}<span v-if="row.cells[test].off_pass"> · другой проход</span></small>
      </template><span v-else class="mut" title="Не измерено в этом проходе">—</span>
    </template>
  </DataTable>
</template>
