<script setup>
// Сворачиваемая легенда статусов нод (словарь — status.js).
import { STATUSES } from '../status.js'
import { t } from '../i18n.js'

defineProps({ open: { type: Boolean, default: false } })
</script>

<template>
  <details class="legend-box" :open="open">
    <summary>{{ t('Node statuses: what each one means') }}</summary>
    <div class="wrap">
      <table>
        <thead>
          <tr>
            <th class="l">{{ t('status') }}</th>
            <th class="l">{{ t('meaning') }}</th>
            <th class="l">{{ t('set by') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="s in STATUSES" :key="s.key">
            <td class="l"><b :class="s.cls">{{ s.label }}</b></td>
            <td class="l wrap-text">{{ s.what }}</td>
            <td class="l mut">{{ s.who }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <p class="mut note">
      {{ t('A node can have several flags at once. All of them are shown in the list and the node card; this table explains the terms.') }}
    </p>
  </details>
</template>

<style scoped>
.legend-box { margin-bottom: 18px; }
.legend-box summary { cursor: pointer; color: var(--muted); font-size: 12px; margin-bottom: 8px; }
.wrap-text { white-space: normal; min-width: 280px; }
.note { font-size: 12px; margin: 8px 2px 0; }
</style>
