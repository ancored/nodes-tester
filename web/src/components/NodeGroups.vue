<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../api.js'
import { CHECK_NAMES } from '../ux.js'
import JsonField from './JsonField.vue'
const props=defineProps({doc:{type:Object,required:true},checks:{type:Array,default:()=>[]},ipKinds:{type:Array,default:()=>[]},builtin:{type:Object,default:()=>({})}})
const emit=defineEmits(['changed','valid'])
// Живое состояние групп работающего тестера (по сохранённому конфигу, не по черновику формы).
const live=ref({})
onMounted(async()=>{ try{ live.value=Object.fromEntries((await api.get('/groups')).groups.filter(g=>g.live).map(g=>[g.tag,g])) }catch{ live.value={} } })
function liveText(g){
  const st=live.value[g.tag]
  if(!st) return ''
  const now=st.active ? 'активна '+st.active : st.fallback ? 'кандидатов нет — на '+st.fallback : 'нода не выбрана'
  return `Сейчас: ${now} · кандидатов ${st.candidates} из ${st.members} · резерв ${st.reserve.length}`
}
const KIND_NAMES={residential:'домашний',mobile:'мобильный',hosting:'дата-центр',proxy:'прокси'}
const COUNTRY_FIELDS=[['countries','Страны (ISO-коды)'],['regions','Регионы'],['exclude_countries','Кроме стран'],['exclude_regions','Кроме регионов']]
const BUILTIN=['eu','us','ru','other']
// Проверка белых списков ещё не выполняется тестером: обязательной её не предлагаем.
const checkList=computed(()=>props.checks.filter(c=>c!=='whitelist'))
const groups=computed(()=>props.doc.groups)
const regionList=computed(()=>[...new Set([...BUILTIN,...Object.keys(props.doc.regions || {})])])
function changed(){emit('changed')}
function add(){(props.doc.groups ||= []).push({tag:''});changed()}
function remove(i){if(window.confirm(`Удалить группу ${props.doc.groups[i].tag || i+1}?`)){props.doc.groups.splice(i,1);changed()}}
function move(i,d){const g=props.doc.groups;const j=i+d;if(j<0||j>=g.length)return;[g[i],g[j]]=[g[j],g[i]];changed()}
function flag(g,key,dflt){return g[key] ?? dflt}
function setFlag(g,key,e){g[key]=e.target.checked;changed()}
function setReserve(g,e){if(e.target.value==='')delete g.reserve; else g.reserve=Number(e.target.value);changed()}
// Проверка в группе: require (без свежего успеха нода не кандидат), prefer (влияет на выбор) или не используется.
function mode(g,check){return g.require?.[check] ? 'require' : g.prefer?.[check] ? 'prefer' : ''}
function params(g,check){return g.require?.[check] || g.prefer?.[check] || {}}
function setMode(g,check,m){
  const p=params(g,check)
  for(const key of ['require','prefer']){if(g[key]){delete g[key][check];if(!Object.keys(g[key]).length)delete g[key]}}
  if(m)(g[m] ||= {})[check]=p
  changed()
}
function setParam(g,check,key,list){
  const p=params(g,check)
  if(list.length)p[key]=list; else delete p[key]
  changed()
}
function listValue(e){return e.target.value.split(',').map(x=>x.trim()).filter(Boolean)}
function toggleKind(g,kind,on){const cur=params(g,'ip_type').kinds || [];setParam(g,'ip_type','kinds',on ? [...new Set([...cur,kind])] : cur.filter(k=>k!==kind))}
function unknown(g,key){
  const values=params(g,'country')[key] || []
  if(key.endsWith('regions'))return values.filter(v=>!regionList.value.includes(String(v).toLowerCase()))
  return values.filter(v=>!/^[a-z]{2}$/i.test(v))
}
function fallbackText(g){
  const direct=flag(g,'direct_fallback',false)
  if(flag(g,'strict',false))return direct ? 'без кандидатов — direct-out' : 'без кандидатов — block-out: трафик группы блокируется'
  return direct ? 'без кандидатов — failsafe-out; direct-out можно выбрать вручную' : 'без кандидатов — failsafe-out (лучшая нода по urltest)'
}
function setRegions(v){if(v && Object.keys(v).length)props.doc.regions=v; else delete props.doc.regions;changed()}
</script>
<template>
  <section class="panel">
    <h2>Группы нод</h2>
    <p class="help-text">Каждая группа — selector <code>{tag}-auto-out</code>, в котором все ноды. Активную ноду выбирает тестер среди прошедших обязательные проверки группы; желательные проверки повышают приоритет. Нестрогая группа без кандидатов переключается на общий <code>failsafe-out</code>, строгая — на <code>block-out</code>, чтобы трафик не ушёл через неподходящую ноду.</p>
    <p v-if="!groups?.length" class="notice">Группы не заданы: сборка создаст одну группу <code>all</code>.</p>
    <template v-if="groups">
      <div v-for="(g,i) in groups" :key="i" class="panel">
        <h3>{{ g.tag || 'Новая группа' }}<span v-if="g.tag" class="mut"> · {{ g.tag }}-auto-out · {{ fallbackText(g) }}</span></h3>
        <p v-if="liveText(g)" class="mut break">{{ liveText(g) }}<template v-if="live[g.tag].top.length"> · первые кандидаты: {{ live[g.tag].top.map(c=>c.node+' ('+c.score+')').join(', ') }}</template></p>
        <div class="form-grid">
          <label>Тег группы (латиница в нижнем регистре, цифры, _)<input :value="g.tag" @input="g.tag=$event.target.value.trim();changed()" /></label>
          <label>Кандидатов в резерве<input type="number" min="0" :value="g.reserve" placeholder="Как в настройках" @input="setReserve(g,$event)" /></label>
          <label class="check"><input type="checkbox" :checked="flag(g,'strict',false)" @change="setFlag(g,'strict',$event)" /> Строгая: без подходящей ноды — блокировать</label>
          <label class="check"><input type="checkbox" :checked="flag(g,'direct_fallback',false)" @change="setFlag(g,'direct_fallback',$event)" /> Разрешить прямой выход (direct-out)</label>
          <label class="check"><input type="checkbox" :checked="flag(g,'in_global',true)" @change="setFlag(g,'in_global',$event)" /> Входит в global-auto-out</label>
        </div>
        <table class="checks">
          <tr v-for="check in checkList" :key="check">
            <td>{{ CHECK_NAMES[check] || check }}</td>
            <td><select :value="mode(g,check)" @change="setMode(g,check,$event.target.value)"><option value="">не учитывать</option><option value="require">обязательна</option><option value="prefer">желательна</option></select></td>
            <td>
              <div v-if="check==='country' && mode(g,check)" class="form-grid">
                <label v-for="[key,label] in COUNTRY_FIELDS" :key="key">{{ label }}<input :value="(params(g,check)[key] || []).join(', ')" placeholder="через запятую" @change="setParam(g,check,key,listValue($event))" />
                  <small v-if="unknown(g,key).length" class="bad">{{ key.endsWith('regions') ? 'Нет такого региона' : 'Нужен двухбуквенный ISO-код' }}: {{ unknown(g,key).join(', ') }}</small></label>
              </div>
              <div v-else-if="check==='ip_type' && mode(g,check)" class="toolbar">
                <label v-for="kind in ipKinds" :key="kind" class="check"><input type="checkbox" :checked="(params(g,check).kinds || []).includes(kind)" @change="toggleKind(g,kind,$event.target.checked)" /> {{ KIND_NAMES[kind] || kind }}</label>
              </div>
            </td>
          </tr>
        </table>
        <div class="toolbar">
          <button class="btn sm" :disabled="i===0" @click="move(i,-1)">Выше</button>
          <button class="btn sm" :disabled="i===groups.length-1" @click="move(i,1)">Ниже</button>
          <button class="btn sm" @click="remove(i)">Удалить группу</button>
        </div>
      </div>
      <p class="help-text">Регионы: {{ regionList.join(', ') }}. Порядок групп — порядок в конфиге и в global-auto-out. Страна выхода берётся из замера соединения, а не из названия ноды у провайдера.</p>
    </template>
    <div class="toolbar"><button class="btn" @click="add">Добавить группу</button></div>
  </section>
  <section class="panel">
    <h2>Регионы</h2>
    <p class="help-text">Регион — именованный список стран для условий групп. Встроенные: eu — {{ (builtin.eu || []).join(', ') }}; us — us; ru — ru; other — страны вне eu, us и ru. Встроенный регион можно переопределить, указав его здесь; свои регионы добавляются так же.</p>
    <JsonField label="Свои и переопределённые регионы" note='Объект: регион → список стран. Например: {"asia": ["jp", "sg", "kr"]}.' :model-value="doc.regions || {}" @update:model-value="setRegions" @valid="emit('valid',$event)" />
  </section>
</template>
<style scoped>
.checks { width: 100%; border-collapse: collapse; margin: 8px 0; }
.checks td { padding: 4px 8px 4px 0; vertical-align: top; text-align: left; border-top: 1px solid var(--line); }
.checks td:first-child { white-space: nowrap; }
</style>
