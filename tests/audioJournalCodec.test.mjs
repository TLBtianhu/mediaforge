import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import test, { after } from 'node:test'
import { createServer } from 'vite'
import { recordsFromEnqueue } from './_audioJournalFixture.mjs'

const vite = await createServer({configFile:false,server:{watch:null,middlewareMode:true,hmr:false,ws:false,preTransformRequests:false},appType:'custom',optimizeDeps:{noDiscovery:true},resolve:{alias:{'@':fileURLToPath(new URL('../src',import.meta.url))}}})
after(()=>vite.close())
const {validateJournalJob,validateJournalSnapshot}=await vite.ssrLoadModule('/src/features/tasks/journal.ts')
const {audioTaskFromJob,audioTasksFromJobs,audioEnqueueFromTasks,audioJobEvent}=await vite.ssrLoadModule('/src/features/tasks/audioJobs.ts')
const {createWorkflowBindings,prepareWorkbenchRun,workbenchExecutionMatches}=await vite.ssrLoadModule('/src/workflows/workbench.ts')
const fixture=JSON.parse(readFileSync(new URL('./fixtures/jobs/current-contract.json',import.meta.url),'utf8'))
const clone=value=>JSON.parse(JSON.stringify(value))
const snapshot=jobs=>({schemaVersion:3,full:true,removed:[],jobs,dataRoot:'/fixture',jobsDir:'/fixture/agent-jobs'})

test('all shared current records validate together without materializing child payloads',()=>{
  const raw=clone(fixture.records),before=JSON.stringify(raw)
  assert.equal(validateJournalSnapshot(snapshot(raw)).jobs.length,raw.length)
  assert.equal(JSON.stringify(raw),before)
  const child=raw.find(job=>job.jobId==='audio-child')
  assert.equal(child.payloadRef,'audio-primary')
  assert.equal(Object.hasOwn(child,'payload'),false)
  assert.equal(JSON.stringify(raw).split('ONE_GRAPH_ONLY').length-1,1)
})

for(const invalid of fixture.invalidRecords) test(`shared schema rejects ${invalid.name} without rewriting input`,()=>{
  const record=clone(fixture.records.find(job=>job.jobId===invalid.base))
  for(const key of invalid.remove||[])delete record[key]
  Object.assign(record,invalid.set||{})
  const before=JSON.stringify(record)
  assert.throws(()=>validateJournalJob(record),/Unsupported task journal schema/)
  assert.equal(JSON.stringify(record),before)
})

test('a missing primary, mismatched membership or duplicate record cannot become an empty history',()=>{
  const child=clone(fixture.records.find(job=>job.jobId==='audio-child'))
  assert.throws(()=>validateJournalSnapshot(snapshot([child])),/Unsupported/)
  assert.throws(()=>validateJournalSnapshot(snapshot([...fixture.records,fixture.records[0]])),/Unsupported/)
  const primary=clone(fixture.records[0]);primary.audio.memberJobIds.push('missing')
  assert.throws(()=>validateJournalSnapshot(snapshot([primary,child])),/Unsupported/)
})

test('owner-backed active stages remain active on projection; only backend interruption becomes failed',()=>{
  const raw=clone(fixture.records.find(job=>job.jobId==='model-primary'))
  Object.assign(raw,{status:'running',fraction:0.42,startedAt:1700000001,owner:{kind:'tauri-worker',pid:123456}})
  raw.runtime.stage='separating';raw.runtime.progress={done:2,total:5,unit:'seconds',message:'current audio'}
  const active=audioTaskFromJob(raw,[raw],true)
  assert.equal(active.status,'separating');assert.equal(active.progress,42)
  assert.equal(active.progressCurrent,2);assert.equal(active.progressTotal,5)
  assert.deepEqual(active.logs,[])
  raw.status='interrupted';raw.finishedAt=1700000002;raw.message='backend lease owner is gone'
  assert.equal(audioTaskFromJob(raw,[raw]).status,'failed')
  assert.equal(audioTaskFromJob(raw,[raw]).error,raw.message)
  const queued=clone(fixture.records.find(job=>job.jobId==='model-primary'))
  assert.equal(audioTaskFromJob(queued,[queued]).status,'queued')
})

