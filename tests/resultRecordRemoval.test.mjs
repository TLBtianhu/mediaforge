import assert from 'node:assert/strict'
import test,{after} from 'node:test'
import {fileURLToPath} from 'node:url'
import {createServer} from 'vite'
import {createPinia} from 'pinia'
import {nextTick} from 'vue'
import {recordsFromEnqueue} from './_audioJournalFixture.mjs'

const id='\0audio-removal-fixture'
const vite=await createServer({configFile:false,server:{watch:null,middlewareMode:true,hmr:false,ws:false,preTransformRequests:false},appType:'custom',optimizeDeps:{noDiscovery:true},resolve:{alias:{'@tauri-apps/api/core':'\0removal-core','@tauri-apps/api/event':'\0removal-events','@':fileURLToPath(new URL('../src',import.meta.url))}},plugins:[{
 name:'canonical-audio-removal-boundary',enforce:'pre',
 resolveId(value){return [id,'\0removal-core','\0removal-events'].includes(value)?value:null},
 load(value){
  if(value==='\0removal-core')return `export {invoke} from '${id}'`
  if(value==='\0removal-events')return 'export const listen=async()=>()=>{}'
  if(value===id)return `export const calls=[];let records=[],removeHook;
   export function reset(rows){calls.length=0;records=structuredClone(rows);removeHook=undefined}
   export function setRemoveHook(hook){removeHook=hook}
   export function rows(){return structuredClone(records)}
   export async function invoke(command,args){calls.push({command,args});
    if(command==='agent_jobs')return {schemaVersion:2,jobs:rows(),dataRoot:'/fixture',jobsDir:'/fixture/agent-jobs'};
    if(command==='load_app_store'){if(args.name==='task-history')throw Error('Removed store');return {version:1}};
    if(command==='save_app_store')return;
    if(command==='remove_audio_jobs'){
     if(removeHook)await removeHook(args);
     const selected=new Set(args.jobIds),groups=new Set(records.filter(job=>selected.has(job.jobId)).map(job=>job.audio.groupId));
     const removing=records.filter(job=>groups.has(job.audio.groupId));
     if(removing.some(job=>!['completed','failed','cancelled','interrupted'].includes(job.status)))throw Error('Active group');
     if(args.checkOnly)return {schemaVersion:2,removedJobIds:[],checkedJobIds:removing.map(job=>job.jobId)};
     records=records.filter(job=>!groups.has(job.audio.groupId));
     return {schemaVersion:2,removedJobIds:removing.map(job=>job.jobId)};
    }
    throw Error('Unexpected '+command)
   }`
 },
 transform(_code,path){path=path.replaceAll('\\','/');
  if(path.endsWith('/src/stores/settings.ts'))return 'export const useSettingsStore=()=>({maxConcurrentSeparations:1,developerMode:false})'
  if(path.endsWith('/src/stores/model.ts'))return 'export const useModelStore=()=>({models:[],selectedModel:""})'
  if(path.endsWith('/src/stores/app.ts'))return 'export const useAppStore=()=>({envInfo:{platform:"win32"}})'
  if(path.endsWith('/src/utils/taskOwner.ts'))return 'export const isMainTaskOwner=()=>false'
 }
}]})
after(()=>vite.close())
globalThis.window={__TAURI_INTERNALS__:{},matchMedia:()=>({matches:false,addEventListener(){},removeEventListener(){}}),addEventListener(){},removeEventListener(){}}
const fixture=await vite.ssrLoadModule(id)
const {useTaskStore}=await vite.ssrLoadModule('/src/stores/task.ts')
const {useMediaJobsStore}=await vite.ssrLoadModule('/src/stores/mediaJobs.ts')
const tick=async()=>{await nextTick();await new Promise(resolve=>setImmediate(resolve));await nextTick()}
function group(name,statuses=['completed'],workbench=false){
 const command=workbench?'infer_workflow':'infer',primary=name+'-0'
 const payload={output:'D:/out',outputLayout:'folders',source:'modelscope',downloadMethod:'aria2c',modelDir:null,device:'cpu',deviceIds:[0],outputFormat:'wav',selectedStems:['Vocals'],inferenceParamsVersion:3,inferenceParams:{},audioParams:{},useTta:false,debug:false,...(workbench?{workflowName:'Workflow',workflow:{version:0.4,nodes:[],links:[]}}:{model:'Fixture'})}
 return recordsFromEnqueue({schemaVersion:2,command,groupId:name,primaryJobId:primary,payload,...(workbench?{presentation:{workflowId:'workflow',workbench:{nativeGraph:true}}}:{}),tasks:statuses.map((_,index)=>({jobId:name+'-'+index,request:{input:'D:/input/'+index+'.wav',output:'D:/out/'+index,inputIndex:index+1}}))},1700000000).map((job,index)=>({...job,status:statuses[index],startedAt:1700000001,updatedAt:1700000002,finishedAt:['completed','failed','cancelled','interrupted'].includes(statuses[index])?1700000002:null,owner:statuses[index]==='running'?{kind:'tauri-worker',pid:123456}:null,logs:[{schemaVersion:1,id:job.jobId+'-log',timestamp:'2026-11-07T00:00:00Z',source:'worker',level:'info',logger:'worker',event:'task.log',message:'Run log',taskId:job.jobId,context:{}}],result:statuses[index]==='completed'?{outputs:[{stem:'Vocals',path:'D:/out/'+job.jobId+'.wav'}]}:null}))
}
async function setup(t,records){fixture.reset(records);const pinia=createPinia();const store=useTaskStore(pinia),journal=useMediaJobsStore(pinia);await store.initialize();t.after(()=>{store.$dispose();journal.$dispose()});return {store,journal}}

