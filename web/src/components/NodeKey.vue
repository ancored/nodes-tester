<script setup>
import { computed } from 'vue'
import { splitNodeKey } from '../ux.js'
// Ключ ноды разными цветами: CRC настроек сервера и CRC учётных данных.
// stacked: голый ключ в таблице — две строки мелким шрифтом, столбец уже.
const props = defineProps({ text: { type: [String, Number], default: '' }, stacked: { type: Boolean, default: false } })
const parts = computed(() => splitNodeKey(props.text))
const twoLines = computed(() => props.stacked && parts.value && !parts.value.head && !parts.value.tail)
</script>
<template>
  <span v-if="twoLines" class="key-stacked"><span class="crc-node" title="CRC настроек сервера">{{ parts.node }}</span><span>.<span class="crc-cred" title="CRC учётных данных">{{ parts.cred }}</span></span></span>
  <span v-else-if="parts">{{ parts.head }}<span class="crc-node" title="CRC настроек сервера">{{ parts.node }}</span>.<span class="crc-cred" title="CRC учётных данных">{{ parts.cred }}</span>{{ parts.tail }}</span>
  <template v-else>{{ text }}</template>
</template>
