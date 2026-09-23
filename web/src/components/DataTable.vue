<script setup>
// Универсальная таблица с пагинацией. columns: [{key,title,l?,fmt?,cls?,slot?,note?}].
//  - fmt(value,row) -> строка (по умолчанию — сырое значение, Vue экранирует).
//  - cls(value,row) -> доп. класс ячейки (напр. 'good'/'bad').
//  - slot: true -> ячейка рендерится через именованный слот #cell-<key> (для SVG и т.п.).
// rowClass(row) -> класс строки (напр. 'active').
import { ref, computed, watch } from 'vue'

const props = defineProps({
  rows: { type: Array, default: () => [] },
  columns: { type: Array, required: true },
  pageSize: { type: Number, default: 15 },
  rowClass: { type: Function, default: null },
  empty: { type: String, default: 'нет данных' },
})

const page = ref(0)
const pages = computed(() => Math.max(1, Math.ceil(props.rows.length / props.pageSize)))
watch(pages, (p) => { if (page.value >= p) page.value = p - 1 })

const slice = computed(() =>
  props.rows.slice(page.value * props.pageSize, (page.value + 1) * props.pageSize)
)

function cellText(col, row) {
  const v = row[col.key]
  return col.fmt ? col.fmt(v, row) : v == null ? '' : v
}
function cellClass(col, row) {
  let c = col.l ? 'l' : ''
  if (col.cls) {
    const x = col.cls(row[col.key], row)
    if (x) c += ' ' + x
  }
  return c
}
</script>

<template>
  <div class="wrap">
    <table>
      <thead>
        <tr>
          <th v-for="c in columns" :key="c.key" :class="{ l: c.l }" :title="c.note || ''">
            {{ c.title }}
          </th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="(row, i) in slice" :key="i" :class="rowClass ? rowClass(row) : ''">
          <td v-for="c in columns" :key="c.key" :class="cellClass(c, row)" :title="c.cellTitle ? c.cellTitle(row) : ''">
            <slot v-if="c.slot" :name="'cell-' + c.key" :row="row" :value="row[c.key]" />
            <b v-else-if="c.strong">{{ cellText(c, row) }}</b>
            <template v-else>{{ cellText(c, row) }}</template>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td class="l empty" :colspan="columns.length">{{ empty }}</td>
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
