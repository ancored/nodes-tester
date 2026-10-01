<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { crcOf, rowKeys, STANDARD_FILTERS } from '../ux.js'
import { useTableFilters } from '../filters.js'
import FilterBar from './FilterBar.vue'
const props = defineProps({ rows:{type:Array,default:()=>[]}, columns:{type:Array,required:true},
  pageSize:{type:Number,default:15}, rowClass:{type:Function,default:null}, empty:{type:String,default:'Нет данных в этом снимке.'},
  queryKey:{type:String,default:''}, total:{type:Number,default:null},
  showSearch:{type:Boolean,default:true}, showSort:{type:Boolean,default:true},
  filters:{type:Array,default:null}, extraFilters:{type:Array,default:()=>[]} })
const route = useRoute(), router = useRouter()
const key = n => props.queryKey + n
function param(name,fallback='') { return String(route.query[key(name)] ?? fallback) }
function update(name,value) { router.replace({query:{...route.query,[key(name)]:value || undefined,[key('page')]:undefined}}) }
const query = computed({get:()=>param('q'),set:v=>update('q',v)})
const sort = computed(()=>param('sort'))
const sortable = computed(()=>props.columns.filter(c=>props.rows.some(r=>r[c.key] != null && typeof r[c.key] !== 'object')))
const sortModel = computed({get:()=>sort.value,set:v=>update('sort',v)})
const filterState = useTableFilters(computed(() => props.rows),
  computed(() => props.filters === null && !props.extraFilters.length ? null : [...(props.filters ?? STANDARD_FILTERS), ...props.extraFilters]), props.queryKey)
const anyFilter = filterState.any
const filtered = computed(() => {
  const text = props.showSearch ? query.value.toLowerCase().trim() : ''
  let rows = filterState.apply(props.rows)
  rows = text ? rows.filter(r => Object.entries(r).filter(([,v]) => typeof v !== 'object').some(([,v]) => String(v ?? '').toLowerCase().includes(text))) : [...rows]
  if(props.showSort && sort.value) {
    const descending = sort.value.startsWith('-'), field = sort.value.replace(/^-/, '')
    rows = [...rows].sort((a,b) => {
      const x=a[field], y=b[field]
      const cmp = x == null ? (y == null ? 0 : 1) : y == null ? -1 :
        typeof x === 'number' && typeof y === 'number' ? x-y : String(x).localeCompare(String(y),'ru',{numeric:true})
      return descending ? -cmp : cmp
    })
  }
  return rows
})
const pages = computed(()=>Math.max(1,Math.ceil(filtered.value.length/props.pageSize)))
const page = computed({
  get:()=>Math.max(0,Math.min(pages.value-1,(Number(param('page','1')) || 1)-1)),
  set:v=>router.replace({query:{...route.query,[key('page')]:String(v+1)}})
})
const slice = computed(()=>filtered.value.slice(page.value*props.pageSize,(page.value+1)*props.pageSize))
function cellText(c,r) { return c.fmt ? c.fmt(r[c.key],r) : r[c.key] ?? '—' }
const keys = computed(() => rowKeys(props.rows))
function order(c) { update('sort',sort.value === c.key ? '-'+c.key : sort.value === '-'+c.key ? '' : c.key) }
</script>
<template>
  <div>
    <div class="toolbar table-toolbar"><FilterBar :state="filterState" /><label v-if="showSearch">Поиск <input v-model="query" type="search" placeholder="Имя, провайдер, страна, CRC…" /></label><label v-if="showSort">Сортировка<select v-model="sortModel"><option value="">Исходный порядок</option><template v-for="c in sortable" :key="c.key"><option :value="c.key">{{ c.title }} ↑</option><option :value="'-'+c.key">{{ c.title }} ↓</option></template></select></label><span>Показано {{ filtered.length }} из {{ total ?? rows.length }}</span></div>
    <div class="wrap">
      <table class="responsive-table">
        <thead><tr><th v-for="c in columns" :key="c.key" :class="{l:c.l,nowrap:c.nowrap}" :aria-sort="sort.replace(/^-/, '') === c.key ? sort.startsWith('-') ? 'descending' : 'ascending' : 'none'">
          <button v-if="showSort && sortable.includes(c)" class="sort-button" @click="order(c)">{{ c.title }} {{ sort === c.key ? '↑' : sort === '-'+c.key ? '↓' : '' }}</button><template v-else>{{ c.title }}</template>
        </th></tr></thead>
        <tbody>
          <tr v-for="row in slice" :key="keys.get(row)" :class="rowClass?.(row)">
            <td v-for="c in columns" :key="c.key" :data-label="c.title" :class="[c.l ? 'l':'',c.nowrap ? 'nowrap':'',c.cls?.(row[c.key],row)]">
              <slot v-if="c.slot" :name="'cell-'+c.key" :row="row" :value="row[c.key]" />
              <RouterLink v-else-if="['crc','id','node'].includes(c.key) && crcOf(row)" :to="'/nodes/'+crcOf(row)" class="break">{{ cellText(c,row) }}</RouterLink>
              <b v-else-if="c.strong">{{ cellText(c,row) }}</b><template v-else>{{ cellText(c,row) }}</template>
            </td>
          </tr>
          <tr v-if="!filtered.length"><td class="l empty" :colspan="columns.length">{{ query || anyFilter ? 'Ничего не найдено. Измените запрос или фильтры.' : empty }}</td></tr>
        </tbody>
      </table>
      <div v-if="pages>1" class="pager"><button class="btn" aria-label="Предыдущая страница" :disabled="page === 0" @click="page--">‹</button><span>{{ page+1 }} / {{ pages }}</span><button class="btn" aria-label="Следующая страница" :disabled="page>=pages-1" @click="page++">›</button></div>
    </div>
    <details v-if="columns.some(c=>c.note)" class="hint"><summary>Пояснения к показателям</summary><p v-for="c in columns.filter(c=>c.note)" :key="c.key">{{ c.title }}: {{ c.note }}</p></details>
  </div>
</template>
