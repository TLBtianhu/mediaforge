import assert from 'node:assert/strict'
import test, { after } from 'node:test'
import { fileURLToPath } from 'node:url'
import { createPinia } from 'pinia'
import { createServer } from 'vite'
import { applyMediaWorkflowMutation } from '../src/features/media/workflowPresets.ts'

const fixtureId = '\0media-presets-store-fixture'
const vite = await createServer({
  configFile: false, appType: 'custom', optimizeDeps: { noDiscovery: true },
  server: { watch: null, middlewareMode: true, hmr: false, preTransformRequests: false },
  resolve: { alias: { '@tauri-apps/api/core': '\0media-presets-core', '@/utils/appStore': '\0media-presets-persistence', '@': fileURLToPath(new URL('../src', import.meta.url)) } },
  plugins: [{
    name: 'media-presets-boundaries', enforce: 'pre',
    resolveId: id => [fixtureId, '\0media-presets-core', '\0media-presets-persistence'].includes(id) ? id : null,
    load(id) {
      if (id === '\0media-presets-core') return `export { invoke } from '${fixtureId}'`
      if (id === '\0media-presets-persistence') return `export { isTauriRuntime, loadAppStore, saveAppStore } from '${fixtureId}'`
      if (id === fixtureId) return `
        export const calls=[]; let hook; export let state=null, native=true
        export function reset(nextState=null,nextHook,nextNative=true){calls.length=0;state=nextState;hook=nextHook;native=nextNative}
        export const isTauriRuntime=()=>native
        export async function invoke(command,args){calls.push({command,args});return hook?.(command,args)}
        export async function loadAppStore(name){calls.push({command:'load',args:{name}});return hook ? hook('load',{name}) : structuredClone(state)}
        export async function saveAppStore(name,value){calls.push({command:'save',args:{name,value}});state=structuredClone(value)}
      `
      return null
    },
  }],
})
after(() => vite.close())
const fixture = await vite.ssrLoadModule(fixtureId)
const { useMediaWorkflowsStore } = await vite.ssrLoadModule('/src/stores/mediaWorkflows.ts')
const recipe = () => ({ version: 1, modality: 'image', steps: [{ capability: 'upscale', bindings: { upscaler: 'media:real-esrgan-x2' }, params: { scale: 2 } }] })
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no }); return { promise, resolve, reject } }
function store(t) { const value = useMediaWorkflowsStore(createPinia()); t.after(() => value.$dispose()); return value }

test('browser persistence merges two independent store instances and reloads reusable recipes', async t => {
  fixture.reset(null, undefined, false)
  const first = store(t), second = store(t)
  const [a, b] = await Promise.all([first.save({ name: 'Image', definition: recipe() }), second.save({ name: 'Image', definition: recipe() })])
  assert.notEqual(a.id, b.id)
  const reloaded = store(t); await reloaded.initialize()
  assert.equal(reloaded.presets.length, 2)
  assert.ok(fixture.calls.filter(call => call.command === 'save').every(call => call.args.name === 'media-workflows'))
})

test('native saves capture the clicked draft before a queued backend mutation can run', async t => {
  const held = deferred(), started = deferred(); let persistent = null, clock = 100
  fixture.reset(null, async (command, args) => {
    if (command === 'load') return persistent
    assert.equal(command, 'mutate_media_workflows')
    if (args.payload.name === 'first') { started.resolve(); await held.promise }
    const response = applyMediaWorkflowMutation(persistent, args.payload, clock++)
    persistent = response.state; return response
  })
  const value = store(t)
  const first = value.save({ name: 'first', definition: recipe() }); await started.promise
  const draft = recipe(); const second = value.save({ name: 'second', definition: draft })
  draft.steps[0].params.scale = 8
  held.resolve(); await Promise.all([first, second])
  assert.equal(persistent.presets.find(item => item.name === 'second').definition.steps[0].params.scale, 2)
  assert.equal(value.presets.length, 2)
})

