<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { useRoute, onBeforeRouteLeave } from 'vue-router'
import { api, auth, can } from '../api.js'
import { getPath, setPath, changedPaths, schemaDefaults, defaultText } from '../ux.js'
import { bytes, dateTime } from '../format.js'
import { t } from '../i18n.js'
import JsonField from '../components/JsonField.vue'
import NodeGroups from '../components/NodeGroups.vue'
import Msg from '../components/Msg.vue'
import VacuumPanel from '../components/VacuumPanel.vue'
const route = useRoute(), subscriptions = computed(()=>route.path === '/subscriptions')
const selected=ref('providers'), branch=ref('router'), options=ref({user_agents:[],happ_headers:{}}), jsonValid=ref({}), customAgents=ref(new WeakSet())
const recipe=computed(()=>subscriptions.value && selected.value==='build')
const document = ref(null), original = ref(null), text = ref(''), advanced = ref(false)
const path = ref(''), revision = ref(''), busy = ref(false), message = ref(''), error = ref('')
const preview = ref(false), showSecrets = ref(false), restart = ref(false), conflict = ref(false)
// Значения по умолчанию — из config.schema.json рядом с config.json (подсказки серым в пустых полях).
const defaults = ref({}), defaultsError = ref('')
// Один файл config.json: подписки, сборка, группы и настройки тестера — разные страницы одного документа.
const endpoint = '/config'
const BRANCHES={router:t('Router'),clients:t('Clients')}, SUB_BRANCHES={router:t('Router'),clients:t('Clients'),both:t('Both branches')}
const candidate = computed(()=>{ if(!advanced.value) return Object.values(jsonValid.value).every(Boolean) ? document.value : null; try{return JSON.parse(text.value)}catch{return null} })
const changed = computed(()=>changedPaths(original.value,candidate.value))
const dirty = computed(()=>document.value !== null && (advanced.value ? text.value !== JSON.stringify(original.value,null,2) : changed.value.length > 0 || !candidate.value))
watch(dirty,v=>{auth.dirty = v})
function discardOK() { return !dirty.value || window.confirm(t('There are unsaved changes. Discard them?')) }
onBeforeRouteLeave(()=>discardOK())
function beforeUnload(e) { if(dirty.value){e.preventDefault(); e.returnValue=''} }
onMounted(()=>{window.addEventListener('beforeunload',beforeUnload); if(can('edit_config')) load()})
onUnmounted(()=>{window.removeEventListener('beforeunload',beforeUnload); auth.dirty=false})
watch(()=>auth.verified,verified=>{ if(verified) load(); else { document.value=null; original.value=null; text.value=''; preview.value=false; showSecrets.value=false } })
async function loadDefaults() {
  try { defaults.value=schemaDefaults(await api.get('/config/schema')); defaultsError.value='' }
  catch(e) { defaults.value={}; defaultsError.value=e.message }
}
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
    await loadDefaults()
    if(subscriptions.value){const opts=await api.get('/config/subscription-options');if(epoch===auth.epoch)options.value=opts}
  } catch(e) {error.value=e.message}
  finally {busy.value=false}
}
// Ветвь и раздел — только фильтр формы: документ тот же, несохранённые правки сохраняются.
function selectDocument(kind,set=branch.value) {selected.value=kind;branch.value=set}
function switchEditor() {
  if(!advanced.value && !candidate.value){error.value=t('Fix the JSON in the form fields before switching the editor');return}
  if(advanced.value) {
    try { const parsed=JSON.parse(text.value); if(!parsed || Array.isArray(parsed) || typeof parsed !== 'object') throw new Error(t('A JSON object is required')); document.value=parsed; advanced.value=false; error.value='' }
    catch(e){error.value=e.message}
  } else {text.value=JSON.stringify(document.value,null,2); advanced.value=true; showSecrets.value=false}
  preview.value=false
}
function value(field) {return getPath(document.value,field)}
// Подсказка пустого поля и состояние флажка без значения в файле — по умолчанию из схемы.
function placeholder(field) {return defaultText(defaults.value[field])}
function checked(field) {return value(field) ?? defaults.value[field] ?? false}
// Пустое поле — «по умолчанию»: ключ в config.json не пишется.
function update(field,event,type='text') {
  let v = type === 'checkbox' ? event.target.checked : type === 'number' ? (event.target.value === '' ? undefined : Number(event.target.value)) : event.target.value
  if(type === 'list') { v = v.split(',').map(x=>x.trim()).filter(Boolean); if(!v.length) v = undefined }
  if(['text','duration','password'].includes(type)) v = v.trim() || undefined
  setPath(document.value,field,v); preview.value=false
}
// Тестер перезапускается отвязанным процессом через секунду; ждём, пока ответит процесс, который
// загрузил сохранённый файл (restart_required=false), — прежний ответил бы с true.
async function waitRestart(epoch,suffix) {
  const pause=ms=>new Promise(r=>setTimeout(r,ms))
  for(let i=0;i<30;i++) {
    await pause(2000)
    try { if((await api.get(endpoint)).restart_required) continue } catch { continue }
    if(epoch===auth.epoch){message.value=t('The tester restarted; the new settings are in effect.')+suffix;restart.value=false}
    return
  }
  if(epoch===auth.epoch) error.value=t('The tester did not restart within a minute: the file is saved, but the new settings are not in effect yet. Log: logread -e nodes-tester')
}
async function save() {
  if(!can('edit_config') || !candidate.value) {error.value=t('Check the JSON: a valid object is required'); return}
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
    const sb=subscriptions.value ? ' '+t('Fetching subscriptions, building and applying to sing-box were not started.') : ''
    message.value=result.restarting ? t('config.json saved. The tester is restarting…')+sb : t('config.json saved; {note}.',{note:result.restart_note || t('restart the tester')})+sb
    if(result.restarting) waitRestart(epoch,sb)
  } catch(e){error.value=e.message; if(e.status === 409) conflict.value=true}
  finally{busy.value=false}
}
// Длительности — строки как в sing-box: 30s, 5m, 3h, 2d.
const groups = [
  {title:t('Connection to sing-box'),note:t('The API controls the selectors, and the SOCKS connection carries the test traffic. The admin panel does not check the connection when saving.'),fields:[
    ['api.url',t('sing-box API address'),'text'],['api.secret',t('sing-box API secret'),'password'],['api.timeout',t('API timeout'),'duration'],
    ['tester.socks',t('Tester SOCKS (socks5h://host:port)'),'text'],['tester.selector',t('Test selector'),'text']]},
  {title:t('Tester passes'),note:t('The tester measures all nodes; groups choose from them by their own checks (“Subscriptions” → “Build and groups”).'),fields:[
    ['tester.host_gap',t('Gap between requests to one host'),'duration'],
    ['tester.request_timeout',t('Check timeout'),'duration'],['selection.reserve',t('Reserve candidates per group'),'number']]},
  {title:t('Switching and monitoring'),note:t('Enabling rotation may change the choice of live selectors after the tester restarts.'),fields:[
    ['selection.rotation.enabled',t('Rotation'),'checkbox'],['selection.rotation.interval',t('Rotation interval'),'duration'],
    ['selection.rotation.min_dwell',t('Minimum time on a node'),'duration'],['monitor.interval',t('Active node monitor tick'),'duration'],
    ['selection.quarantine.duration',t('Quarantine duration'),'duration'],['selection.quarantine.after_skips',t('Quarantine after skipped connection periods'),'number']]},
  {title:t('Admin panel and storage'),note:t('Changing the token, address or port requires restarting the admin panel. The form edits the file, not the settings of the running process.'),fields:[
    ['admin.enabled',t('Embedded admin panel'),'checkbox'],['admin.listen',t('Listen address'),'text'],['admin.port',t('Admin panel port'),'number'],
    ['admin.token',t('Administrator token'),'password'],['admin.public_read',t('Viewing without a token'),'checkbox'],
    ['storage.path',t('SQLite file (relative to the data directory)'),'text'],['storage.retention',t('Keep history for'),'duration'],
    ['storage.traffic.enabled',t('Collect user traffic'),'checkbox'],
    ['storage.vacuum.every',t('Database VACUUM every (0s: manually only)'),'duration'],['storage.vacuum.at',t('VACUUM time, HH:MM'),'text']]},
  {id:'notify',title:t('Notifications'),note:t('Telegram and/or webhook. New settings take effect after the tester restarts. If sending directly from the router fails (the provider often cuts Telegram), the message goes through the tester on the best healthy node. Node switches with the “rotation” reason are frequent; by default only emergencies are reported.'),fields:[
    ['notify.enabled',t('Send notifications'),'checkbox'],['notify.name',t('Router signature in the message'),'text'],
    ['notify.telegram.token',t('Telegram bot token'),'password'],['notify.telegram.chat_id',t('Recipient Telegram chat_id'),'text'],
    ['notify.webhook.url',t('Webhook URL (POST JSON)'),'text'],['notify.proxy',t('Proxy for sending (empty: direct)'),'text'],
    ['notify.expiry_warning',t('Warn about the subscription end in advance by'),'duration']]},
  {title:t('Exit IP type'),note:t('The IP type (data centre, residential, mobile) is requested from ip-api.com for the exit IPs of nodes: through the tester on the best healthy node, otherwise directly.'),fields:[
    ['checks.ip_type.ttl',t('Refresh the data every'),'duration'],['checks.ip_type.via_tester',t('Request through the tester'),'checkbox']]},
]
const NOTIFY_EVENTS={switch:t('Node switch'),failsafe:t('Fallback group'),rollback:t('sing-box config rollback'),pipeline:t('Pipeline error'),subscription:t('Subscription failure'),expiry:t('Subscription and traffic end'),singbox:t('sing-box stop and failure')}
const SWITCH_REASONS={emergency:t('emergency'),['emergency-stuck']:t('emergency without replacement'),rotation:t('rotation'),quality:t('quality'),manual:t('manually'),init:t('first choice')}
const defaultList=field=>defaults.value[field] || []
function toggleList(field,item,on){const cur=[...(value(field) ?? defaultList(field))];const next=on?[...new Set([...cur,item])]:cur.filter(x=>x!==item);setPath(document.value,field,next);preview.value=false}
const notifyBusy=ref(false), notifyResult=ref('')
async function notifyTest(){notifyBusy.value=true;notifyResult.value='';try{const r=await api.post('/notify/test',{});notifyResult.value=r.ok?t('Sent. Check Telegram/webhook.'):t('Not sent: {error}',{error:r.error||t('error')})}catch(e){notifyResult.value=e.message}finally{notifyBusy.value=false}}
function subState(sub){return (options.value.sources || {})[sub.tag] || null}
function subMeta(sub){return subState(sub)?.meta || {}}
function daysLeft(m){return m.expire ? Math.ceil((m.expire*1000-Date.now())/86400000) : null}
function trafficLine(m){if(m.download==null && m.total==null)return '';const used=(m.upload||0)+(m.download||0);return m.total ? t('{used} of {total}',{used:bytes(used),total:bytes(m.total)}) : t('{used}, no limit',{used:bytes(used)})}
function lastOk(st){return st?.last_ok_at ? dateTime(Date.parse(st.last_ok_at)/1000) : t('none')}
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
function addHeader(sub){const key=window.prompt(t('Name of the additional HTTP header'));if(key?.trim())setHeader(sub,key.trim(),'')}
// Сборка nodes.json ветви (pipeline.<ветвь>): фильтры против падения sing-box и параметры групп.
const recipeGroups=[
  {title:t('Schedule and filters'),fields:[['enabled',t('Branch enabled (scheduled build)'),'checkbox'],['at',t('Start time, HH:MM (comma-separated)'),'list'],
    ['exclude_types',t('Exclude outbound types (comma-separated)'),'list'],['exclude_protocols',t('Exclude protocols / transports (comma-separated)'),'list'],['domain_resolver',t('Node DNS resolver tag'),'text']]},
  {title:t('Selector groups'),fields:[['selector.interrupt_exist_connections',t('Interrupt existing connections when the node changes'),'checkbox']]},
  {title:t('failsafe-out group (urltest)'),fields:[['urltest.url',t('Check URL'),'text'],['urltest.interval',t('Interval'),'duration'],['urltest.tolerance',t('Latency tolerance, ms'),'number'],['urltest.idle_timeout',t('Idle timeout'),'duration'],['urltest.interrupt_exist_connections',t('Interrupt existing connections'),'checkbox']]},
]
const recipePath=field=>`pipeline.${branch.value}.${field}`
function recipeValue(field){return value(recipePath(field))}
function recipeUpdate(field,event,type){update(recipePath(field),event,type)}
// selector и urltest передаются в sing-box как есть: без значения действует умолчание sing-box.
function recipePlaceholder(field){return placeholder(recipePath(field)) || (/^(selector|urltest)\./.test(field) ? t('sing-box default') : '')}
function removeSub(index){if(window.confirm(t('Remove this subscription from the file? The running sing-box changes after the pipeline runs.'))) document.value.subscriptions.splice(index,1)}
const SOURCE_KINDS=[['url',t('URL / link')],['happ',t('Happ · encrypted link (crypt–crypt5)')],['file',t('File')],['folder',t('AWG folder')],['inline',t('Own nodes (sing-box outbounds)')]]
const FETCH_FIELDS=[['pipeline.fetch.timeout',t('Download timeout'),'duration'],['pipeline.fetch.min_ratio',t('Minimum share of remaining nodes (0–1)'),'number',{min:0,max:1,step:0.05}],
  ['pipeline.fetch.retries',t('HTTP retries'),'number',{min:0}],['pipeline.fetch.keep_stale',t('Keep previous nodes on failure'),'duration'],
  ['pipeline.fetch.every',t('Poll router subscriptions every (0s: only when building)'),'duration'],['pipeline.fetch.proxy',t('Download proxy (empty: direct)'),'text']]
