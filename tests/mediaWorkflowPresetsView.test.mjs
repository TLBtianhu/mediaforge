import assert from 'node:assert/strict'
import test, { after } from 'node:test'
import { fileURLToPath } from 'node:url'
import { createRenderer, nextTick } from 'vue'
import { createServer, transformWithEsbuild } from 'vite'
import { parse, compileScript } from 'vue/compiler-sfc'

const fixtureId = '\0media-presets-view-fixture'
const stubs = {
  'vue-i18n': 'export const useI18n = () => ({ t: (key, params) => params ? key+":"+JSON.stringify(params) : key })',
  'naive-ui': `export { useMessage } from '${fixtureId}'`,
  '@vicons/ionicons5': 'export const RefreshOutline={}, GitNetworkOutline={}, CloudUploadOutline={}, DocumentOutline={}, CloseCircleOutline={}, FolderOpenOutline={}, PlayOutline={}',
  '@tauri-apps/plugin-dialog': 'export const open = async () => null',
}
const vite = await createServer({
  configFile: false, server: { watch: null, middlewareMode: true, hmr: false, preTransformRequests: false },
  appType: 'custom', optimizeDeps: { noDiscovery: true },
  resolve: { alias: { ...Object.fromEntries(Object.keys(stubs).map(id => [id, `\0media-presets-stub:${id}`])), '@': fileURLToPath(new URL('../src', import.meta.url)) } },
  plugins: [{
    name: 'media-presets-view-boundaries', enforce: 'pre',
    resolveId(id) { return id === fixtureId || id.startsWith('\0media-presets-stub:') ? id : null },
    load(id) {
      if (id.startsWith('\0media-presets-stub:')) return stubs[id.slice('\0media-presets-stub:'.length)]
      if (id === fixtureId) return `
        import { reactive } from 'vue'
        export const calls=[], messages=[], hooks={}
        const clone=value=>JSON.parse(JSON.stringify(value))
        export const store=reactive({presets:[],loading:false,error:'',
          async initialize(){calls.push({action:'initialize'});if(hooks.initialize)return hooks.initialize()},
          async refresh(){calls.push({action:'refresh'});if(hooks.refresh)return hooks.refresh()},
          async save(request){calls.push({action:'save',request:clone(request)});if(hooks.save)return hooks.save(request)
            const saved={id:request.id||'new-preset',name:request.name,definition:clone(request.definition),createdAt:1,updatedAt:20}
            store.presets=[saved,...store.presets.filter(item=>item.id!==saved.id)];return clone(saved)},
          async remove(id,revision){calls.push({action:'remove',id,revision});if(hooks.remove)return hooks.remove(id,revision)
            store.presets=store.presets.filter(item=>item.id!==id)},
        })
        export function reset(presets){store.presets=clone(presets);store.loading=false;store.error='';calls.length=messages.length=0;for(const key of Object.keys(hooks))delete hooks[key]}
        export const useMessage=()=>Object.fromEntries(['success','warning','error','info'].map(level=>[level,text=>messages.push({level,text})]))
      `
      return null
    },
    async transform(code, id) {
      const path = id.replaceAll('\\', '/')
      if (path.endsWith('/src/components/media/MediaWorkflowPresets.vue') || path.endsWith('/src/views/MediaWorkflowView.vue')) {
        const { descriptor } = parse(code, { filename: path })
        const script = compileScript(descriptor, { id: path })
        return transformWithEsbuild(script.content, `${path}.ts`, { loader: 'ts', tsconfigRaw: {} })
      }
      if (path.endsWith('.vue')) return 'export default { render: () => null }'
      if (path.endsWith('/src/stores/mediaWorkflows.ts')) return `export { store as useStoreFixture } from '${fixtureId}'; import {store} from '${fixtureId}'; export const useMediaWorkflowsStore=()=>store`
      if (path.endsWith('/src/composables/useMediaDrop.ts')) return 'export const useMediaDrop=()=>({})'
      if (path.endsWith('/src/composables/useMediaJob.ts')) return `import{ref}from'vue';export const useMediaJob=()=>({busy:ref(false),progress:ref(0),status:ref(''),outputs:ref([]),start:async()=>{}})`
      return null
    },
  }],
})
after(() => vite.close())
const fixture = await vite.ssrLoadModule(fixtureId)
const Presets = (await vite.ssrLoadModule('/src/components/media/MediaWorkflowPresets.vue')).default
const View = (await vite.ssrLoadModule('/src/views/MediaWorkflowView.vue')).default
Presets.render = View.render = () => null
const renderer = createRenderer({
  createComment: text => ({ text }), createText: text => ({ text }), createElement: tag => ({ tag, children: [] }),
  insert(node, parent) { node.parent = parent; parent.children.push(node) },
  remove(node) { node.parent.children = node.parent.children.filter(item => item !== node) },
  parentNode: node => node.parent, nextSibling: () => null,
  setText(node, text) { node.text = text }, setElementText(node, text) { node.text = text }, patchProp() {},
})
const definition = (modality = 'image', scale = 2) => ({ version: 1, modality, steps: [{ capability: 'upscale', bindings: { upscaler: 'media:real-esrgan-x2' }, params: { scale } }] })
const preset = (id = 'first', revision = 10) => ({ id, name: `Plan ${id}`, definition: definition(), createdAt: 1, updatedAt: revision })
const clone = value => JSON.parse(JSON.stringify(value))
const flush = async () => { await nextTick(); await new Promise(resolve => setImmediate(resolve)) }
function conflict() { return Object.assign(new Error('Changed elsewhere'), { code: 'MEDIA_WORKFLOW_CONFLICT' }) }
async function mount(t, { saved = [preset()], draft = definition(), Component = Presets } = {}) {
  fixture.reset(saved)
  const applied = []
  const app = renderer.createApp(Component, { definition: draft, busy: false, onApply: value => applied.push(value) })
  app.mount({ children: [] })
  const instance = app._instance
  t.after(() => app.unmount())
  await flush()
  return { state: instance.setupState, props: instance.props, applied }
}

