import assert from 'node:assert/strict'
import test from 'node:test'
import { normalizeOutputLayout, normalizeOutputNaming, normalizeStatus, validateSeparateState } from '../src/stores/taskNormalize.ts'

test('output layout and naming normalize current editable preferences',()=>{
 assert.equal(normalizeOutputLayout('flat'),'flat');assert.equal(normalizeOutputLayout('folders'),'folders')
 assert.equal(normalizeOutputLayout('unknown'),'folders');assert.equal(normalizeOutputNaming(undefined),undefined)
 assert.deepEqual(normalizeOutputNaming({enabled:true,template:' {stem} ',stemOrder:[' vocals ','','drums']}),{enabled:true,template:'{stem}',stemOrder:['vocals','drums']})
})
test('current separate preferences reject missing, old and future schema without mutation',()=>{
 assert.equal(validateSeparateState(null),null)
 assert.deepEqual(validateSeparateState({version:1}),{version:1})
 for(const value of [{},{version:0},{version:2},{version:true}]){
  const before=JSON.stringify(value);assert.throws(()=>validateSeparateState(value),/Unsupported/);assert.equal(JSON.stringify(value),before)
 }
})
test('stage display handles recognized and absent live event stages',()=>{
 assert.equal(normalizeStatus('separating'),'separating');assert.equal(normalizeStatus('done'),'done')
 assert.equal(normalizeStatus('unrecognized'),'preparing');assert.equal(normalizeStatus(undefined),'queued')
})
