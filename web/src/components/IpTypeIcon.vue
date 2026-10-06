<script setup>
// Значок типа выходного IP: ДЦ — здание, мобильный — телефон, домашний — дом, прокси — маска.
// ДЦ и прокси — красным (хуже для AI-сервисов), мобильный и домашний — зелёным.
const props = defineProps({ kind: { type: String, default: '' } })
const BAD = ['hosting', 'proxy']
</script>
<template>
  <svg class="ip-icon" :class="BAD.includes(props.kind) ? 'bad' : 'good'" viewBox="0 0 16 16" width="16" height="16" aria-hidden="true">
    <template v-if="props.kind === 'hosting'">
      <path d="M3 15V2.5A1 1 0 0 1 4 1.5h8a1 1 0 0 1 1 1V15z" fill="currentColor" />
      <path d="M5.5 4h1.5M9 4h1.5M5.5 7h1.5M9 7h1.5M5.5 10h1.5M9 10h1.5" stroke="var(--panel)" stroke-width="1.4" />
      <path d="M7 15v-2.5h2V15" fill="var(--panel)" />
    </template>
    <template v-else-if="props.kind === 'mobile'">
      <rect x="4" y="1" width="8" height="14" rx="1.6" fill="currentColor" />
      <rect x="5.4" y="2.8" width="5.2" height="8.6" rx=".4" fill="var(--panel)" />
      <circle cx="8" cy="13.2" r=".9" fill="var(--panel)" />
    </template>
    <template v-else-if="props.kind === 'residential'">
      <path d="M8 1.5 15 7.6h-2V14.5H3V7.6H1z" fill="currentColor" />
      <path d="M6.6 14.5v-4h2.8v4" fill="var(--panel)" />
    </template>
    <template v-else-if="props.kind === 'proxy'">
      <path d="M1 6.2C1 4.8 2.2 4 3.6 4.3L8 5.4l4.4-1.1C13.8 4 15 4.8 15 6.2v1.6c0 2.3-1.8 4-4 4-1.3 0-2.3-.7-3-1.8-.7 1.1-1.7 1.8-3 1.8-2.2 0-4-1.7-4-4z" fill="currentColor" />
      <ellipse cx="4.9" cy="7.6" rx="1.7" ry="1.1" fill="var(--panel)" /><ellipse cx="11.1" cy="7.6" rx="1.7" ry="1.1" fill="var(--panel)" />
    </template>
  </svg>
</template>
<style scoped>
.ip-icon { vertical-align: -3px; }
</style>
