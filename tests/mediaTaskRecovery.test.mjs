import assert from 'node:assert/strict'
import test, { after } from 'node:test'
import { fileURLToPath } from 'node:url'
import { createRenderer, nextTick } from 'vue'
import { createPinia } from 'pinia'
import { createServer, transformWithEsbuild } from 'vite'
import { parse, compileScript } from 'vue/compiler-sfc'

// Mount the actual component scripts without a browser, runtime or user profile.
// Only external IPC/UI boundaries are replaced, so terminal-event/retry behavior
// remains covered when the components are refactored.
const fixtureId = '\0media-recovery-fixture'
const stubs = {
  '@tauri-apps/api/core': `export { invoke } from '${fixtureId}'`,
  '@tauri-apps/api/event': `export { listen } from '${fixtureId}'`,
  '@tauri-apps/plugin-dialog': 'export const open = async () => null',
  'vue-i18n': 'export const useI18n = () => ({ t: key => key })',
  'naive-ui': `export { useMessage } from '${fixtureId}'`,
  '@vicons/ionicons5': 'export const GitNetworkOutline={}, CloudUploadOutline={}, DocumentOutline={}, CloseCircleOutline={}, FolderOpenOutline={}, PlayOutline={}',
}
const vite = await createServer({
  configFile: false,
  server: { watch: null, middlewareMode: true, hmr: false, preTransformRequests: false },
  appType: 'custom',
  optimizeDeps: { noDiscovery: true },
  resolve: { alias: {
    ...Object.fromEntries(Object.keys(stubs).map(id => [id, `\0media-recovery-stub:${id}`])),
    '@': fileURLToPath(new URL('../src', import.meta.url)),
  } },
  plugins: [{
    name: 'media-recovery-boundaries',
    enforce: 'pre',
    resolveId(id) { return id === fixtureId || id.startsWith('\0media-recovery-stub:') ? id : null },
    load(id) {
      if (id.startsWith('\0media-recovery-stub:')) return stubs[id.slice('\0media-recovery-stub:'.length)]
      if (id === fixtureId) return `
        export const calls = [], messages = [], listeners = new Map()
        let hook, listenHook
        export function reset(nextHook, nextListenHook) { calls.length = messages.length = 0; listeners.clear(); hook = nextHook; listenHook = nextListenHook }
        export async function invoke(command, args) {
          calls.push({ command, args })
          if (hook) return hook(command, args)
          if (command === 'runtime_asset_status') return { installed: false, size: 0 }
          if (command === 'agent_jobs') return {schemaVersion:1,jobs:[],dataRoot:'/fixture',jobsDir:'/fixture/agent-jobs'}
          return {}
        }
        export async function listen(name, callback) { if (listenHook) await listenHook(name); listeners.set(name, callback); return () => listeners.delete(name) }
        export async function emit(name, payload) { await listeners.get(name)?.({ payload }) }
        export const useMessage = () => Object.fromEntries(['success', 'warning', 'error', 'info'].map(level => [level, text => messages.push({ level, text })]))
      `
      return null
    },
    async transform(code, id) {
      const path = id.replaceAll('\\', '/')
      if (path.endsWith('/src/views/MediaWorkflowView.vue') || path.endsWith('/src/components/RuntimeAssetBanner.vue') || path.endsWith('/src/components/settings/VersionUpdateCard.vue')) {
        const { descriptor } = parse(code, { filename: path })
        const script = compileScript(descriptor, { id: path })
        return transformWithEsbuild(script.content, `${path}.ts`, { loader: 'ts', tsconfigRaw: {} })
      }
      if (path.endsWith('.vue')) return 'export default { render: () => null }'
      if (path.endsWith('/src/composables/useMediaDrop.ts')) return 'export const useMediaDrop = () => ({})'
      if (path.endsWith('/src/stores/app.ts')) return 'export const useAppStore = () => ({ checkRuntimeInfo: async () => {} })'
      if (path.endsWith('/src/stores/model.ts')) return 'export const useModelStore = () => ({ loadModels: async () => {} })'
      return null
    },
  }],
})
after(() => vite.close())
const fixture = await vite.ssrLoadModule(fixtureId)
const Workflow = (await vite.ssrLoadModule('/src/views/MediaWorkflowView.vue')).default
const Banner = (await vite.ssrLoadModule('/src/components/RuntimeAssetBanner.vue')).default
const VersionCard = (await vite.ssrLoadModule('/src/components/settings/VersionUpdateCard.vue')).default
Workflow.render = Banner.render = VersionCard.render = () => null
const renderer = createRenderer({
  createComment: text => ({ text }), createText: text => ({ text }),
  createElement: tag => ({ tag, children: [] }),
  insert(node, parent) { node.parent = parent; parent.children.push(node) },
  remove(node) { node.parent.children = node.parent.children.filter(item => item !== node) },
  parentNode: node => node.parent, nextSibling: () => null,
  setText(node, text) { node.text = text }, setElementText(node, text) { node.text = text }, patchProp() {},
})
const flush = async () => { await nextTick(); await new Promise(resolve => setImmediate(resolve)); await nextTick() }
async function mount(t, Component, hook, listenHook) {
  fixture.reset(hook, listenHook)
  const app = renderer.createApp(Component)
  app.use(createPinia())
  app.mount({ children: [] })
  t.after(() => app.unmount())
  await flush()
  return app._instance.setupState
}
async function startWorkflow(state) {
  state.inputs = ['input.png']
  state.outputDir = 'out'
  await state.start()
  return fixture.calls.at(-1).args.payload.requestId
}
const workflowEvent = payload => fixture.emit('pymss://worker-event', payload)
const runtimeEvent = payload => fixture.emit('mediaforge://runtime-asset', payload)