test('result deletion removes the full terminal group and its logs from the canonical journal',async t=>{
 const {store,journal}=await setup(t,[...group('batch',['completed','failed','cancelled']),...group('other')])
 assert.equal(await store.removeResult('batch-0'),3)
 assert.deepEqual(store.tasks.map(task=>task.id),['other-0'])
 assert.deepEqual(fixture.rows().map(job=>job.jobId),['other-0'])
 assert.deepEqual(journal.jobs.map(job=>job.jobId),['other-0'])
 assert.equal(fixture.calls.some(call=>call.command.includes('app_store')&&call.args?.name==='task-history'),false)
})
test('active or queued siblings block removal before the backend transaction',async t=>{
 for(const status of ['running','queued']){
  const {store}=await setup(t,group('batch',['completed',status]))
  const before=fixture.calls.length
  await assert.rejects(store.removeResult('batch-0'),/running|运行/i)
  assert.equal(fixture.calls.slice(before).some(call=>call.command==='remove_audio_jobs'),false)
  assert.equal(store.tasks.length,2)
 }
})
test('empty selection does not delete all records and clear-results preserves unrelated failures',async t=>{
 const {store}=await setup(t,[...group('one'),...group('failed',['failed'])])
 assert.equal(await store.clearResults([]),0)
 assert.equal(await store.clearResults(),1)
 assert.deepEqual(store.tasks.map(task=>task.id),['failed-0'])
})
test('a rejected removal never hides records or overwrites concurrent journal progress',async t=>{
 const {store,journal}=await setup(t,[...group('result'),...group('live',['running'])])
 let reject;const gate=new Promise((_,no)=>{reject=no});fixture.setRemoveHook(()=>gate)
 const original=store.tasks.find(task=>task.id==='live-0')
 const rejected=assert.rejects(store.removeResult('result-0'),/disk transaction failed/)
 journal.handleWorkerEvent({type:'task.progress',taskId:'live-0',payload:{stage:'separating',done:2,total:4,progress:50}})
 await tick();reject(new Error('disk transaction failed'));await rejected
 assert.equal(store.tasks.find(task=>task.id==='live-0'),original)
 assert.equal(original.progress,50)
 assert.ok(store.tasks.some(task=>task.id==='result-0'))
 assert.equal(fixture.rows().length,2)
})
test('large groups are not truncated by a former history limit',async t=>{
 const {store}=await setup(t,group('large',Array(125).fill('completed')))
 assert.equal(store.tasks.length,125)
 assert.equal(await store.removeResult('large-50'),125)
 assert.equal(store.tasks.length,0)
})
test('workbench run deletion verifies workflow ownership and preserves visible results',async t=>{
 const records=group('workflow-run',['failed'],true)
 const {store}=await setup(t,records)
 await assert.rejects(store.removeWorkflowRun('other','workflow-run'))
 await store.removeWorkflowRun('workflow','workflow-run')
 assert.equal(store.tasks.length,0)
})
test('metadata preflight is non-destructive and rejects an owned cleanup lease',async t=>{
 const {store}=await setup(t,group('result'))
 await store.checkRemoval(['result-0']);assert.equal(fixture.rows().length,1)
 fixture.setRemoveHook(()=>{throw Error('runtime cleanup lease is busy')})
 await assert.rejects(store.checkRemoval(['result-0']),/cleanup lease/)
 assert.equal(fixture.rows().length,1);assert.equal(store.tasks.length,1)
})
