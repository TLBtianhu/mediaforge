import assert from 'node:assert/strict'
import test, { after } from 'node:test'
import { fileURLToPath } from 'node:url'
import { createRenderer, nextTick } from 'vue'
import { createServer, transformWithEsbuild } from 'vite'
import { parse, compileScript } from 'vue/compiler-sfc'

const fixtureId = '\0queue-fixture'
const stubs = {
  '@tauri-apps/api/core': `export { invoke } from '${fixtureId}'`,
  'vue-i18n': 'export const useI18n = () => ({ t: key => key })',
  'naive-ui': `export { useMessage } from '${fixtureId}'`,
  '@vicons/ionicons5': 'export const FolderOpenOutline={}, RefreshOutline={}, SparklesOutline={}',
  '@/stores/task': `export { useTaskStore } from '${fixtureId}'`,
  '@/stores/mediaJobs': `export { useMediaJobsStore } from '${fixtureId}'`,
}
const vite = await createServer({
  configFile: false,
  server: { watch: null, middlewareMode: true, hmr: false, preTransformRequests: false },
  appType: 'custom', optimizeDeps: { noDiscovery: true },
  resolve: { alias: {
    ...Object.fromEntries(Object.keys(stubs).map(id => [id, `\0queue-stub:${id}`])),
    '@': fileURLToPath(new URL('../src', import.meta.url)),
  } },
  plugins: [{
    name: 'queue-boundaries', enforce: 'pre',
    resolveId(id) { return id === fixtureId || id.startsWith('\0queue-stub:') ? id : null },
    load(id) {
      if (id.startsWith('\0queue-stub:')) return stubs[id.slice('\0queue-stub:'.length)]
      if (id === fixtureId) return `
        import { reactive } from 'vue'
        export const calls=[], messages=[]
        let cancelHook
        export const task = reactive({ tasks:[], async cancelTask(id) { calls.push(['audio', id]); return cancelHook ? cancelHook(id) : true } })
        export const media = reactive({ jobs:[], dataRoot:'/isolated', jobsDir:'/isolated/agent-jobs', loading:false, error:'',
          async initialize() { calls.push(['initialize']) }, async refresh() { calls.push(['refresh']) } })
        export function reset(hook) { calls.length=messages.length=0; task.tasks=[]; media.jobs=[]; media.error=''; media.loading=false; cancelHook=hook }
        export const useTaskStore = () => task
        export const useMediaJobsStore = () => media
        export async function invoke(command,args) { calls.push([command,args]); return true }
        export const useMessage = () => Object.fromEntries(['success','error'].map(level => [level, text => messages.push([level,text])]))
      `
      return null
    },
    async transform(code, id) {
      const path = id.replaceAll('\\', '/')
      if (path.endsWith('/src/views/QueueView.vue')) {
        const { descriptor } = parse(code, { filename: path })
        const script = compileScript(descriptor, { id: path })
        return transformWithEsbuild(script.content, `${path}.ts`, { loader: 'ts', tsconfigRaw: {} })
      }
      if (path.endsWith('.vue')) return 'export default { render: () => null }'
      return null
    },
  }],
})
after(() => vite.close())
const fixture = await vite.ssrLoadModule(fixtureId)
const Queue = (await vite.ssrLoadModule('/src/views/QueueView.vue')).default
Queue.render = () => null
const renderer = createRenderer({
  createComment: text => ({ text }), createText: text => ({ text }), createElement: tag => ({ tag, children: [] }),
  insert(node, parent) { node.parent = parent; parent.children.push(node) },
  remove(node) { node.parent.children = node.parent.children.filter(item => item !== node) },
  parentNode: node => node.parent, nextSibling: () => null, setText() {}, setElementText() {}, patchProp() {},
})
const flush = async () => { await nextTick(); await new Promise(resolve => setImmediate(resolve)); await nextTick() }
async function mount(t, hook) {
  fixture.reset(hook)
  const app = renderer.createApp(Queue)
  app.mount({ children: [] })
  t.after(() => app.unmount())
  await flush()
  return { state: app._instance.setupState, app }
}
const audio = (id, status = 'queued') => ({ id, status, input: 'song.wav', output: '/out', model: 'model', createdAt: 1000, outputs: [], files: [], progress: 0 })

