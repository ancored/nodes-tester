import { test } from 'node:test'
import assert from 'node:assert/strict'
import { getPath, setPath, changedPaths, nodeFlags, crcOf, rowKeys } from '../src/ux.js'
import { nodeStatus } from '../src/status.js'

test('nested form fields retain unknown config and group overrides', () => {
  const doc = { custom:42, testing_groups:[{connection:{host:'old',custom:'kept'}}], run:{region_groups_specifics:[{tag:'eu'}]} }
  setPath(doc,'testing_groups.0.connection.host','new')
  setPath(doc,'dashboard.token','demo')
  assert.equal(getPath(doc,'testing_groups.0.connection.host'),'new')
  assert.equal(doc.testing_groups[0].connection.custom,'kept')
  assert.equal(doc.custom,42)
  assert.deepEqual(doc.run.region_groups_specifics,[{tag:'eu'}])
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
test('stable CRC links work in all legacy table shapes', () => {
  assert.equal(crcOf({crc:'1234abcd'}),'1234abcd')
  assert.equal(crcOf({id:'1234abcd'}),'1234abcd')
  assert.equal(crcOf({node:'DEMO-out [1234abcd]'}),'1234abcd')
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
