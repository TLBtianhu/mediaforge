import assert from 'node:assert/strict'
import test, { after } from 'node:test'
import { fileURLToPath } from 'node:url'
import { createPinia, disposePinia } from 'pinia'
import { nextTick } from 'vue'
import { createServer } from 'vite'
import { recordsFromEnqueue } from './_audioJournalFixture.mjs'

const id = '\0audio-admission-fixture'
const vite = await createServer({
  configFile: false, appType: 'custom', optimizeDeps: { noDiscovery: true },
  server: { watch: null, middlewareMode: true, hmr: false, ws: false, preTransformRequests: false },
  resolve: { alias: { '@tauri-apps/api/core': '\0admission-core', '@tauri-apps/api/event': '\0admission-events', '@': fileURLToPath(new URL('../src', import.meta.url)) } },
  plugins: [{
    name: 'canonical-audio-admission-boundary', enforce: 'pre',
    resolveId(value) { return [id, '\0admission-core', '\0admission-events'].includes(value) ? value : null },
    load(value) {
      if (value === '\0admission-core') return `export {invoke} from '${id}'`
      if (value === '\0admission-events') return `export {listen} from '${id}'`
      if (value === id) return `
        export const calls=[], remote=new Map(), listeners=new Map(); let startHook;
        export function reset(records,hook){calls.length=0;remote.clear();listeners.clear();records.forEach(job=>remote.set(job.jobId,structuredClone(job)));startHook=hook}
        export function emit(event){listeners.get('pymss://worker-event')?.({payload:event})}
        export async function listen(name,callback){listeners.set(name,callback);return ()=>listeners.delete(name)}
        export async function invoke(command,args){calls.push({command,args});
          if(command==='agent_jobs')return {schemaVersion:3,full:true,removed:[],jobs:structuredClone([...remote.values()]),dataRoot:'/fixture',jobsDir:'/fixture/agent-jobs'};
          if(command==='load_app_store')return {version:1};
          if(command==='save_app_store')return;
          if(command==='start_separation'||command==='start_workflow_inference')return startHook?startHook(args.payload):{queued:true,started:false};
          if(command==='cancel_task'){
            const job=remote.get(args.taskId);if(!job||['completed','failed','cancelled','interrupted'].includes(job.status))return false;
            const now=Date.now()/1000;Object.assign(job,{status:'cancelled',updatedAt:now,finishedAt:now});
            emit({type:'task.cancelled',taskId:job.jobId,payload:{message:'Cancelled'}});await Promise.resolve();return true;
          }
          throw Error('Unexpected '+command)
        }
      `
    },
    transform(_code, path) {
      path = path.replaceAll('\\', '/')
      if (path.endsWith('/src/stores/settings.ts')) return 'export const useSettingsStore=()=>({maxConcurrentSeparations:1,developerMode:false})'
      if (path.endsWith('/src/stores/model.ts')) return 'export const useModelStore=()=>({models:[],selectedModel:""})'
      if (path.endsWith('/src/stores/app.ts')) return 'export const useAppStore=()=>({envInfo:{platform:"win32"}})'
      if (path.endsWith('/src/utils/taskOwner.ts')) return 'export const isMainTaskOwner=()=>true'
      if (path.endsWith('/src/i18n/index.ts')) return 'export default {global:{t:key=>key}}'
    },
  }],
})
after(() => vite.close())
globalThis.window = { __TAURI_INTERNALS__: {} }
const fixture = await vite.ssrLoadModule(id)
const { useTaskStore } = await vite.ssrLoadModule('/src/stores/task.ts')
const { useMediaJobsStore, JOURNAL_POLL_INTERVAL_MS } = await vite.ssrLoadModule('/src/stores/mediaJobs.ts')
const { activeWorkflowTask } = await vite.ssrLoadModule('/src/workflows/batchExecution.ts')
const flush = async () => { await nextTick(); await new Promise(resolve => setImmediate(resolve)); await nextTick() }
const starts = () => fixture.calls.filter(call => ['start_separation', 'start_workflow_inference'].includes(call.command))
function group(name, count = 2, workbench = false, createdAt = 1700000000) {
  return recordsFromEnqueue({
    schemaVersion: 2, command: workbench ? 'infer_workflow' : 'infer', groupId: name, primaryJobId: `${name}-0`,
    payload: { output: 'D:/out', outputLayout: 'flat', device: 'cpu', deviceIds: [0],
      ...(workbench ? { workflowName: 'Workflow', workflow: { version: 0.4, nodes: [{ id: 1, type: 'pymss_load_audio', widgets_values: ['D:/input.wav', ''] }], links: [] } } : { model: 'Fixture' }) },
    ...(workbench ? { presentation: { workflowId: 'workflow', workbench: { nativeGraph: true } } } : {}),
    tasks: Array.from({ length: count }, (_, index) => ({ jobId: `${name}-${index}`, request: { input: `D:/input/${index}.wav`, output: 'D:/out', inputIndex: index + 1 } })),
  }, createdAt)
}
async function setup(t, records, hook) {
  fixture.reset(records, hook)
  const pinia = createPinia(), tasks = useTaskStore(pinia), journal = useMediaJobsStore(pinia)
  t.after(() => disposePinia(pinia))
  await tasks.initialize()
  return { tasks, journal }
}
function claim(name, activeIds) {
  const now = Date.now() / 1000
  for (const job of fixture.remote.values()) if (job.audio.groupId === name && (!activeIds || activeIds.includes(job.jobId))) {
    Object.assign(job, { status: 'running', owner: { kind: 'tauri-worker', pid: 12345 }, startedAt: now, updatedAt: now })
    job.runtime = { stage: 'separating', progress: { done: 1, total: 2 }, nodes: { '1': { status: 'running', progress: 50 } }, order: ['1'] }
  }
}