test('initialization and selecting a preset do not apply it or change a restored draft', async t => {
  const draft = definition('video', 4)
  const env = await mount(t, { draft })
  assert.equal(env.state.selected, null)
  env.state.selectPreset('first')
  assert.equal(env.state.selected.updatedAt, 10)
  assert.equal(env.state.name, 'Plan first')
  assert.deepEqual(env.applied, [])
  assert.deepEqual(env.props.definition, draft)
})

test('explicit loading applies a detached definition and the parent keeps all task paths and inputs', async t => {
  const saved = preset(); saved.definition = definition('video', 4)
  const env = await mount(t, { saved: [saved] })
  env.state.selectPreset('first'); env.state.applySelected()
  assert.deepEqual(env.applied, [saved.definition])
  env.applied[0].steps[0].params.scale = 3
  assert.equal(fixture.store.presets[0].definition.steps[0].params.scale, 4)
  const parent = await mount(t, { Component: View })
  parent.state.inputs = ['C:/input.png']; parent.state.outputDir = 'C:/out'; parent.state.modelDir = 'C:/models'; parent.state.maskPath = 'C:/mask.png'
  parent.state.applyPreset(saved.definition)
  assert.equal(parent.state.modality, 'video')
  assert.equal(parent.state.steps[0].params.scale, 4)
  assert.deepEqual(parent.state.inputs, ['C:/input.png'])
  assert.equal(parent.state.outputDir, 'C:/out'); assert.equal(parent.state.modelDir, 'C:/models'); assert.equal(parent.state.maskPath, 'C:/mask.png')
})

test('save as new snapshots only the plan and never reuses the selected identity or saves task paths', async t => {
  const draft = { ...definition('video', 4), inputs: ['private.wav'], outputDir: 'private-out', modelDir: 'private-models', maskPath: 'mask.png', requestId: 'task-id' }
  draft.steps[0].outputDir = 'step-output'
  const env = await mount(t, { draft })
  env.state.selectPreset('first'); env.state.name = '  New plan  '
  await env.state.save(true)
  const request = fixture.calls.find(call => call.action === 'save').request
  assert.deepEqual(request, { name: 'New plan', definition: definition('video', 4) })
  assert.deepEqual(env.applied, [])
})

