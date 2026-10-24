<script setup>
// Цепочка ноды: флаг страны входа → флаг страны выхода; выход через Cloudflare WARP — оранжевое облако.
// Прямая нода (IP входа = IP выхода): флаг страны выхода, если столбец заменяет страну (fallback), иначе прочерк.
import { flag } from '../format.js'
defineProps({ row: { type: Object, required: true }, fallback: { type: Boolean, default: false } })
</script>
<template>
  <span v-if="row.chain" class="chain">{{ flag(row.entry_cc || '?') }}<span class="arrow">→</span>{{ flag(row.exit_cc) }}<svg v-if="row.warp" class="warp" viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path d="M4.6 13.2h7.6a3.1 3.1 0 0 0 .3-6.2A4.4 4.4 0 0 0 4 7.3a3 3 0 0 0 .6 5.9z" fill="currentColor" /></svg></span>
  <template v-else-if="fallback && (row.exit_cc || row.country)">{{ flag(row.exit_cc || row.country) }}</template>
  <span v-else class="mut">—</span>
</template>
<style scoped>
.chain { display: inline-flex; align-items: center; white-space: nowrap; }
.arrow { margin: 0 2px; color: var(--muted); }
.warp { margin-left: 3px; color: #f38020; }
</style>
