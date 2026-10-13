<script setup>
import { computed } from 'vue'
import { useSnapshot } from '../store.js'
import { testTitle, nodeColumns } from '../ux.js'
import { flag } from '../format.js'
// Компактное время: ячейки тестов должны помещаться в ширину страницы.
const short = ts => ts ? new Date(ts*1000).toLocaleString('ru-RU',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}) : '—'
const day = ts => new Date(ts*1000).toLocaleDateString('ru-RU',{day:'2-digit',month:'2-digit'})
const time = ts => new Date(ts*1000).toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit'})
import DataTable from '../components/DataTable.vue'
import IpTypeIcon from '../components/IpTypeIcon.vue'
const s=useSnapshot()
const tests=computed(()=>s.data.results?.tests || [])
const rows=computed(()=>s.data.results?.rows || [])
// AI-тесты — узкие колонки: галка, крест или «?» (сеть, вердикт прежний); у Google AI — флаг страны.
const AI_NOTE='Google AI — страна по мнению Google (флаг) и доступность Gemini; OpenAI и Claude AI — доступность сервиса. ✓ — доступен, ✗ — нет, ? — сбой сети, действует прошлый результат. Проверяются только кандидаты групп с обязательными тестами; наведите курсор, чтобы увидеть время и причину.'
// Узкие колонки-отметки: AI, соединение (страна — во флаге строки), белый список.
const AI={gemini:1,openai:1,anthropic:1,connectivity:1,whitelist:1}
// Короткие заголовки, чтобы таблица помещалась; полное название — в пояснениях и сортировке.
const HEADS={connectivity:'Связь',whitelist:'Белый список',reachability:'Сайты',download:'Скорость, Мбит/с'}
const NOTES={gemini:AI_NOTE,whitelist:'Пропустит ли ноду режим белых списков мобильного интернета: IP сервера в IP-списке и SNI в списке доменов (без TLS — достаточно IP). Проверяется локально раз в сутки; наведите курсор, чтобы увидеть адреса и SNI.'}
const aiMark = c => c.unk ? '?' : c.ok ? '✓' : '✗'
const aiTitle = c => [c.v, c.title, short(c.ts) + (c.off_pass ? ' · другой проход' : '')].filter(Boolean).join('\n')
const columns=computed(()=>[
  ...nodeColumns(),{key:'ts',title:'Проход',slot:true,l:true,nowrap:true},
  {key:'ip_type',title:'Тип IP',head:'IP',slot:true,narrow:true,note:'Выходной IP по базе ip-api.com: здание — дата-центр, телефон — мобильная сеть, дом — домашний; маска — IP известен как VPN/прокси; флаг ниже — база относит IP к другой стране, чем показал тест соединения. Красное хуже для AI-сервисов. Наведите курсор: IP и сеть (ASN).'},
  ...tests.value.map(t=>t in AI ? {key:t,title:testTitle(t),head:HEADS[t],slot:true,narrow:true,note:NOTES[t] || null} : {key:t,title:testTitle(t),head:HEADS[t],slot:true,l:true}),
])
</script>
<template>
  <p class="hint">Множитель скорости показывает троттлинг. Время обычных тестов указано в колонке «Проход»; «—» — тест не измерялся. Тяжёлая загрузка может относиться к другому проходу — значок ↻. Нажмите на провал, чтобы увидеть подробности.</p>
  <DataTable :rows="rows" :columns="columns" empty="Сохранённых результатов нет. Проверьте журнал тестера.">
    <template #cell-ts="{row}"><template v-if="row.ts">{{ day(row.ts) }}<br>{{ time(row.ts) }}</template><template v-else>—</template></template>
    <template #cell-ip_type="{row}"><span v-if="row.ip_kind" class="ip-type" :title="row.ip_title || null"><span><IpTypeIcon :kind="row.ip_kind" /><IpTypeIcon v-if="row.ip_proxy" kind="proxy" /></span><span v-if="row.ip_db_cc" class="bad">{{ flag(row.ip_db_cc) }}</span></span><span v-else class="mut">—</span></template>
    <template v-for="test in tests" :key="test" #[`cell-${test}`]="{row}">
      <template v-if="row.cells[test]?.ai">
        <span class="ai-cell" :title="aiTitle(row.cells[test])"><b :class="row.cells[test].unk ? 'mut' : row.cells[test].ok ? 'good' : 'bad'">{{ aiMark(row.cells[test]) }}</b><span v-if="row.cells[test].cc">{{ flag(row.cells[test].cc) }}</span></span>
      </template>
      <template v-else-if="row.cells[test]">
        <details v-if="!row.cells[test].ok && row.cells[test].title" class="failure-detail"><summary><span class="bad">{{ row.cells[test].v }}</span></summary><pre>{{ row.cells[test].title }}</pre></details>
        <span v-else :class="row.cells[test].ok ? 'good' : 'bad'" :title="row.cells[test].title || null">{{ row.cells[test].v }}</span>
        <small v-if="row.cells[test].heavy" class="cell-note">{{ short(row.cells[test].ts) }}<span v-if="row.cells[test].off_pass" title="Другой проход"> ↻</span></small>
      </template><span v-else class="mut" title="Не измерено">—</span>
    </template>
  </DataTable>
</template>
<style scoped>
.ip-type, .ai-cell { display: inline-flex; flex-direction: column; align-items: center; gap: 2px; line-height: 1.2; }
.ip-type > span:first-child { display: inline-flex; gap: 2px; }
</style>
