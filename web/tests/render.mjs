// Render actual compiled Vue views with a memory router and synthetic API data.
// This is a component smoke test, NOT a substitute for visual browser review.
import assert from 'node:assert/strict'
import { createServer } from 'vite'
import { createSSRApp, h } from 'vue'
import { renderToString } from 'vue/server-renderer'
import { createRouter, createMemoryHistory, RouterView } from 'vue-router'

const node = {crc:'abcd1234',node:'DEMO-vless-nl-out [abcd1234]',provider:'DEMO',country:'nl',protocol:'vless',
  present:1,banned:0,score:null,region:'eu',can_activate:false}
const data = {nodes:[node],source:{state:'ok',message:'Снимок SQLite'},rating:[],results:{rows:[],tests:[]},history:[],
  runner:{running:true,pass:2,day:'2026-09-27',progress:{phase:'waiting',total:1,processed:1},regions:[],request_queued:true},
  node_events:[],traffic_range:{},retention_days:30}
globalThis.fetch = async()=>new Response(JSON.stringify(data),{status:200})
const vite = await createServer({configLoader:'native',server:{middlewareMode:true},appType:'custom',optimizeDeps:{noDiscovery:true,include:[]}})
const store = await vite.ssrLoadModule('/src/store.js')
try {
  Object.assign(store.useSnapshot(),{data,ready:true,loading:false,error:''})
  await store.refresh()
  const session = await vite.ssrLoadModule('/src/api.js')
  session.auth.capabilities={mode:'embedded',edit_config:true,node_actions:true,switch:true}
  for(const [view,path,expected] of [
    ['Control','/nodes','Открыть карточку'],
    ['Control','/nodes?q=not-found','Ничего не найдено'],
    ['NodeDetail','/nodes/abcd1234','Нет замеров рейтинга'],
    ['NodeDetail','/nodes/abcd1234?tab=Действия','disabled'],
    ['Overview','/','Ожидание следующего прохода'],
    ['Results','/results','Сохранённых результатов нет'],
    ['History','/history','Последние 200 записей'],
    ['Config','/subscriptions','Для редактора требуется'],
    ['Runs','/runs','disabled'],
    ['Traffic','/traffic','Это не последние 24 часа'],
    ['Pipeline','/pipeline','Конвейер обновления'],
    ['Singbox','/singbox','Исходные файлы sing-box'],
  ]) {
    const page = (await vite.ssrLoadModule('/src/views/'+view+'.vue')).default
    const router=createRouter({history:createMemoryHistory(),routes:[{path:'/nodes/:crc',component:page},{path:'/:rest(.*)*',component:page}]})
    const app=createSSRApp({render:()=>h(RouterView)});app.use(router);await router.push(path);await router.isReady()
    const html=await renderToString(app)
    assert(html.includes(expected),view+': expected '+expected)
    assert(!html.includes('YOUR-CLASH-API-SECRET'))
    console.log('Rendered '+path+' OK')
  }
} finally {store.stopSnapshot();await vite.close()}
