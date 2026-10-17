<script setup>
import { computed, ref, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { sections, nodeSections } from './router.js'
import { useSnapshot, refresh } from './store.js'
import { auth, login, requestLogout, initSession } from './api.js'
import { BOX_DASHBOARD, provisionBoxDashboard } from './boxdash.js'
import { t, lang, setLang, LANGS, locale } from './i18n.js'
import Msg from './components/Msg.vue'
const s = useSnapshot(), route = useRoute()
const SOURCE_URL = 'https://github.com/ancored/nodes-tester'
const token = ref(''), remember = ref(false), showLogin = ref(false), menuOpen = ref(false)
const isNodes = computed(() => nodeSections.some(x => x.path === route.path) || route.path.startsWith('/nodes/'))
async function enter() { await login(token.value,remember.value); token.value = ''; if(auth.verified) showLogin.value = false }
onMounted(initSession)
// Секрет API sing-box подставляется в Dashboard только администратору; без входа она спросит его сама.
watch(() => auth.verified, v => { if (v) provisionBoxDashboard().catch(() => {}) }, { immediate: true })
watch(() => [route.name, lang.value], () => { if (globalThis.document && route.name) document.title = t(route.name) + ' · nodes-tester' }, { immediate: true })
</script>
<template>
  <div class="layout">
    <aside class="sidebar">
      <div class="brand"><span class="brand-identity"><img class="project-icon" src="/nodes-tester-icon.svg" width="32" height="32" alt="" />nodes-tester</span><button class="btn mobile-menu" @click="menuOpen = !menuOpen" :aria-expanded="menuOpen">{{ t('Menu') }}</button></div>
      <nav class="nav" :class="{ expanded: menuOpen }" :aria-label="t('Main sections')">
        <RouterLink v-for="item in sections" :key="item.path" :to="item.path" @click="menuOpen = false" :class="{ selected: item.path === '/nodes' && isNodes }">{{ t(item.name) }}</RouterLink>
        <a :href="BOX_DASHBOARD" target="_blank" rel="noopener" class="external" :title="t('The official panel of the sing-box API service')">sing-box dashboard ↗</a>
      </nav>
      <div class="sidebar-foot" :class="{ expanded: menuOpen }">
        <label class="lang-switch">{{ t('Language') }}<select :value="lang" @change="setLang($event.target.value)"><option v-for="l in LANGS" :key="l.code" :value="l.code" :lang="l.code">{{ l.name }}</option></select></label>
        <a :href="auth.capabilities.source_url || SOURCE_URL" target="_blank" rel="noopener" class="source-link" :title="t('nodes-tester {version}, AGPL-3.0 licence', { version: auth.capabilities.version || '' })">{{ t('Source code') }} ↗</a>
      </div>
    </aside>
    <div class="content">
      <header class="topbar">
        <h1>{{ t($route.name || '') }}</h1><div class="spacer"></div>
        <small>{{ s.error ? t('No fresh data') : s.generated ? t('Snapshot {time}', { time: s.generated }) : t('Loading…') }}</small>
        <button class="btn" @click="refresh" :aria-label="t('Refresh data')">{{ t('Refresh') }}</button>
        <button v-if="auth.verified" class="btn" @click="requestLogout">{{ t('Log out') }}</button>
        <button v-else class="btn" @click="showLogin = !showLogin">{{ t('Log in to manage') }}</button>
      </header>
      <main class="main">
        <form v-if="showLogin && !auth.verified" class="panel login" @submit.prevent="enter">
          <h2>{{ t('Administrator access') }}</h2>
          <p><Msg :text="t('The token is in {key} of your config.json on the router. It is not the sing-box API secret. Send it only over a trusted network or HTTPS.')"><template #key><code>admin.token</code></template></Msg></p>
          <p v-if="auth.capabilities.auth_configured === false" class="notice">{{ t('No token is configured on the server. Set admin.token over SSH and restart the admin panel.') }}</p>
          <label>{{ t('Token') }} <input v-model="token" type="password" autocomplete="off" required /></label>
          <label class="check"><input v-model="remember" type="checkbox" /> {{ t('Remember on this device (localStorage)') }}</label>
          <button class="btn" :disabled="auth.checking">{{ t('Check and log in') }}</button>
        </form>
        <p v-if="auth.error" class="notice bad" role="alert">{{ auth.error }}</p>
        <p v-if="!auth.verified && !showLogin" class="hint">{{ t('View mode. Log in with the administrator token for settings and actions.') }}</p>
        <p v-if="auth.capabilities.mode === 'standalone'" class="hint">{{ t('The admin panel runs on its own. Settings are available after logging in; tester control, the log and live selectors are unavailable.') }}</p>
        <p v-if="s.error" class="notice bad" role="alert">{{ s.error }}<template v-if="s.ready">. {{ t('Showing a stale snapshot received at {time}.', { time: new Date(s.received).toLocaleString(locale()) }) }}</template></p>
        <p v-if="s.data.source && s.data.source.state !== 'ok'" class="notice">{{ s.data.source.message }}. {{ t('Check the database path (storage.path) in the settings. Empty lists do not prove that there are no nodes.') }}</p>
        <nav v-if="isNodes" class="chips subnav" :aria-label="t('Node details')">
          <RouterLink v-for="item in nodeSections" :key="item.path" class="chip" :to="item.path">{{ t(item.name) }}</RouterLink>
        </nav>
        <RouterView :key="$route.path + '|' + lang" />
      </main>
    </div>
  </div>
</template>
