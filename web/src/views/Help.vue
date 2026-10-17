<script setup>
// Страница «Справка»: оглавление, поиск по всем разделам и текст выбранного раздела.
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { HELP_PAGES, hasPage, loadHelp } from '../help.js'
import { lang, t } from '../i18n.js'
import HelpDoc from '../components/HelpDoc.vue'
const route = useRoute(), router = useRouter()
const page = computed(() => hasPage(String(route.query.page || '')) ? String(route.query.page) : 'README')
const anchor = computed(() => String(route.query.s || ''))
const titles = ref({}), headings = ref([]), query = ref(''), docs = ref({})
function go(next, section) { router.push({ path: '/help', query: { page: next, ...(section ? { s: section } : {}) } }) }
// Заголовки всех страниц — для оглавления; тексты — для поиска.
watch(lang, async code => {
  const loaded = await Promise.all(HELP_PAGES.map(p => loadHelp(p, code).then(d => [p, d]).catch(() => [p, null])))
  if (code !== lang.value) return
  docs.value = Object.fromEntries(loaded.filter(([, d]) => d))
  titles.value = Object.fromEntries(loaded.map(([p, d]) => [p, d?.title || p]))
}, { immediate: true })
function loaded(doc) { headings.value = doc.headings.filter(h => h.level === 2) }
// Поиск: разделы (по заголовкам второго-третьего уровня), где встречаются все слова запроса.
const results = computed(() => {
  const words = query.value.toLowerCase().split(/\s+/).filter(w => w.length > 1)
  if (!words.length) return []
  const found = []
  for (const p of HELP_PAGES) {
    const doc = docs.value[p]
    if (!doc) continue
    let section = { text: doc.title, id: '' }, buffer = []
    const flush = () => {
      const body = buffer.join(' ').replace(/[`*_|#>[\]]|\(([^)]*)\)/g, ' ').replace(/\s+/g, ' ')
      const low = (section.text + ' ' + body).toLowerCase()
      if (words.every(w => low.includes(w))) {
        const hit = low.indexOf(words[0]) - section.text.length - 1
        const at = hit > 60 ? body.indexOf(' ', hit - 60) + 1 : 0       // отрывок — с начала слова
        found.push({ page: p, id: section.id, title: section.text, snippet: (at ? '…' : '') + body.slice(at, at + 160) + '…' })
      }
    }
    let pending = '', code = false
    for (const line of doc.text.split('\n')) {
      if (line.startsWith('```')) { code = !code; continue }
      if (code) continue                          // схемы и примеры кода в поиск не идут
      const anchorLine = /^<a id="([\w-]+)"><\/a>/.exec(line)
      if (anchorLine) { pending = anchorLine[1]; continue }
      const h = /^#{2,3}\s+(.*)/.exec(line)
      if (h) { flush(); section = { text: h[1].replace(/`/g, ''), id: pending || doc.headings.find(x => x.text === h[1].replace(/`/g, ''))?.id || '' }; buffer = []; pending = ''; continue }
      buffer.push(line)
    }
    flush()
  }
  return found.slice(0, 40)
})
</script>
<template>
  <div class="help-layout">
    <nav class="help-toc" :aria-label="t('Help contents')">
      <label class="help-search">{{ t('Search the help') }}<input v-model="query" type="search" /></label>
      <ul v-if="!query.trim()">
        <li v-for="p in HELP_PAGES" :key="p" :class="{ on: p === page }">
          <a :href="'#/help?page=' + p" @click.prevent="go(p)">{{ titles[p] || p }}</a>
          <ul v-if="p === page && headings.length"><li v-for="h in headings" :key="h.id"><a :href="'#/help?page=' + p + '&s=' + h.id" @click.prevent="go(p, h.id)">{{ h.text }}</a></li></ul>
        </li>
      </ul>
      <template v-else>
        <p v-if="!results.length" class="mut">{{ t('Nothing found.') }}</p>
        <ul class="help-results"><li v-for="r in results" :key="r.page + r.id + r.title"><a href="#" @click.prevent="go(r.page, r.id)"><b>{{ titles[r.page] }}</b> › {{ r.title }}</a><small>{{ r.snippet }}</small></li></ul>
      </template>
    </nav>
    <HelpDoc class="panel" :page="page" :anchor="anchor" @navigate="go" @loaded="loaded" />
  </div>
</template>
