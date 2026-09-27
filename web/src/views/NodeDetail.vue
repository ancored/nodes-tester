<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useSnapshot } from '../store.js'
import { nodeFlags, TEST_NAMES } from '../ux.js'
import { dateTime, bytes } from '../format.js'
import { useQuery } from '../query.js'
import NodeActions from '../components/NodeActions.vue'
import Sparkline from '../components/Sparkline.vue'
const s = useSnapshot(), route = useRoute(), tab = useQuery('tab','Состояние')
const crc = computed(() => String(route.params.crc).toLowerCase())
const node = computed(() => (s.data.nodes || []).find(n => n.crc.toLowerCase() === crc.value))
const result = computed(() => s.data.results?.rows?.find(r => r.crc.toLowerCase() === crc.value))
const events = computed(() => (s.data.node_events || []).filter(e => e.crc.toLowerCase() === crc.value))
const switches = computed(() => (s.data.history || []).filter(e => e.crc?.toLowerCase() === crc.value))
const traffic = computed(() => (s.data.node_traffic || []).find(n => n.crc === crc.value))
const eventNames = { added:'Добавлена', removed:'Исчезла из списка', backoff:'Пауза', garbage:'Карантин', recovered:'Восстановлена', ban:'Исключена', unban:'Исключение снято', restriction_cleared:'Ограничение снято' }
</script>
<template>
  <p><RouterLink :to="{ path:'/nodes', query:{ q:crc } }">← К списку нод</RouterLink></p>
  <p v-if="!node" class="notice">Нода не найдена в текущем снимке. Проверьте базу или обновите данные.</p>
  <template v-else>
    <section class="panel">
      <h2 class="break">{{ node.node }}</h2>
      <p>{{ node.provider }} · {{ node.protocol }} · {{ node.country }} · регион {{ node.region || 'не определён' }}</p>
      <code>{{ node.crc }}</code><div class="chips"><span v-for="flag in nodeFlags(node)" :key="flag" class="badge">{{ flag }}</span></div>
      <p v-if="node.guntil">Ограничение по времени до {{ dateTime(node.guntil) }}. Для паузы также учитывается номер прохода.</p>
      <p v-if="node.gstate === 'backoff'">{{ node.passes_left ? 'Будет пропущено ещё проходов: '+node.passes_left : 'Повторная проба возможна в ближайшем проходе' }}. Точную дату заранее определить нельзя.</p>
      <p class="mut">Выбор отражает состояние переключателя. Он не подтверждает связь с sing-box или текущий трафик.</p>
    </section>
    <nav class="chips" aria-label="Карточка ноды"><button v-for="v in ['Состояние','Результаты','Трафик','События','Действия']" class="chip" :class="{on:tab === v}" @click="tab = v" :key="v">{{ v }}</button></nav>
    <section v-if="tab === 'Состояние'" class="panel">
      <h2>Изменение рейтинга</h2><Sparkline :vals="s.data.score_spark?.[crc] || []" />
      <p>CRC связывает данные ноды между переименованиями. Нулевой рейтинг и отсутствие замеров показаны отдельно.</p>
      <details><summary>Технические сведения</summary><p class="break">Сервер: {{ node.server || 'неизвестен' }}; причина ограничения: {{ node.gstate || 'нет' }}; последовательных провалов: {{ node.gstreak || 0 }}.</p></details>
    </section>
    <section v-if="tab === 'Результаты'" class="panel">
      <p v-if="!result">Сохранённых результатов нет. Это не означает провал проверки.</p>
      <template v-else><p>Последний проход: {{ result.pass_label }}, {{ dateTime(result.ts) }}</p>
        <dl class="facts"><template v-for="(cell,test) in result.cells" :key="test"><dt>{{ TEST_NAMES[test] || test }}</dt><dd>{{ cell.v }} · {{ dateTime(cell.ts) }} <span v-if="cell.off_pass">(другой проход)</span><p v-if="cell.title">{{ cell.title }}</p></dd></template></dl>
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
