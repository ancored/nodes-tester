<script setup>
import { ref, watch, nextTick } from 'vue'
import { api, can, auth } from '../api.js'
import { refresh, useSnapshot } from '../store.js'
const props = defineProps({ node: { type: Object, required: true } })
const s = useSnapshot(), busy = ref(false), message = ref(''), proposal = ref(null), dialog = ref(null)
watch(proposal, async p => { await nextTick(); if (p) dialog.value?.showModal(); else dialog.value?.close() })
function prepare(action) {
  const n = props.node, old = s.data.runner?.regions?.find(r => r.region === n.region)?.active
  const details = {
    ban: 'Нода будет исключена из следующих проверок бессрочно. Текущий выбор региона не меняется немедленно.',
    unban: 'Нода снова сможет участвовать в следующих проверках.',
    quarantine: 'Проверки будут отложены на ' + auth.capabilities.quarantine_hours + ' ч. Текущий выбор региона не меняется немедленно.',
    unquarantine: 'Пауза или карантин будут сняты. Проверка возможна со следующего прохода; ручное исключение остаётся.',
    activate: 'Регион: ' + n.region + '. Сейчас выбрана: ' + (old || 'неизвестно') + '. Будет выбрана эта нода. Автоматика впоследствии может изменить выбор.',
  }
  proposal.value = { action, text: details[action] }
}
async function execute() {
  const action = proposal.value.action, n = props.node
  busy.value = true; message.value = ''; proposal.value = null
  try {
    const result = action === 'activate'
      ? await api.post('/regions/' + encodeURIComponent(n.region) + '/switch', { node: n.node })
      : await api.post('/nodes/' + n.crc + '/' + action)
    message.value = action === 'activate' ? 'Переключение выполнено. Выбор временный.'
      : result.until ? 'Карантин до ' + new Date(result.until*1000).toLocaleString('ru-RU') : 'Изменение сохранено.'
    await refresh()
  } catch(e) { message.value = e.message }
  finally { busy.value = false }
}
</script>
<template>
  <div class="node-actions">
    <div class="actions">
      <button class="btn sm" :disabled="busy || !can('node_actions')" @click="prepare(+node.banned ? 'unban' : 'ban')">{{ +node.banned ? 'Снять исключение' : 'Исключить' }}</button>
      <button class="btn sm" :disabled="busy || !can('node_actions') || (!node.gstate && !auth.capabilities.cooldown_enabled)" @click="prepare(node.gstate ? 'unquarantine' : 'quarantine')">{{ node.gstate ? 'Снять ограничение' : 'В карантин' }}</button>
      <button class="btn sm" :disabled="busy || !can('switch') || !node.can_activate" @click="prepare('activate')">Выбрать для региона</button>
    </div>
    <p class="mut" v-if="!node.can_activate">Ручной выбор недоступен: нужен допустимый кандидат региона без ограничений.</p>
    <p class="mut" v-if="!auth.capabilities.cooldown_enabled">Карантин отключён настройкой cooldown.enabled.</p>
    <p v-if="message" role="status">{{ message }}</p>
    <dialog ref="dialog" @cancel="proposal = null" @close="proposal = null">
      <template v-if="proposal">
        <h2>Подтвердите действие</h2>
        <p class="break">{{ node.node || node.crc }}</p><p>{{ proposal.text }}</p>
        <div class="actions"><button class="btn" autofocus @click="proposal = null">Отмена</button><button class="btn" @click="execute">Подтвердить</button></div>
      </template>
    </dialog>
  </div>
</template>
