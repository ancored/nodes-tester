<script setup>
import { computed, ref, watch, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import { useSnapshot } from '../store.js'
import { nodeFlags, TEST_NAMES } from '../ux.js'
import { dateTime, bytes } from '../format.js'
import { useQuery } from '../query.js'
import NodeActions from '../components/NodeActions.vue'
import Sparkline from '../components/Sparkline.vue'
import DataTable from '../components/DataTable.vue'
import { api, auth, can } from '../api.js'
const s = useSnapshot(), route = useRoute(), tab = useQuery('tab','Состояние')
const crc = computed(() => String(route.params.crc).toLowerCase())
const node = computed(() => (s.data.nodes || []).find(n => n.crc.toLowerCase() === crc.value))
const result = computed(() => s.data.results?.rows?.find(r => r.crc.toLowerCase() === crc.value))
const events = computed(() => (s.data.node_events || []).filter(e => e.crc.toLowerCase() === crc.value))
const switches = computed(() => (s.data.history || []).filter(e => e.crc?.toLowerCase() === crc.value))
const traffic = computed(() => (s.data.node_traffic || []).find(n => n.crc === crc.value))
const detail=ref(null), history=ref([]), detailError=ref(''), historyError=ref('')
const historyFrom=ref(''), historyTo=ref('')
const filteredHistory=computed(()=>history.value.filter(item=>{
  const from=historyFrom.value ? new Date(historyFrom.value).getTime()/1000 : -Infinity
  const to=historyTo.value ? new Date(historyTo.value).getTime()/1000 + 60 : Infinity
  return item.ts >= from && item.ts < to
}))
function clearHistoryRange(){historyFrom.value='';historyTo.value=''}
let requestId=0
async function loadDetails() {
  const id=++requestId, epoch=auth.epoch, selected=crc.value
  detail.value=null;history.value=[];detailError.value='';historyError.value='';clearHistoryRange()
  await Promise.all([
    api.get('/nodes/'+selected+'/score-history').then(data=>{if(id===requestId && epoch===auth.epoch)history.value=data.score_history || []}).catch(e=>{if(id===requestId && epoch===auth.epoch)historyError.value=e.message}),
    can('edit_config') ? api.get('/nodes/'+selected+'/details').then(data=>{if(id===requestId && epoch===auth.epoch)detail.value=data}).catch(e=>{if(id===requestId && epoch===auth.epoch)detailError.value=e.message}) : Promise.resolve(),
  ])
}
watch([crc,()=>auth.epoch,()=>auth.verified],loadDetails,{immediate:true})
onUnmounted(()=>{requestId++})
watch(()=>s.data.generated,async()=>{
  const id=requestId, epoch=auth.epoch, selected=crc.value
  try {const data=await api.get('/nodes/'+selected+'/score-history');if(id===requestId && epoch===auth.epoch){history.value=data.score_history || [];historyError.value=''}}
  catch(e){if(id===requestId && epoch===auth.epoch)historyError.value=e.message}
})
const jsonLines=computed(()=>{
  const fragment=detail.value?.fragment
  if(!fragment)return []
  // Порядок полей — как в nodes.json; поле payload выделяется целиком, вместе с вложенными строками.
  const included=new Set(Object.keys(JSON.parse(detail.value.payload || '{}')))
  const keys=Object.keys(fragment)
  return [{text:'{',included:false},...keys.flatMap((key,index)=>{
    const lines=JSON.stringify(fragment[key],null,2).split('\n')
    return lines.map((line,i)=>({text:'  '+(i===0 ? JSON.stringify(key)+': ' : '')+line+(i===lines.length-1 && index<keys.length-1 ? ',' : ''),included:included.has(key)}))
  }),{text:'}',included:false}]
})
const historyColumns=[{key:'ts',title:'Дата / время',fmt:dateTime,l:true},{key:'score',title:'Рейтинг',fmt:v=>v == null ? '—' : Number(v).toFixed(1)}]
const eventNames = { added:'Добавлена', removed:'Исчезла из списка', backoff:'Пауза', garbage:'Карантин', recovered:'Восстановлена', ban:'Исключена', unban:'Исключение снято', restriction_cleared:'Ограничение снято' }
</script>
<template>
  <p><RouterLink :to="{ path:'/nodes', query:{ q:crc } }">← К списку нод</RouterLink></p>
  <p v-if="!node" class="notice">Нода не найдена в текущем снимке. Проверьте базу или обновите данные.</p>
  <template v-else>
    <section class="panel">
      <h2 class="break">{{ node.node }}</h2>
      <div class="chips"><span v-if="node.groups?.length" class="badge">Группы: {{ node.groups.join(', ') }}</span><span v-for="flag in nodeFlags(node)" :key="flag" class="badge">{{ flag }}</span></div>
      <p v-if="node.guntil">Ограничение по времени до {{ dateTime(node.guntil) }}. Для паузы также учитывается номер прохода.</p>
      <p v-if="node.gstate === 'backoff'">{{ node.passes_left ? 'Будет пропущено ещё проходов: '+node.passes_left : 'Повторная проба возможна в ближайшем проходе' }}. Точную дату заранее определить нельзя.</p>
      <p class="mut">Выбор отражает состояние переключателя. Он не подтверждает связь с sing-box или текущий трафик.</p>
    </section>
    <nav class="chips" aria-label="Карточка ноды"><button v-for="v in ['Состояние','Результаты','Трафик','События','Действия']" class="chip" :class="{on:tab === v}" @click="tab = v" :key="v">{{ v }}</button></nav>
    <section v-if="tab === 'Состояние'" class="panel">
      <h2>Изменение рейтинга</h2><Sparkline :vals="s.data.score_spark?.[crc] || []" />
      <p class="mut">График показывает последние 24 значения. Все сохранённые замеры доступны в таблице ниже.</p>
      <p v-if="historyError" class="notice bad">{{ historyError }} <button class="btn" @click="loadDetails">Повторить загрузку</button></p>
      <details class="rating-history"><summary>Замеры рейтинга · {{ history.length }}</summary>
        <div class="toolbar"><label>С даты и времени<input v-model="historyFrom" type="datetime-local" :max="historyTo || undefined" /></label><label>По дату и время<input v-model="historyTo" type="datetime-local" :min="historyFrom || undefined" /></label><button v-if="historyFrom || historyTo" class="btn" @click="clearHistoryRange">Сбросить период</button></div>
        <DataTable :rows="filteredHistory" :columns="historyColumns" :page-size="15" query-key="score_" :show-search="false" :show-sort="false" empty="За выбранный период замеров нет." />
      </details>
      <p>CRC связывает данные ноды между переименованиями. Нулевой рейтинг и отсутствие замеров показаны отдельно.</p>
      <details><summary>Технические сведения</summary>
        <p v-if="!can('edit_config')">Войдите с токеном администратора, чтобы посмотреть JSON ноды.</p>
        <p v-if="detailError" class="notice bad">{{ detailError }} <button class="btn" @click="loadDetails">Повторить загрузку</button></p>
        <template v-if="detail?.fragment">
            <pre class="node-json"><code><span v-for="(line,i) in jsonLines" :key="i" class="json-line" :class="{'crc-payload':line.included}">{{ line.text }}{{ '\n' }}</span></code></pre>
            <p class="mut"><span class="crc-legend">Цветом отмечены поля payload.</span> Служебные поля не участвуют в CRC.</p>
            <p v-if="detail.fragment_source!=='nodes_file'" class="mut">Фрагмент восстановлен из SQLite; исключённые из CRC поля, кроме tag, в базе не сохраняются.</p>
            <p>Проверка CRC: <b :class="detail.calculated_crc===crc ? 'good' : 'bad'">{{ detail.calculated_crc===crc ? '✓ OK' : '✕ Не совпадает' }}</b></p>
            <details><summary>Строка для CRC32 — ровно то, что хешируется</summary><pre class="node-json">{{ detail.payload }}</pre></details>
            <p class="mut break">Причина ограничения: {{ node.gstate || 'нет' }}; последовательных провалов: {{ node.gstreak || 0 }}.</p>
        </template>
        <p v-else-if="detail">JSON этой ноды не сохранён.</p>
      </details>
    </section>
    <section v-if="tab === 'Результаты'" class="panel">
      <p v-if="!result">Сохранённых результатов нет. Это не означает провал проверки.</p>
      <template v-else><p>Последний проход: {{ result.pass_label }}, {{ dateTime(result.ts) }}</p>
        <dl class="facts"><template v-for="(cell,test) in result.cells" :key="test"><dt>{{ TEST_NAMES[test] || test }}</dt><dd>{{ cell.v }}<small v-if="cell.heavy" class="cell-note">{{ dateTime(cell.ts) }}<span v-if="cell.off_pass"> · другой проход</span></small><details v-if="!cell.ok && cell.title" class="failure-detail"><summary>Подробности</summary><pre>{{ cell.title }}</pre></details></dd></template></dl>
      </template>
    </section>
    <section v-if="tab === 'Трафик'" class="panel">
      <p v-if="traffic">Входящий {{ bytes(traffic.down) }}, исходящий {{ bytes(traffic.up) }} за сохранённый период {{ dateTime(s.data.traffic_range?.start) }} — {{ dateTime(s.data.traffic_range?.end) }}.</p>
      <p v-else>Для этой ноды нет сохранённого пользовательского трафика. Это не доказывает, что она не использовалась: проверьте сбор и срок хранения.</p><RouterLink to="/traffic">Все агрегаты трафика и период хранения</RouterLink>
    </section>
    <section v-if="tab === 'События'" class="panel">
      <h2>Ограничения и присутствие</h2><p class="mut">События этой ноды из последних 500 записей общего журнала.</p>
      <p v-for="(e,i) in events" :key="e.ts+':'+i">{{ dateTime(e.ts) }} · {{ eventNames[e.event] || e.event }} · {{ e.reason === 'manual' ? 'вручную администратором' : e.reason || 'автоматически' }}</p><p v-if="!events.length">В доступном окне событий нет.</p>
      <h2>Переключения</h2><p v-for="(e,i) in switches" :key="e.ts+':'+i">{{ dateTime(e.ts) }} · {{ e.region }} · {{ e.reason }}</p><p v-if="!switches.length">В последних 200 переключениях записей нет.</p>
    </section>
    <section v-if="tab === 'Действия'" class="panel"><NodeActions :node="node" /></section>
  </template>
</template>
