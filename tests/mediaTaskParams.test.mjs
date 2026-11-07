import assert from 'node:assert/strict'
import test, { after } from 'node:test'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { createRenderer, nextTick } from 'vue'
import { createPinia, disposePinia } from 'pinia'
import { createServer, transformWithEsbuild } from 'vite'
import { parse, compileScript } from 'vue/compiler-sfc'

const fixtureId = '\0media-params-fixture'
const stubs = {
  '@tauri-apps/api/core': `export { invoke } from '${fixtureId}'; export const convertFileSrc = path => path`,
  '@tauri-apps/api/event': 'export const listen = async () => () => {}',
  '@tauri-apps/api/webview': 'export const getCurrentWebview = () => ({ onDragDropEvent: async () => () => {} })',
  '@tauri-apps/plugin-dialog': 'export const open = async () => null',
  'vue-i18n': 'export const useI18n = () => ({ t: key => key })',
  'vue-router': 'export const useRoute = () => ({ query: {} }); export const useRouter = () => ({ push() {} })',
  'naive-ui': 'export const useMessage = () => ({ warning() {}, error() {}, success() {}, info() {} })',
  '@vicons/ionicons5': 'export const CloseCircleOutline={}, DocumentOutline={}, FolderOpenOutline={}, SparklesOutline={}, CubeOutline={}, VideocamOutline={}',
}
const vite = await createServer({
  configFile: false, server: { watch: null, middlewareMode: true, hmr: false, ws: false, preTransformRequests: false },
  appType: 'custom', optimizeDeps: { noDiscovery: true },
  resolve: { alias: {
    ...Object.fromEntries(Object.keys(stubs).map(id => [id, `\0media-param-stub:${id}`])),
    '@': fileURLToPath(new URL('../src', import.meta.url)),
  } },
  plugins: [{
    name: 'media-param-boundaries', enforce: 'pre',
    resolveId(id) { return id === fixtureId || id.startsWith('\0media-param-stub:') ? id : null },
    load(id) {
      if (id.startsWith('\0media-param-stub:')) return stubs[id.slice('\0media-param-stub:'.length)]
      if (id === fixtureId) return `export const calls = [], data = { capabilities: [] }; export async function invoke(command, args) { calls.push({command, args}); return command === 'media_capabilities' ? data : command === 'agent_jobs' ? {schemaVersion:2,jobs:[],dataRoot:'/fixture',jobsDir:'/fixture/agent-jobs'} : {} }`
      return null
    },
    async transform(code, id) {
      const path = id.replaceAll('\\', '/')
      if (['/src/views/ImageView.vue', '/src/views/VideoView.vue', '/src/components/media/ProcessingPlan.vue'].some(name => path.endsWith(name))) {
        const { descriptor } = parse(code, { filename: path })
        const script = compileScript(descriptor, { id: path })
        return transformWithEsbuild(script.content, `${path}.ts`, { loader: 'ts', tsconfigRaw: {} })
      }
      if (path.endsWith('.vue')) return 'export default { render: () => null }'
      if (path.endsWith('/src/composables/useMediaDrop.ts')) return 'export const useMediaDrop = () => ({})'
      if (path.endsWith('/src/stores/settings.ts')) return 'export const useSettingsStore = () => ({ outputDir: "" })'
      return null
    },
  }],
})
after(() => vite.close())
globalThis.window = { __TAURI_INTERNALS__: {} }
const fixture = await vite.ssrLoadModule(fixtureId)
const ImageView = (await vite.ssrLoadModule('/src/views/ImageView.vue')).default
const VideoView = (await vite.ssrLoadModule('/src/views/VideoView.vue')).default
const Plan = (await vite.ssrLoadModule('/src/components/media/ProcessingPlan.vue')).default
ImageView.render = VideoView.render = Plan.render = () => null
const renderer = createRenderer({
  createComment: text => ({ text }), createText: text => ({ text }), createElement: tag => ({ tag, children: [] }),
  insert(node, parent) { node.parent = parent; parent.children.push(node) },
  remove(node) { node.parent.children = node.parent.children.filter(item => item !== node) },
  parentNode: node => node.parent, nextSibling: () => null,
  setText(node, text) { node.text = text }, setElementText(node, text) { node.text = text }, patchProp() {},
})
async function mount(t, Component, props) {
  fixture.calls.length = 0
  const app = renderer.createApp(Component, props)
  const pinia = createPinia()
  app.use(pinia)
  app.mount({ children: [] })
  t.after(() => { app.unmount(); disposePinia(pinia) })
  await nextTick(); await new Promise(resolve => setImmediate(resolve))
  return app._instance.setupState
}
const capData = JSON.parse(readFileSync(new URL('../python/media_ops/capabilities.json', import.meta.url), 'utf8')).capabilities
const models = JSON.parse(readFileSync(new URL('../python/media_ops/media_catalog.json', import.meta.url), 'utf8')).models
fixture.data.capabilities = capData.map(cap => ({ ...cap, implemented: true, models: cap.modelIds.map(id => {
  const model = models.find(item => `media:${item.id}` === id)
  return { ...model, id, implemented: true, downloaded: true, roles: Object.entries(cap.roles).filter(([, role]) => role.task === model.task).map(([name]) => name) }
}) }))

