<script setup>
// Свод результатов тестов — пивот по тестам (порт resultsTable/renderResults).
import { ref, computed, watch } from 'vue'
import { useSnapshot } from '../store.js'

const s = useSnapshot()
const page = ref(0)
const pageSize = 15

const HDR = { heavy_download: 'dl50↓ *' }
const HNOTE = {
  heavy_download:
    'DL50 — отдельный veto-тест кандидатов, идёт вне обычного прогона (раз в N прогонов). ' +
    'Значение может быть из другого пасса; такие ячейки помечены *.',
}

const tests = computed(() => (s.data.results && s.data.results.tests) || [])
const rows = computed(() => (s.data.results && s.data.results.rows) || [])
const pages = computed(() => Math.max(1, Math.ceil(rows.value.length / pageSize)))
watch(pages, (p) => { if (page.value >= p) page.value = p - 1 })
const slice = computed(() => rows.value.slice(page.value * pageSize, (page.value + 1) * pageSize))

function cell(row, t) {
  const c = row.cells[t]
  if (!c) return { text: '–', cls: 'mut', title: '' }
  return {
    text: c.v + (c.off_pass ? ' *' : ''),
    cls: (c.heavy ? 'heavy ' : '') + (c.ok ? 'good' : 'bad'),
    title: c.title || '',
  }
}
</script>

<template>
  <div v-if="s.loading" class="empty">Загрузка…</div>
  <div v-else-if="s.error && !s.ready" class="empty bad">Ошибка: {{ s.error }}</div>
  <div v-else-if="!rows.length" class="empty">нет данных (первый прогон ещё идёт?)</div>
  <div v-else class="wrap">
    <table>
      <thead>
        <tr>
          <th class="l">провайдер</th>
          <th class="l">протокол</th>
          <th class="l">cc</th>
          <th class="l">crc</th>
          <th class="l" title="дата/номер прогона">прогон</th>
          <th v-for="t in tests" :key="t" :title="HNOTE[t] || t">{{ HDR[t] || t }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="(r, i) in slice" :key="i">
          <td class="l">{{ r.provider }}</td>
          <td class="l">{{ r.protocol }}</td>
          <td class="l">{{ r.country }}</td>
          <td class="l mut">{{ r.crc }}</td>
          <td class="l num">{{ r.pass_label }}</td>
          <td v-for="t in tests" :key="t" :class="cell(r, t).cls" :title="cell(r, t).title">
            {{ cell(r, t).text }}
          </td>
        </tr>
      </tbody>
    </table>
    <div v-if="pages > 1" class="pager">
      <button class="btn" :disabled="page === 0" @click="page--">‹</button>
      <span>{{ page + 1 }} / {{ pages }}</span>
      <button class="btn" :disabled="page >= pages - 1" @click="page++">›</button>
    </div>
  </div>
</template>
