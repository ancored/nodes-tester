import { test } from 'node:test'
import assert from 'node:assert/strict'
const stored = new Map()
globalThis.localStorage = { getItem:k=>stored.get(k), setItem:(k,v)=>stored.set(k,v), removeItem:k=>stored.delete(k) }
const { auth, login, logout, can, getToken, api, retrySession } = await import('../src/api.js')
const response = (data,status=200)=>new Response(JSON.stringify(data),{status,headers:{'Content-Type':'application/json'}})

test('typed token grants no rights until validated; remember is opt-in', async()=>{
  let finish, header
  globalThis.fetch=async(url,options)=>{ header=options.headers; return await new Promise(resolve=>{finish=resolve}) }
  const pending=login('  demo-token  ')
  assert.equal(auth.verified,false)
  assert.equal(can('node_actions'),false)
  assert.equal(header['X-Admin-Token'],'demo-token')
  finish(response({capabilities:{node_actions:true,edit_config:true}}));await pending
  assert.equal(can('node_actions'),true)
  assert.equal(stored.size,0)
  logout(); assert.equal(getToken(),'');assert.equal(can('node_actions'),false)
})
test('invalid token is removed and an error is visible', async()=>{
  stored.set('nt_admin_token','old')
  globalThis.fetch=async()=>response({error:'Неверный токен'},401)
  await login('invalid',true)
  assert.equal(auth.verified,false);assert.equal(getToken(),'');assert.equal(stored.size,0)
  assert.equal(auth.error,'Неверный токен')
})
test('network or server failure keeps the remembered token for a retry', async()=>{
  stored.set('nt_admin_token','kept')
  globalThis.fetch=async()=>{ throw new TypeError('Failed to fetch') }
  await login('kept',true)
  assert.equal(auth.verified,false);assert.equal(getToken(),'kept');assert.equal(stored.get('nt_admin_token'),'kept')
  assert.match(auth.error,/Нет связи/)
  globalThis.fetch=async()=>response({error:'boom'},500)
  await login('kept',true)
  assert.equal(stored.get('nt_admin_token'),'kept')
  const epoch=auth.epoch
  globalThis.fetch=async()=>response({capabilities:{edit_config:true}})
  await retrySession()
  assert.equal(auth.verified,true);assert.equal(auth.epoch,epoch);assert.equal(auth.error,'')
  logout()
})
test('logout invalidates an in-flight login response', async()=>{
  let finish
  globalThis.fetch=async()=>await new Promise(resolve=>{finish=resolve})
  const pending=login('demo',true);logout()
  finish(response({capabilities:{edit_config:true}}));await pending
  assert.equal(auth.verified,false);assert.equal(stored.size,0)
})
test('save sends the exact revision and preserves full body', async()=>{
  let sent
  globalThis.fetch=async(url,options)=>{sent={url,...options};return response({ok:true})}
  const body={subscribes:[{tag:'demo',url:'https://example.invalid',unknown:42}],extra:true}
  await api.put('/config/providers',body,{'If-Match':'rev'})
  assert.equal(sent.headers['If-Match'],'rev');assert.equal(sent.url,'/api/config/providers')
  assert.deepEqual(JSON.parse(sent.body),body)
})
