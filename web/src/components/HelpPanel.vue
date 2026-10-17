<script setup>
// Боковая панель справки поверх страницы; «Открыть всю справку» — страница «Справка».
import { onMounted, onUnmounted } from 'vue'
import { helpPanel, closeHelp } from '../help.js'
import { t } from '../i18n.js'
import HelpDoc from './HelpDoc.vue'
function navigate(page, anchor) { Object.assign(helpPanel, { page, anchor: anchor || '' }) }
const key = e => { if (e.key === 'Escape' && helpPanel.open) closeHelp() }
onMounted(() => window.addEventListener('keydown', key))
onUnmounted(() => window.removeEventListener('keydown', key))
</script>
<template>
  <aside v-if="helpPanel.open" class="help-panel" role="dialog" :aria-label="t('Help')">
    <header class="help-panel-head">
      <b>{{ t('Help') }}</b><span class="spacer"></span>
      <RouterLink :to="{ path: '/help', query: { page: helpPanel.page, ...(helpPanel.anchor ? { s: helpPanel.anchor } : {}) } }" @click="closeHelp">{{ t('Open the full help') }}</RouterLink>
      <button type="button" class="btn sm" :aria-label="t('Close help')" @click="closeHelp">✕</button>
    </header>
    <div class="help-scroll"><HelpDoc :page="helpPanel.page" :anchor="helpPanel.anchor" @navigate="navigate" /></div>
  </aside>
</template>
