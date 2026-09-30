<script setup>
import { computed } from 'vue'
import JsonField from './JsonField.vue'
const props=defineProps({doc:{type:Object,required:true},builtin:{type:Object,default:()=>({})}})
const emit=defineEmits(['changed','valid'])
const CONDITIONS=[
  ['countries','Страны (ISO-коды)'],['regions','Регионы'],['labels','Метки'],
  ['exclude_countries','Кроме стран'],['exclude_regions','Кроме регионов'],['exclude_labels','Кроме меток'],
]
const LEGACY=['eu','us','ru','other']
const groups=computed(()=>props.doc.groups)
const regionNames=computed(()=>[...new Set([...LEGACY,...Object.keys(props.doc.regions || {})])].join(', '))
const labelNames=computed(()=>Object.keys(props.doc.rename?.labels || {}).join(', ') || 'нет')
function changed(){emit('changed')}
function legacyGroups(){
  const ensure=props.doc.emit?.ensure_regions ?? ['eu','us','other']
  return LEGACY.map(r=>({name:r,enabled:true,in_global:true,fallback:ensure.includes(r),match:{regions:[r]}}))
}
function init(){props.doc.groups=legacyGroups();changed()}
function reset(){if(window.confirm('Удалить список групп и вернуть прежнюю схему eu, us, ru, other?')){delete props.doc.groups;changed()}}
function add(){(props.doc.groups ||= []).push({name:'',enabled:true,in_global:false,fallback:true,match:{}});changed()}
function remove(i){if(window.confirm(`Удалить группу ${props.doc.groups[i].name || i+1}?`)){props.doc.groups.splice(i,1);changed()}}
function move(i,d){const g=props.doc.groups;const j=i+d;if(j<0||j>=g.length)return;[g[i],g[j]]=[g[j],g[i]];changed()}
function flag(g,key,dflt){return g[key] ?? dflt}
function setFlag(g,key,e){g[key]=e.target.checked;changed()}
function cond(g,key){return (g.match?.[key] || []).join(', ')}
function setCond(g,key,e){
  const v=e.target.value.split(',').map(x=>x.trim()).filter(Boolean)
  g.match ||= {}
  if(v.length)g.match[key]=v; else delete g.match[key]
  changed()
}
function setRegions(v){if(v && Object.keys(v).length)props.doc.regions=v; else delete props.doc.regions;changed()}
</script>
<template>
  <section class="panel">
    <h2>Группы нод</h2>
    <p class="mut">Для каждой группы сборка создаёт selector <code>{имя}-auto-out</code> и urltest <code>{имя}-auto-out-failsafe</code>. Тестер выбирает активную ноду в каждой группе отдельно. Нода может входить в несколько групп. Условия объединяются через И, значения внутри условия — через ИЛИ; без условий в группу попадают все ноды.</p>
    <template v-if="!groups">
      <p>Список групп не задан: действует прежняя схема — группы eu, us, ru и other по регионам нод, обязательные группы задаются ниже в «Создаваемых группах».</p>
      <button class="btn" @click="init">Настроить группы</button>
    </template>
    <template v-else>
      <div v-for="(g,i) in groups" :key="i" class="panel">
        <h3>{{ g.name || 'Новая группа' }}<span v-if="g.name" class="mut"> · {{ g.name }}-auto-out</span><span v-if="g.enabled===false" class="mut"> · не создаётся</span></h3>
        <div class="form-grid">
          <label>Имя группы (латиница, цифры, _)<input :value="g.name" @input="g.name=$event.target.value.trim();changed()" /></label>
          <label class="check"><input type="checkbox" :checked="flag(g,'enabled',true)" @change="setFlag(g,'enabled',$event)" /> Создавать группу</label>
          <label class="check"><input type="checkbox" :checked="flag(g,'in_global',true)" @change="setFlag(g,'in_global',$event)" /> Входит в global-auto-out</label>
          <label class="check"><input type="checkbox" :checked="flag(g,'fallback',true)" @change="setFlag(g,'fallback',$event)" /> Если нод нет — заполнить всеми нодами</label>
        </div>
        <div class="form-grid">
          <label v-for="[key,label] in CONDITIONS" :key="key">{{ label }}<input :value="cond(g,key)" placeholder="через запятую" @change="setCond(g,key,$event)" /></label>
        </div>
        <div class="toolbar">
          <button class="btn sm" :disabled="i===0" @click="move(i,-1)">Выше</button>
          <button class="btn sm" :disabled="i===groups.length-1" @click="move(i,1)">Ниже</button>
          <button class="btn sm" @click="remove(i)">Удалить группу</button>
        </div>
      </div>
      <p class="mut">Регионы: {{ regionNames }}. Метки из переименования: {{ labelNames }}. Порядок групп — порядок в конфиге и в global-auto-out.</p>
      <div class="toolbar">
        <button class="btn" @click="add">Добавить группу</button>
        <button class="btn" @click="reset">Вернуть прежнюю схему</button>
      </div>
    </template>
  </section>
  <section class="panel">
    <h2>Регионы</h2>
    <p class="mut">Регион — именованный список стран для условий групп. Встроенные: eu — {{ (builtin.eu || []).join(', ') }}; us — us; ru — ru; other — страны вне eu, us и ru. Встроенный регион можно переопределить, указав его здесь; свои регионы добавляются так же.</p>
    <JsonField label="Свои и переопределённые регионы" note='Объект: регион → список стран. Например: {"asia": ["jp", "sg", "kr"]}.' :model-value="doc.regions || {}" @update:model-value="setRegions" @valid="emit('valid',$event)" />
  </section>
</template>
