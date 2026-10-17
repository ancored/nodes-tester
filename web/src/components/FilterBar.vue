<script setup>
// Выпадающие списки фильтров таблицы; состояние — в useTableFilters.
import { t } from '../i18n.js'
defineProps({ state: { type: Object, required: true } })
</script>
<template>
  <label v-for="f in state.filters.value" :key="f.key">{{ f.title }}<select :value="state.value(f)" @change="state.set(f, $event.target.value)">
    <option value="">{{ t('All') }}</option><option v-for="o in f.options" :key="o" :value="o">{{ f.label ? f.label(o) : o }}</option>
  </select></label>
  <button v-if="state.any.value" class="btn sm" @click="state.reset">{{ t('Reset filters') }}</button>
</template>
