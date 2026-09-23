<script setup>
import { computed } from 'vue'
import { sections } from './router.js'
import { useSnapshot, refresh } from './store.js'
import { setToken, useAdminToken } from './api.js'

const s = useSnapshot()
const token = useAdminToken()
const status = computed(() =>
  s.error ? 'ошибка загрузки' : s.generated ? 'обновлено ' + s.generated : 'загрузка…'
)

function saveToken() {
  setToken(token.value.trim())
}
</script>

<template>
  <div class="layout">
    <aside class="sidebar">
      <div class="brand">nodes-tester</div>
      <nav class="nav">
        <RouterLink v-for="s in sections" :key="s.path" :to="s.path">{{ s.name }}</RouterLink>
      </nav>
    </aside>
    <div class="content">
      <header class="topbar">
        <h1>{{ $route.name || 'nodes-tester' }}</h1>
        <div class="spacer"></div>
        <small :class="{ bad: s.error }">{{ status }}</small>
        <button class="btn" style="margin-left: 12px" @click="refresh">↻</button>
        <input
          v-model="token"
          type="password"
          class="token"
          placeholder="X-Admin-Token"
          title="Токен для write/control-действий (X-Admin-Token). Сохраняется в localStorage."
          @change="saveToken"
        />
      </header>
      <main class="main">
        <RouterView />
      </main>
    </div>
  </div>
</template>
