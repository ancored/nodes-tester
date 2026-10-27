<script setup>
import { computed, ref, watch, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import { useSnapshot } from '../store.js'
import { FAILURE_CAUSES, TEST_NAMES } from '../ux.js'
import { dateTime, bytes, dur } from '../format.js'
import { api, auth } from '../api.js'
import { t, tn } from '../i18n.js'
import HelpButton from '../components/HelpButton.vue'
const s = useSnapshot(), route = useRoute()
const tag = computed(() => String(route.params.tag))
const card = ref(null), error = ref('')
let requestId = 0
async function load() {
  const id = ++requestId, epoch = auth.epoch
  try {
    const data = await api.get('/providers/' + encodeURIComponent(tag.value))
    if (id === requestId && epoch === auth.epoch) { card.value = data; error.value = '' }
  } catch (e) { if (id === requestId && epoch === auth.epoch) error.value = e.message }
}
watch([tag, () => auth.epoch, () => s.data.generated], load, { immediate: true })
onUnmounted(() => { requestId++ })
const src = computed(() => card.value?.source)
const meta = computed(() => src.value?.meta || {})
const nodes = computed(() => card.value?.nodes || {})
const pct = (part, total) => total ? Math.round(100 * part / total) + '%' : '—'
const epoch = iso => iso ? Date.parse(iso) / 1000 : 0
const daysLeft = computed(() => meta.value.expire ? Math.ceil((meta.value.expire - Date.now() / 1000) / 86400) : null)
const used = computed(() => (meta.value.upload || 0) + (meta.value.download || 0))
const causes = computed(() => Object.entries(card.value?.causes || {}).sort((a, b) => b[1] - a[1]))
const windowDays = computed(() => Math.round((card.value?.window || 0) / 86400))
</script>
<template>
  <p><RouterLink to="/subscriptions">← {{ t('To the subscriptions') }}</RouterLink></p>
  <p v-if="error" class="notice bad">{{ error }} <button class="btn" @click="load">{{ t('Retry loading') }}</button></p>
  <p v-else-if="!card">{{ t('Loading…') }}</p>
  <template v-else>
    <section class="panel">
      <h2 class="break">{{ card.tag }}<HelpButton topic="subscriptions#provider-card" /></h2>
      <p v-if="meta.title">{{ meta.title }}</p>
      <p v-if="meta.support_url || meta.web_page_url" class="chips">
        <a v-if="meta.web_page_url" :href="meta.web_page_url" target="_blank" rel="noopener noreferrer">{{ t('Provider site') }}</a>
        <a v-if="meta.support_url" :href="meta.support_url" target="_blank" rel="noopener noreferrer">{{ t('Support') }}</a>
      </p>
    </section>
    <section class="panel">
      <h2>{{ t('Subscription') }}<HelpButton topic="subscriptions#provider-card" /></h2>
      <p v-if="!src">{{ t('The router has not fetched this subscription yet, or it is not in the router build.') }}</p>
      <dl v-else class="facts">
        <dt>{{ t('Last fetch') }}</dt><dd :class="src.ok === false ? 'bad' : ''">{{ src.ok === false ? (src.stale ? t('failed, the previous nodes work') : t('failed')) : t('loaded') }} · {{ dateTime(epoch(src.fetched_at)) }} · {{ tn(src.count ?? 0, '{n} node', '{n} nodes') }}</dd>
        <dt>{{ t('Paid until') }}</dt><dd :class="daysLeft !== null && daysLeft <= 3 ? 'bad' : ''">{{ meta.expire ? dateTime(meta.expire) + ' · ' + (daysLeft > 0 ? t('{n} days left', { n: daysLeft }) : t('expired')) : t('no term given by the provider') }}</dd>
        <dt>{{ t('Plan traffic') }}</dt><dd>{{ meta.total ? t('{used} of {total} used ({pct})', { used: bytes(used), total: bytes(meta.total), pct: pct(used, meta.total) }) : t('no limit given by the provider') }}</dd>
      </dl>
    </section>
    <section class="panel">
      <h2>{{ t('Nodes') }}</h2>
      <dl class="facts">
        <dt>{{ t('In the subscription now') }}</dt><dd>{{ nodes.total }}</dd>
        <dt>{{ t('Working (rating above zero)') }}</dt><dd>{{ nodes.live }} · {{ pct(nodes.live, nodes.total) }}</dd>
        <dt>{{ t('Paused or quarantined') }}</dt><dd>{{ nodes.restricted }}</dd>
        <dt>{{ t('Active now in groups') }}</dt><dd>{{ nodes.active?.length ? nodes.active.join(', ') : '—' }}</dd>
        <dt>{{ t('In reserve') }}</dt><dd>{{ nodes.reserve }}</dd>
      </dl>
      <p><RouterLink :to="{ path: '/nodes', query: { q: card.tag } }">{{ t('The nodes of this subscription') }}</RouterLink></p>
    </section>
    <section class="panel">
      <h2>{{ t('Active node over {n} days', { n: windowDays }) }}</h2>
      <p v-if="!card.activity.length">{{ t('Nodes of this subscription were not active in any group over the period.') }}</p>
      <table v-else class="responsive-table">
        <thead><tr><th class="l">{{ t('Group') }}</th><th>{{ t('Share of time') }}</th><th>{{ t('Time') }}</th><th>{{ t('Activations') }}</th><th>{{ t('Emergency replacements') }}</th></tr></thead>
        <tbody><tr v-for="a in card.activity" :key="a.group">
          <td class="l"><b>{{ a.group }}</b></td><td>{{ Math.round(a.share * 100) }}%</td><td>{{ dur(a.seconds) }}</td><td>{{ a.activations }}</td><td :class="a.emergencies ? 'bad' : ''">{{ a.emergencies }}</td>
        </tr></tbody>
      </table>
    </section>
    <section class="panel">
      <h2>{{ t('AI services') }}</h2>
      <p v-if="!Object.keys(card.ai).length">{{ t('AI checks did not run on the nodes of this subscription.') }}</p>
      <dl v-else class="facts"><template v-for="(a, test) in card.ai" :key="test">
        <dt>{{ TEST_NAMES[test] || test }}</dt><dd>{{ t('{ok} of {total} checked nodes pass', a) }}</dd>
      </template></dl>
    </section>
    <section class="panel">
      <h2>{{ t('Why nodes fail') }}<HelpButton topic="node-card#results" /></h2>
      <p v-if="!causes.length">{{ t('No failed checks in the latest results.') }}</p>
      <dl v-else class="facts"><template v-for="[code, n] in causes" :key="code">
        <dt>{{ FAILURE_CAUSES[code] || code }}</dt><dd>{{ tn(n, '{n} node', '{n} nodes') }}</dd>
      </template></dl>
    </section>
    <section class="panel">
      <h2>{{ t('User traffic over {n} days', { n: windowDays }) }}</h2>
      <p>{{ t('Incoming {down}, outgoing {up}.', { down: bytes(card.traffic.down), up: bytes(card.traffic.up) }) }}</p>
    </section>
  </template>
</template>