test('media workflow failure unlocks retry and late events cannot overwrite the terminal state', async t => {
  const state = await mount(t, Workflow)
  const id = await startWorkflow(state)
  await workflowEvent({ requestId: id, type: 'error', payload: { message: 'missing models' } })
  assert.equal(state.busy, false)
  assert.equal(state.status, 'missing models')
  await workflowEvent({ requestId: id, type: 'task.progress', payload: { fraction: 0.9, current: 'late progress' } })
  await workflowEvent({ requestId: id, type: 'task.result', payload: { outputs: [{ path: 'late.png' }] } })
  assert.equal(state.status, 'missing models')
  assert.deepEqual(state.outputs, [])
  const nextId = await startWorkflow(state)
  assert.notEqual(nextId, id)
  assert.equal(state.busy, true)
})

test('media workflow cancellation accepts taskId and releases the start action', async t => {
  const state = await mount(t, Workflow)
  const id = await startWorkflow(state)
  await workflowEvent({ taskId: id, type: 'task.cancelled', payload: {} })
  assert.equal(state.busy, false)
  assert.equal(state.status, 'tasks.cancelSuccess')
  assert.equal(fixture.messages.at(-1).level, 'info')
  await startWorkflow(state)
  assert.equal(state.busy, true)
})

test('media workflow keeps partial outputs while reporting partial failure', async t => {
  const state = await mount(t, Workflow)
  const id = await startWorkflow(state)
  await workflowEvent({ requestId: id, type: 'task.result', payload: {
    outputs: [{ path: 'good.png' }], failed: [{ path: 'bad.png', error: 'invalid input' }],
  } })
  assert.equal(state.busy, false)
  assert.equal(state.outputs[0].path, 'good.png')
  assert.equal(state.status, 'mediaWorkflow.failed')
  assert.equal(fixture.messages.at(-1).level, 'warning')
})

test('media workflow survives route unmount and restores outputs and the submitted plan on remount', async t => {
  fixture.reset()
  const pinia = createPinia()
  const first = renderer.createApp(Workflow).use(pinia)
  first.mount({ children: [] })
  await flush()
  const state = first._instance.setupState
  state.inputs = ['original.png']
  state.outputDir = 'original-out'
  state.steps = [{ capability: 'upscale', bindings: { upscaler: 'media:real-esrgan-x2' }, params: { scale: 2 } }]
  await state.start()
  const id = fixture.calls.find(call => call.command === 'start_audio_tool').args.payload.requestId
  state.inputs.push('draft.png')
  state.steps[0].params.scale = 4
  first.unmount()
  assert.equal(fixture.listeners.has('pymss://worker-event'), true)
  await workflowEvent({ taskId: id, type: 'task.progress', payload: { fraction: 0.5, current: 'halfway' } })
  const second = renderer.createApp(Workflow).use(pinia)
  second.mount({ children: [] })
  t.after(() => second.unmount())
  await flush()
  const restored = second._instance.setupState
  assert.equal(restored.busy, true)
  assert.equal(restored.progress, 50)
  assert.deepEqual(restored.inputs, ['original.png'])
  assert.equal(restored.steps[0].params.scale, 2)
  await workflowEvent({ taskId: id, type: 'task.result', payload: { outputs: [{ path: 'original-out/result.png' }], failed: [] } })
  await flush()
  assert.equal(restored.busy, false)
  assert.equal(restored.outputs[0].path, 'original-out/result.png')
  assert.equal(restored.status, 'mediaWorkflow.completed')
})

