<script setup>
// Переведённая строка с разметкой внутри: {name} в тексте заменяется слотом name.
// <Msg :text="t('Token is in {key} of config.json')"><template #key><code>admin.token</code></template></Msg>
import { computed } from 'vue'
const props = defineProps({ text: { type: String, required: true } })
const parts = computed(() => props.text.split(/(\{\w+\})/).filter(Boolean)
  .map(p => /^\{\w+\}$/.test(p) ? { slot: p.slice(1, -1) } : { text: p }))
</script>
<template><template v-for="(p, i) in parts" :key="i"><slot v-if="p.slot" :name="p.slot" /><template v-else>{{ p.text }}</template></template></template>
