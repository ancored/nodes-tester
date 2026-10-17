<script setup>
import { ref, computed, watch, nextTick } from 'vue'
import { api, can, auth } from '../api.js'
import { refresh, useSnapshot } from '../store.js'
import { t, locale } from '../i18n.js'
const props = defineProps({ node: { type: Object, required: true } })
const s = useSnapshot(), busy = ref(false), message = ref(''), proposal = ref(null), dialog = ref(null), group = ref('')
watch(proposal, async p => { await nextTick(); if (p) dialog.value?.showModal(); else dialog.value?.close() })
const groups = computed(() => props.node.activate_groups || [])
watch(groups, g => { if (!g.includes(group.value)) group.value = g[0] || '' }, { immediate: true })
const current = g => s.data.runner?.regions?.find(r => r.region === g)?.active
function prepare(action) {
  const details = {
    ban: () => t('The node will be excluded from checks indefinitely. If it is active, the tester will immediately switch its groups to another node or the fallback outbound.'),
    unban: () => t('The node will take part in the next checks again.'),
    quarantine: () => t('Checks will be postponed for {hours} h. If the node is active, the tester will immediately switch its groups to another node or the fallback outbound.', { hours: auth.capabilities.quarantine_hours }),
    unquarantine: () => t('The pause or quarantine will be lifted and the node queued for checks; a manual exclusion stays.'),
    activate: () => t('Group: {group}. Currently selected: {current}. This node will be selected. Automation may change the choice later.',
      { group: group.value, current: current(group.value) || t('unknown') }),
  }
  proposal.value = { action, group: group.value, text: details[action]() }
}
async function execute() {
  const { action, group: target } = proposal.value, n = props.node
  busy.value = true; message.value = ''; proposal.value = null
  try {
    const result = action === 'activate'
      ? await api.post('/groups/' + encodeURIComponent(target) + '/switch', { node: n.node })
      : await api.post('/nodes/' + n.crc + '/' + action)
    message.value = action === 'activate' ? t('The node is selected in group {group}. The choice is temporary.', { group: target })
      : result.until ? t('Quarantined until {time}', { time: new Date(result.until*1000).toLocaleString(locale()) }) : t('Change saved.')
    await refresh()
  } catch(e) { message.value = e.message }
  finally { busy.value = false }
}
</script>
<template>
  <div class="node-actions">
    <div class="actions">
      <button class="btn sm" :disabled="busy || !can('node_actions')" @click="prepare(+node.banned ? 'unban' : 'ban')">{{ +node.banned ? t('Remove exclusion') : t('Exclude') }}</button>
      <button class="btn sm" :disabled="busy || !can('node_actions')" @click="prepare(node.gstate ? 'unquarantine' : 'quarantine')">{{ node.gstate ? t('Lift restriction') : t('Quarantine') }}</button>
      <label v-if="groups.length > 1" class="inline-select">{{ t('Group') }}<select v-model="group"><option v-for="g in groups" :key="g">{{ g }}</option></select></label>
      <button class="btn sm" :disabled="busy || !can('switch') || !groups.length" @click="prepare('activate')">{{ groups.length === 1 ? t('Select in group {group}', { group: groups[0] }) : t('Select in group') }}</button>
    </div>
    <p class="mut" v-if="!groups.length">{{ t('Manual selection is unavailable: the node is not a candidate of any group (it needs a positive rating, passed required checks of the group and no restrictions).') }}</p>
    <p v-if="message" role="status">{{ message }}</p>
    <dialog ref="dialog" @cancel="proposal = null" @close="proposal = null">
      <template v-if="proposal">
        <h2>{{ t('Confirm the action') }}</h2>
        <p class="break">{{ node.node || node.crc }}</p><p>{{ proposal.text }}</p>
        <div class="actions"><button class="btn" autofocus @click="proposal = null">{{ t('Cancel') }}</button><button class="btn" @click="execute">{{ t('Confirm') }}</button></div>
      </template>
    </dialog>
  </div>
</template>
