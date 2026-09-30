<script setup>
import { computed, ref, watch } from 'vue'
import { api } from '../api.js'
import JsonField from './JsonField.vue'
const props=defineProps({doc:{type:Object,required:true},builtin:{type:Object,default:()=>({})}})
const emit=defineEmits(['changed','valid'])
const CONDITIONS=[
  ['countries','Страны (ISO-коды)'],['regions','Регионы'],['labels','Метки'],
  ['exclude_countries','Кроме стран'],['exclude_regions','Кроме регионов'],['exclude_labels','Кроме меток'],
]
const BUILTIN=['eu','us','ru','other']
const groups=computed(()=>props.doc.groups)
const regionList=computed(()=>[...new Set([...BUILTIN,...Object.keys(props.doc.regions || {})])])
const labelList=computed(()=>Object.keys(props.doc.rename?.labels || {}))
const regionNames=computed(()=>regionList.value.join(', '))
const labelNames=computed(()=>labelList.value.join(', ') || 'нет')
// Предпросмотр: оценка состава по текущему raw/main.json; после правки формы устаревает.
const preview=ref(null), previewError=ref(''), previewBusy=ref(false)
const byName=computed(()=>Object.fromEntries((preview.value?.groups || []).map(g=>[g.name,g])))
function changed(){preview.value=null;emit('changed')}
watch(()=>JSON.stringify(props.doc),()=>{preview.value=null})
async function estimate(){
  previewBusy.value=true;previewError.value=''
  try{preview.value=await api.post('/config/groups/preview',JSON.parse(JSON.stringify(props.doc)))}
  catch(e){previewError.value=e.message}
  finally{previewBusy.value=false}
}
// Неизвестные регионы и метки — подсказка рядом с полем (сервер их тоже отклонит или не найдёт).
function unknown(g,key){
  const values=g.match?.[key] || []
  if(key.endsWith('regions'))return values.filter(v=>!regionList.value.includes(String(v).toLowerCase()))
  if(key.endsWith('labels'))return values.filter(v=>!labelList.value.includes(v))
  return values.filter(v=>!/^[a-z]{2}$/i.test(v))
}
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
    <p v-if="!groups?.length" class="notice">Группы не заданы. Добавьте хотя бы одну группу.</p>
    <template v-if="groups">
      <div v-for="(g,i) in groups" :key="i" class="panel">
        <h3>{{ g.name || 'Новая группа' }}<span v-if="g.name" class="mut"> · {{ g.name }}-auto-out</span><span v-if="g.enabled===false" class="mut"> · не создаётся</span></h3>
        <div class="form-grid">
          <label>Имя группы (латиница, цифры, _)<input :value="g.name" @input="g.name=$event.target.value.trim();changed()" /></label>
          <label class="check"><input type="checkbox" :checked="flag(g,'enabled',true)" @change="setFlag(g,'enabled',$event)" /> Создавать группу</label>
          <label class="check"><input type="checkbox" :checked="flag(g,'in_global',true)" @change="setFlag(g,'in_global',$event)" /> Входит в global-auto-out</label>
          <label class="check"><input type="checkbox" :checked="flag(g,'fallback',true)" @change="setFlag(g,'fallback',$event)" /> Если нод нет — заполнить всеми нодами</label>
        </div>
        <div class="form-grid">
          <label v-for="[key,label] in CONDITIONS" :key="key">{{ label }}<input :value="cond(g,key)" placeholder="через запятую" @change="setCond(g,key,$event)" />
            <small v-if="unknown(g,key).length" class="bad">{{ key.endsWith('regions') ? 'Нет такого региона' : key.endsWith('labels') ? 'Нет такой метки' : 'Нужен двухбуквенный ISO-код' }}: {{ unknown(g,key).join(', ') }}</small></label>
        </div>
        <div v-if="preview && g.name && g.enabled !== false" class="group-preview">
          <template v-if="byName[g.name]">
            <p><b>{{ byName[g.name].count }}</b> нод<template v-if="Object.keys(byName[g.name].overlaps).length"> · пересечения: {{ Object.entries(byName[g.name].overlaps).map(([o,n])=>o+' '+n).join(', ') }}</template></p>
            <p v-if="byName[g.name].fallback" class="notice bad">Условиям не подошла ни одна нода: сработает fallback, группа получит все ноды.</p>
            <p v-if="byName[g.name].samples.length" class="mut break">Например: {{ byName[g.name].samples.join(', ') }}</p>
          </template>
          <p v-else class="notice bad">Группа не будет создана: подходящих нод нет, а fallback выключен.</p>
        </div>
        <div class="toolbar">
          <button class="btn sm" :disabled="i===0" @click="move(i,-1)">Выше</button>
          <button class="btn sm" :disabled="i===groups.length-1" @click="move(i,1)">Ниже</button>
          <button class="btn sm" @click="remove(i)">Удалить группу</button>
        </div>
      </div>
      <p class="mut">Регионы: {{ regionNames }}. Метки из переименования: {{ labelNames }}. Порядок групп — порядок в конфиге и в global-auto-out.</p>
    </template>
    <div class="toolbar">
      <button class="btn" @click="add">Добавить группу</button>
      <button class="btn" :disabled="previewBusy || !groups?.length" @click="estimate">Оценить состав групп</button>
    </div>
    <p v-if="previewError" class="notice bad">{{ previewError }}</p>
    <div v-if="preview" class="notice">
      <p>Оценка по текущим данным подписок ({{ new Date(preview.raw_mtime*1000).toLocaleString('ru-RU') }}): нод {{ preview.leaf_nodes }}, вне групп {{ preview.ungrouped }}. Новая загрузка подписок может изменить результат.</p>
      <p v-if="preview.current_known && (preview.added.length || preview.removed.length)">По сравнению с текущим nodes.json<template v-if="preview.added.length"> появятся: <b>{{ preview.added.join(', ') }}</b></template><template v-if="preview.removed.length"><template v-if="preview.added.length">;</template> исчезнут: <b class="bad">{{ preview.removed.join(', ') }}</b></template>.</p>
      <p v-else-if="preview.current_known">Набор групп совпадает с текущим nodes.json.</p>
    </div>
  </section>
  <section class="panel">
    <h2>Регионы</h2>
    <p class="mut">Регион — именованный список стран для условий групп. Встроенные: eu — {{ (builtin.eu || []).join(', ') }}; us — us; ru — ru; other — страны вне eu, us и ru. Встроенный регион можно переопределить, указав его здесь; свои регионы добавляются так же.</p>
    <JsonField label="Свои и переопределённые регионы" note='Объект: регион → список стран. Например: {"asia": ["jp", "sg", "kr"]}.' :model-value="doc.regions || {}" @update:model-value="setRegions" @valid="emit('valid',$event)" />
  </section>
</template>
<style scoped>
.group-preview { border-top: 1px solid var(--line); padding-top: 8px; }
.group-preview p { margin: 4px 0; }
</style>
