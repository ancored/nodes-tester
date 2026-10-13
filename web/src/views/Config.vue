<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { useRoute, onBeforeRouteLeave } from 'vue-router'
import { api, auth, can } from '../api.js'
import { getPath, setPath, changedPaths } from '../ux.js'
import { bytes, dateTime } from '../format.js'
import JsonField from '../components/JsonField.vue'
import NodeGroups from '../components/NodeGroups.vue'
const route = useRoute(), subscriptions = computed(()=>route.path === '/subscriptions')
const selected=ref('providers'), branch=ref('router'), options=ref({user_agents:[],happ_headers:{}}), jsonValid=ref({}), customAgents=ref(new WeakSet())
const recipe=computed(()=>subscriptions.value && selected.value==='build')
const document = ref(null), original = ref(null), text = ref(''), advanced = ref(false)
const path = ref(''), revision = ref(''), busy = ref(false), message = ref(''), error = ref('')
const preview = ref(false), showSecrets = ref(false), restart = ref(false), conflict = ref(false)
// Один файл config.json: подписки, сборка, группы и настройки тестера — разные страницы одного документа.
const endpoint = '/config'
const BRANCHES={router:'Роутер',clients:'Клиенты'}, SUB_BRANCHES={router:'Роутер',clients:'Клиенты',both:'Обе ветви'}
const candidate = computed(()=>{ if(!advanced.value) return Object.values(jsonValid.value).every(Boolean) ? document.value : null; try{return JSON.parse(text.value)}catch{return null} })
const changed = computed(()=>changedPaths(original.value,candidate.value))
const dirty = computed(()=>document.value !== null && (advanced.value ? text.value !== JSON.stringify(original.value,null,2) : changed.value.length > 0 || !candidate.value))
watch(dirty,v=>{auth.dirty = v})
function discardOK() { return !dirty.value || window.confirm('Есть несохранённые изменения. Отбросить их?') }
onBeforeRouteLeave(()=>discardOK())
function beforeUnload(e) { if(dirty.value){e.preventDefault(); e.returnValue=''} }
onMounted(()=>{window.addEventListener('beforeunload',beforeUnload); if(can('edit_config')) load()})
onUnmounted(()=>{window.removeEventListener('beforeunload',beforeUnload); auth.dirty=false})
watch(()=>auth.verified,verified=>{ if(verified) load(); else { document.value=null; original.value=null; text.value=''; preview.value=false; showSecrets.value=false } })
async function load() {
  if(!can('edit_config') || !discardOK()) return
  const epoch = auth.epoch; busy.value=true; error.value=''; message.value=''
  try {
    const data=await api.get(endpoint)
    if(epoch !== auth.epoch) return
    document.value=data.data; original.value=JSON.parse(JSON.stringify(data.data))
    text.value=JSON.stringify(data.data,null,2); path.value=data.path; revision.value=data.revision
    restart.value=!!data.restart_required; preview.value=false; conflict.value=false
    jsonValid.value={}
    if(subscriptions.value){const opts=await api.get('/config/subscription-options');if(epoch===auth.epoch)options.value=opts}
  } catch(e) {error.value=e.message}
  finally {busy.value=false}
}
// Ветвь и раздел — только фильтр формы: документ тот же, несохранённые правки сохраняются.
function selectDocument(kind,set=branch.value) {selected.value=kind;branch.value=set}
function switchEditor() {
  if(!advanced.value && !candidate.value){error.value='Исправьте JSON в полях формы перед сменой редактора';return}
  if(advanced.value) {
    try { const parsed=JSON.parse(text.value); if(!parsed || Array.isArray(parsed) || typeof parsed !== 'object') throw new Error('Нужен JSON-объект'); document.value=parsed; advanced.value=false; error.value='' }
    catch(e){error.value=e.message}
  } else {text.value=JSON.stringify(document.value,null,2); advanced.value=true; showSecrets.value=false}
  preview.value=false
}
function value(field) {return getPath(document.value,field)}
function update(field,event,type='text') {
  let v = type === 'checkbox' ? event.target.checked : type === 'number' ? (event.target.value === '' ? undefined : Number(event.target.value)) : event.target.value
  if(type === 'list') v = v.split(',').map(x=>x.trim()).filter(Boolean)
  if(type === 'duration') v = v.trim() || undefined
  setPath(document.value,field,v); preview.value=false
}
// Тестер перезапускается отвязанным процессом через секунду; ждём, пока админка снова ответит.
async function waitRestart(epoch,suffix) {
  const pause=ms=>new Promise(r=>setTimeout(r,ms))
  await pause(5000)
  for(let i=0;i<30;i++) {
    try { await api.get('/capabilities'); if(epoch===auth.epoch){message.value='Тестер перезапущен, новые настройки действуют.'+suffix;restart.value=false}; return }
    catch { await pause(2000) }
  }
  if(epoch===auth.epoch) error.value='Тестер не ответил за минуту после перезапуска. Журнал: logread -e nodes-tester'
}
async function save() {
  if(!can('edit_config') || !candidate.value) {error.value='Проверьте JSON: нужен корректный объект'; return}
  busy.value=true; error.value=''; const epoch=auth.epoch
  const data=JSON.parse(JSON.stringify(candidate.value))
  try {
    // Форма сохраняет только изменённые разделы: правка подписок не конфликтует с правкой групп в
    // другом окне. Расширенный JSON — весь файл по ревизии.
    const sections=[...new Set(changed.value.map(p=>p.split('.')[0]))]
    const result=advanced.value ? await api.put(endpoint,data,{'If-Match':revision.value})
      : await api.post('/config/sections',{base:Object.fromEntries(sections.map(k=>[k,original.value[k] ?? null])),
                                            set:Object.fromEntries(sections.map(k=>[k,data[k] ?? null]))})
    if(epoch !== auth.epoch) return
    const saved=advanced.value ? {data,revision:result.revision} : await api.get(endpoint)
    if(epoch !== auth.epoch) return
    document.value=saved.data; original.value=JSON.parse(JSON.stringify(saved.data)); text.value=JSON.stringify(saved.data,null,2)
    revision.value=saved.revision; restart.value=!!result.restart_required; preview.value=false
    const sb=subscriptions.value ? ' Загрузка подписок, сборка и применение к sing-box не запускались.' : ''
    message.value=result.restarting ? 'config.json сохранён. Тестер перезапускается…'+sb : 'config.json сохранён; '+(result.restart_note || 'перезапустите тестер')+'.'+sb
    if(result.restarting) waitRestart(epoch,sb)
  } catch(e){error.value=e.message; if(e.status === 409) conflict.value=true}
  finally{busy.value=false}
}
// Длительности — строки как в sing-box: 30s, 5m, 3h, 2d.
const groups = [
  {title:'Соединение с sing-box',note:'API управляет селекторами, а SOCKS-соединение служит маршрутом тестового трафика. Админка не проверяет соединение при сохранении.',fields:[
    ['api.url','Адрес API sing-box','text'],['api.secret','Секрет API sing-box','password'],['api.timeout','Тайм-аут API (например, 5s)','duration'],
    ['tester.socks','SOCKS тестера (socks5h://хост:порт)','text'],['tester.selector','Тестовый селектор','text']]},
  {title:'Проходы тестера',note:'Тестер замеряет все ноды; группы выбирают из них по своим проверкам (страница «Подписки» → «Сборка и группы»).',fields:[
    ['tester.follow_rotation','Привязать проходы к ротации','checkbox'],['tester.pause','Пауза между проходами (например, 0s)','duration'],
    ['tester.host_gap','Зазор обращений к одному хосту (например, 2m)','duration'],
    ['tester.request_timeout','Тайм-аут проверки (например, 10s)','duration'],['selection.reserve','Кандидатов в резерве группы','number']]},
  {title:'Переключение и наблюдение',note:'Включение ротации может изменить выбор боевых селекторов после перезапуска тестера.',fields:[
    ['selection.rotation.enabled','Ротация','checkbox'],['selection.rotation.interval','Интервал ротации (например, 3h)','duration'],
    ['selection.rotation.min_dwell','Минимальное время на ноде (например, 30m)','duration'],['monitor.interval','Тик монитора активных нод (например, 20s)','duration'],
    ['selection.quarantine.duration','Длительность карантина (например, 3d)','duration'],['selection.quarantine.after_skips','Карантин после пропусков проходов','number']]},
  {title:'Админка и хранение',note:'Смена токена, адреса или порта потребует перезапуска админки. Форма редактирует файл, не настройки текущего процесса.',fields:[
    ['admin.enabled','Встроенная админка','checkbox'],['admin.listen','Адрес прослушивания','text'],['admin.port','Порт админки','number'],
    ['admin.token','Токен администратора','password'],['admin.public_read','Просмотр без токена','checkbox'],
    ['storage.path','Файл SQLite (относительно каталога данных)','text'],['storage.retention','Хранить историю (например, 30d)','duration'],
    ['storage.traffic.enabled','Собирать пользовательский трафик','checkbox']]},
  {title:'Уведомления',note:'Telegram и/или webhook. Новые настройки действуют после перезапуска тестера. Если напрямую с роутера отправить не удалось (Telegram часто режется провайдером), сообщение уходит через тестер на лучшей здоровой ноде. Переключения нод с причиной «ротация» происходят часто — по умолчанию сообщаются только аварийные.',fields:[
    ['notify.enabled','Отправлять уведомления','checkbox'],['notify.name','Подпись роутера в сообщении','text'],
    ['notify.telegram.token','Токен Telegram-бота','password'],['notify.telegram.chat_id','Telegram chat_id получателя','text'],
    ['notify.webhook.url','Webhook URL (POST JSON)','text'],['notify.proxy','Прокси для отправки (пусто — напрямую)','text'],
    ['notify.expiry_warning','Предупреждать о конце подписки за (например, 3d)','duration']]},
  {title:'Тип выходного IP',note:'Тип IP (дата-центр, домашний, мобильный) запрашивается у ip-api.com по выходным IP нод — через тестер на лучшей здоровой ноде, иначе напрямую.',fields:[
    ['checks.ip_type.ttl','Обновлять сведения раз в (например, 1d)','duration'],['checks.ip_type.via_tester','Запрашивать через тестер','checkbox']]},
]
// Флажки, которые загрузчик конфига считает включёнными, если поля нет в файле.
const ON_BY_DEFAULT=new Set(['tester.follow_rotation','selection.rotation.enabled','admin.enabled','admin.public_read','storage.traffic.enabled','notify.enabled','checks.ip_type.via_tester'])
const NOTIFY_EVENTS={switch:'Переключение ноды',failsafe:'Запасная группа',rollback:'Откат конфига sing-box',pipeline:'Ошибка конвейера',subscription:'Сбой подписки',expiry:'Конец подписки и трафика',singbox:'Остановка и сбой sing-box'}
const SWITCH_REASONS={emergency:'авария',['emergency-stuck']:'авария без замены',rotation:'ротация',quality:'качество',manual:'вручную',init:'первый выбор'}
const DEFAULT_EVENTS=Object.keys(NOTIFY_EVENTS), DEFAULT_REASONS=['emergency','emergency-stuck']
function toggleList(field,item,on,defaults){const cur=[...(value(field) ?? defaults)];const next=on?[...new Set([...cur,item])]:cur.filter(x=>x!==item);setPath(document.value,field,next);preview.value=false}
const notifyBusy=ref(false), notifyResult=ref('')
async function notifyTest(){notifyBusy.value=true;notifyResult.value='';try{const r=await api.post('/notify/test',{});notifyResult.value=r.ok?'Отправлено. Проверьте Telegram/webhook.':'Не отправлено: '+(r.error||'ошибка')}catch(e){notifyResult.value=e.message}finally{notifyBusy.value=false}}
function subState(sub){return (options.value.sources || {})[sub.tag] || null}
function subMeta(sub){return subState(sub)?.meta || {}}
function daysLeft(m){return m.expire ? Math.ceil((m.expire*1000-Date.now())/86400000) : null}
function trafficLine(m){if(m.download==null && m.total==null)return '';const used=(m.upload||0)+(m.download||0);return m.total ? `${bytes(used)} из ${bytes(m.total)}` : `${bytes(used)}, без лимита`}
function lastOk(st){return st?.last_ok_at ? dateTime(Date.parse(st.last_ok_at)/1000) : 'нет'}
function setHappKeys(v) {v=v.trim();setPath(document.value,'pipeline.fetch.happ_keys_url',v && v!==options.value.happ_keys?.default_url ? v : undefined);preview.value=false}
function sourceKind(sub) {return ['folder','inline'].includes(sub.type) ? sub.type : sub.file != null ? 'file' : String(sub.url || '').trim().startsWith('happ://crypt') ? 'happ' : 'url'}
function changeKind(sub,kind) {
  delete sub.url; delete sub.file; delete sub.type; delete sub.path; delete sub.outbounds
  if(kind === 'folder'){sub.type='folder';sub.path=''; if(!sub.format)sub.format='awg'}
  else if(kind === 'inline'){sub.type='inline';sub.outbounds=[]}
  else if(kind==='happ')sub.url='happ://crypt/'
  else sub[kind]=''
  preview.value=false
}
// Подписки текущей ветви с индексами в общем списке (branch both — в обеих).
const branchSubs=computed(()=>(document.value?.subscriptions || []).map((sub,index)=>({sub,index})).filter(({sub})=>[branch.value,'both'].includes(sub.branch || 'router')))
function setBranch(sub,v){if(v==='router')delete sub.branch; else sub.branch=v;preview.value=false}
function setList(sub,key,v){const list=v.split(',').map(x=>x.trim()).filter(Boolean);if(list.length)sub[key]=list; else delete sub[key];preview.value=false}
function addSub(){(document.value.subscriptions ||= []).push(branch.value==='router' ? {tag:'',url:''} : {tag:'',url:'',branch:branch.value})}
function userAgent(sub){return sub.user_agent ?? ''}
function uaChoice(sub){const v=userAgent(sub);return customAgents.value.has(sub) ? '__custom' : options.value.user_agents.some(o=>o.value===v) ? v : '__custom'}
function setUA(sub,v,selection=false){if(v==='__custom' && selection){customAgents.value.add(sub);return}if(selection)customAgents.value.delete(sub);sub.user_agent=v;preview.value=false}
function happFields(sub){return [...new Set([...Object.keys(options.value.happ_headers),...Object.keys(sub.happ_headers || {})])]}
function setHeader(sub,key,v){(sub.happ_headers ||= {})[key]=v;preview.value=false}
function removeHeader(sub,key){if(sub.happ_headers)delete sub.happ_headers[key];preview.value=false}
function addHeader(sub){const key=window.prompt('Имя дополнительного HTTP-заголовка');if(key?.trim())setHeader(sub,key.trim(),'')}
// Сборка nodes.json ветви (pipeline.<ветвь>): фильтры против падения sing-box и параметры групп.
const recipeGroups=[
  {title:'Расписание и фильтры',fields:[['enabled','Ветвь включена (сборка по расписанию)','checkbox'],['at','Время запуска, ЧЧ:ММ (через запятую)','list'],
    ['exclude_types','Исключить типы outbound (через запятую)','list'],['exclude_protocols','Исключить протоколы / транспорт (через запятую)','list'],['domain_resolver','Тег DNS resolver нод','text']]},
  {title:'Группы selector',fields:[['selector.interrupt_exist_connections','Прерывать существующие соединения при смене ноды','checkbox']]},
  {title:'Группа failsafe-out (urltest)',fields:[['urltest.url','URL проверки','text'],['urltest.interval','Интервал (например, 5m)','duration'],['urltest.tolerance','Допуск задержки, мс','number'],['urltest.idle_timeout','Тайм-аут бездействия (например, 5m)','duration'],['urltest.interrupt_exist_connections','Прерывать существующие соединения','checkbox']]},
]
function recipeValue(field){return value(`pipeline.${branch.value}.${field}`)}
function recipeUpdate(field,event,type){update(`pipeline.${branch.value}.${field}`,event,type)}
function removeSub(index){if(window.confirm('Удалить эту подписку из файла? Работающий sing-box изменится после запуска конвейера.')) document.value.subscriptions.splice(index,1)}
</script>
<template>
  <p v-if="!can('edit_config')" class="notice">Для редактора требуется проверенный токен администратора. Войдите с помощью кнопки в шапке.</p>
  <template v-else>
    <nav v-if="subscriptions && (options.branches || []).length > 1" class="chips" aria-label="Ветвь конвейера"><button v-for="set in options.branches" :key="set" class="chip" :class="{on:branch===set}" :disabled="busy" @click="selectDocument(selected,set)">{{ BRANCHES[set] || set }}</button></nav>
    <p v-if="subscriptions && branch==='clients'" class="help-text">Клиентская ветвь: подписки и сборка для клиентских конфигураций (режим «Клиенты» в <RouterLink to="/pipeline">«Конвейере»</RouterLink>), результат — <code>whnodes.json</code>. На sing-box роутера не влияет.</p>
    <nav v-if="subscriptions" class="chips" aria-label="Разделы подписок и сборки"><button v-for="[kind,label] in [['providers','Источники подписок'],['build','Сборка и группы']]" :key="kind" class="chip" :class="{on:selected===kind}" :disabled="busy" @click="selectDocument(kind)">{{ label }}</button></nav>
    <div class="toolbar">
      <button class="btn" :disabled="busy" @click="load">Перечитать файл</button>
      <button class="btn" :disabled="busy || !document" @click="switchEditor">{{ advanced ? 'К форме' : 'Расширенный JSON' }}</button>
      <button class="btn primary" :disabled="busy || !dirty || !candidate" @click="preview=true">Просмотреть изменения</button>
      <span>{{ dirty ? 'Есть несохранённые изменения' : 'Нет несохранённых изменений' }}</span>
    </div>
    <p class="mut break">Файл: {{ path || 'Загрузка…' }}</p>
    <p v-if="restart" class="notice">Файл настроек отличается от загруженного при старте админки. Перезапуск тестера требуется; применение к текущему процессу не подтверждено.</p>
    <p v-if="message" class="notice" role="status">{{ message }}</p><p v-if="error" class="notice bad" role="alert">{{ error }}</p>
    <section v-if="preview" class="panel">
      <h2>Изменяемые поля</h2><p class="break">{{ changed.join(', ') }}</p>
      <p>Значения скрыты, чтобы не раскрывать секреты. Сервер проверит конфигурацию перед атомарной записью. После сохранения тестер перезапустится сам: на несколько секунд остановятся мониторинг и прогон, соединения пользователей не рвутся.{{ subscriptions ? ' Подписки и группы попадут в sing-box со следующей сборкой конвейера.' : '' }}</p>
      <button class="btn primary" :disabled="busy || !dirty || conflict" @click="save">Подтвердить и сохранить файл</button><p v-if="conflict" class="mut">Сохранение заблокировано: перечитайте файл. Несохранённые правки придётся внести заново.</p>
    </section>
    <template v-if="document">
      <fieldset :disabled="busy" class="config-fields">
      <p class="hint">Неизвестные поля и переопределения сохраняются. Изменение формы не заменяет весь документ шаблоном.</p>
      <template v-if="advanced"><p class="notice">JSON содержит секреты и полные URL подписок. Не публикуйте его и не отправляйте снимки экрана.</p><textarea v-model="text" class="cfg-editor" aria-label="Расширенный JSON конфигурации" spellcheck="false" /></template>
      <template v-else>
        <label class="check"><input v-model="showSecrets" type="checkbox" /> Показать секреты и URL подписок</label>
        <template v-if="recipe">
          <section v-for="group in recipeGroups" :key="group.title" class="panel"><h2>{{ group.title }}</h2>
            <div class="form-grid"><label v-for="[field,label,type] in group.fields" :key="field" :class="{check:type==='checkbox'}">
              <template v-if="type==='checkbox'"><input type="checkbox" :checked="recipeValue(field) ?? field==='enabled'" @change="recipeUpdate(field,$event,type)" /> {{ label }}</template>
              <template v-else>{{ label }}<input :type="type==='number' ? 'number' : 'text'" :value="type==='list' ? (recipeValue(field) || []).join(', ') : recipeValue(field)" placeholder="По умолчанию" @input="recipeUpdate(field,$event,type)" /></template>
            </label></div>
          </section>
          <NodeGroups v-if="branch==='router'" :doc="document" :checks="options.checks || []" :ip-kinds="options.ip_kinds || []" :builtin="options.builtin_regions || {}" @changed="preview=false" @valid="jsonValid.regions=$event" />
          <p v-else class="help-text">В клиентской ветви одна группа <code>global-auto-out</code> со всеми нодами и запасной <code>failsafe-out</code>: выбор делает sing-box клиента.</p>
        </template>
        <template v-else-if="subscriptions">
          <section v-for="{sub,index} in branchSubs" :key="index" class="panel">
            <h2>Подписка {{ index+1 }} · {{ sub.tag || 'новая' }}</h2>
            <div class="form-grid">
              <label>Название провайдера (уникальный tag)<input v-model="sub.tag" /></label>
              <label class="check"><input type="checkbox" :checked="sub.enabled !== false" @change="sub.enabled=$event.target.checked" /> Использовать подписку</label>
              <label>Ветвь <select :value="sub.branch || 'router'" @change="setBranch(sub,$event.target.value)"><option v-for="(name,b) in SUB_BRANCHES" :key="b" :value="b">{{ name }}</option></select></label>
              <label>Источник <select :value="sourceKind(sub)" @change="changeKind(sub,$event.target.value)"><option value="url">URL / ссылка</option><option value="happ">Happ · зашифрованная ссылка (crypt–crypt5)</option><option value="file">Файл</option><option value="folder">Папка AWG</option><option value="inline">Свои ноды (outbound-ы sing-box)</option></select></label>
              <label>Исключать ноды по подстроке имени (через запятую)<input :value="(sub.exclude_names || []).join(', ')" @change="setList(sub,'exclude_names',$event.target.value)" /></label>
              <label v-if="['url','happ'].includes(sourceKind(sub))">{{ sourceKind(sub)==='happ' ? 'Ссылка happ://crypt…' : 'URL подписки / share-ссылка' }} <input v-model="sub.url" :type="showSecrets ? 'text' : 'password'" autocomplete="off" /></label>
              <label v-else-if="sourceKind(sub)==='file'">Файл <input v-model="sub.file" /></label>
              <template v-else-if="sourceKind(sub)==='folder'"><label>Папка <input v-model="sub.path" /></label><label>Формат <input v-model="sub.format" /></label><label>Расширение файлов <input v-model="sub.ext" placeholder=".conf" /></label></template>
              <template v-if="sourceKind(sub)==='url'"><label>User-Agent<select :value="uaChoice(sub)" @change="setUA(sub,$event.target.value,true)"><option v-for="o in options.user_agents" :key="o.value" :value="o.value">{{ o.label }}</option><option value="__custom">Свой User-Agent</option></select></label><label v-if="uaChoice(sub)==='__custom'">Свой User-Agent<input :value="userAgent(sub)" @input="setUA(sub,$event.target.value)" /></label></template>
            </div>
            <JsonField v-if="sourceKind(sub)==='inline'" label="Outbound-ы" note="Список outbound-ов sing-box. Теги задаёт сборка: провайдер — tag подписки." :model-value="sub.outbounds || []" @update:model-value="sub.outbounds=$event;preview=false" @valid="jsonValid['inline'+index]=$event" />
            <p v-if="sourceKind(sub)==='url'" class="help-text">User-Agent отправляется при HTTP-загрузке (включая sub:// со ссылкой на HTTP). Для готовых share-ссылок и локальных файлов он не используется. Провайдер может выбирать формат ответа по этому заголовку. При пустом HTTP-ответе загрузчик повторяет запрос с clashmeta.</p>
            <details v-if="sourceKind(sub)==='url' && !userAgent(sub)"><summary>User-Agent по умолчанию</summary><p class="break">{{ options.user_agents[0]?.effective }}</p></details>
            <label v-if="sourceKind(sub)==='url'" class="check"><input type="checkbox" :checked="!!sub.send_device" @change="sub.send_device=$event.target.checked || undefined; preview=false" /> Отправлять HWID и описание устройства (для панелей с лимитом устройств)</label>
            <div v-if="subState(sub)" class="sub-state">
              <p><span :class="subState(sub).ok ? '' : 'bad'">{{ subState(sub).ok ? 'Загружена' : (subState(sub).stale ? 'Сбой — работают прошлые ноды' : 'Сбой — нод нет') }}</span> · нод {{ subState(sub).count }} · последняя удачная загрузка: {{ lastOk(subState(sub)) }}<template v-if="subMeta(sub).title"> · {{ subMeta(sub).title }}</template></p>
              <p v-if="!subState(sub).ok && subState(sub).error" class="mut break">{{ subState(sub).error }}</p>
              <p v-if="trafficLine(subMeta(sub)) || daysLeft(subMeta(sub)) !== null">Трафик: {{ trafficLine(subMeta(sub)) || '—' }}<template v-if="daysLeft(subMeta(sub)) !== null"> · действует до {{ dateTime(subMeta(sub).expire).split(',')[0] }} <span :class="daysLeft(subMeta(sub)) <= 3 ? 'bad' : ''">(осталось {{ daysLeft(subMeta(sub)) }} дн.)</span></template></p>
              <details v-if="subMeta(sub).announce"><summary>Сообщение провайдера</summary><p class="pre">{{ subMeta(sub).announce }}</p></details>
            </div>
            <details v-if="sourceKind(sub)==='happ'"><summary>HTTP-заголовки Happ</summary>
              <p class="help-text">Используется набор Happ по умолчанию. Отдельный user_agent для этого типа игнорируется; User-Agent меняется здесь, в happ_headers. Изменённые значения переопределяют стандартные заголовки.</p>
              <div class="form-grid"><label v-for="key in happFields(sub)" :key="key">{{ key }}<input type="text" :value="sub.happ_headers?.[key] ?? options.happ_headers[key]" @input="setHeader(sub,key,$event.target.value)" /><span class="mut">{{ Object.hasOwn(sub.happ_headers || {},key) ? 'Переопределён' : 'По умолчанию' }}</span><button v-if="Object.hasOwn(sub.happ_headers || {},key)" class="btn" @click="removeHeader(sub,key)">{{ Object.hasOwn(options.happ_headers,key) ? 'Вернуть по умолчанию' : 'Удалить заголовок' }}</button></label></div>
              <button class="btn" @click="addHeader(sub)">Добавить заголовок</button> <button class="btn" @click="delete sub.happ_headers">Вернуть весь набор Happ по умолчанию</button>
            </details><button class="btn" @click="removeSub(index)">Удалить подписку</button>
          </section>
          <button class="btn" @click="addSub">Добавить подписку</button>
          <p v-if="options.device?.hwid" class="help-text">HWID роутера для панелей подписок: <code>{{ options.device.hwid }}</code> (файл {{ options.device.path }}). Отправляется happ-подписками всегда, URL-подписками — при включённом флажке. Свой HWID для одной подписки задаётся заголовком X-Hwid.</p>
          <section v-if="(document.subscriptions || []).some(s=>sourceKind(s)==='happ')" class="panel"><h2>Ключи Happ</h2><div class="form-grid">
            <label>Источник ключей<input :value="value('pipeline.fetch.happ_keys_url') ?? options.happ_keys?.default_url" @input="setHappKeys($event.target.value)" /><span class="mut">{{ value('pipeline.fetch.happ_keys_url') ? 'Переопределён' : 'По умолчанию' }}</span><button v-if="value('pipeline.fetch.happ_keys_url')" class="btn" @click="setHappKeys('')">Вернуть по умолчанию</button></label>
          </div><p class="help-text">Ключи расшифровки ссылок happ://crypt… принадлежат приложению Happ и в nodes-tester не входят. Они скачиваются из указанного источника (URL или путь к файлу keys.rs или JSON) и хранятся в {{ options.happ_keys?.path }}; обновляются, если для ссылки не нашлось ключа.</p></section>
          <section class="panel"><h2>Защита загрузки</h2><div class="form-grid">
            <label>Тайм-аут загрузки<input :value="value('pipeline.fetch.timeout')" @input="update('pipeline.fetch.timeout',$event,'duration')" placeholder="2m" /></label>
            <label>Минимальная доля оставшихся нод (0–1)<input type="number" min="0" max="1" step="0.05" :value="value('pipeline.fetch.min_ratio')" @input="update('pipeline.fetch.min_ratio',$event,'number')" placeholder="0.5" /></label>
            <label>Повторов HTTP<input type="number" min="0" :value="value('pipeline.fetch.retries')" @input="update('pipeline.fetch.retries',$event,'number')" placeholder="3" /></label>
            <label>Хранить прошлые ноды при сбое<input :value="value('pipeline.fetch.keep_stale')" @input="update('pipeline.fetch.keep_stale',$event,'duration')" placeholder="48h" /></label>
            <label>Прокси загрузки<input :value="value('pipeline.fetch.proxy')" @input="update('pipeline.fetch.proxy',$event)" placeholder="socks5://127.0.0.1:2080" /></label>
          </div><p class="help-text">При резком уменьшении списка fetch guard может сохранить предыдущие данные. Результат загрузки виден в журнале <RouterLink to="/pipeline">«Конвейера»</RouterLink>.</p></section>
        </template>
        <details v-if="subscriptions" class="panel"><summary><b>Как изменения попадают в sing-box</b></summary>
          <ol><li>Подписки, параметры сборки и группы хранятся в одном <code>config.json</code>; его читают конвейер и тестер.</li><li>Админка запускает конвейер по расписанию (<code>pipeline.router.at</code>, <code>pipeline.clients.at</code>) или вручную в <RouterLink to="/pipeline">«Конвейере»</RouterLink>: загрузка подписок, сборка <code>nodes.json</code>, <code>sing-box check</code>, применение. sing-box перезапускается только при изменениях; без связности возвращается прежний конфиг.</li><li>Тестер подхватывает новые ноды в следующем проходе; изменения групп и настроек тестера — после его перезапуска.</li></ol>
          <h3>Файлы конвейера</h3>
          <p>Загруженные подписки — <code>raw/router.json</code> и <code>raw/clients.json</code>, результат сборки — <code>nodes.json</code> и <code>whnodes.json</code> в каталоге данных (в пакете OpenWrt — <code>/opt/nodes-tester</code>, задаётся data_dir в /etc/config/nodes-tester).</p>
          <p>Отдельный запуск: <code>nodes-tester fetch -c config.json --branch router -o raw/router.json</code>, затем <code>nodes-tester config -c config.json --branch router --raw raw/router.json -o nodes.json</code>.</p>
        </details>
        <template v-if="!subscriptions">
          <section v-for="group in groups" :key="group.title" class="panel">
            <h2>{{ group.title }}</h2><p v-if="group.note" class="help-text">{{ group.note }}</p>
            <div class="form-grid">
              <label v-for="[field,label,type] in group.fields" :key="field" :class="{check:type==='checkbox'}">
                <template v-if="type==='checkbox'"><input type="checkbox" :checked="value(field) ?? ON_BY_DEFAULT.has(field)" @change="update(field,$event,type)" /> {{ label }}</template>
                <template v-else-if="type==='list'">{{ label }}<input type="text" :value="(value(field) || []).join(', ')" @input="update(field,$event,type)" /></template>
                <template v-else>{{ label }}<input :type="type==='password' && showSecrets ? 'text' : type==='duration' ? 'text' : type" :value="value(field)" autocomplete="off" @input="update(field,$event,type)" /></template>
              </label>
            </div>
            <template v-if="group.title==='Уведомления'">
              <p class="mut">События:</p>
              <div class="toolbar"><label v-for="(name,ev) in NOTIFY_EVENTS" :key="ev" class="check"><input type="checkbox" :checked="(value('notify.events') ?? DEFAULT_EVENTS).includes(ev)" @change="toggleList('notify.events',ev,$event.target.checked,DEFAULT_EVENTS)" /> {{ name }}</label></div>
              <p class="mut">Переключения нод — причины:</p>
              <div class="toolbar"><label v-for="(name,r) in SWITCH_REASONS" :key="r" class="check"><input type="checkbox" :checked="(value('notify.switch_reasons') ?? DEFAULT_REASONS).includes(r)" @change="toggleList('notify.switch_reasons',r,$event.target.checked,DEFAULT_REASONS)" /> {{ name }}</label></div>
              <div v-if="can('notify')" class="actions"><button class="btn" :disabled="notifyBusy" @click="notifyTest">Отправить тестовое сообщение</button> <span class="mut">{{ notifyResult || 'Тест использует настройки работающего тестера (после перезапуска).' }}</span></div>
            </template>
          </section>
        </template>
      </template>
      </fieldset>
    </template>
  </template>
</template>

<style scoped>
.sub-state p { margin: .2rem 0 }
.sub-state .pre { white-space: pre-wrap }
</style>