</script>
<template>
  <p v-if="!can('edit_config')" class="notice">{{ t('The editor requires a verified administrator token. Log in with the button in the header.') }}</p>
  <template v-else>
    <nav v-if="subscriptions && (options.branches || []).length > 1" class="chips" :aria-label="t('Pipeline branch')"><button v-for="set in options.branches" :key="set" class="chip" :class="{on:branch===set}" :disabled="busy" @click="selectDocument(selected,set)">{{ BRANCHES[set] || set }}</button></nav>
    <p v-if="subscriptions && branch==='clients'" class="help-text"><Msg :text="t('Client branch: subscriptions and the build for client configurations (the “Clients” mode in the {pipeline}), the result is {file}. It does not affect the router sing-box.')"><template #pipeline><RouterLink to="/pipeline">{{ t('“Pipeline”') }}</RouterLink></template><template #file><code>whnodes.json</code></template></Msg></p>
    <nav v-if="subscriptions" class="chips" :aria-label="t('Subscription and build sections')"><button v-for="[kind,label] in [['providers',t('Subscription sources')],['build',t('Build and groups')]]" :key="kind" class="chip" :class="{on:selected===kind}" :disabled="busy" @click="selectDocument(kind)">{{ label }}</button></nav>
    <div class="toolbar">
      <button class="btn" :disabled="busy" @click="load">{{ t('Reload file') }}</button>
      <button class="btn" :disabled="busy || !document" @click="switchEditor">{{ advanced ? t('To the form') : t('Advanced JSON') }}</button>
      <button class="btn primary" :disabled="busy || !dirty || !candidate" @click="preview=true">{{ t('Review changes') }}</button>
      <span>{{ dirty ? t('There are unsaved changes') : t('No unsaved changes') }}</span>
    </div>
    <p class="mut break">{{ t('File: {path}', { path: path || t('Loading…') }) }}</p>
    <p v-if="restart" class="notice">{{ t('The settings file differs from the one loaded when the admin panel started. A tester restart is required; applying to the current process is not confirmed.') }}</p>
    <p v-if="defaultsError" class="notice">{{ t('Defaults are not shown: the config schema is unavailable ({error}).', { error: defaultsError }) }}</p>
    <p v-if="message" class="notice" role="status">{{ message }}</p><p v-if="error" class="notice bad" role="alert">{{ error }}</p>
    <section v-if="preview" class="panel">
      <h2>{{ t('Changed fields') }}</h2><p class="break">{{ changed.join(', ') }}</p>
      <p>{{ t('Values are hidden so as not to reveal secrets. The server checks the configuration before an atomic write. After saving, the tester restarts by itself: monitoring and testing stop for a few seconds, user connections are not dropped.') }}{{ subscriptions ? ' ' + t('Subscriptions and groups reach sing-box with the next pipeline build.') : '' }}</p>
      <button class="btn primary" :disabled="busy || !dirty || conflict" @click="save">{{ t('Confirm and save the file') }}</button><p v-if="conflict" class="mut">{{ t('Saving is blocked: reload the file. Unsaved edits will have to be made again.') }}</p>
    </section>
    <template v-if="document">
      <fieldset :disabled="busy" class="config-fields">
      <p class="hint">{{ t('Unknown fields and overrides are kept. Changing the form does not replace the whole document with a template. An empty field means the default value (shown in grey) and is not written to the file.') }}</p>
      <template v-if="advanced"><p class="notice">{{ t('The JSON contains secrets and full subscription URLs. Do not publish it or send screenshots.') }}</p><textarea v-model="text" class="cfg-editor" :aria-label="t('Advanced configuration JSON')" spellcheck="false" /></template>
      <template v-else>
        <label class="check"><input v-model="showSecrets" type="checkbox" /> {{ t('Show secrets and subscription URLs') }}</label>
        <template v-if="recipe">
          <section v-for="group in recipeGroups" :key="group.title" class="panel"><h2>{{ group.title }}</h2>
            <div class="form-grid"><label v-for="[field,label,type] in group.fields" :key="field" :class="{check:type==='checkbox'}">
              <template v-if="type==='checkbox'"><input type="checkbox" :checked="checked(recipePath(field))" @change="recipeUpdate(field,$event,type)" /> {{ label }}</template>
              <template v-else>{{ label }}<input :type="type==='number' ? 'number' : 'text'" :value="type==='list' ? (recipeValue(field) || []).join(', ') : recipeValue(field)" :placeholder="recipePlaceholder(field)" @input="recipeUpdate(field,$event,type)" /></template>
            </label></div>
          </section>
          <NodeGroups v-if="branch==='router'" :doc="document" :checks="options.checks || []" :ip-kinds="options.ip_kinds || []" :builtin="options.builtin_regions || {}" :reserve-default="value('selection.reserve') ?? defaults['selection.reserve']" @changed="preview=false" @valid="jsonValid.regions=$event" />
          <p v-else class="help-text"><Msg :text="t('The client branch has a single group {global} with all nodes and a fallback {failsafe}: the client sing-box makes the choice.')"><template #global><code>global-auto-out</code></template><template #failsafe><code>failsafe-out</code></template></Msg></p>
        </template>
        <template v-else-if="subscriptions">
          <section v-for="{sub,index} in branchSubs" :key="index" class="panel">
            <h2>{{ t('Subscription {n}', { n: index+1 }) }} · {{ sub.tag || t('new') }}</h2>
            <div class="form-grid">
              <label>{{ t('Provider name (unique tag)') }}<input v-model="sub.tag" /></label>
              <label class="check"><input type="checkbox" :checked="sub.enabled !== false" @change="sub.enabled=$event.target.checked" /> {{ t('Use the subscription') }}</label>
              <label>{{ t('Branch') }} <select :value="sub.branch || 'router'" @change="setBranch(sub,$event.target.value)"><option v-for="(name,b) in SUB_BRANCHES" :key="b" :value="b">{{ name }}</option></select></label>
              <label>{{ t('Source') }} <select :value="sourceKind(sub)" @change="changeKind(sub,$event.target.value)"><option v-for="[kind,label] in SOURCE_KINDS" :key="kind" :value="kind">{{ label }}</option></select></label>
              <label>{{ t('Exclude nodes by a name substring (comma-separated)') }}<input :value="(sub.exclude_names || []).join(', ')" @change="setList(sub,'exclude_names',$event.target.value)" /></label>
              <label v-if="['url','happ'].includes(sourceKind(sub))">{{ sourceKind(sub)==='happ' ? t('happ://crypt… link') : t('Subscription URL / share link') }} <input v-model="sub.url" :type="showSecrets ? 'text' : 'password'" autocomplete="off" /></label>
              <label v-else-if="sourceKind(sub)==='file'">{{ t('File') }} <input v-model="sub.file" /></label>
              <template v-else-if="sourceKind(sub)==='folder'"><label>{{ t('Folder') }} <input v-model="sub.path" /></label><label>{{ t('Format') }} <input v-model="sub.format" /></label><label>{{ t('File extension') }} <input v-model="sub.ext" placeholder=".conf" /></label></template>
              <template v-if="sourceKind(sub)==='url'"><label>User-Agent<select :value="uaChoice(sub)" @change="setUA(sub,$event.target.value,true)"><option v-for="o in options.user_agents" :key="o.value" :value="o.value">{{ o.label }}</option><option value="__custom">{{ t('Custom User-Agent') }}</option></select></label><label v-if="uaChoice(sub)==='__custom'">{{ t('Custom User-Agent') }}<input :value="userAgent(sub)" @input="setUA(sub,$event.target.value)" /></label></template>
            </div>
            <JsonField v-if="sourceKind(sub)==='inline'" array :label="t('Outbounds')" :note="t('A list of sing-box outbounds. Tags are set by the build: the provider is the subscription tag.')" :model-value="sub.outbounds || []" @update:model-value="sub.outbounds=$event;preview=false" @valid="jsonValid['inline'+index]=$event" />
            <p v-if="sourceKind(sub)==='url'" class="help-text">{{ t('The User-Agent is sent on HTTP downloads (including sub:// with an HTTP link). It is not used for ready share links and local files. The provider may choose the response format by this header. On an empty HTTP response the loader repeats the request with clashmeta.') }}</p>
            <details v-if="sourceKind(sub)==='url' && !userAgent(sub)"><summary>{{ t('Default User-Agent') }}</summary><p class="break">{{ options.user_agents[0]?.effective }}</p></details>
            <label v-if="sourceKind(sub)==='url'" class="check"><input type="checkbox" :checked="!!sub.send_device" @change="sub.send_device=$event.target.checked || undefined; preview=false" /> {{ t('Send the HWID and device description (for panels with a device limit)') }}</label>
            <div v-if="subState(sub)" class="sub-state">
              <p><span :class="subState(sub).ok ? '' : 'bad'">{{ subState(sub).ok ? t('Fetched') : (subState(sub).stale ? t('Failure: previous nodes are in use') : t('Failure: no nodes')) }}</span> · {{ t('nodes {n}', { n: subState(sub).count }) }} · {{ t('last successful fetch: {time}', { time: lastOk(subState(sub)) }) }}<template v-if="subMeta(sub).title"> · {{ subMeta(sub).title }}</template></p>
              <p v-if="subState(sub).diff">{{ t('Nodes in the active config: {in_config}, by the last poll: {in_poll}', { in_config: subState(sub).diff.in_config, in_poll: subState(sub).diff.in_poll }) }}<template v-if="subState(sub).diff.new || subState(sub).diff.gone"> {{ t('({new} new, {gone} gone: they reach sing-box with the next build)', { new: subState(sub).diff.new, gone: subState(sub).diff.gone }) }}</template></p>
              <p v-if="!subState(sub).ok && subState(sub).error" class="mut break">{{ subState(sub).error }}</p>
              <p v-if="trafficLine(subMeta(sub)) || daysLeft(subMeta(sub)) !== null">{{ t('Traffic') }}: {{ trafficLine(subMeta(sub)) || '—' }}<template v-if="daysLeft(subMeta(sub)) !== null"> · {{ t('valid until {date}', { date: dateTime(subMeta(sub).expire).split(',')[0] }) }} <span :class="daysLeft(subMeta(sub)) <= 3 ? 'bad' : ''">({{ t('{n} days left', { n: daysLeft(subMeta(sub)) }) }})</span></template></p>
              <details v-if="subMeta(sub).announce"><summary>{{ t('Provider message') }}</summary><p class="pre">{{ subMeta(sub).announce }}</p></details>
            </div>
            <details v-if="sourceKind(sub)==='happ'"><summary>{{ t('Happ HTTP headers') }}</summary>
              <p class="help-text">{{ t('The default Happ set is used. A separate user_agent for this type is ignored; the User-Agent is changed here, in happ_headers. Changed values override the standard headers.') }}</p>
              <div class="form-grid"><label v-for="key in happFields(sub)" :key="key">{{ key }}<input type="text" :value="sub.happ_headers?.[key] ?? options.happ_headers[key]" @input="setHeader(sub,key,$event.target.value)" /><span class="mut">{{ Object.hasOwn(sub.happ_headers || {},key) ? t('Overridden') : t('Default') }}</span><button v-if="Object.hasOwn(sub.happ_headers || {},key)" class="btn" @click="removeHeader(sub,key)">{{ Object.hasOwn(options.happ_headers,key) ? t('Restore default') : t('Delete header') }}</button></label></div>
              <button class="btn" @click="addHeader(sub)">{{ t('Add header') }}</button> <button class="btn" @click="delete sub.happ_headers">{{ t('Restore the whole default Happ set') }}</button>
            </details><button class="btn" @click="removeSub(index)">{{ t('Delete subscription') }}</button>
          </section>
          <button class="btn" @click="addSub">{{ t('Add subscription') }}</button>
          <p v-if="options.device?.hwid" class="help-text"><Msg :text="t('Router HWID for subscription panels: {hwid} (file {file}). It is always sent by happ subscriptions and by URL subscriptions when the checkbox is on. A custom HWID for one subscription is set with the X-Hwid header.')"><template #hwid><code>{{ options.device.hwid }}</code></template><template #file>{{ options.device.path }}</template></Msg></p>
          <section v-if="(document.subscriptions || []).some(s=>sourceKind(s)==='happ')" class="panel"><h2>{{ t('Happ keys') }}</h2><div class="form-grid">
            <label>{{ t('Key source') }}<input :value="value('pipeline.fetch.happ_keys_url') ?? options.happ_keys?.default_url" @input="setHappKeys($event.target.value)" /><span class="mut">{{ value('pipeline.fetch.happ_keys_url') ? t('Overridden') : t('Default') }}</span><button v-if="value('pipeline.fetch.happ_keys_url')" class="btn" @click="setHappKeys('')">{{ t('Restore default') }}</button></label>
          </div><p class="help-text">{{ t('The keys for decrypting happ://crypt… links belong to the Happ app and are not part of nodes-tester. They are downloaded from the given source (a URL or a path to a keys.rs or JSON file) and stored in {path}; they are refreshed when no key is found for a link.', { path: options.happ_keys?.path }) }}</p></section>
          <section class="panel"><h2>{{ t('Download protection') }}</h2><div class="form-grid">
            <label v-for="[field,label,type,attrs] in FETCH_FIELDS" :key="field">{{ label }}<input :type="type==='number' ? 'number' : 'text'" v-bind="attrs" :value="value(field)" :placeholder="placeholder(field)" @input="update(field,$event,type)" /></label>
          </div><p class="help-text"><Msg :text="t('When the list shrinks sharply, the fetch guard may keep the previous data. The fetch result is visible in the {pipeline} log.')"><template #pipeline><RouterLink to="/pipeline">{{ t('“Pipeline”') }}</RouterLink></template></Msg></p></section>
        </template>
        <details v-if="subscriptions" class="panel"><summary><b>{{ t('How changes reach sing-box') }}</b></summary>
          <ol>
            <li><Msg :text="t('Subscriptions, build parameters and groups are stored in one {file}; the pipeline and the tester read it.')"><template #file><code>config.json</code></template></Msg></li>
            <li><Msg :text="t('The admin panel runs the pipeline on schedule ({router_at}, {clients_at}) or manually in the {pipeline}: fetching subscriptions, building {nodes}, {check}, applying. sing-box restarts only on changes; without connectivity the previous config comes back.')"><template #router_at><code>pipeline.router.at</code></template><template #clients_at><code>pipeline.clients.at</code></template><template #pipeline><RouterLink to="/pipeline">{{ t('“Pipeline”') }}</RouterLink></template><template #nodes><code>nodes.json</code></template><template #check><code>sing-box check</code></template></Msg></li>
            <li>{{ t('The tester picks up new nodes within a minute; changes of groups and tester settings after its restart.') }}</li>
          </ol>
          <h3>{{ t('Pipeline files') }}</h3>
          <p><Msg :text="t('Fetched subscriptions are {raw_router} and {raw_clients}, the build result is {nodes} and {whnodes} in the data directory (in the OpenWrt package {dir}, set by data_dir in /etc/config/nodes-tester).')"><template #raw_router><code>raw/router.json</code></template><template #raw_clients><code>raw/clients.json</code></template><template #nodes><code>nodes.json</code></template><template #whnodes><code>whnodes.json</code></template><template #dir><code>/opt/nodes-tester</code></template></Msg></p>
          <p><Msg :text="t('A separate run: {fetch}, then {config}.')"><template #fetch><code>nodes-tester fetch -c config.json --branch router -o raw/router.json</code></template><template #config><code>nodes-tester config -c config.json --branch router --raw raw/router.json -o nodes.json</code></template></Msg></p>
        </details>
        <template v-if="!subscriptions">
          <section v-for="group in groups" :key="group.title" class="panel">
            <h2>{{ group.title }}</h2><p v-if="group.note" class="help-text">{{ group.note }}</p>
            <div class="form-grid">
              <label v-for="[field,label,type] in group.fields" :key="field" :class="{check:type==='checkbox'}">
                <template v-if="type==='checkbox'"><input type="checkbox" :checked="checked(field)" @change="update(field,$event,type)" /> {{ label }}</template>
                <template v-else>{{ label }}<input :type="type==='password' && showSecrets ? 'text' : type==='duration' ? 'text' : type" :value="value(field)" :placeholder="type==='password' ? null : placeholder(field)" autocomplete="off" @input="update(field,$event,type)" /></template>
              </label>
            </div>
            <template v-if="group.id==='notify'">
              <p class="mut">{{ t('Events:') }}</p>
              <div class="toolbar"><label v-for="(name,ev) in NOTIFY_EVENTS" :key="ev" class="check"><input type="checkbox" :checked="(value('notify.events') ?? defaultList('notify.events')).includes(ev)" @change="toggleList('notify.events',ev,$event.target.checked)" /> {{ name }}</label></div>
              <p class="mut">{{ t('Node switches: reasons') }}</p>
              <div class="toolbar"><label v-for="(name,r) in SWITCH_REASONS" :key="r" class="check"><input type="checkbox" :checked="(value('notify.switch_reasons') ?? defaultList('notify.switch_reasons')).includes(r)" @change="toggleList('notify.switch_reasons',r,$event.target.checked)" /> {{ name }}</label></div>
              <div v-if="can('notify')" class="actions"><button class="btn" :disabled="notifyBusy" @click="notifyTest">{{ t('Send a test message') }}</button> <span class="mut">{{ notifyResult || t('The test uses the settings of the running tester (after a restart).') }}</span></div>
            </template>
          </section>
          <VacuumPanel v-if="can('vacuum')" />
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
