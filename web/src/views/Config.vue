<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { useRoute, onBeforeRouteLeave } from 'vue-router'
import { api, auth, can } from '../api.js'
import { getPath, setPath, changedPaths, TEST_NAMES } from '../ux.js'
import { bytes, dateTime } from '../format.js'
import JsonField from '../components/JsonField.vue'
import NodeGroups from '../components/NodeGroups.vue'
const route = useRoute(), subscriptions = computed(()=>route.path === '/subscriptions')
const selected=ref('providers'), branch=ref('router'), options=ref({user_agents:[],happ_headers:{}}), jsonValid=ref({}), customAgents=ref(new WeakSet())
const recipe=computed(()=>subscriptions.value && selected.value==='groups')
const document = ref(null), original = ref(null), text = ref(''), advanced = ref(false)
const path = ref(''), revision = ref(''), busy = ref(false), message = ref(''), error = ref('')
const preview = ref(false), showSecrets = ref(false), restart = ref(false), conflict = ref(false)
const endpoint = computed(()=>subscriptions.value ? `/config/${selected.value}?set=${branch.value}` : '/config')
const BRANCHES={router:'Роутер',clients:'Клиенты'}
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
    const data=await api.get(endpoint.value)
    if(epoch !== auth.epoch) return
    document.value=data.data; original.value=JSON.parse(JSON.stringify(data.data))
    text.value=JSON.stringify(data.data,null,2); path.value=data.path; revision.value=data.revision
    restart.value=!!data.restart_required; preview.value=false; conflict.value=false
    jsonValid.value={}
    if(subscriptions.value){const opts=await api.get('/config/subscription-options');if(epoch===auth.epoch)options.value=opts}
  } catch(e) {error.value=e.message}
  finally {busy.value=false}
}
async function selectDocument(kind,set=branch.value) {
  if((kind===selected.value && set===branch.value) || !discardOK())return
  selected.value=kind;branch.value=set;document.value=null;original.value=null;advanced.value=false;jsonValid.value={};await load()
}
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
  setPath(document.value,field,v); preview.value=false
}
async function save() {
  if(!can('edit_config') || !candidate.value) {error.value='Проверьте JSON: нужен корректный объект'; return}
  busy.value=true; error.value=''; const epoch=auth.epoch
  const data=JSON.parse(JSON.stringify(candidate.value))
  try {
    const result=await api.put(endpoint.value,data,{'If-Match':revision.value})
    if(epoch !== auth.epoch) return
    document.value=data; original.value=JSON.parse(JSON.stringify(data)); text.value=JSON.stringify(data,null,2)
    revision.value=result.revision; restart.value=!!result.restart_required; preview.value=false
    message.value=subscriptions.value ? (recipe.value ? 'Параметры сборки' : 'Подписки')+(branch.value==='clients' ? ' клиентской ветви' : '')+' сохранены в файл. Загрузка, сборка и применение к sing-box не запускались.' : 'Настройки сохранены в файл. Перезапустите процесс, который использует этот конфиг.'
  } catch(e){error.value=e.message; if(e.status === 409) conflict.value=true}
  finally{busy.value=false}
}
const groups = [
  {title:'Соединение с sing-box',note:'API управляет селекторами, а SOCKS-соединение служит маршрутом тестового трафика. Админка не проверяет соединение при сохранении.',fields:[
    ['box_api.url','Адрес API sing-box','text'],['box_api.secret','Секрет API sing-box','password'],['box_api.timeout','Тайм-аут API, с','number'],
    ['testing_groups.0.connection.host','SOCKS-хост','text'],['testing_groups.0.connection.port','SOCKS-порт','number'],
    ['testing_groups.0.connection.username','SOCKS-логин','text'],['testing_groups.0.connection.password','SOCKS-пароль','password'],
    ['testing_groups.0.selector.group','Тестовый селектор','text']]},
  {title:'План проверок',note:'Форма меняет общие параметры run.default. Переопределения регионов и групп остаются в JSON и могут иметь приоритет.',fields:[
    ['run.default.loop','Непрерывная работа','checkbox'],['run.default.rotation_bound','Привязать проходы к ротации','checkbox'],
    ['run.default.pass_pause','Пауза между проходами, с','number'],['run.default.min_host_gap','Зазор обращений к одному хосту, с','number'],
    ['run.default.request_timeout','Тайм-аут проверки, с','number'],['run.default.heavy_candidates','Кандидатов на тяжёлую проверку в группе','number']]},
  {title:'Переключение и наблюдение',note:'Включение автоматики может изменить выбор боевых селекторов после перезапуска тестера.',fields:[
    ['switching.enabled','Автоматическое переключение','checkbox'],['switching.rotation.enabled','Ротация','checkbox'],
    ['switching.rotation.interval','Интервал ротации, с','number'],['monitor.enabled','Монитор активных нод','checkbox'],
    ['cooldown.garbage_hours','Длительность карантина, ч','number'],['cooldown.max_skip','Максимум пропускаемых проходов','number']]},
  {title:'Админка и хранение',note:'Смена токена, адреса или порта потребует перезапуска админки. Форма редактирует файл, не настройки текущего процесса.',fields:[
    ['dashboard.enabled','Встроенная админка','checkbox'],['dashboard.host','Адрес прослушивания','text'],['dashboard.port','Порт админки','number'],
    ['dashboard.token','Токен администратора','password'],['dashboard.read_open','Просмотр без токена','checkbox'],
    ['dashboard.providers_file','Файл подписок (относительно config.json)','text'],['dashboard.groups_file','Файл groups_params.json (пусто: рядом с подписками)','text'],['storage.enabled','Хранить статистику','checkbox'],
    ['storage.db_file','Файл SQLite','text'],['storage.nodes_file','Файл описаний нод','text'],['storage.retention_days','Хранить историю, дней','number'],
    ['storage.traffic.enabled','Собирать пользовательский трафик','checkbox']]},
  {title:'Уведомления',note:'Telegram и/или webhook. Новые настройки действуют после перезапуска тестера. Если напрямую с роутера отправить не удалось (Telegram часто режется провайдером), сообщение уходит через тестер на лучшей здоровой ноде. Переключения нод с причиной «ротация» происходят часто — по умолчанию сообщаются только аварийные.',fields:[
    ['notify.enabled','Отправлять уведомления','checkbox'],['notify.name','Подпись роутера в сообщении','text'],
    ['notify.telegram.token','Токен Telegram-бота','password'],['notify.telegram.chat_id','Telegram chat_id получателя','text'],
    ['notify.webhook.url','Webhook URL (POST JSON)','text'],['notify.proxy','Прокси для отправки (пусто — напрямую)','text'],
    ['notify.expiry_days','Предупреждать о конце подписки за, дней','number']]},
  {title:'Тип выходного IP',note:'Тип IP (дата-центр, домашний, мобильный) запрашивается у ip-api.com по выходным IP нод — через тестер на лучшей здоровой ноде, иначе напрямую.',fields:[
    ['ip_info.enabled','Определять тип выходного IP','checkbox'],['ip_info.ttl_days','Обновлять сведения раз в, дней','number']]},
]
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
function sourceKind(sub) {return sub.type === 'folder' ? 'folder' : sub.file != null ? 'file' : String(sub.url || '').trim().startsWith('happ://crypt') ? 'happ' : 'url'}
function changeKind(sub,kind) {
  delete sub.url; delete sub.file; delete sub.type; delete sub.path
  if(kind === 'folder'){sub.type='folder';sub.path=''; if(!sub.format)sub.format='awg'}
  else if(kind==='happ')sub.url='happ://crypt/'
  else sub[kind]=''
  preview.value=false
}
function userAgent(sub){return sub.user_agent ?? ''}
function uaChoice(sub){const v=userAgent(sub);return customAgents.value.has(sub) ? '__custom' : options.value.user_agents.some(o=>o.value===v) ? v : '__custom'}
function setUA(sub,v,selection=false){if(v==='__custom' && selection){customAgents.value.add(sub);return}if(selection)customAgents.value.delete(sub);sub.user_agent=v;preview.value=false}
function happFields(sub){return [...new Set([...Object.keys(options.value.happ_headers),...Object.keys(sub.happ_headers || {})])]}
function setHeader(sub,key,v){(sub.happ_headers ||= {})[key]=v;preview.value=false}
function removeHeader(sub,key){if(sub.happ_headers)delete sub.happ_headers[key];preview.value=false}
function addHeader(sub){const key=window.prompt('Имя дополнительного HTTP-заголовка');if(key?.trim())setHeader(sub,key.trim(),'')}
const recipeGroups=[
  {title:'Фильтры нод',fields:[['filters.exclude_types','Исключить типы outbound (через запятую)','list'],['filters.exclude_protocols','Исключить протоколы / транспорт (через запятую)','list'],['filters.exclude_countries','Исключить страны, ISO-коды (через запятую)','list']]},
  {title:'Переименование',fields:[['rename.domain_resolver_tag','Тег DNS resolver','text']]},
  {title:'Группы selector',fields:[['selector.interrupt_exist_connections','Прерывать существующие соединения при смене ноды','checkbox']]},
  {title:'Группы urltest',fields:[['urltest.url','URL проверки','text'],['urltest.interval','Интервал (например, 5m)','text'],['urltest.tolerance','Допуск задержки, мс','number'],['urltest.idle_timeout','Тайм-аут бездействия (например, 5m)','text'],['urltest.interrupt_exist_connections','Прерывать существующие соединения','checkbox']]},
  {title:'Создаваемые группы',fields:[['emit.nodes_tester','Создать группу nodes-tester','checkbox'],['emit.global_failsafe','Создать глобальные failsafe-группы','checkbox'],['raw_user_nodes','Сохранить пользовательские ноды без переименования','checkbox']]},
]
function recipeValue(field){return value(field) ?? getPath(options.value.group_defaults,field)}
const objectFields=[{field:'filters.exclude_names',label:'Исключать исходные имена по провайдерам',note:'Объект: провайдер или * → список подстрок имени. Например: {"*": ["test"]}.'},{field:'rename.labels',label:'Метки по словам исходного имени',note:'Объект: метка → список слов. Первая совпавшая метка используется в имени ноды.'}]
function toggleTest(test,enabled) {
  const tests=[...(value('run.default.tests_enabled') || [])]
  setPath(document.value,'run.default.tests_enabled',enabled ? [...new Set([...tests,test])] : tests.filter(t=>t!==test))
}
function removeSub(index){if(window.confirm('Удалить эту подписку из файла? Работающий sing-box изменится после запуска конвейера.')) document.value.subscribes.splice(index,1)}
</script>
<template>
  <p v-if="!can('edit_config')" class="notice">Для редактора требуется проверенный токен администратора. Войдите с помощью кнопки в шапке.</p>
  <template v-else>
    <nav v-if="subscriptions && (options.branches || []).length > 1" class="chips" aria-label="Ветвь конвейера"><button v-for="set in options.branches" :key="set" class="chip" :class="{on:branch===set}" :disabled="busy" @click="selectDocument(selected,set)">{{ BRANCHES[set] || set }}</button></nav>
    <p v-if="subscriptions && branch==='clients'" class="help-text">Клиентская ветвь: подписки и сборка для клиентских конфигураций (режим «Клиенты» в <RouterLink to="/pipeline">«Конвейере»</RouterLink>), результат — <code>whnodes.json</code>. На sing-box роутера не влияет.</p>
    <nav v-if="subscriptions" class="chips" aria-label="Файлы подписок и сборки"><button v-for="[kind,label] in [['providers','Источники подписок'],['groups','Параметры сборки']]" :key="kind" class="chip" :class="{on:selected===kind}" :disabled="busy" @click="selectDocument(kind)">{{ label }}</button></nav>
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
      <p>Значения скрыты, чтобы не раскрывать секреты. Сервер проверит конфигурацию перед атомарной записью. {{ subscriptions ? 'Применение к sing-box не выполняется.' : 'Изменения начнут действовать после перезапуска.' }}</p>
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
              <template v-if="type==='checkbox'"><input type="checkbox" :checked="!!recipeValue(field)" @change="update(field,$event,type)" /> {{ label }}</template>
              <template v-else>{{ label }}<input :type="type==='list' ? 'text' : type" :value="type==='list' ? (recipeValue(field) || []).join(', ') : recipeValue(field)" @input="update(field,$event,type)" /></template>
            </label></div>
            <template v-for="entry in objectFields.filter(o=>group.fields.some(([f])=>f.split('.')[0]===o.field.split('.')[0]))" :key="entry.field"><JsonField :label="entry.label" :note="entry.note" :model-value="value(entry.field)" @update:model-value="setPath(document,entry.field,$event)" @valid="jsonValid[entry.field]=$event" /></template>
          </section>
          <NodeGroups :doc="document" :branch="branch" :builtin="options.builtin_regions || {}" @changed="preview=false" @valid="jsonValid.regions=$event" />
        </template>
        <template v-else-if="subscriptions">
          <section v-for="(sub,index) in document.subscribes || []" :key="index" class="panel">
            <h2>Подписка {{ index+1 }} · {{ sub.tag || 'новая' }}</h2>
            <div class="form-grid">
              <label>Название провайдера (уникальный tag)<input v-model="sub.tag" /></label>
              <label class="check"><input type="checkbox" :checked="sub.enabled !== false" @change="sub.enabled=$event.target.checked" /> Использовать подписку</label>
              <label>Источник <select :value="sourceKind(sub)" @change="changeKind(sub,$event.target.value)"><option value="url">URL / ссылка</option><option value="happ">Happ · зашифрованная ссылка (crypt–crypt5)</option><option value="file">Файл</option><option value="folder">Папка AWG</option></select></label>
              <label v-if="['url','happ'].includes(sourceKind(sub))">{{ sourceKind(sub)==='happ' ? 'Ссылка happ://crypt…' : 'URL подписки / share-ссылка' }} <input v-model="sub.url" :type="showSecrets ? 'text' : 'password'" autocomplete="off" /></label>
              <label v-else-if="sourceKind(sub)==='file'">Файл <input v-model="sub.file" /></label>
              <template v-else><label>Папка <input v-model="sub.path" /></label><label>Формат <input v-model="sub.format" /></label><label>Расширение файлов <input v-model="sub.ext" placeholder=".conf" /></label></template>
              <template v-if="sourceKind(sub)==='url'"><label>User-Agent<select :value="uaChoice(sub)" @change="setUA(sub,$event.target.value,true)"><option v-for="o in options.user_agents" :key="o.value" :value="o.value">{{ o.label }}</option><option value="__custom">Свой User-Agent</option></select></label><label v-if="uaChoice(sub)==='__custom'">Свой User-Agent<input :value="userAgent(sub)" @input="setUA(sub,$event.target.value)" /></label></template>
            </div>
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
          <button class="btn" @click="(document.subscribes ||= []).push({tag:'',url:'',enabled:true})">Добавить подписку</button>
          <p v-if="options.device?.hwid" class="help-text">HWID роутера для панелей подписок: <code>{{ options.device.hwid }}</code> (файл {{ options.device.path }}). Отправляется happ-подписками всегда, URL-подписками — при включённом флажке. Свой HWID для одной подписки задаётся заголовком X-Hwid.</p>
          <section class="panel"><h2>Защита загрузки</h2><div class="form-grid">
            <label>Тайм-аут, с<input type="number" :value="value('fetch.timeout')" @input="update('fetch.timeout',$event,'number')" placeholder="По умолчанию" /></label>
            <label>Минимальная доля оставшихся нод (0–1)<input type="number" min="0" max="1" step="0.05" :value="value('fetch.min_ratio')" @input="update('fetch.min_ratio',$event,'number')" placeholder="По умолчанию" /></label>
            <label>Повторов HTTP<input type="number" min="0" :value="value('fetch.retries')" @input="update('fetch.retries',$event,'number')" placeholder="3" /></label>
            <label>Хранить прошлые ноды при сбое, ч<input type="number" min="0" :value="value('fetch.stale_max_hours')" @input="update('fetch.stale_max_hours',$event,'number')" placeholder="48" /></label>
            <label>Прокси загрузки<input :value="value('fetch.proxy')" @input="update('fetch.proxy',$event)" placeholder="socks5://127.0.0.1:2080" /></label>
          </div><p class="help-text">При резком уменьшении списка fetch guard может сохранить предыдущие данные. Результат загрузки виден в журнале <RouterLink to="/pipeline">«Конвейера»</RouterLink>.</p></section>
        </template>
        <details v-if="subscriptions" class="panel"><summary><b>Как изменения попадают в sing-box</b></summary>
          <ol><li>Сохранённые здесь <code>providers.json</code> и <code>groups_params.json</code> читает конвейер.</li><li>Админка запускает конвейер по расписанию или вручную в <RouterLink to="/pipeline">«Конвейере»</RouterLink>: загрузка подписок, сборка <code>nodes.json</code>, <code>sing-box check</code>, применение. sing-box перезапускается только при изменениях; без связности возвращается прежний конфиг.</li><li>Тестер подхватывает новые ноды и группы в следующем проходе.</li></ol>
          <h3>Файлы конвейера</h3>
          <p>В пакете OpenWrt путь raw задаётся конвейером: <code>/opt/nodes-tester/raw/main.json</code> для router и <code>/opt/nodes-tester/raw/wh.json</code> для clients. Это файлы формата raw_nodes.json; отдельно в providers.json путь не задаётся. В пакете каталог задаёт data_dir в /etc/config/nodes-tester; при прямом запуске скрипта — переменная DATA.</p>
          <p>Выход сборки задаётся аргументом <code>-o</code> команды nodes_config. В pipeline router это <code>/etc/sing-box-subscribe/nodes.json</code>, в clients — <code>/etc/sing-box-subscribe/whnodes.json</code>. При --dry-run используются промежуточные *.dry.json. В groups_params.json путь вывода не хранится.</p>
          <p>Для отдельного запуска укажите нужные пути: <code>nodes-tester fetch -p providers.json -o raw_nodes.json</code>, затем <code>nodes-tester config --raw raw_nodes.json --groups groups_params.json -o nodes.json</code>.</p>
          <p>Поле storage.nodes_file в настройках задаёт файл, который тестер читает для описаний нод. Сам конвейер этот параметр не использует.</p>
        </details>
        <template v-if="!subscriptions">
          <section v-for="group in groups" :key="group.title" class="panel">
            <h2>{{ group.title }}</h2><p v-if="group.note" class="help-text">{{ group.note }}</p>
            <div class="form-grid">
              <label v-for="[field,label,type] in group.fields" :key="field" :class="{check:type==='checkbox'}">
                <template v-if="type==='checkbox'"><input type="checkbox" :checked="!!value(field)" @change="update(field,$event,type)" /> {{ label }}</template>
                <template v-else-if="type==='list'">{{ label }}<input type="text" :value="(value(field) || []).join(', ')" @input="update(field,$event,type)" /></template>
                <template v-else>{{ label }}<input :type="type==='password' && showSecrets ? 'text' : type" :value="value(field)" autocomplete="off" @input="update(field,$event,type)" /></template>
              </label>
            </div>
            <template v-if="group.title==='Уведомления'">
              <p class="mut">События:</p>
              <div class="toolbar"><label v-for="(name,ev) in NOTIFY_EVENTS" :key="ev" class="check"><input type="checkbox" :checked="(value('notify.events') ?? DEFAULT_EVENTS).includes(ev)" @change="toggleList('notify.events',ev,$event.target.checked,DEFAULT_EVENTS)" /> {{ name }}</label></div>
              <p class="mut">Переключения нод — причины:</p>
              <div class="toolbar"><label v-for="(name,r) in SWITCH_REASONS" :key="r" class="check"><input type="checkbox" :checked="(value('notify.switch_reasons') ?? DEFAULT_REASONS).includes(r)" @change="toggleList('notify.switch_reasons',r,$event.target.checked,DEFAULT_REASONS)" /> {{ name }}</label></div>
              <div v-if="can('notify')" class="actions"><button class="btn" :disabled="notifyBusy" @click="notifyTest">Отправить тестовое сообщение</button> <span class="mut">{{ notifyResult || 'Тест использует настройки работающего тестера (после перезапуска).' }}</span></div>
            </template>
            <div v-if="group.title==='План проверок'" class="toolbar"><label v-for="(name,test) in Object.fromEntries(Object.entries(TEST_NAMES).filter(([key])=>!['heavy_download','gemini'].includes(key)))" class="check" :key="test"><input type="checkbox" :checked="(value('run.default.tests_enabled') || []).includes(test)" @change="toggleTest(test,$event.target.checked)" /> {{ name }}</label></div>
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
