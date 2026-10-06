<script setup>
import { computed, ref, watch, onMounted, onUnmounted } from 'vue'
import { useSnapshot } from '../store.js'
import { api, auth } from '../api.js'
import { PHASES } from '../ux.js'
import { bytes, dateTime } from '../format.js'
const s=useSnapshot(), nodes=computed(()=>s.data.nodes || []), runner=computed(()=>s.data.runner)
// sing-box: остановка/запуск и killswitch (только встроенная админка на OpenWrt).
const box=ref(null), boxBusy=ref(false), boxError=ref('')
const boxAvailable=computed(()=>!!auth.capabilities.singbox_control)
async function loadBox(){ if(!boxAvailable.value) return; try{ box.value=await api.get('/singbox/control'); boxError.value='' }catch(e){ boxError.value=e.message } }
let boxTimer
onMounted(()=>{ loadBox(); boxTimer=setInterval(loadBox,10000) })
onUnmounted(()=>clearInterval(boxTimer))
watch(boxAvailable,v=>{ if(v) loadBox() })
async function boxAction(kind){
  const ks=box.value?.killswitch?.active
  const ask = kind==='stop'
    ? `Остановить sing-box? ${ks ? 'Killswitch загружен: устройства, которые он защищает, останутся без интернета' : 'Killswitch не загружен: устройства LAN пойдут в интернет напрямую через провайдера, без прокси'}, пока sing-box не запустят снова. Применение конфига конвейером будет отложено. Админка и SSH останутся доступны.`
    : 'Запустить sing-box?'
  if(!window.confirm(ask)) return
  boxBusy.value=true
  try{ box.value=await api.post('/singbox/'+kind,{}); boxError.value='' }catch(e){ boxError.value=e.message }finally{ boxBusy.value=false }
}
async function setKillswitch(on){
  boxBusy.value=true
  try{ box.value=await api.put('/singbox/killswitch',{enabled:on}); boxError.value='' }catch(e){ boxError.value=e.message; await loadBox() }finally{ boxBusy.value=false }
}
const boxState=computed(()=>!box.value ? '—' : box.value.running ? 'работает' : box.value.stopped_by_admin ? 'остановлен из админки' : 'не работает')
const kpis=computed(()=>[
  ['В списке тестера',nodes.value.filter(n=>+n.present === 1).length],
  ['Есть замеры рейтинга',nodes.value.filter(n=>n.score != null && +n.present === 1).length],
  ['Кандидаты групп',nodes.value.filter(n=>n.can_activate).length],
  ['С ограничениями',nodes.value.filter(n=>n.banned || n.gstate).length],
])
</script>
<template>
  <section class="panel">
    <h2>Состояние системы</h2>
    <dl class="facts">
      <dt>Этап тестера</dt><dd>{{ runner ? (runner.running ? PHASES[runner.progress?.phase] || 'Процесс запущен' : 'Остановлен') : 'Живое состояние недоступно в отдельной админке' }}<template v-if="runner?.running && runner.progress?.total"> · обработано {{ runner.progress.processed }} из {{ runner.progress.total }} нод (включая пропуски)</template></dd>
      <template v-if="runner?.running && runner.progress?.node"><dt>Текущая нода</dt><dd>{{ runner.progress.node }}</dd></template>
      <template v-if="runner"><dt>Проход</dt><dd>{{ runner.day || '—' }} / {{ runner.pass || '—' }} · <RouterLink to="/runs">Подробности и журнал</RouterLink></dd></template>
      <dt>Последний замер в базе</dt><dd>{{ dateTime(s.data.source?.last_measurement) }}</dd>
      <dt>Настройки и управление</dt><dd>{{ auth.verified ? 'Токен проверен' : 'Доступен просмотр; войдите для действий' }}</dd>
    </dl>
    <p v-if="s.error || s.data.source?.state !== 'ok'" class="notice bad">{{ s.error || s.data.source?.message || 'Получаем данные…' }}</p>
    <div class="help-text">
      <p>Этап показывает, чем тестер занят сейчас. Один проход может длиться долго: прогресс обновляется после обработки каждой ноды.</p>
      <p>Доступность этой страницы не подтверждает работу sing-box. Проверки соединения выполняет тестер; их результаты доступны у каждой ноды.</p>
    </div>
    <p v-if="runner?.request_queued" class="notice">Внеплановая проверка ожидает начала. Повторный запрос не создаст отдельную очередь.</p>
  </section>
  <section v-if="boxAvailable" class="panel">
    <h2>sing-box</h2>
    <dl class="facts">
      <dt>Состояние</dt><dd :class="box && !box.running ? 'bad' : ''">{{ boxState }}</dd>
      <dt>Killswitch</dt><dd>
        <template v-if="!box?.killswitch?.installed">служба не установлена — без sing-box трафик LAN идёт напрямую</template>
        <template v-else-if="box.killswitch.active">правила загружены · отбито пакетов: {{ box.killswitch.blocked ?? '—' }}<template v-if="!box.killswitch.enabled"> · <span class="bad">автозапуск выключен</span></template></template>
        <template v-else><b class="bad">правила не загружены</b> — без sing-box трафик LAN идёт напрямую</template>
      </dd>
    </dl>
    <div v-if="auth.verified" class="actions">
      <button v-if="box?.running" class="btn" :disabled="boxBusy" @click="boxAction('stop')">Остановить sing-box</button>
      <button v-else class="btn" :disabled="boxBusy" @click="boxAction('start')">Запустить sing-box</button>
      <label v-if="box?.killswitch?.installed" class="check"><input type="checkbox" :checked="box.killswitch.active" :disabled="boxBusy" @change="setKillswitch($event.target.checked)" /> Killswitch (служба роутера): не выпускать LAN мимо sing-box</label>
    </div>
    <p v-else class="mut">Войдите с токеном, чтобы управлять sing-box.</p>
    <div class="help-text">
      <p>Остановка sing-box помогает вернуть сеть при сбое. Пока он остановлен из админки, конвейер не применяет новый конфиг. Перезагрузка роутера снова запускает sing-box.</p>
      <p>Killswitch блокирует выход LAN в интернет мимо sing-box. Защищаемые устройства заданы в правилах службы. Переключатель управляет службой и её автозапуском; загруженные правила действуют постоянно. Доступ к роутеру сохраняется.</p>
    </div>
    <p v-if="boxError || box?.error" class="notice bad">{{ boxError || box.error }}</p>
  </section>
  <h2>Ноды: количество и состояние</h2>
  <div class="kpis"><div v-for="[label,value] in kpis" :key="label" class="kpi"><div class="label">{{ label }}</div><div class="value">{{ s.data.source?.state === 'ok' ? value : '—' }}</div></div></div>
  <section class="panel">
    <h2>Трафик за сохранённый период</h2>
    <p v-if="s.data.source?.state !== 'ok'" class="mut">Данные трафика недоступны.</p>
    <p v-else-if="s.data.traffic_range?.start" class="mut">Пользовательские замеры: {{ dateTime(s.data.traffic_range.start) }} — {{ dateTime(s.data.traffic_range.end) }}</p>
    <p v-else class="mut">Пользовательских замеров за сохранённый период нет.</p>
    <h3>Пользовательский трафик</h3>
    <div class="kpis"><div v-for="[key,label] in [['down','Входящий'],['up','Исходящий'],['total','Всего']]" :key="key" class="kpi"><div class="label">{{ label }}</div><div class="value">{{ s.data.source?.state === 'ok' ? bytes(s.data.traffic_user_totals?.[key] || 0) : '—' }}</div></div></div>
    <p>Скачано тестами за 24 часа: <b>{{ s.data.source?.state === 'ok' ? bytes(s.data.traffic_test_download_24h?.down || 0) : '—' }}</b> <span class="mut">({{ s.data.traffic_test_download_24h?.tests || 0 }} замеров; тело ответов без сетевых накладных расходов)</span></p>
    <h3>Трафик тестера</h3>
    <div class="kpis"><div v-for="[key,label] in [['down','Входящий'],['up','Исходящий'],['total','Всего']]" :key="key" class="kpi"><div class="label">{{ label }}</div><div class="value">{{ s.data.source?.state === 'ok' ? bytes(s.data.traffic_tester_totals?.[key] || 0) : '—' }}</div></div></div>
    <RouterLink to="/traffic">Подробности пользовательского трафика</RouterLink>
  </section>
  <section class="panel">
    <h2>Что настраивать и где смотреть</h2>
    <ol class="guide">
      <li><RouterLink to="/config">Настройки</RouterLink>: адрес API sing-box, секрет, SOCKS-подключение, селектор и план тестов. Сохранённый конфиг требует перезапуска.</li>
      <li><RouterLink to="/subscriptions">Подписки и сборка</RouterLink>: источники и группы нод. После сохранения откройте <RouterLink to="/pipeline">Конвейер</RouterLink>, проверьте сборку и примените её.</li>
      <li><RouterLink to="/singbox">Файлы sing-box</RouterLink> и <RouterLink to="/presets">Правила</RouterLink>: база и пресеты. Сохранение файла не меняет работающий sing-box.</li>
      <li><RouterLink to="/runs">Проверки</RouterLink>: этап прохода, очередь и журнал. Кнопка проверки не обновляет подписки.</li>
      <li><RouterLink to="/nodes">Ноды</RouterLink>: найдите ноду и откройте карточку с результатами, ограничениями и действиями.</li>
      <li><RouterLink to="/history">Переключения</RouterLink> и <RouterLink to="/traffic">трафик</RouterLink>: как выбирались ноды групп и что использовалось.</li>
    </ol>
    <a href="https://github.com/ancored/nodes-tester/blob/master/openwrt/README.md" target="_blank" rel="noopener noreferrer">Первый запуск на OpenWrt по SSH</a>
  </section>
  <section class="panel">
    <h2>Активные ноды по группам</h2>
    <p v-if="!runner">Недоступен: нет подключённого Runner. История и рейтинг могут содержать старые значения.</p>
    <p v-else-if="!runner.regions?.length">{{ runner.switching ? 'Переключатель пока не выбрал ноды групп.' : 'Автоматическое переключение отключено.' }}</p>
    <p v-for="r in runner?.regions || []" :key="r.region" class="break"><b>{{ r.region }}</b> · {{ r.active || 'Нода не выбрана' }}</p>
    <p class="help-text">Показано состояние переключателя. Боевые селекторы sing-box здесь отдельно не проверяются.</p>
  </section>
</template>
