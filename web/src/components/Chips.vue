<script setup>
// Селектор-чипы (v-model). options: массив строк; label — подпись слева.
defineProps({
  options: { type: Array, default: () => [] },
  modelValue: { type: String, default: '' },
  label: { type: String, default: '' },
})
defineEmits(['update:modelValue'])
</script>

<template>
  <div class="chips">
    <span v-if="label" class="mut" style="font-size: 11px; margin-right: 2px">{{ label }}</span>
    <button
      v-for="o in options"
      :key="o"
      class="chip"
      :class="{ on: o === modelValue }"
      @click="$emit('update:modelValue', o)"
    >{{ o }}</button>
  </div>
</template>
