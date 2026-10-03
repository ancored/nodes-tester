<script setup>
import { computed } from 'vue'
import { useSnapshot } from '../store.js'
import { testTitle } from '../ux.js'
// Компактное время: ячейки тестов должны помещаться в ширину страницы.
const short = ts => ts ? new Date(ts*1000).toLocaleString('ru-RU',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}) : '—'
const day = ts => new Date(ts*1000).toLocaleDateString('ru-RU',{day:'2-digit',month:'2-digit'})
const time = ts => new Date(ts*1000).toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit'})
import DataTable from '../components/DataTable.vue'
const s=useSnapshot()
const tests=computed(()=>s.data.results?.tests || [])
const rows=computed(()=>s.data.results?.rows || [])
const columns=computed(()=>[
  {key:'provider',title:'Провайдер',l:true},{key:'protocol',title:'Протокол',l:true},{key:'country',title:'Страна',head:'',l:true},
  {key:'crc',title:'Нода',l:true,nowrap:true},{key:'ts',title:'Проход',slot:true,l:true,nowrap:true},
  {key:'ip_type',title:'Тип IP',l:true,nowrap:true,slot:true,note:'Выходной IP по базе ip-api.com: ДЦ — дата-центр, домашний или мобильный; «прокси» — IP известен как VPN/прокси; «по базе XX» — база относит IP к другой стране. Наведите курсор: IP и сеть (ASN).'},
  ...tests.value.map(t=>({key:t,title:testTitle(t),slot:true,l:true})),
])
</script>
<template>
  <p class="hint">Множитель скорости показывает троттлинг. Время обычных тестов указано в колонке «Проход»; «—» — тест не измерялся. Тяжёлая загрузка и Gemini могут относиться к другому проходу — значок ↻. Нажмите на провал, чтобы увидеть подробности.</p>
  <DataTable :rows="rows" :columns="columns" empty="Сохранённых результатов нет. Проверьте журнал и настройку storage.store_results.">
    <template #cell-ts="{row}"><template v-if="row.ts">{{ day(row.ts) }}<br>{{ time(row.ts) }}</template><template v-else>—</template></template>
    <template #cell-ip_type="{row}"><span :title="row.ip_title || null">{{ row.ip_type || '—' }}</span></template>
    <template v-for="test in tests" :key="test" #[`cell-${test}`]="{row}">
      <template v-if="row.cells[test]">
        <details v-if="!row.cells[test].ok && row.cells[test].title" class="failure-detail"><summary><span class="bad">{{ row.cells[test].v }}</span></summary><pre>{{ row.cells[test].title }}</pre></details>
        <span v-else :class="row.cells[test].ok ? 'good' : 'bad'" :title="row.cells[test].title || null">{{ row.cells[test].v }}</span>
        <small v-if="row.cells[test].heavy" class="cell-note">{{ short(row.cells[test].ts) }}<span v-if="row.cells[test].off_pass" title="Другой проход"> ↻</span></small>
      </template><span v-else class="mut" title="Не измерено в этом проходе">—</span>
    </template>
  </DataTable>
</template>
