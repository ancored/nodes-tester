<script setup>
import { computed } from 'vue'
import { splitNodeKey } from '../ux.js'
// Ключ ноды разными цветами: CRC настроек сервера и CRC учётных данных.
const props = defineProps({ text: { type: [String, Number], default: '' } })
const parts = computed(() => splitNodeKey(props.text))
</script>
<template>
  <span v-if="parts">{{ parts.head }}<span class="crc-node" title="CRC настроек сервера">{{ parts.node }}</span>.<span class="crc-cred" title="CRC учётных данных">{{ parts.cred }}</span>{{ parts.tail }}</span>
  <template v-else>{{ text }}</template>
</template>
