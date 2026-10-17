<script setup>
// VACUUM базы тестера: итог последнего запуска, следующий срок и запуск вручную.
import { onMounted, onUnmounted, ref } from 'vue'
import { api } from '../api.js'
import { bytes, dateTime, dur } from '../format.js'
import { t } from '../i18n.js'
import HelpButton from './HelpButton.vue'
const state = ref(null), error = ref(''), busy = ref(false)
let timer = null
async function load() {
  try { state.value = await api.get('/storage/vacuum'); error.value = '' } catch (e) { error.value = e.message }
  clearTimeout(timer)
  if (state.value?.running) timer = setTimeout(load, 2000)        // идёт VACUUM — ждём итога
}
async function run() {
  if (!window.confirm(t('Run VACUUM now? Writing to the database waits while it runs (from seconds to a few minutes); measurements are delayed, not lost.'))) return
  busy.value = true
  try { state.value = await api.post('/storage/vacuum', {}); error.value = ''; load() } catch (e) { error.value = e.message }
  finally { busy.value = false }
}
const triggers = { schedule: t('on schedule'), manual: t('manually') }
onMounted(load)
onUnmounted(() => clearTimeout(timer))
</script>
<template>
  <section class="panel">
    <h2>{{ t('Database maintenance') }}<HelpButton topic="settings#vacuum" /></h2>
    <p v-if="error" class="notice bad" role="alert">{{ error }}</p>
    <dl v-if="state" class="facts">
      <dt>{{ t('Database size now') }}</dt><dd>{{ bytes(state.size) }}</dd>
      <dt>{{ t('Last VACUUM') }}</dt>
      <dd v-if="state.running">{{ t('running…') }}</dd>
      <dd v-else-if="!state.last">{{ t('not run yet') }}</dd>
      <dd v-else>{{ dateTime(state.last.ts) }} · {{ triggers[state.last.trigger] || state.last.trigger }} ·
        <template v-if="state.last.error"><span class="bad">{{ t('error: {error}', { error: state.last.error }) }}</span></template>
        <template v-else>{{ t('{before} → {after} in {time}', { before: bytes(state.last.size_before), after: bytes(state.last.size_after), time: state.last.duration < 60 ? t('{n} s', { n: state.last.duration }) : dur(state.last.duration) }) }}</template>
      </dd>
      <dt>{{ t('Next by schedule') }}</dt><dd>{{ state.next ? dateTime(state.next) : t('manually only') }}</dd>
    </dl>
    <div class="actions"><button class="btn" :disabled="busy || state?.running" @click="run">{{ t('Run now') }}</button></div>
  </section>
</template>
