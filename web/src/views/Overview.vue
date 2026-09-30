<script setup>
import { computed } from 'vue'
import { useSnapshot } from '../store.js'
import { auth } from '../api.js'
import { PHASES } from '../ux.js'
import { bytes, dateTime } from '../format.js'
const s=useSnapshot(), nodes=computed(()=>s.data.nodes || []), runner=computed(()=>s.data.runner)
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
    <p class="mut">Этап показывает, чем тестер занят сейчас. Один проход может длиться долго: прогресс обновляется после обработки каждой ноды.</p>
    <p class="mut">Доступность этой страницы не подтверждает работу sing-box. Проверки соединения выполняет тестер; их результаты доступны у каждой ноды.</p>
    <p v-if="runner?.request_queued" class="notice">Внеплановая проверка ожидает начала. Повторный запрос не создаст отдельную очередь.</p>
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
    <a href="https://github.com/andreydyadyk/nodes-tester/blob/master/openwrt/README.md" target="_blank" rel="noopener noreferrer">Первый запуск на OpenWrt по SSH</a>
  </section>
  <section class="panel">
    <h2>Активные ноды по группам</h2>
    <p v-if="!runner">Недоступен: нет подключённого Runner. История и рейтинг могут содержать старые значения.</p>
    <p v-else-if="!runner.regions?.length">{{ runner.switching ? 'Переключатель пока не выбрал ноды групп.' : 'Автоматическое переключение отключено.' }}</p>
    <p v-for="r in runner?.regions || []" :key="r.region" class="break"><b>{{ r.region }}</b> · {{ r.active || 'Нода не выбрана' }}</p>
    <p class="mut">Это состояние переключателя, не самостоятельная проверка боевых селекторов sing-box.</p>
  </section>
</template>