test('canonical output roots, leaf outputs, node values and duration survive projection',()=>{
  const raw=clone(fixture.records.find(job=>job.jobId==='model-primary'))
  Object.assign(raw,{status:'completed',fraction:1,startedAt:100,finishedAt:102,result:{outputRoot:'D:/physical root ',outputDir:'D:/physical root /song',outputs:[{stem:'Vocals',path:'D:/physical root /song/Vocals.wav'}],nodeValues:[{nodeId:1,nodeType:'ASR',slot:0,value:'transcript'}]}})
  const task=audioTaskFromJob(raw,[raw],true)
  assert.equal(task.jobOutput,'D:/physical root ')
  assert.equal(task.output,'D:/physical root /song')
  assert.deepEqual(task.files,task.outputs.map(output=>output.path))
  assert.equal(task.nodeValues[0].value,'transcript');assert.equal(task.durationMs,2000)
})

test('audio enqueue carries one common graph and only individual leaf requests',()=>{
  const tasks=audioTasksFromJobs(fixture.records,true).filter(task=>task.jobId==='group-fixture').sort((a,b)=>a.batchIndex-b.batchIndex)
  const enqueue=audioEnqueueFromTasks(tasks)
  assert.equal(enqueue.tasks.length,2)
  assert.equal(JSON.stringify(enqueue).split('ONE_GRAPH_ONLY').length-1,1)
  assert.equal(Object.hasOwn(enqueue.payload,'tasks'),false)
  assert.equal(Object.hasOwn(enqueue.payload,'input'),false)
  assert.deepEqual(enqueue.presentation.workbench,{nativeGraph:true})
  assert.equal(JSON.stringify(enqueue).includes('sourceDefinition'),false)
  assert.equal(JSON.stringify(enqueue).includes('runConfig'),false)
})

test('terminal audio events cannot replace completed outputs with late worker payloads',()=>{
  const raw=clone(fixture.records.find(job=>job.jobId==='model-primary'))
  Object.assign(raw,{status:'completed',finishedAt:1700000002,result:{outputs:[{path:'good.wav'}]}})
  const before=JSON.stringify(raw)
  assert.equal(audioJobEvent(raw,[raw],{type:'task.result',taskId:raw.jobId,payload:{outputs:[{path:'late.wav'}]}}),raw)
  assert.equal(JSON.stringify(raw),before)
})

test('simple workbench highlighting matches effective leaf requests after journal roundtrip',()=>{
  const definition={version:1,steps:[{id:'split',model:'model',save:{Vocals:'Default'}}]}
  const bindings=createWorkflowBindings()
  bindings.inputs={input:{files:['D:/input/first.wav','D:/input/second.wav'],folder:'D:/picker-only'}}
  bindings.outputs={'save:split:Vocals':'D:/chosen-output'}
  bindings.outputLayout='folders'
  const prepared=prepareWorkbenchRun(definition,bindings,'D:/default')
  const tasks=audioTasksFromJobs(fixture.records,true).filter(task=>task.jobId==='group-fixture').sort((a,b)=>a.batchIndex-b.batchIndex)
  tasks.forEach((task,index)=>{
    task.input=prepared.runs[index].input
    task.jobOutput='D:/default'
    task.runConfig.workflowDefinition=definition
    task.runConfig.workbench={version:2,sourceUpdatedAt:1,nativeGraph:false,bindings,
      inputs:prepared.runs[index].inputs,outputs:prepared.runs[index].outputs}
  })
  const records=recordsFromEnqueue(audioEnqueueFromTasks(tasks),1700000000)
  validateJournalSnapshot(snapshot(records))
  assert.equal(JSON.stringify(records).includes('D:/picker-only'),false)
  const restored=audioTasksFromJobs(records,true).sort((a,b)=>a.batchIndex-b.batchIndex)
  const runs=restored.map(task=>({input:task.input,inputs:task.runConfig.workbench.inputs,outputs:task.runConfig.workbench.outputs}))
  assert.equal(workbenchExecutionMatches(definition,bindings,restored[0].runConfig.workflowDefinition,runs,'D:/default'),true)
  const changed=clone(bindings)
  changed.outputs['save:split:Vocals']='D:/different-output'
  assert.equal(workbenchExecutionMatches(definition,changed,restored[0].runConfig.workflowDefinition,runs,'D:/default'),false)
})