test('a refresh never advances the selected base revision, and conflict keeps the editable draft', async t => {
  const env = await mount(t, { draft: definition('video', 4) })
  env.state.selectPreset('first'); env.state.name = 'My draft'
  fixture.store.presets[0].updatedAt = 50
  fixture.hooks.save = async () => { throw conflict() }
  await env.state.save(false)
  assert.equal(env.state.selected.updatedAt, 10)
  assert.equal(env.state.conflicted, true)
  assert.equal(env.state.name, 'My draft')
  assert.deepEqual(env.props.definition, definition('video', 4))
  fixture.hooks.refresh = async () => { fixture.store.presets[0].updatedAt = 90 }
  await env.state.refresh(); await env.state.save(false)
  const writes = fixture.calls.filter(call => call.action === 'save')
  assert.deepEqual(writes.map(call => call.request.expectedUpdatedAt), [10, 10])
  assert.deepEqual(env.applied, [])
})

test('explicit loading after a refresh takes the latest definition and establishes a new base', async t => {
  const env = await mount(t)
  env.state.selectPreset('first')
  fixture.store.presets[0].updatedAt = 90; fixture.store.presets[0].definition = definition('video', 3)
  await env.state.refresh()
  assert.equal(env.state.selected.updatedAt, 10)
  env.state.applySelected()
  assert.equal(env.state.selected.updatedAt, 90)
  assert.deepEqual(env.applied, [definition('video', 3)])
  await env.state.save(false)
  assert.equal(fixture.calls.find(call => call.action === 'save').request.expectedUpdatedAt, 90)
})

test('busy handlers cannot apply, save, refresh or delete a plan', async t => {
  const env = await mount(t)
  env.state.selectPreset('first'); env.state.prepareRemoval(); env.props.busy = true
  const before = fixture.calls.length
  env.state.applySelected(); env.state.selectPreset(null); await env.state.save(true); await env.state.save(false); await env.state.refresh(); await env.state.removeSelected()
  assert.equal(env.state.locked, true)
  assert.equal(fixture.calls.length, before)
  assert.deepEqual(env.applied, [])
  assert.equal(env.state.selected.id, 'first')
})

test('an in-flight save captures its definition once and prevents duplicate writes', async t => {
  const env = await mount(t)
  let resolve
  fixture.hooks.save = request => new Promise(done => { resolve = () => done({ ...preset('new-preset', 30), name: request.name, definition: clone(request.definition) }) })
  env.state.name = 'Captured'
  const saving = env.state.save(true)
  env.props.definition.steps[0].params.scale = 4
  await env.state.save(true)
  assert.equal(fixture.calls.filter(call => call.action === 'save').length, 1)
  assert.equal(fixture.calls.find(call => call.action === 'save').request.definition.steps[0].params.scale, 2)
  resolve(); await saving
  assert.equal(env.props.definition.steps[0].params.scale, 4)
  assert.equal(env.state.selected.definition.steps[0].params.scale, 2)
})

test('delete confirmation retains its original target even if the selected preset changes', async t => {
  const env = await mount(t, { saved: [preset('first'), preset('second', 40)] })
  env.state.selectPreset('first'); env.state.prepareRemoval(); env.state.selectPreset('second')
  await env.state.removeSelected()
  assert.deepEqual(fixture.calls.find(call => call.action === 'remove'), { action: 'remove', id: 'first', revision: 10 })
  assert.equal(env.state.selected.id, 'second')
  assert.deepEqual(env.applied, [])
  assert.deepEqual(env.props.definition, definition())
})

test('a delete conflict leaves the selected plan and working draft available', async t => {
  const env = await mount(t)
  env.state.selectPreset('first'); env.state.prepareRemoval()
  fixture.hooks.remove = async () => { throw conflict() }
  assert.equal(await env.state.removeSelected(), false)
  assert.equal(env.state.selected.id, 'first')
  assert.equal(env.state.conflicted, true)
  assert.deepEqual(env.props.definition, definition())
})

test('blank names do not persist and a failed refresh keeps the selection and draft name', async t => {
  const env = await mount(t)
  env.state.selectPreset('first'); env.state.name = ' '
  await env.state.save(true)
  assert.equal(fixture.calls.filter(call => call.action === 'save').length, 0)
  env.state.name = 'Unsaved name'
  fixture.hooks.refresh = async () => { throw new Error('read failed') }
  await env.state.refresh()
  assert.equal(env.state.selected.updatedAt, 10)
  assert.equal(env.state.name, 'Unsaved name')
  assert.equal(env.state.notice, 'read failed')
})
