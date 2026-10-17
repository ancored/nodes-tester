// Render actual compiled Vue views with a memory router and synthetic API data.
// This is a component smoke test, NOT a substitute for visual browser review.
import assert from 'node:assert/strict'
import { createServer } from 'vite'
import { createSSRApp, h } from 'vue'
import { renderToString } from 'vue/server-renderer'
import { createRouter, createMemoryHistory, RouterView } from 'vue-router'

const node = {crc:'abcd1234.0000beef',node:'DEMO-vless-out [abcd1234.0000beef]',provider:'DEMO',country:'nl',protocol:'vless',
  present:1,banned:0,score:null,region:'eu',can_activate:false}
const data = {nodes:[node],source:{state:'ok',message:'Снимок SQLite'},rating:[],results:{rows:[],tests:[]},history:[],
  runner:{running:true,progress:{phase:'waiting'},regions:[],request_queued:true,forced_left:1},
  node_events:[],traffic_range:{},retention_days:30}
globalThis.fetch = async()=>new Response(JSON.stringify(data),{status:200})
const vite = await createServer({configLoader:'native',server:{middlewareMode:true},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}})
const store = await vite.ssrLoadModule('/src/store.js')
try {
  Object.assign(store.useSnapshot(),{data,ready:true,loading:false,error:''})
  await store.refresh()
  const session = await vite.ssrLoadModule('/src/api.js')
  session.auth.capabilities={mode:'embedded',edit_config:true,node_actions:true,switch:true}
  const i18n = await vite.ssrLoadModule('/src/i18n.js')
  // Каждая страница — на обоих языках: английский по умолчанию, русский из каталога.
  for(const [view,path,expected,english] of [
    ['Control','/nodes','чтобы открыть карточку','to open its card'],
    ['Control','/nodes?q=not-found','Ничего не найдено','Nothing found'],
    ['NodeDetail','/nodes/abcd1234.0000beef','Нет замеров рейтинга','No rating measurements'],
    ['NodeDetail','/nodes/abcd1234.0000beef','<span class="crc-cred" title="CRC учётных данных">0000beef</span>','<span class="crc-cred" title="Credentials CRC">0000beef</span>'],
    ['NodeDetail','/nodes/abcd1234.0000beef?tab=actions','disabled','disabled'],
    ['Control','/nodes','<span class="crc-node" title="CRC настроек сервера">abcd1234</span>','<span class="crc-node" title="Server settings CRC">abcd1234</span>'],
    ['Overview','/','Ожидание сроков тестов','Waiting for test deadlines'],
    ['Results','/results','Сохранённых результатов нет','No saved results'],
    ['History','/history','Последние 200 записей','The last 200 records'],
    ['Config','/subscriptions','Для редактора требуется','The editor requires'],
    ['Runs','/runs','disabled','disabled'],
    ['Traffic','/traffic','Это не последние 24 часа','This is not the last 24 hours'],
    ['Pipeline','/pipeline','Конвейер обновления','Update pipeline'],
    ['Singbox','/singbox','Исходные файлы sing-box','sing-box source files'],
  ]) {
    const page = (await vite.ssrLoadModule('/src/views/'+view+'.vue')).default
    for (const [code, text] of [['ru', expected], ['en', english]]) {
      i18n.setLang(code)
      const router=createRouter({history:createMemoryHistory(),routes:[{path:'/nodes/:crc',component:page},{path:'/:rest(.*)*',component:page}]})
      const app=createSSRApp({render:()=>h(RouterView)});app.use(router);await router.push(path);await router.isReady()
      const html=await renderToString(app)
      assert(html.includes(text),view+' ('+code+'): expected '+text)
      assert(!html.includes('YOUR-CLASH-API-SECRET'))
      if (code === 'en') assert(!/[А-Яа-яЁё]/.test(html.replace('Снимок SQLite','')), view+': Cyrillic in the English page')
    }
    console.log('Rendered '+path+' OK')
  }
} finally {store.stopSnapshot();await vite.close()}
