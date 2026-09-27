<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { useRoute, onBeforeRouteLeave } from 'vue-router'
import { api, auth, can } from '../api.js'
import { getPath, setPath, changedPaths, TEST_NAMES } from '../ux.js'
const route = useRoute(), subscriptions = computed(()=>route.path === '/subscriptions')
const document = ref(null), original = ref(null), text = ref(''), advanced = ref(false)
const path = ref(''), revision = ref(''), busy = ref(false), message = ref(''), error = ref('')
const preview = ref(false), showSecrets = ref(false), restart = ref(false), conflict = ref(false)
const endpoint = computed(()=>subscriptions.value ? '/config/providers' : '/config')
const candidate = computed(()=>{ if(!advanced.value) return document.value; try{return JSON.parse(text.value)}catch{return null} })
const changed = computed(()=>changedPaths(original.value,candidate.value))
const dirty = computed(()=>document.value !== null && (advanced.value ? text.value !== JSON.stringify(original.value,null,2) : changed.value.length > 0))
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
  } catch(e) {error.value=e.message}
  finally {busy.value=false}
}
function switchEditor() {
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
    message.value=subscriptions.value ? 'Подписки сохранены в файл. Загрузка, сборка и применение к sing-box не запускались.' : 'Настройки сохранены в файл. Перезапустите процесс, который использует этот конфиг.'
  } catch(e){error.value=e.message; if(e.status === 409) conflict.value=true}
  finally{busy.value=false}
}
const groups = [
  {title:'Соединение с sing-box',note:'API управляет селекторами, а SOCKS-соединение служит маршрутом тестового трафика. Админка не проверяет соединение при сохранении.',fields:[
    ['clash_api.base_url','Адрес Clash API','text'],['clash_api.secret','Секрет Clash API','password'],['clash_api.timeout','Тайм-аут API, с','number'],
    ['testing_groups.0.connection.host','SOCKS-хост','text'],['testing_groups.0.connection.port','SOCKS-порт','number'],
    ['testing_groups.0.connection.username','SOCKS-логин','text'],['testing_groups.0.connection.password','SOCKS-пароль','password'],
    ['testing_groups.0.selector.group','Тестовый селектор','text']]},
  {title:'План проверок',note:'Форма меняет общие параметры run.default. Переопределения регионов и групп остаются в JSON и могут иметь приоритет.',fields:[
    ['run.default.loop','Непрерывная работа','checkbox'],['run.default.rotation_bound','Привязать проходы к ротации','checkbox'],
    ['run.default.pass_pause','Пауза между проходами, с','number'],['run.default.min_host_gap','Зазор обращений к одному хосту, с','number'],
    ['run.default.request_timeout','Тайм-аут проверки, с','number'],['run.default.heavy_candidates','Кандидатов на тяжёлую проверку в регионе','number']]},
  {title:'Переключение и наблюдение',note:'Включение автоматики может изменить выбор боевых селекторов после перезапуска тестера.',fields:[
    ['switching.enabled','Автоматическое переключение','checkbox'],['switching.rotation.enabled','Ротация','checkbox'],
    ['switching.rotation.interval','Интервал ротации, с','number'],['monitor.enabled','Монитор активных нод','checkbox'],
    ['cooldown.garbage_hours','Длительность карантина, ч','number'],['cooldown.max_skip','Максимум пропускаемых проходов','number']]},
  {title:'Админка и хранение',note:'Смена токена, адреса или порта потребует перезапуска админки. Форма редактирует файл, не настройки текущего процесса.',fields:[
    ['dashboard.enabled','Встроенная админка','checkbox'],['dashboard.host','Адрес прослушивания','text'],['dashboard.port','Порт админки','number'],
    ['dashboard.token','Токен администратора','password'],['dashboard.read_open','Просмотр без токена','checkbox'],
    ['dashboard.providers_file','Файл подписок (относительно config.json)','text'],['storage.enabled','Хранить статистику','checkbox'],
    ['storage.db_file','Файл SQLite','text'],['storage.nodes_file','Файл описаний нод','text'],['storage.retention_days','Хранить историю, дней','number'],
    ['storage.traffic.enabled','Собирать пользовательский трафик','checkbox']]},
]
function sourceKind(sub) {return sub.type === 'folder' ? 'folder' : sub.file != null ? 'file' : 'url'}
function changeKind(sub,kind) {
  delete sub.url; delete sub.file; delete sub.type; delete sub.path
  if(kind === 'folder'){sub.type='folder';sub.path=''; if(!sub.format)sub.format='awg'}
  else sub[kind]=''
  preview.value=false
}
function toggleTest(test,enabled) {
  const tests=[...(value('run.default.tests_enabled') || [])]
  setPath(document.value,'run.default.tests_enabled',enabled ? [...new Set([...tests,test])] : tests.filter(t=>t!==test))
}
function removeSub(index){if(window.confirm('Удалить эту подписку из файла? sing-box изменится только после внешнего применения.')) document.value.subscribes.splice(index,1)}
</script>
<template>
  <section v-if="subscriptions" class="panel">
    <h2>Три разных действия</h2>
    <ol><li>Сохранить подписки здесь: записать providers.json.</li><li>Загрузить подписки, собрать и применить конфигурацию через SSH.</li><li><RouterLink to="/runs">Проверить ноды</RouterLink>, уже доступные в sing-box.</li></ol>
    <p>Админка не запускает конвейер и не знает время последнего применения. Сохранение файла не подтверждает, что новые ноды появились в sing-box.</p>
    <details><summary>Как обновить через SSH</summary><p>Для подготовленного конвейера OpenWrt: <code>nodes-tester pipeline router --dry-run</code>, затем <code>nodes-tester pipeline router</code>. Предварительная сборка тоже загружает подписки и записывает промежуточные файлы; применение может перезапустить sing-box.</p><p>Сначала проверьте необходимые base.json и репозиторий правил по <a href="https://github.com/andreydyadyk/nodes-tester/blob/master/openwrt/README.md" target="_blank" rel="noopener noreferrer">инструкции OpenWrt</a>. Проектный конвейер не является универсальной командой первого запуска.</p></details>
  </section>
  <p v-if="!can('edit_config')" class="notice">Для редактора требуется проверенный токен администратора. Войдите с помощью кнопки в шапке.</p>
  <template v-else>
    <div class="toolbar">
      <button class="btn" :disabled="busy" @click="load">Перечитать файл</button>
      <button class="btn" :disabled="busy || !document" @click="switchEditor">{{ advanced ? 'К форме' : 'Расширенный JSON' }}</button>
      <button class="btn primary" :disabled="busy || !dirty || !candidate" @click="preview=true">Проверить изменения</button>
      <span>{{ dirty ? 'Есть несохранённые изменения' : 'Нет несохранённых изменений' }}</span>
    </div>
    <p class="mut break">Файл: {{ path || 'Загрузка…' }}</p>
    <p v-if="restart" class="notice">Файл настроек отличается от загруженного при старте админки. Перезапуск требуется; применение к текущему процессу не подтверждено.</p>
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
        <template v-if="subscriptions">
          <section v-for="(sub,index) in document.subscribes || []" :key="index" class="panel">
            <h2>Подписка {{ index+1 }} · {{ sub.tag || 'новая' }}</h2>
            <div class="form-grid">
              <label>Название провайдера (уникальный tag)<input v-model="sub.tag" /></label>
              <label class="check"><input type="checkbox" :checked="sub.enabled !== false" @change="sub.enabled=$event.target.checked" /> Использовать подписку</label>
              <label>Источник <select :value="sourceKind(sub)" @change="changeKind(sub,$event.target.value)"><option value="url">URL / happ</option><option value="file">Файл</option><option value="folder">Папка AWG</option></select></label>
              <label v-if="sourceKind(sub)==='url'">URL подписки <input v-model="sub.url" :type="showSecrets ? 'text' : 'password'" autocomplete="off" /></label>
              <label v-else-if="sourceKind(sub)==='file'">Файл <input v-model="sub.file" /></label>
              <template v-else><label>Папка <input v-model="sub.path" /></label><label>Формат <input v-model="sub.format" /></label><label>Расширение файлов <input v-model="sub.ext" placeholder=".conf" /></label></template>
              <label v-if="sourceKind(sub)==='url'">User-Agent<input :value="sub.user_agent ?? sub['User-Agent'] ?? ''" @input="sub[Object.hasOwn(sub,'User-Agent') && !Object.hasOwn(sub,'user_agent') ? 'User-Agent' : 'user_agent']=$event.target.value" /></label>
            </div><button class="btn" @click="removeSub(index)">Удалить подписку</button>
          </section>
          <button class="btn" @click="(document.subscribes ||= []).push({tag:'',url:'',enabled:true})">Добавить подписку</button>
          <section class="panel"><h2>Защита загрузки</h2><div class="form-grid">
            <label>Тайм-аут, с<input type="number" :value="value('fetch.timeout')" @input="update('fetch.timeout',$event,'number')" placeholder="По умолчанию" /></label>
            <label>Минимальная доля оставшихся нод (0–1)<input type="number" min="0" max="1" step="0.05" :value="value('fetch.min_ratio')" @input="update('fetch.min_ratio',$event,'number')" placeholder="По умолчанию" /></label>
          </div><p>При резком уменьшении списка fetch guard может сохранить предыдущие данные. Статус этого выполнения нужно смотреть в журнале конвейера по SSH.</p></section>
        </template>
        <template v-else>
          <section v-for="group in groups" :key="group.title" class="panel">
            <h2>{{ group.title }}</h2><p class="mut">{{ group.note }}</p>
            <div class="form-grid">
              <label v-for="[field,label,type] in group.fields" :key="field" :class="{check:type==='checkbox'}">
                <template v-if="type==='checkbox'"><input type="checkbox" :checked="!!value(field)" @change="update(field,$event,type)" /> {{ label }}</template>
                <template v-else>{{ label }}<input :type="type==='password' && showSecrets ? 'text' : type" :value="value(field)" autocomplete="off" @input="update(field,$event,type)" /></template>
              </label>
            </div>
            <div v-if="group.title==='План проверок'" class="toolbar"><label v-for="(name,test) in Object.fromEntries(Object.entries(TEST_NAMES).filter(([key])=>key!=='heavy_download'))" class="check" :key="test"><input type="checkbox" :checked="(value('run.default.tests_enabled') || []).includes(test)" @change="toggleTest(test,$event.target.checked)" /> {{ name }}</label></div>
          </section>
        </template>
      </template>
      </fieldset>
    </template>
  </template>
</template>
