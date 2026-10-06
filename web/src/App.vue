<script setup>
import { computed, ref, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { sections, nodeSections } from './router.js'
import { useSnapshot, refresh } from './store.js'
import { auth, login, requestLogout, initSession } from './api.js'
import { BOX_DASHBOARD, provisionBoxDashboard } from './boxdash.js'
const s = useSnapshot(), route = useRoute()
const SOURCE_URL = 'https://github.com/ancored/nodes-tester'
const token = ref(''), remember = ref(false), showLogin = ref(false), menuOpen = ref(false)
const isNodes = computed(() => nodeSections.some(x => x.path === route.path) || route.path.startsWith('/nodes/'))
async function enter() { await login(token.value,remember.value); token.value = ''; if(auth.verified) showLogin.value = false }
onMounted(initSession)
// Секрет API sing-box подставляется в Dashboard только администратору; без входа она спросит его сама.
watch(() => auth.verified, v => { if (v) provisionBoxDashboard().catch(() => {}) }, { immediate: true })
</script>
<template>
  <div class="layout">
    <aside class="sidebar">
      <div class="brand"><span class="brand-identity"><img class="project-icon" src="/nodes-tester-icon.svg" width="32" height="32" alt="" />nodes-tester</span><button class="btn mobile-menu" @click="menuOpen = !menuOpen" :aria-expanded="menuOpen">Меню</button></div>
      <nav class="nav" :class="{ expanded: menuOpen }" aria-label="Основные разделы">
        <RouterLink v-for="item in sections" :key="item.path" :to="item.path" @click="menuOpen = false" :class="{ selected: item.path === '/nodes' && isNodes }">{{ item.name }}</RouterLink>
        <a :href="BOX_DASHBOARD" target="_blank" rel="noopener" class="external" title="Официальная панель API-сервиса sing-box">sing-box dashboard ↗</a>
        <a :href="auth.capabilities.source_url || SOURCE_URL" target="_blank" rel="noopener" class="external" :title="`nodes-tester ${auth.capabilities.version || ''}, лицензия AGPL-3.0`">Исходный код ↗</a>
      </nav>
    </aside>
    <div class="content">
      <header class="topbar">
        <h1>{{ $route.name }}</h1><div class="spacer"></div>
        <small>{{ s.error ? 'Нет свежих данных' : s.generated ? 'Снимок ' + s.generated : 'Загрузка…' }}</small>
        <button class="btn" @click="refresh" aria-label="Обновить данные">Обновить</button>
        <button v-if="auth.verified" class="btn" @click="requestLogout">Выйти</button>
        <button v-else class="btn" @click="showLogin = !showLogin">Войти для управления</button>
      </header>
      <main class="main">
        <form v-if="showLogin && !auth.verified" class="panel login" @submit.prevent="enter">
          <h2>Доступ администратора</h2>
          <p>Токен находится в <code>dashboard.token</code> вашего config.json на роутере. Это не секрет API sing-box. Передавайте его только через доверенную сеть или HTTPS.</p>
          <p v-if="auth.capabilities.auth_configured === false" class="notice">На сервере токен не настроен. Задайте dashboard.token через SSH и перезапустите админку.</p>
          <label>Токен <input v-model="token" type="password" autocomplete="off" required /></label>
          <label class="check"><input v-model="remember" type="checkbox" /> Запомнить на этом устройстве (localStorage)</label>
          <button class="btn" :disabled="auth.checking">Проверить и войти</button>
        </form>
        <p v-if="auth.error" class="notice bad" role="alert">{{ auth.error }}</p>
        <p v-if="!auth.verified && !showLogin" class="hint">Режим просмотра. Для настроек и действий войдите с токеном администратора.</p>
        <p v-if="auth.capabilities.mode === 'standalone'" class="hint">Админка запущена отдельно. Настройки доступны после входа; управление тестером, журнал и живые селекторы недоступны.</p>
        <p v-if="s.error" class="notice bad" role="alert">{{ s.error }}<template v-if="s.ready">. Показан устаревший снимок, полученный {{ new Date(s.received).toLocaleString('ru-RU') }}.</template></p>
        <p v-if="s.data.source && s.data.source.state !== 'ok'" class="notice">{{ s.data.source.message }}. Проверьте storage.enabled и путь к базе в настройках. Пустые списки не подтверждают отсутствие нод.</p>
        <nav v-if="isNodes" class="chips subnav" aria-label="Сведения о нодах">
          <RouterLink v-for="item in nodeSections" :key="item.path" class="chip" :to="item.path">{{ item.name }}</RouterLink>
        </nav>
        <RouterView :key="$route.path" />
      </main>
    </div>
  </div>
</template>
