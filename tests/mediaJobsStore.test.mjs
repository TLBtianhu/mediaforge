import assert from 'node:assert/strict'
import test, { after } from 'node:test'
import { fileURLToPath } from 'node:url'
import { createPinia } from 'pinia'
import { createRenderer, nextTick } from 'vue'
import { createServer } from 'vite'

const fixtureId = '\0media-jobs-store-fixture'
const vite = await createServer({
  configFile: false, appType: 'custom', optimizeDeps: { noDiscovery: true },
  server: { watch: null, middlewareMode: true, hmr: false, preTransformRequests: false },
  resolve: { alias: {
    '@tauri-apps/api/core': '\0media-jobs-core', '@tauri-apps/api/event': '\0media-jobs-event',
    '@': fileURLToPath(new URL('../src', import.meta.url)),
  } },
  plugins: [{
    name: 'media-jobs-native-boundaries', enforce: 'pre',
    resolveId(id) { return [fixtureId, '\0media-jobs-core', '\0media-jobs-event'].includes(id) ? id : null },
    load(id) {
      if (id === '\0media-jobs-core') return `export { invoke } from '${fixtureId}'`
      if (id === '\0media-jobs-event') return `export { listen } from '${fixtureId}'`
      if (id === fixtureId) return `
        export const calls=[], timeline=[], listeners=new Map()
        let invokeHook, listenHook
        export function reset(nextInvoke,nextListen) { calls.length=timeline.length=0; listeners.clear(); invokeHook=nextInvoke; listenHook=nextListen }
        export async function invoke(command,args) {
          calls.push({command,args}); timeline.push(command)
          if(invokeHook) return invokeHook(command,args)
          return command==='agent_jobs' ? {schemaVersion:1,jobs:[],dataRoot:'/fixture',jobsDir:'/fixture/agent-jobs'} : {started:true}
        }
        export async function listen(name,callback) {
          timeline.push('listen:start')
          if(listenHook) await listenHook(name)
          listeners.set(name,callback); timeline.push('listen:ready')
          return ()=>listeners.delete(name)
        }
        export function emit(event) { listeners.get('pymss://worker-event')?.({payload:event}) }
      `
      return null
    },
  }],
})
after(() => vite.close())
const fixture = await vite.ssrLoadModule(fixtureId)
const { useMediaJobsStore } = await vite.ssrLoadModule('/src/stores/mediaJobs.ts')
const { useMediaJob } = await vite.ssrLoadModule('/src/composables/useMediaJob.ts')
const deferred = () => {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
const flush = async () => { await nextTick(); await new Promise(resolve => setImmediate(resolve)); await nextTick() }
const journal = (jobId, overrides = {}) => ({
  jobId, command: 'audio_tools', source: 'manual', status: 'running', cancelTaskId: jobId,
  owner: { kind: 'tauri-worker' },
  fraction: 0, message: '', createdAt: 100, payload: { operation: 'media_image' }, result: null, ...overrides,
})
const response = jobs => ({ schemaVersion:1, jobs, dataRoot: '/fixture', jobsDir: '/fixture/agent-jobs' })
function setup(t, invokeHook, listenHook) {
  fixture.reset(invokeHook, listenHook)
  const pinia = createPinia()
  const store = useMediaJobsStore(pinia)
  t.after(() => store.$dispose())
  return { pinia, store }
}

test('journal schema and worker events accept only the current contract', async t => {
  const {store}=setup(t,async command=>command==='agent_jobs'?response([journal('current')]):{started:true})
  await store.initialize()
  fixture.emit({taskId:'current',type:'operation_progress',payload:{progress:0.8}})
  fixture.emit({taskId:'current',type:'operation_result',payload:{outputs:[{path:'obsolete.png'}]}})
  assert.equal(store.jobs[0].status,'running')
  assert.equal(store.jobs[0].fraction,0)
  assert.equal(store.jobs[0].result,null)
  fixture.emit({taskId:'current',type:'task.result',payload:{outputs:[{path:'current.png'}]}})
  assert.equal(store.jobs[0].status,'completed')
  assert.equal(store.jobs[0].result.outputs[0].path,'current.png')
  const before=JSON.stringify(store.jobs)
  fixture.reset(async()=>({index:{jobs:[journal('obsolete')]},dataRoot:'/fixture',workspace:'/fixture/agent-workspace.json'}))
  await store.refresh()
  assert.match(store.error,/Unsupported.*journal.*schema/i)
  assert.equal(JSON.stringify(store.jobs),before)
  await assert.rejects(store.start({operation:'media_image',inputs:['current.png']}),/Unsupported.*journal.*schema/i)
  assert.equal(fixture.calls.some(call=>call.command==='start_audio_tool'),false)
})

test('start waits for its event listener and registers the local job before native invocation', async t => {
  const listening = deferred()
  let store
  ;({ store } = setup(t, async (command, args) => {
    if (command === 'agent_jobs') return response([])
    assert.equal(fixture.listeners.has('pymss://worker-event'), true)
    assert.equal(store.jobs[0].jobId, args.payload.requestId)
    fixture.emit({ requestId: args.payload.requestId, type: 'task.result', payload: { outputs: [{ path: 'early.png' }] } })
    return { started: true }
  }, () => listening.promise))
  const pending = store.start({ operation: 'media_image', requestId: 'early' })
  await flush()
  assert.equal(fixture.calls.length, 0)
  listening.resolve()
  const job = await pending
  assert.equal(job.status, 'completed')
  assert.equal(job.result.outputs[0].path, 'early.png')
  assert.ok(fixture.timeline.indexOf('listen:ready') < fixture.timeline.indexOf('start_audio_tool'))
})

test('submitted payload remains deeply isolated while native work is pending', async t => {
  const ack = deferred(), started = deferred()
  const { store } = setup(t, async (command, args) => {
    if (command === 'agent_jobs') return response([])
    started.resolve(args.payload)
    return ack.promise
  })
  const input = { operation: 'media_image', requestId: 'snapshot', inputs: ['one.png'], steps: [{ params: { scale: 2 } }] }
  const pending = store.start(input)
  const nativePayload = await started.promise
  input.inputs.push('two.png')
  input.steps[0].params.scale = 8
  assert.deepEqual(nativePayload.inputs, ['one.png'])
  assert.equal(store.jobs[0].payload.steps[0].params.scale, 2)
  ack.resolve({ started: true })
  await pending
})

test('payload is captured when start is called even if initialization is delayed', async t => {
  const listening = deferred()
  const { store } = setup(t, undefined, () => listening.promise)
  const payload = { operation: 'media_image', requestId: 'slow-init', inputs: ['original.png'], params: { scale: 2 } }
  const pending = store.start(payload)
  payload.inputs[0] = 'edited-after-click.png'
  payload.params.scale = 8
  listening.resolve()
  await pending
  const submitted = fixture.calls.find(call => call.command === 'start_audio_tool').args.payload
  assert.deepEqual(submitted.inputs, ['original.png'])
  assert.equal(submitted.params.scale, 2)
})

test('concurrent refreshes share one native read and cannot overwrite newer live events', async t => {
  const read = deferred()
  const { store } = setup(t, command => command === 'agent_jobs' ? read.promise : {})
  store.jobs.push(journal('job'))
  const first = store.refresh(), second = store.refresh()
  assert.equal(fixture.calls.filter(call => call.command === 'agent_jobs').length, 1)
  // Initialize the actual event handler without making its shared refresh complete yet.
  const initialized = store.initialize()
  await flush()
  fixture.emit({ taskId: 'job', type: 'task.result', payload: { outputs: [{ path: 'finished.png' }] } })
  read.resolve(response([journal('job', { fraction: 0.1 })]))
  await Promise.all([first, second, initialized])
  assert.equal(store.jobs[0].status, 'completed')
  assert.equal(store.jobs[0].result.outputs[0].path, 'finished.png')
  assert.equal(store.loading, false)
})

test('a later stale journal snapshot does not revert a terminal job to running', async t => {
  const { store } = setup(t, () => response([journal('job')]))
  await store.initialize()
  fixture.emit({ taskId: 'job', type: 'task.cancelled', payload: { message: 'cancelled' } })
  await store.refresh()
  assert.equal(store.jobs[0].status, 'cancelled')
  fixture.emit({ taskId: 'job', type: 'task.progress', payload: { fraction: 0.9 } })
  fixture.emit({ taskId: 'job', type: 'task.result', payload: { outputs: [{ path: 'late.png' }] } })
  assert.equal(store.jobs[0].status, 'cancelled')
  assert.deepEqual(store.jobs[0].result, { message: 'cancelled' })
})

test('partial failure keeps its outputs and cannot be overwritten by late success', async t => {
  const { store } = setup(t, () => response([journal('partial')]))
  await store.initialize()
  const payload = { outputs: [{ path: 'good.png' }], failed: [{ path: 'bad.png', error: 'invalid image' }] }
  fixture.emit({ requestId: 'partial', type: 'task.result', payload })
  payload.outputs[0].path = 'changed-outside-store.png'
  fixture.emit({ requestId: 'partial', type: 'task.result', payload: { outputs: [] } })
  assert.equal(store.jobs[0].status, 'failed')
  assert.equal(store.jobs[0].result.outputs[0].path, 'good.png')
  assert.equal(store.jobs[0].result.failed[0].error, 'invalid image')
})

test('media terminals with no outputs are failures while retaining their original payload', async t => {
  const { store } = setup(t, () => response([journal('empty')]))
  await store.initialize()
  fixture.emit({ taskId: 'empty', type: 'task.result', payload: { outputs: [], failed: [] } })
  assert.equal(store.jobs[0].status, 'failed')
  assert.deepEqual(store.jobs[0].result, { outputs: [], failed: [] })
})

test('error terminals preserve structured recovery information even without output paths', async t => {
  const { store } = setup(t, () => response([journal('error')]))
  await store.initialize()
  const recovery = { retryable: true, actions: [{ action: 'switch_device', to: 'cpu' }] }
  fixture.emit({ taskId: 'error', type: 'error', payload: { code: 'OOM', message: 'out of memory', recovery } })
  assert.equal(store.jobs[0].status, 'failed')
  assert.deepEqual(store.jobs[0].result.recovery, recovery)
  assert.equal(store.jobs[0].result.code, 'OOM')
})

test('an error keeps already journaled partial outputs and merges its recovery payload', async t => {
  const { store } = setup(t, () => response([journal('partial-error', { result: { outputs: [{ path: 'partial.png' }] } })]))
  await store.initialize()
  fixture.emit({ taskId: 'partial-error', type: 'error', payload: { message: 'decoder failed', recovery: { retryable: true } } })
  assert.equal(store.jobs[0].result.outputs[0].path, 'partial.png')
  assert.equal(store.jobs[0].result.recovery.retryable, true)
})

test('only one manual job per operation can start while another operation remains independent', async t => {
  const { store } = setup(t)
  await store.start({ operation: 'media_image', requestId: 'image' })
  await assert.rejects(store.start({ operation: 'media_image', requestId: 'duplicate' }), /already running/)
  await store.start({ operation: 'media_video', requestId: 'video' })
  assert.equal(fixture.calls.filter(call => call.command === 'start_audio_tool').length, 2)
  assert.equal(store.active.length, 2)
})

test('legacy ownerless running records remain diagnostic history without blocking a new submission', async t => {
  const stale = journal('legacy-running', { owner: undefined, createdAt: 200, payload: { operation: 'media_image', inputs: ['stale.png'] } })
  const completed = journal('legacy-completed', {
    owner: undefined, status: 'completed', result: { outputs: [{ path: 'old-result.png' }] },
    payload: { operation: 'media_image', inputs: ['old.png'] },
  })
  const { store } = setup(t, command => command === 'agent_jobs' ? response([stale, completed]) : { started: true })
  await store.initialize()
  assert.equal(store.jobs.length, 2)
  assert.equal(store.jobs.find(job => job.jobId === stale.jobId).status, 'running')
  assert.equal(store.active.length, 0)
  assert.equal(store.latest('media_image').jobId, completed.jobId)
  await store.start({ operation: 'media_image', requestId: 'fresh-submission', inputs: ['fresh.png'] })
  assert.equal(store.active.length, 1) // This session owns it before the backend journal arrives.
  assert.equal(store.latest('media_image').jobId, 'fresh-submission')
  assert.equal(fixture.calls.filter(call => call.command === 'start_audio_tool').length, 1)
})

test('backend-owned running records restore their request and still prevent duplicate operations', async t => {
  const live = journal('backend-live', { payload: { operation: 'media_image', inputs: ['live.png'], params: { scale: 2 } } })
  const { store } = setup(t, () => response([live]))
  await store.initialize()
  assert.equal(store.active.length, 1)
  assert.equal(store.latest('media_image').jobId, live.jobId)
  assert.deepEqual(store.latest('media_image').payload.inputs, ['live.png'])
  await assert.rejects(store.start({ operation: 'media_image', requestId: 'duplicate-live' }), /already running/)
  assert.equal(fixture.calls.some(call => call.command === 'start_audio_tool'), false)
})

test('an IPC start failure becomes a terminal record and allows an explicit retry', async t => {
  let attempts = 0
  const { store } = setup(t, command => {
    if (command === 'agent_jobs') return response([])
    if (++attempts === 1) throw new Error('worker launch failed')
    return { started: true }
  })
  await assert.rejects(store.start({ operation: 'media_image', requestId: 'failed' }), /worker launch failed/)
  assert.equal(store.jobs[0].status, 'failed')
  assert.match(store.jobs[0].error, /worker launch failed/)
  await store.start({ operation: 'media_image', requestId: 'retry' })
  assert.equal(store.latest('media_image').jobId, 'retry')
  assert.equal(store.active.length, 1)
})

test('reusing an existing job ID is rejected without replacing its historical outputs', async t => {
  const prior = journal('original', { status: 'completed', result: { outputs: [{ path: 'original.png' }] } })
  const { store } = setup(t, () => response([prior]))
  await store.initialize()
  await assert.rejects(store.start({ operation: 'media_video', requestId: 'original' }), /ID already exists/)
  assert.equal(fixture.calls.some(call => call.command === 'start_audio_tool'), false)
  assert.equal(store.jobs[0].result.outputs[0].path, 'original.png')
})

test('latest prefers the newly submitted job when two starts share the same millisecond', async t => {
  const { store } = setup(t)
  t.mock.method(Date, 'now', () => 1700000000000)
  await store.start({ operation: 'media_image', requestId: 'z-first' })
  fixture.emit({ requestId: 'z-first', type: 'task.result', payload: { outputs: [{ path: 'first.png' }] } })
  await store.start({ operation: 'media_image', requestId: 'a-second' })
  assert.equal(store.latest('media_image').jobId, 'a-second')
  assert.equal(store.latest('media_image').status, 'queued')
})

test('listener initialization is retryable and concurrent initialization subscribes once', async t => {
  let listens = 0
  const { store } = setup(t, undefined, async () => { if (++listens === 1) throw new Error('bridge not ready') })
  await assert.rejects(store.initialize(), /bridge not ready/)
  assert.equal(fixture.calls.length, 0)
  await Promise.all([store.initialize(), store.initialize()])
  assert.equal(listens, 2)
  assert.equal(fixture.listeners.size, 1)
  await store.initialize()
  assert.equal(listens, 2)
})

test('a failed journal refresh retains existing work and the next refresh clears its error', async t => {
  let attempts = 0
  const { store } = setup(t, () => {
    if (++attempts === 1) throw new Error('temporary journal read failure')
    return response([journal('restored', { status: 'completed', result: { outputs: [{ path: 'restored.png' }] } })])
  })
  await store.initialize()
  assert.match(store.error, /temporary journal read failure/)
  assert.equal(store.loading, false)
  await store.refresh()
  assert.equal(store.error, '')
  assert.equal(store.jobs[0].result.outputs[0].path, 'restored.png')
  assert.equal(store.dataRoot, '/fixture')
})

test('operation runtime progress and named output terminals are consumed without waiting for polling', async t => {
  const { store } = setup(t, () => response([journal('graph', { command: 'run_operation' })]))
  await store.initialize()
  fixture.emit({ taskId: 'graph', type: 'task.progress', payload: { fraction: 0.5, message: 'rendering' } })
  assert.equal(store.jobs[0].fraction, 0.5)
  fixture.emit({ taskId: 'graph', type: 'task.result', payload: { outputs: { score: { kind: 'scalar', value: 42 } } } })
  assert.equal(store.jobs[0].status, 'completed')
  assert.equal(store.jobs[0].result.outputs.score.value, 42)
})

const renderer = createRenderer({
  createComment: text => ({ text }), createText: text => ({ text }), createElement: tag => ({ tag, children: [] }),
  insert(node, parent) { node.parent = parent; parent.children.push(node) }, remove() {},
  parentNode: node => node.parent, nextSibling: () => null, setText() {}, setElementText() {}, patchProp() {},
})
function mountJob(t, pinia, restore) {
  const messages = []
  const notifications = Object.fromEntries(['success', 'error', 'info', 'warning'].map(level => [level, text => messages.push([level, text])]))
  const labels = Object.fromEntries(['running', 'completed', 'failed', 'cancelled'].map(status => [status, () => status]))
  let state
  const app = renderer.createApp({ setup() { state = useMediaJob('media_image', labels, notifications, restore); return () => null } })
  app.use(pinia)
  app.mount({ children: [] })
  t.after(() => app.unmount())
  return { state, app, messages }
}

test('the composable quietly restores historical parameters and outputs without sharing mutable payloads', async t => {
  const old = journal('historical', { status: 'completed', payload: { operation: 'media_image', inputs: ['old.png'] }, result: { outputs: [{ path: 'old-result.png' }] } })
  const { pinia, store } = setup(t, () => response([old]))
  await store.initialize()
  const restored = []
  const { state, messages } = mountJob(t, pinia, payload => { restored.push(payload); payload.inputs[0] = 'local edit' })
  await flush()
  assert.equal(restored.length, 1)
  assert.equal(store.jobs[0].payload.inputs[0], 'old.png')
  assert.equal(state.outputs.value[0].path, 'old-result.png')
  assert.equal(state.busy.value, false)
  assert.equal(messages.length, 0)
})

test('route unmount stops notifications while the resident store still completes the job', async t => {
  const { pinia, store } = setup(t)
  const { state, app, messages } = mountJob(t, pinia)
  await state.start({ operation: 'media_image', requestId: 'route-job' })
  assert.equal(state.busy.value, true)
  app.unmount()
  fixture.emit({ taskId: 'route-job', type: 'task.result', payload: { outputs: [{ path: 'after-navigation.png' }] } })
  assert.equal(store.latest('media_image').status, 'completed')
  assert.equal(messages.length, 0)
})

test('a mounted partial-failure transition notifies once and releases the composable start action', async t => {
  const { pinia } = setup(t)
  const { state, messages } = mountJob(t, pinia)
  await state.start({ operation: 'media_image', requestId: 'partial-view' })
  fixture.emit({ taskId: 'partial-view', type: 'task.result', payload: { outputs: [{ path: 'partial.png' }], failed: [{ error: 'bad input' }] } })
  await flush()
  assert.equal(state.busy.value, false)
  assert.equal(state.failedCount.value, 1)
  assert.equal(state.outputs.value.length, 1)
  assert.equal(messages.length, 1)
  assert.equal(messages[0][0], 'warning')
})

test('a rejected submission reports its own error even when the previous job is already terminal', async t => {
  const prior = journal('old', { status: 'completed', result: { outputs: [{ path: 'old.png' }] } })
  const { pinia, store } = setup(t, () => response([prior]))
  await store.initialize()
  const { state, messages } = mountJob(t, pinia)
  await flush()
  await state.start({ operation: 'media_image', requestId: 'old' })
  assert.equal(messages.length, 1)
  assert.equal(messages[0][0], 'error')
  assert.match(messages[0][1], /ID already exists/)
  assert.equal(state.outputs.value[0].path, 'old.png')
})

test('a newly registered IPC failure notifies once through its mounted terminal transition', async t => {
  const { pinia } = setup(t, command => {
    if (command === 'agent_jobs') return response([])
    throw new Error('launch rejected')
  })
  const { state, messages } = mountJob(t, pinia)
  await state.start({ operation: 'media_image', requestId: 'launch-failed' })
  await flush()
  assert.equal(state.busy.value, false)
  assert.equal(messages.length, 1)
  assert.equal(messages[0][0], 'error')
  assert.match(messages[0][1], /launch rejected/)
})