test('all groups are handed off once while queued ACKs consume no frontend worker slots', async t => {
  const { tasks, journal } = await setup(t, [...group('first'), ...group('second', 3), ...group('third', 1)])
  tasks.scheduleQueue()
  await flush()
  assert.deepEqual(starts().map(call => call.args.payload), [{ jobId: 'first-0' }, { jobId: 'second-0' }, { jobId: 'third-0' }])
  assert.ok(tasks.tasks.every(task => task.status === 'queued' && task.startedAt === undefined))
  assert.ok(journal.jobs.every(job => job.owner === null))
  for (let index = 0; index < 5; index++) { tasks.scheduleQueue(); await journal.refresh(); await flush() }
  assert.equal(starts().length, 3)
  claim('first')
  await journal.refresh(); await flush()
  assert.ok(tasks.tasks.filter(task => task.jobId === 'first').every(task => task.status === 'separating'))
  assert.ok(tasks.tasks.filter(task => task.jobId !== 'first').every(task => task.status === 'queued'))
  assert.equal(starts().length, 3)
})

test('an IPC error with an unchanged queued record retries only on a bounded later poll', async t => {
  t.mock.timers.enable({ apis: ['Date', 'setTimeout'], now: 1700000010000 })
  let attempts = 0
  const { tasks, journal } = await setup(t, group('retry'), () => {
    if (++attempts === 1) throw new Error('transport failed before ACK')
    return { queued: true, started: false }
  })
  tasks.scheduleQueue(); await flush()
  for (let index = 0; index < 10; index++) { tasks.scheduleQueue(); await journal.refresh(); await flush() }
  assert.equal(attempts, 1)
  assert.ok(tasks.tasks.every(task => task.status === 'queued' && !task.error))
  t.mock.timers.tick(JOURNAL_POLL_INTERVAL_MS - 1); await flush()
  assert.equal(attempts, 1)
  t.mock.timers.tick(1); await flush()
  assert.equal(attempts, 2)
  t.mock.timers.tick(JOURNAL_POLL_INTERVAL_MS * 3); await flush()
  assert.equal(attempts, 2)
})

test('a lost ACK after a real claim preserves running ownership and does not resubmit', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const { tasks } = await setup(t, group('claimed'), () => { claim('claimed'); throw new Error('ACK lost') })
  tasks.scheduleQueue(); await flush()
  assert.ok(tasks.tasks.every(task => task.status === 'separating' && !task.error))
  t.mock.timers.tick(JOURNAL_POLL_INTERVAL_MS * 2); await flush()
  tasks.scheduleQueue(); await flush()
  assert.equal(starts().length, 1)
})

test('cancelling a queued primary leaves its sibling waiter and native workbench highlighting intact', async t => {
  const { tasks, journal } = await setup(t, group('workbench', 2, true))
  tasks.scheduleQueue(); await flush()
  assert.equal(await tasks.cancelTask('workbench-0'), true)
  assert.equal(tasks.tasks.find(task => task.id === 'workbench-0').status, 'cancelled')
  assert.equal(tasks.tasks.find(task => task.id === 'workbench-1').status, 'queued')
  assert.equal(fixture.remote.get('workbench-0').payload.workflow.nodes.length, 1)
  claim('workbench', ['workbench-1'])
  await journal.refresh(); await flush()
  const active = activeWorkflowTask(tasks.tasks)
  assert.equal(active.id, 'workbench-1')
  assert.equal(active.nodeStates['1'].status, 'running')
  assert.deepEqual(active.nodeExecutionOrder, ['1'])
  assert.equal(starts().length, 1)
})

test('workbench cancellation suppresses handoff until all queued members are cancelled', async t => {
  const { tasks } = await setup(t, group('cancel-all', 3, true))
  await tasks.cancelWorkbenchJob('cancel-all'); await flush()
  assert.ok(tasks.tasks.every(task => task.status === 'cancelled'))
  assert.equal(starts().length, 0)
})
