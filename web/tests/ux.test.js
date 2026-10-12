import { test } from 'node:test'
import assert from 'node:assert/strict'
import { getPath, setPath, changedPaths, nodeFlags, crcOf, rowKeys, splitNodeKey, nodeJsonLines } from '../src/ux.js'
import { nodeStatus } from '../src/status.js'

test('nested form fields retain unknown config and group overrides', () => {
  const doc = { custom:42, testing_groups:[{connection:{host:'old',custom:'kept'}}], run:{custom_list:[{tag:'eu'}]} }
  setPath(doc,'testing_groups.0.connection.host','new')
  setPath(doc,'dashboard.token','demo')
  assert.equal(getPath(doc,'testing_groups.0.connection.host'),'new')
  assert.equal(doc.testing_groups[0].connection.custom,'kept')
  assert.equal(doc.custom,42)
  assert.deepEqual(doc.run.custom_list,[{tag:'eu'}])
})
test('preview lists changed field paths, never secret values', () => {
  const changes = changedPaths({secret:'old',subscribes:[{tag:'a'}]}, {secret:'new-secret',subscribes:[{tag:'b'}]})
  assert.deepEqual(changes,['secret','subscribes.0.tag'])
  assert(!JSON.stringify(changes).includes('new-secret'))
})
test('independent restrictions and presence are not lost', () => {
  const flags=nodeFlags({present:0,banned:1,gstate:'garbage',active:1,score:null})
  assert.equal(flags.length,5)
  assert.equal(nodeStatus({score:null}).key,'unknown')
  assert.equal(nodeStatus({score:0}).key,'zero')
})
test('node key links work in all table shapes', () => {
  assert.equal(crcOf({crc:'1234abcd.0000beef'}),'1234abcd.0000beef')
  assert.equal(crcOf({id:'1234abcd.0000beef'}),'1234abcd.0000beef')
  assert.equal(crcOf({node:'DEMO-vless-out [1234abcd.0000beef]'}),'1234abcd.0000beef')
})
test('node key splits into server and credentials parts for colouring', () => {
  assert.deepEqual(splitNodeKey('DEMO-vless|reality-out [1234abcd.0000beef]'),
    { head:'DEMO-vless|reality-out [', node:'1234abcd', cred:'0000beef', tail:']' })
  assert.deepEqual(splitNodeKey('1234abcd.0000beef'), { head:'', node:'1234abcd', cred:'0000beef', tail:'' })
  assert.equal(splitNodeKey('global-auto-out'), null)
})
test('node JSON lines are marked by the CRC they feed', () => {
  const fragment = { tag:'T', type:'vless', uuid:'U', tls:{ server_name:'s', reality:{ public_key:'K', short_id:'ab' } },
    peers:[{ address:'1.2.3.4', pre_shared_key:'P' }], domain_resolver:'bootstrap' }
  const lines = nodeJsonLines(fragment, ['type','uuid','tls','peers'], [['uuid'],['tls','reality','short_id'],['peers',0,'pre_shared_key']])
  const kind = text => lines.find(l => l.text.trim().startsWith(text)).kind
  assert.equal(kind('"tag"'), '')
  assert.equal(kind('"domain_resolver"'), '')
  assert.equal(kind('"type"'), 'node')
  assert.equal(kind('"uuid"'), 'cred')
  assert.equal(kind('"public_key"'), 'node')
  assert.equal(kind('"short_id"'), 'cred')
  assert.equal(kind('"address"'), 'node')
  assert.equal(kind('"pre_shared_key"'), 'cred')
  assert.deepEqual(JSON.parse(lines.map(l => l.text).join('\n')), fragment)
})
test('table row keys are unique even when rows share identity-like fields', () => {
  const eu = {provider:'AWG',group:'eu',total:6}, ai = {provider:'AWG',group:'ai',total:6}, dup = {...eu}
  const keys = rowKeys([eu,ai,dup])
  assert.equal(new Set(keys.values()).size,3)
  assert.equal(rowKeys([ai,eu]).get(eu),keys.get(eu))
})

test('country codes become flags, other values stay as is', async () => {
  const { flag } = await import('../src/format.js')
  assert.equal(flag('nl'), '🇳🇱')
  assert.equal(flag('UK'), '🇬🇧')
  assert.equal(flag('other'), 'other')
  assert.equal(flag(null), '—')
})
