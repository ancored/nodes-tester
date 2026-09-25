<script setup>
// Сворачиваемая легенда статусов нод (словарь — status.js).
import { STATUSES } from '../status.js'

defineProps({ open: { type: Boolean, default: false } })
</script>

<template>
  <details class="legend-box" :open="open">
    <summary>Статусы нод — что значит каждый</summary>
    <div class="wrap">
      <table>
        <thead>
          <tr>
            <th class="l">статус</th>
            <th class="l">что значит</th>
            <th class="l">кто ставит</th>
            <th class="l">раньше называлось</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="s in STATUSES" :key="s.key">
            <td class="l"><b :class="s.cls">{{ s.label }}</b></td>
            <td class="l wrap-text">{{ s.what }}</td>
            <td class="l mut">{{ s.who }}</td>
            <td class="l mut">{{ s.was }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <p class="mut note">
      Порядок = приоритет: если у ноды несколько признаков, показывается верхний.
      «Удалённые» живут только на «Кладбище»; остальные разделы показывают ноды из подписки.
    </p>
  </details>
</template>

<style scoped>
.legend-box { margin-bottom: 18px; }
.legend-box summary { cursor: pointer; color: var(--muted); font-size: 12px; margin-bottom: 8px; }
.wrap-text { white-space: normal; min-width: 280px; }
.note { font-size: 12px; margin: 8px 2px 0; }
</style>
