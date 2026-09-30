<script setup>
// Значок причины переключения (история переключений).
const props = defineProps({ reason: { type: String, default: '' } })
const KIND = { emergency: 'bad', 'emergency-stuck': 'bad', quality: 'accent', rotation: 'good',
  initial: 'mut', init: 'mut', manual: 'accent', failsafe: 'heavy' }
</script>
<template>
  <svg class="reason-icon" :class="KIND[props.reason] || 'mut'" viewBox="0 0 16 16" width="16" height="16" aria-hidden="true">
    <template v-if="props.reason === 'emergency'">
      <path d="M8 1.5 15 14H1z" fill="currentColor" /><path d="M8 5.5v4.2M8 11.4v1.1" stroke="var(--panel)" stroke-width="1.7" stroke-linecap="round" />
    </template>
    <template v-else-if="props.reason === 'emergency-stuck'">
      <path d="M5 1h6l4 4v6l-4 4H5l-4-4V5z" fill="currentColor" /><path d="M5.5 5.5l5 5M10.5 5.5l-5 5" stroke="var(--panel)" stroke-width="1.7" stroke-linecap="round" />
    </template>
    <template v-else-if="props.reason === 'rotation'">
      <circle cx="8" cy="8" r="8" fill="currentColor" />
      <path d="M4.3 7.2A3.8 3.8 0 0 1 11 5.6M11.7 8.8A3.8 3.8 0 0 1 5 10.4" fill="none" stroke="var(--panel)" stroke-width="1.5" stroke-linecap="round" />
      <path d="M11.6 3.4v2.6H9M4.4 12.6V10H7" fill="none" stroke="var(--panel)" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" />
    </template>
    <template v-else-if="props.reason === 'quality'">
      <circle cx="8" cy="8" r="8" fill="currentColor" /><path d="M3.5 11 6.5 8l2 2 4-4.5M9.5 5.5h3v3" fill="none" stroke="var(--panel)" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" />
    </template>
    <template v-else-if="props.reason === 'initial' || props.reason === 'init'">
      <path d="M3.5 15V1.5M3.5 2h9l-2 3 2 3h-9" fill="currentColor" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round" />
    </template>
    <template v-else-if="props.reason === 'manual'">
      <circle cx="8" cy="4.5" r="3" fill="currentColor" /><path d="M2 15a6 6 0 0 1 12 0z" fill="currentColor" />
    </template>
    <template v-else-if="props.reason === 'failsafe'">
      <path d="M8 1 14 3.5V8c0 3.5-2.6 6-6 7-3.4-1-6-3.5-6-7V3.5z" fill="currentColor" />
    </template>
    <circle v-else cx="8" cy="8" r="3" fill="currentColor" />
  </svg>
</template>
<style scoped>
.reason-icon { flex: 0 0 auto; vertical-align: -3px; margin-right: 6px; }
.accent { color: var(--accent); }
.heavy { color: var(--heavy); }
</style>
