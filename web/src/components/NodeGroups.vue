<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../api.js'
import { CHECK_NAMES } from '../ux.js'
import { t } from '../i18n.js'
import JsonField from './JsonField.vue'
import Msg from './Msg.vue'
import HelpButton from './HelpButton.vue'
const props=defineProps({doc:{type:Object,required:true},checks:{type:Array,default:()=>[]},ipKinds:{type:Array,default:()=>[]},builtin:{type:Object,default:()=>({})},reserveDefault:{type:Number,default:null}})
const emit=defineEmits(['changed','valid'])
// Живое состояние групп работающего тестера (по сохранённому конфигу, не по черновику формы).
const live=ref({})
onMounted(async()=>{ try{ live.value=Object.fromEntries((await api.get('/groups')).groups.filter(g=>g.live).map(g=>[g.tag,g])) }catch{ live.value={} } })
function liveText(g){
  const st=live.value[g.tag]
  if(!st) return ''
  const now=st.active ? t('active {node}',{node:st.active}) : st.fallback ? t('no candidates, on {outbound}',{outbound:st.fallback}) : t('no node selected')
  return t('Now: {now} · {candidates} of {members} candidates · reserve {reserve}',{now,candidates:st.candidates,members:st.members,reserve:st.reserve.length})
}
const KIND_NAMES={get residential(){return t('residential')},get mobile(){return t('mobile')},get hosting(){return t('data centre')},get proxy(){return t('proxy')}}
const COUNTRY_FIELDS=computed(()=>[['countries',t('Countries (ISO codes)')],['regions',t('Regions')],['exclude_countries',t('Except countries')],['exclude_regions',t('Except regions')]])
const BUILTIN=['eu','us','ru','other']
const groups=computed(()=>props.doc.groups)
// Подписки ветви роутера: их можно исключить из группы.
const routerSubs=computed(()=>(props.doc.subscriptions || []).filter(s=>(s.branch || 'router')==='router' && s.tag).map(s=>s.tag))
const regionList=computed(()=>[...new Set([...BUILTIN,...Object.keys(props.doc.regions || {})])])
function changed(){emit('changed')}
function add(){(props.doc.groups ||= []).push({tag:''});changed()}
function remove(i){if(window.confirm(t('Delete group {group}?',{group:props.doc.groups[i].tag || i+1}))){props.doc.groups.splice(i,1);changed()}}
function move(i,d){const g=props.doc.groups;const j=i+d;if(j<0||j>=g.length)return;[g[i],g[j]]=[g[j],g[i]];changed()}
function flag(g,key,dflt){return g[key] ?? dflt}
function setFlag(g,key,e){g[key]=e.target.checked;changed()}
function toggleSub(g,tag,on){
  const cur=(g.exclude_subscriptions || []).filter(x=>x!==tag)
  if(on)cur.push(tag)
  if(cur.length)g.exclude_subscriptions=cur; else delete g.exclude_subscriptions
  changed()
}
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
  if(flag(g,'strict',false))return direct ? t('without candidates: direct-out') : t('without candidates: block-out, the group traffic is blocked')
  return direct ? t('without candidates: failsafe-out; if it does not respond either, the tester switches the group to direct-out') : t('without candidates: failsafe-out (the best node by urltest)')
}
function setRegions(v){if(v && Object.keys(v).length)props.doc.regions=v; else delete props.doc.regions;changed()}
</script>
<template>
  <section class="panel">
    <h2>{{ t('Node groups') }}<HelpButton topic="subscriptions#groups" /></h2>
    <p v-if="!groups?.length" class="notice"><Msg :text="t('No groups defined: the build will create a single group {all}.')"><template #all><code>all</code></template></Msg></p>
    <template v-if="groups">
      <div v-for="(g,i) in groups" :key="i" class="panel">
        <h3>{{ g.tag || t('New group') }}<span v-if="g.tag" class="mut"> · {{ g.tag }}-auto-out · {{ fallbackText(g) }}</span></h3>
        <p v-if="liveText(g)" class="mut break">{{ liveText(g) }}<template v-if="live[g.tag].top.length"> · {{ t('first candidates') }}: {{ live[g.tag].top.map(c=>c.node+' ('+c.score+')').join(', ') }}</template></p>
        <div class="form-grid">
          <label>{{ t('Group tag (lowercase Latin letters, digits, _)') }}<input :value="g.tag" @input="g.tag=$event.target.value.trim();changed()" /></label>
          <label>{{ t('Reserve candidates') }}<input type="number" min="0" :value="g.reserve" :placeholder="reserveDefault ?? ''" :title="t('Empty: as in the settings (selection.reserve)')" @input="setReserve(g,$event)" /></label>
          <label class="check"><input type="checkbox" :checked="flag(g,'strict',false)" @change="setFlag(g,'strict',$event)" /> {{ t('Strict: block when no node fits') }}</label>
          <label class="check"><input type="checkbox" :checked="flag(g,'direct_fallback',false)" @change="setFlag(g,'direct_fallback',$event)" /> {{ t('Allow direct outbound (direct-out)') }}</label>
          <label class="check"><input type="checkbox" :checked="flag(g,'in_global',true)" @change="setFlag(g,'in_global',$event)" /> {{ t('Part of global-auto-out') }}</label>
        </div>
        <div v-if="routerSubs.length" class="toolbar">
          <span>{{ t('Except subscriptions') }}:</span>
          <label v-for="tag in routerSubs" :key="tag" class="check"><input type="checkbox" :checked="(g.exclude_subscriptions || []).includes(tag)" @change="toggleSub(g,tag,$event.target.checked)" /> {{ tag }}</label>
        </div>
        <table class="checks">
          <tr v-for="check in checks" :key="check">
            <td>{{ CHECK_NAMES[check] || check }}</td>
            <td><select :value="mode(g,check)" @change="setMode(g,check,$event.target.value)"><option value="">{{ t('ignored') }}</option><option value="require">{{ t('required') }}</option><option value="prefer">{{ t('preferred') }}</option></select></td>
            <td>
              <div v-if="check==='country' && mode(g,check)" class="form-grid">
                <label v-for="[key,label] in COUNTRY_FIELDS" :key="key">{{ label }}<input :value="(params(g,check)[key] || []).join(', ')" :placeholder="t('comma-separated')" @change="setParam(g,check,key,listValue($event))" />
                  <small v-if="unknown(g,key).length" class="bad">{{ key.endsWith('regions') ? t('No such region') : t('A two-letter ISO code is required') }}: {{ unknown(g,key).join(', ') }}</small></label>
              </div>
              <div v-else-if="check==='ip_type' && mode(g,check)" class="toolbar">
                <label v-for="kind in ipKinds" :key="kind" class="check"><input type="checkbox" :checked="(params(g,check).kinds || []).includes(kind)" @change="toggleKind(g,kind,$event.target.checked)" /> {{ KIND_NAMES[kind] || kind }}</label>
              </div>
            </td>
          </tr>
        </table>
        <div class="toolbar">
          <button class="btn sm" :disabled="i===0" @click="move(i,-1)">{{ t('Up') }}</button>
          <button class="btn sm" :disabled="i===groups.length-1" @click="move(i,1)">{{ t('Down') }}</button>
          <button class="btn sm" @click="remove(i)">{{ t('Delete group') }}</button>
        </div>
      </div>
    </template>
    <div class="toolbar"><button class="btn" @click="add">{{ t('Add group') }}</button></div>
  </section>
  <section class="panel">
    <h2>{{ t('Regions') }}<HelpButton topic="subscriptions#regions" /></h2>
    <JsonField :label="t('Custom and overridden regions')" :note="t('Object: region → list of countries. For example: {example}.', { example: '{&quot;asia&quot;: [&quot;jp&quot;, &quot;sg&quot;, &quot;kr&quot;]}' })" :model-value="doc.regions || {}" @update:model-value="setRegions" @valid="emit('valid',$event)" />
  </section>
</template>
<style scoped>
.checks { width: 100%; border-collapse: collapse; margin: 8px 0; }
.checks td { padding: 4px 8px 4px 0; vertical-align: top; text-align: left; border-top: 1px solid var(--line); }
.checks td:first-child { white-space: nowrap; }
</style>
