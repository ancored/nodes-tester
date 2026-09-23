<script setup>
// Спарклайн истории рейтинга (порт spark() из server.py). vals — массив [0..100].
import { computed } from 'vue'

const props = defineProps({ vals: { type: Array, default: () => [] } })

const d = computed(() => {
  const vals = props.vals
  if (!vals || vals.length < 2) return null
  const w = 76, h = 18, n = vals.length
  const pts = vals
    .map((v, i) => `${(i * (w / (n - 1))).toFixed(1)},${(h - h * Math.max(0, Math.min(1, v / 100))).toFixed(1)}`)
    .join(' ')
  const last = vals[n - 1]
  const col = last >= 70 ? '--good' : last <= 20 ? '--bad' : '--accent'
  return { w, h, pts, col }
})
</script>

<template>
  <svg v-if="d" :width="d.w" :height="d.h" :viewBox="`0 0 ${d.w} ${d.h}`">
    <polyline :points="d.pts" fill="none" :stroke="`var(${d.col})`" stroke-width="1.5" />
  </svg>
  <span v-else class="mut">–</span>
</template>
