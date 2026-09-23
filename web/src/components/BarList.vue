<script setup>
// Горизонтальные бары топа: items [{name, value}], value форматируется fmt().
import { computed } from 'vue'

const props = defineProps({
  items: { type: Array, default: () => [] },
  fmt: { type: Function, default: (v) => v },
  limit: { type: Number, default: 8 },
})

const view = computed(() => {
  const top = props.items.slice(0, props.limit)
  const max = Math.max(1, ...top.map((i) => +i.value || 0))
  return top.map((i) => ({ name: i.name, value: i.value, w: (100 * (+i.value || 0) / max).toFixed(1) }))
})
</script>

<template>
  <div class="wrap" style="padding: 16px">
    <div v-if="!view.length" class="empty">нет данных</div>
    <div v-else class="bars">
      <div v-for="i in view" :key="i.name" class="bar-row">
        <span class="name" :title="i.name">{{ i.name }}</span>
        <span class="bar-track"><span class="bar-fill" :style="{ width: i.w + '%' }" /></span>
        <span class="val">{{ fmt(i.value) }}</span>
      </div>
    </div>
  </div>
</template>
