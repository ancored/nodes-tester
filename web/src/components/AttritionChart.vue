<script setup>
// График динамики выбытия (порт attritionChart из server.py): по дням две колонки —
// появилось (added, зелёный) и выбыло (removed, красный), последние 30 дней.
import { computed } from 'vue'

const props = defineProps({ rows: { type: Array, default: () => [] } })

const chart = computed(() => {
  let rows = props.rows || []
  if (!rows.length) return null
  rows = rows.slice(-30)
  const H = 150, pad = 20
  const gw = Math.max(14, Math.min(30, Math.floor(760 / rows.length)))
  const bw = Math.max(4, Math.floor(gw / 2) - 1)
  const W = pad * 2 + rows.length * gw
  const max = Math.max(1, ...rows.map((r) => Math.max(+r.added || 0, +r.removed || 0)))
  const y = (v) => H - pad - (H - 2 * pad) * (v / max)
  const bars = []
  rows.forEach((r, i) => {
    const x = pad + i * gw
    const a = +r.added || 0, rm = +r.removed || 0, g = +r.garbage || 0
    bars.push({ x, y: y(a), w: bw, h: H - pad - y(a), fill: 'var(--good)', title: `${r.day}: появилось ${a}` })
    bars.push({ x: x + bw + 1, y: y(rm), w: bw, h: H - pad - y(rm), fill: 'var(--bad)', title: `${r.day}: выбыло ${rm}, в мусор ${g}` })
  })
  const step = Math.ceil(rows.length / 8) || 1
  const labels = rows
    .map((r, i) => (i % step ? null : { x: pad + i * gw, text: r.day.slice(5) }))
    .filter(Boolean)
  return { W, H, pad, bars, labels }
})
</script>

<template>
  <div class="wrap" style="padding: 12px 14px">
    <div class="legend">
      <b class="good">■</b> появилось &nbsp; <b class="bad">■</b> выбыло <small>(по дням)</small>
    </div>
    <div v-if="!chart" class="empty">нет данных</div>
    <div v-else style="overflow-x: auto">
      <svg :viewBox="`0 0 ${chart.W} ${chart.H}`" :width="chart.W" :height="chart.H" style="max-width: 100%">
        <line :x1="chart.pad" :y1="chart.H - chart.pad" :x2="chart.W - chart.pad" :y2="chart.H - chart.pad" stroke="var(--line)" />
        <rect
          v-for="(b, i) in chart.bars"
          :key="i"
          :x="b.x" :y="b.y.toFixed(1)" :width="b.w" :height="b.h.toFixed(1)"
          rx="1" :fill="b.fill" opacity="0.85"
        >
          <title>{{ b.title }}</title>
        </rect>
        <text
          v-for="(l, i) in chart.labels"
          :key="'l' + i"
          :x="l.x" :y="chart.H - 6" fill="var(--muted)" font-size="9"
        >{{ l.text }}</text>
      </svg>
    </div>
  </div>
</template>