test('queue uses the resident journal and reacts to unspawned and completed audio without extra native reads', async t => {
  const { state } = await mount(t)
  fixture.task.tasks = [audio('audio')]
  fixture.media.jobs = [{ jobId: 'audio', command: 'infer', source: 'manual', status: 'running' }]
  await flush()
  assert.equal(state.jobs.length, 1)
  assert.equal(state.jobs[0].status, 'queued')
  assert.equal(state.running, 1)
  fixture.task.tasks[0].status = 'done'
  await flush()
  assert.equal(state.completed, 1)
  assert.equal(state.running, 0)
  assert.deepEqual(fixture.calls.map(call => call[0]), ['initialize'])
  fixture.media.error = 'journal unavailable'
  assert.equal(state.error, 'journal unavailable')
})

test('audio cancellation reaches the task scheduler while media and agent work keep backend cancellation', async t => {
  const { state } = await mount(t)
  fixture.task.tasks = [audio('audio')]
  fixture.media.jobs = [
    { jobId: 'media', command: 'audio_tools', source: 'manual', status: 'running', cancelTaskId: 'media-worker' },
    { jobId: 'agent', command: 'infer', source: 'agent', status: 'queued' },
  ]
  for (const row of state.jobs) await state.cancel(row)
  assert.ok(fixture.calls.some(call => call[0] === 'audio' && call[1] === 'audio'))
  assert.ok(fixture.calls.some(call => call[0] === 'cancel_task' && call[1].taskId === 'media-worker'))
  assert.ok(fixture.calls.some(call => call[0] === 'cancel_agent_job' && call[1].jobId === 'agent'))
  assert.equal(fixture.calls.some(call => call[0] === 'cancel_task' && call[1].taskId === 'audio'), false)
})

test('pending cancellation is deduplicated and completing after unmount does not toast or refresh', async t => {
  let resolveCancel
  const pending = new Promise(resolve => { resolveCancel = resolve })
  const { state, app } = await mount(t, () => pending)
  fixture.task.tasks = [audio('audio')]
  const row = state.jobs[0]
  const first = state.cancel(row)
  await state.cancel(row)
  assert.equal(fixture.calls.filter(call => call[0] === 'audio').length, 1)
  app.unmount()
  resolveCancel(true)
  await first
  await state.load()
  assert.equal(fixture.messages.length, 0)
  assert.equal(fixture.calls.some(call => call[0] === 'refresh'), false)
})

test('a false cancellation response is not announced as success', async t => {
  const { state } = await mount(t, async () => false)
  fixture.task.tasks = [audio('audio')]
  await state.cancel(state.jobs[0])
  assert.equal(fixture.messages.length, 0)
})

test('journal polling remains ten seconds, skips hidden documents, and stops with the view', async t => {
  let tick, interval, cleared = false
  const token = {}
  t.mock.method(globalThis, 'setInterval', (callback, delay) => { tick = callback; interval = delay; return token })
  t.mock.method(globalThis, 'clearInterval', handle => { cleared = handle === token })
  const previousDocument = Object.getOwnPropertyDescriptor(globalThis, 'document')
  Object.defineProperty(globalThis, 'document', { configurable: true, value: { hidden: true } })
  t.after(() => {
    if (previousDocument) Object.defineProperty(globalThis, 'document', previousDocument)
    else delete globalThis.document
  })
  const { app } = await mount(t)
  assert.equal(interval, 10000)
  tick()
  await flush()
  assert.equal(fixture.calls.some(call => call[0] === 'refresh'), false)
  document.hidden = false
  tick()
  await flush()
  assert.equal(fixture.calls.filter(call => call[0] === 'refresh').length, 1)
  app.unmount()
  assert.equal(cleared, true)
  tick()
  await flush()
  assert.equal(fixture.calls.filter(call => call[0] === 'refresh').length, 1)
})