test('a stale native response cannot replace a successfully saved library', async t => {
  const held = deferred(); let persistent = null
  fixture.reset(null, (command, args) => {
    if (command === 'load') return held.promise
    const response = applyMediaWorkflowMutation(persistent, args.payload, 100); persistent = response.state; return response
  })
  const value = store(t)
  const refreshing = value.refresh()
  await value.save({ name: 'new', definition: recipe() })
  held.resolve(null); await refreshing
  assert.equal(value.presets.length, 1)
  assert.equal(value.presets[0].name, 'new')
})

test('a refresh failure from before a successful save cannot report an obsolete read error', async t => {
  const held = deferred(); let persistent = null
  fixture.reset(null, (command, args) => {
    if (command === 'load') return held.promise
    const response = applyMediaWorkflowMutation(persistent, args.payload, 100)
    persistent = response.state
    return response
  })
  const value = store(t)
  const refreshing = value.refresh()
  await value.save({ name: 'Saved successfully', definition: recipe() })
  held.reject(new Error('obsolete read failure'))
  await assert.doesNotReject(refreshing)
  assert.equal(value.error, '')
  assert.equal(value.presets[0].name, 'Saved successfully')
})

test('an older successful refresh cannot clear a newer mutation conflict or its current state', async t => {
  const held = deferred()
  const persistent = applyMediaWorkflowMutation(null, { action: 'upsert', id: 'existing', name: 'Current', definition: recipe() }, 100).state
  fixture.reset(null, (command, args) => command === 'load' ? held.promise : applyMediaWorkflowMutation(persistent, args.payload, 101))
  const value = store(t)
  const refreshing = value.refresh()
  await assert.rejects(value.save({ id: 'existing', name: 'Stale', definition: recipe(), expectedUpdatedAt: 99 }), error => error.code === 'MEDIA_WORKFLOW_CONFLICT')
  const conflict = value.error
  held.resolve(null)
  await refreshing
  assert.equal(value.error, conflict)
  assert.ok(conflict)
  assert.equal(value.presets[0].name, 'Current')
})

test('conflicting edits refresh the list and surface a structured conflict without overwriting another window', async t => {
  let persistent = null, clock = 100
  fixture.reset(null, (command, args) => {
    if (command === 'load') return structuredClone(persistent)
    const response = applyMediaWorkflowMutation(persistent, args.payload, clock++); persistent = response.state; return response
  })
  const a = store(t), b = store(t)
  const original = await a.save({ name: 'Original', definition: recipe() }); await b.initialize()
  const latest = await a.save({ ...original, name: 'Latest', expectedUpdatedAt: original.updatedAt })
  await assert.rejects(b.save({ ...original, name: 'Stale', expectedUpdatedAt: original.updatedAt }), error => error.code === 'MEDIA_WORKFLOW_CONFLICT')
  assert.equal(b.presets[0].name, 'Latest')
  await assert.rejects(b.remove(original.id, original.updatedAt), error => error.code === 'MEDIA_WORKFLOW_CONFLICT')
  await a.remove(latest.id, latest.updatedAt)
  await assert.rejects(b.save({ ...latest, name: 'Resurrected', expectedUpdatedAt: latest.updatedAt }), error => error.code === 'MEDIA_WORKFLOW_CONFLICT')
  assert.equal(persistent.presets.length, 0)
})

test('failed initialization retries and invalid persisted data never becomes a successful empty load', async t => {
  let fail = true
  fixture.reset(null, () => { if (fail) throw new Error('cannot read'); return { version: 2, presets: [] } })
  const value = store(t)
  await assert.rejects(value.initialize(), /cannot read/)
  assert.equal(value.loading, false)
  fail = false
  await assert.rejects(value.initialize(), /Invalid media workflow preset/)
  fixture.reset(null)
  await value.initialize()
  assert.equal(value.error, '')
  assert.deepEqual(value.presets, [])
})

test('an update without its base revision is refused before any native write', async t => {
  fixture.reset(null)
  await assert.rejects(store(t).save({ id: 'existing', name: 'Unsafe update', definition: recipe() }), /original revision/)
  assert.equal(fixture.calls.length, 0)
})