test('runtime preparation can retry after IPC failure and blocks duplicate starts while running', async t => {
  let attempts = 0
  const state = await mount(t, Banner, command => {
    if (command === 'runtime_asset_status') return { installed: false, size: 0 }
    if (command === 'runtime_asset_install' && ++attempts === 1) throw new Error('network unavailable')
    return {}
  })
  await state.startInstall()
  assert.equal(state.error, 'network unavailable')
  assert.equal(state.canStart, true)
  await state.startInstall()
  assert.equal(state.error, '')
  assert.equal(state.running, true)
  await state.startInstall()
  assert.equal(attempts, 2)
})

test('runtime preparation retries after an asynchronous download failure with reset progress', async t => {
  const state = await mount(t, Banner)
  await state.startInstall()
  await runtimeEvent({ stage: 'download', percent: 40, downloaded: 20 * 1024 * 1024 })
  await runtimeEvent({ stage: 'error', message: 'connection reset' })
  assert.equal(state.canStart, true)
  assert.equal(state.error, 'connection reset')
  await state.startInstall()
  assert.equal(state.error, '')
  assert.equal(state.percent, 0)
  assert.equal(state.downloadedMb, 0)
})

test('remote runtime acceptance can retry after a failed acceptance request', async t => {
  let attempts = 0
  const state = await mount(t, Banner, command => {
    if (command === 'runtime_asset_status') return { installed: false, size: 0 }
    if (command === 'versions_accept_runtime' && ++attempts === 1) throw new Error('accept failed')
    return {}
  })
  state.remoteRuntime = 'new-runtime.7z'
  await state.acceptRemoteRuntime()
  assert.equal(state.error, 'accept failed')
  assert.equal(state.remoteRuntime, 'new-runtime.7z')
  await state.acceptRemoteRuntime()
  assert.equal(state.error, '')
  assert.equal(state.running, true)
  assert.equal(attempts, 2)
})

test('runtime event listener and local status stay ready during a pending version check', async t => {
  let resolveVersions
  const pending = new Promise(resolve => { resolveVersions = resolve })
  t.after(() => resolveVersions({}))
  const state = await mount(t, Banner, command => {
    if (command === 'runtime_asset_status') return { installed: true, size: 123 }
    if (command === 'versions_check') return pending
    return {}
  })
  assert.equal(state.checked, true)
  assert.ok(fixture.calls.some(call => call.command === 'versions_check'))
  assert.ok(fixture.listeners.has('mediaforge://runtime-asset'))
  state.running = true
  await runtimeEvent({ stage: 'error', message: 'local download failed while checking versions' })
  assert.equal(state.running, false)
  assert.equal(state.error, 'local download failed while checking versions')
})

test('runtime listener failure keeps local status available and retries subscription before installing', async t => {
  let bridgeReady = false
  const state = await mount(t, Banner, command => {
    if (command === 'runtime_asset_status') return { installed: false, size: 0 }
    return {}
  }, async () => { if (!bridgeReady) throw new Error('event bridge unavailable') })
  assert.equal(state.checked, true)
  assert.equal(state.installed, false)
  assert.equal(fixture.listeners.has('mediaforge://runtime-asset'), false)
  await state.startInstall()
  assert.equal(state.running, false)
  assert.equal(state.error, 'event bridge unavailable')
  assert.equal(fixture.calls.some(call => call.command === 'runtime_asset_install'), false)
  bridgeReady = true
  await state.startInstall()
  assert.equal(state.error, '')
  assert.equal(state.running, true)
  assert.equal(fixture.listeners.has('mediaforge://runtime-asset'), true)
  assert.equal(fixture.calls.some(call => call.command === 'runtime_asset_install'), true)
})

test('unmount during listener registration releases the late subscription without starting checks', async () => {
  let resolveListen
  const pending = new Promise(resolve => { resolveListen = resolve })
  fixture.reset(undefined, () => pending)
  const app = renderer.createApp(Banner)
  app.use(createPinia())
  app.mount({ children: [] })
  app.unmount()
  resolveListen()
  await flush()
  assert.equal(fixture.listeners.has('mediaforge://runtime-asset'), false)
  assert.equal(fixture.calls.length, 0)
})

test('a failed manual version recheck clears the previous success instead of reporting up to date', async t => {
  let attempts = 0
  const state = await mount(t, VersionCard, command => {
    if (command === 'versions_check') {
      attempts += 1
      if (attempts === 2) throw new Error('version service unavailable')
      return { local: { app: '0.0.31' }, remote: { app: '0.0.31' }, available: { app: false } }
    }
    return {}
  })
  await state.check()
  assert.equal(state.checked, true)
  assert.notEqual(state.result, null)
  await state.check()
  assert.equal(attempts, 2)
  assert.equal(state.result, null)
  assert.equal(state.error, 'version service unavailable')
})