test('image UI sends complete step bindings and parameters for detection instead of losing confidence', async t => {
  const state = await mount(t, ImageView)
  const step = { capability: 'object_detection', bindings: { detector: 'media:yolo11n' }, params: { conf: 0 } }
  state.inputs = ['input.png']; state.outputDir = 'out'; state.planStep = step
  await state.run()
  const payload = fixture.calls.find(call => call.command === 'start_audio_tool').args.payload
  assert.deepEqual(JSON.parse(JSON.stringify(payload.steps)), [step])
  assert.equal(payload.mode, undefined)
})

test('video UI preserves nested custom target, tile and model selection', async t => {
  const state = await mount(t, VideoView)
  const step = { capability: 'upscale', bindings: { upscaler: 'media:real-esrgan-x4' }, params: { target: 'custom', width: 1920, height: 1080, scale: 3, tile: 128, fit: 'cover' } }
  state.inputs = ['input.mp4']; state.outputDir = 'out'; state.planStep = step; state.crf = 0
  await state.run()
  const payload = fixture.calls.find(call => call.command === 'start_audio_tool').args.payload
  assert.deepEqual(JSON.parse(JSON.stringify(payload.steps)), [step])
  assert.equal(payload.crf, 0)
})

test('image interpolation exposes timestep while video interpolation exposes multiplier from the same schema', async t => {
  const step = { capability: 'frame_interpolation', bindings: { interpolator: 'media:rife-v46' }, params: {} }
  const image = await mount(t, Plan, { modelValue: step, modality: 'image' })
  assert.deepEqual(Object.keys(image.effectiveParams), ['timestep'])
  assert.deepEqual(Object.keys(image.buildStep(fixture.data.capabilities.find(cap => cap.id === step.capability)).params), ['timestep'])
  const video = await mount(t, Plan, { modelValue: step, modality: 'video' })
  assert.deepEqual(Object.keys(video.effectiveParams), ['multiplier'])
  assert.deepEqual(Object.keys(video.buildStep(fixture.data.capabilities.find(cap => cap.id === step.capability)).params), ['multiplier'])
})

test('video super-resolution does not expose or submit the unimplemented tile control', async t => {
  const cap = fixture.data.capabilities.find(item => item.id === 'video_super_resolution')
  const state = await mount(t, Plan, { modelValue: { capability: cap.id, bindings: { upscaler: 'media:basicvsrpp' }, params: {} }, modality: 'video' })
  assert.deepEqual(Object.keys(state.effectiveParams), [])
  assert.deepEqual(Object.keys(state.buildStep(cap).params), [])
})

function workflowMatrix(t) {
  for (const cap of fixture.data.capabilities) {
    cap.workflow = {
      image: { supported: ['upscale', 'watermark_removal', 'denoise'].includes(cap.id), supportedParams: Object.keys(cap.params) },
      video: { supported: ['upscale', 'watermark_removal', 'video_super_resolution', 'video_inpainting'].includes(cap.id),
        supportedParams: cap.id === 'video_super_resolution' ? [] : Object.keys(cap.params), ...(['video_super_resolution', 'video_inpainting'].includes(cap.id) ? { maxSteps: 1 } : {}) },
    }
  }
  t.after(() => { for (const cap of fixture.data.capabilities) delete cap.workflow })
}

test('workflow selectors expose only implemented serial capabilities without replacing an unsupported restored step', async t => {
  workflowMatrix(t)
  const state = await mount(t, Plan, { modelValue: { capability: 'frame_interpolation', bindings: { interpolator: 'media:rife-v46' }, params: { timestep: 0.5 } }, modality: 'image', workflow: true })
  assert.deepEqual(state.available.map(cap => cap.id).sort(), ['denoise', 'upscale', 'watermark_removal'])
  assert.equal(state.current.id, 'frame_interpolation')
  assert.equal(state.hasUsableModel, false)
})

test('sequence-only workflow steps disable execution when composed and submit only supported parameters', async t => {
  workflowMatrix(t)
  const cap = fixture.data.capabilities.find(item => item.id === 'video_super_resolution')
  const state = await mount(t, Plan, { modelValue: { capability: cap.id, bindings: { upscaler: 'media:basicvsrpp' }, params: {} }, modality: 'video', workflow: true, stepCount: 2 })
  assert.equal(state.tooManySteps, true)
  assert.equal(state.hasUsableModel, false)
  assert.deepEqual(Object.keys(state.effectiveParams), [])
  assert.deepEqual(Object.keys(state.buildStep(cap).params), [])
})

test('availability validates every selected required role rather than an unrelated downloaded model', async t => {
  const cap = fixture.data.capabilities.find(item => item.id === 'upscale')
  const chosen = cap.models.find(model => model.id === 'media:real-esrgan-x2')
  const downloaded = chosen.downloaded
  chosen.downloaded = false
  t.after(() => { chosen.downloaded = downloaded })
  const state = await mount(t, Plan, { modelValue: { capability: cap.id, bindings: { upscaler: chosen.id }, params: {} }, modality: 'image' })
  assert.ok(cap.models.some(model => model.downloaded && model.implemented))
  assert.equal(state.hasUsableModel, false)
  assert.notEqual(state.buildStep(cap).bindings.upscaler, chosen.id)
})
