<script setup>
import { computed } from 'vue'
import { useSnapshot } from '../store.js'
import { bytes } from '../format.js'
import BarList from '../components/BarList.vue'
import AttritionChart from '../components/AttritionChart.vue'

const s = useSnapshot()

const kpis = computed(() => {
  const d = s.data
  const rating = d.rating || []
  const live = rating.filter((r) => (+r.score || 0) > 0)
  const active = rating.filter((r) => +r.active === 1)
  const garbage = d.garbage || []
  const quarantine = garbage.filter((r) => r.state === 'garbage').length
  const total = +d.traffic_total || 0
  return [
    { label: 'Нод в подписке', value: rating.length },
    { label: 'Рейтинг > 0', value: live.length },
    { label: 'Активных', value: active.length },
    { label: 'Пауза / карантин', value: `${garbage.length - quarantine} / ${quarantine}` },
    { label: 'Трафик (всего)', value: bytes(total) },
  ]
})

const topProviders = computed(() =>
  (s.data.traffic_providers || []).map((r) => ({ name: r.provider, value: r.total }))
)
const topCountries = computed(() =>
  (s.data.traffic_countries || []).map((r) => ({ name: r.cc, value: r.total }))
)
const topProtocols = computed(() =>
  (s.data.traffic_protocols || []).map((r) => ({ name: r.protocol, value: r.total }))
)
const attrition = computed(() => s.data.attrition || [])
</script>

<template>
  <div v-if="s.loading" class="empty">Загрузка…</div>
  <div v-else-if="s.error && !s.ready" class="empty bad">Ошибка: {{ s.error }}</div>
  <template v-else>
    <div class="kpis">
      <div v-for="k in kpis" :key="k.label" class="kpi">
        <div class="label">{{ k.label }}</div>
        <div class="value">{{ k.value }}</div>
      </div>
    </div>

    <div class="grid3">
      <section>
        <h2>Топ провайдеров по трафику</h2>
        <BarList :items="topProviders" :fmt="bytes" :limit="8" />
      </section>
      <section>
        <h2>Топ стран по трафику</h2>
        <BarList :items="topCountries" :fmt="bytes" :limit="8" />
      </section>
      <section>
        <h2>Топ протоколов по трафику</h2>
        <BarList :items="topProtocols" :fmt="bytes" :limit="8" />
      </section>
    </div>

    <section>
      <h2>Динамика выбытия нод</h2>
      <AttritionChart :rows="attrition" />
    </section>
  </template>
</template>
