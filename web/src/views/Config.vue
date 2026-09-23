<script setup>
import { ref, computed, onMounted } from 'vue'
import { api, useAdminToken } from '../api.js'

const tab = ref('config')
const configText = ref('')
const providersText = ref('')
const msg = ref('')
const busy = ref(false)
const paths = ref({})
const adminToken = useAdminToken()
const hasToken = computed(() => !!adminToken.value)

const editorText = computed({
  get: () => (tab.value === 'config' ? configText.value : providersText.value),
  set: (v) => { if (tab.value === 'config') configText.value = v; else providersText.value = v },
})

async function load() {
  msg.value = ''
  const errors = []
  try {
    const c = await api.get('/config')
    configText.value = JSON.stringify(c.data, null, 2)
    paths.value.config = c.path
  } catch (e) {
    configText.value = ''
    paths.value.config = ''
    errors.push('config: ' + e.message)
  }
  try {
    const p = await api.get('/config/providers')
    providersText.value = JSON.stringify(p.data, null, 2)
    paths.value.providers = p.path
  } catch (e) {
    providersText.value = ''
    paths.value.providers = ''
    errors.push('providers: ' + e.message)
  }
  if (errors.length) msg.value = errors.join('; ')
}

async function save() {
  busy.value = true
  msg.value = ''
  const isConfig = tab.value === 'config'
  const text = isConfig ? configText.value : providersText.value
  let obj
  try {
    obj = JSON.parse(text)
  } catch (e) {
    msg.value = 'невалидный JSON: ' + e.message
    busy.value = false
    return
  }
  try {
    const path = isConfig ? '/config' : '/config/providers'
    const r = await api.put(path, obj)
    msg.value = 'сохранено: ' + r.path
    if (r.restart_required) msg.value += ' (перезапустите тестер, чтобы применить)'
  } catch (e) {
    msg.value = e.message
  } finally {
    busy.value = false
  }
}

onMounted(load)
</script>

<template>
  <div>
    <div v-if="!hasToken" class="hint">
      Редактор конфигов требует токен — введите <code>X-Admin-Token</code> в шапке.
    </div>

    <div class="chips">
      <button class="chip" :class="{ on: tab === 'config' }" @click="tab = 'config'">config.json</button>
      <button class="chip" :class="{ on: tab === 'providers' }" @click="tab = 'providers'">providers.json</button>
      <button class="btn sm" style="margin-left: auto" :disabled="busy" @click="load">↻ перечитать</button>
      <button class="btn sm" :disabled="busy || !hasToken" @click="save">Сохранить</button>
      <span class="mut" style="font-size: 12px">{{ msg }}</span>
    </div>

    <div class="mut" style="font-size: 12px; margin-bottom: 8px">
      {{ tab === 'config' ? (paths.config || '') : (paths.providers || '') }}
    </div>

    <textarea
      v-model="editorText"
      class="cfg-editor"
      spellcheck="false"
      :disabled="!hasToken"
    ></textarea>
  </div>
</template>
