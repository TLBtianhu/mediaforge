import assert from 'node:assert/strict'
import test, { after } from 'node:test'
import { fileURLToPath } from 'node:url'
import { createPinia, disposePinia } from 'pinia'
import { createRenderer, h, nextTick } from 'vue'
import { createServer, transformWithEsbuild } from 'vite'
import { parse, compileScript, compileTemplate } from 'vue/compiler-sfc'

const id = '\0journal-removal-view-fixture'
const stubs = {
  '@tauri-apps/api/core': `export {invoke} from '${id}'; export const convertFileSrc=path=>path`,
  '@tauri-apps/api/event': `export const listen=async()=>()=>{}`,
  'vue-i18n': `export const useI18n=()=>({t:(key,values)=>key+(values?JSON.stringify(values):'')})`,
  'naive-ui': `export {useMessage,useDialog,NCheckbox} from '${id}'`,
  '@vicons/ionicons5': `export const FolderOpenOutline={},RefreshOutline={},TrashOutline={}`,
}
const vite = await createServer({
  configFile: false, appType: 'custom', optimizeDeps: { noDiscovery: true },
  server: { middlewareMode: true, watch: null, hmr: false, ws: false, preTransformRequests: false },
  resolve: { alias: { ...Object.fromEntries(Object.keys(stubs).map(name => [name, '\0journal-removal-stub:' + name])), '@': fileURLToPath(new URL('../src', import.meta.url)) } },
  plugins: [{
    name: 'journal-removal-native-boundaries', enforce: 'pre',
    resolveId(name) { return name === id || name.startsWith('\0journal-removal-stub:') ? name : null },
    load(name) {
      if (name.startsWith('\0journal-removal-stub:')) return stubs[name.slice('\0journal-removal-stub:'.length)]
      if (name === id) return `
        import {reactive} from 'vue';
        export const calls=[],dialogs=[],messages=[],remote=new Map(); export const NCheckbox={name:'FixtureCheckbox'};let hook;
        export function reset(jobs,nextHook){calls.length=dialogs.length=messages.length=0;remote.clear();jobs.forEach(job=>remote.set(job.jobId,structuredClone(job)));hook=nextHook}
        export function setHook(next){hook=next}
        export function response(args,extra={}){return {schemaVersion:2,checkedJobIds:[...args.jobIds],removedJobIds:args.checkOnly?[]:[...args.jobIds],trashed:[],failed:[],retained:[],...extra}}
        export async function invoke(command,args){calls.push({command,args:structuredClone(args)});
          if(command==='agent_jobs')return {schemaVersion:3,full:true,removed:[],jobs:structuredClone([...remote.values()]),dataRoot:'/fixture',jobsDir:'/fixture/agent-jobs'};
          if(command==='remove_result_jobs'){
            if(hook){const result=await hook(args);if(result!==undefined)return result}
            if(!args.checkOnly)args.jobIds.forEach(id=>remote.delete(id));return response(args);
          }
          throw Error('Unexpected native call '+command)
        }
        export const useMessage=()=>Object.fromEntries(['success','warning','error'].map(level=>[level,text=>messages.push({level,text})]));
        export const useDialog=()=>({warning(options){const entry=reactive({...options,destroyed:false,destroy(){entry.destroyed=true;options.onAfterLeave?.()}});dialogs.push(entry);return entry}});
      `
    },
    async transform(code, filename) {
      filename = filename.replaceAll('\\', '/')
      if (filename.endsWith('/src/views/results/JournalResults.vue')) {
        const { descriptor } = parse(code, { filename })
        const script = compileScript(descriptor, { id: filename })
        const template = compileTemplate({ source: descriptor.template.content, filename, id: filename, compilerOptions: { bindingMetadata: script.bindings } })
        assert.deepEqual(template.errors, [])
        return transformWithEsbuild(script.content + '\n' + template.code, filename + '.ts', { loader: 'ts', tsconfigRaw: {} })
      }
      if (filename.endsWith('/src/stores/settings.ts')) return 'export const useSettingsStore=()=>({developerMode:false})'
    },
  }],
})
after(() => vite.close())
globalThis.window = { __TAURI_INTERNALS__: {} }
const fixture = await vite.ssrLoadModule(id)
const viewModule = await vite.ssrLoadModule('/src/views/results/JournalResults.vue')
const Component = viewModule.default
Component.render = () => null
const { useMediaJobsStore } = await vite.ssrLoadModule('/src/stores/mediaJobs.ts')
const { buildJournalResults } = await vite.ssrLoadModule('/src/views/results/journalResults.ts')
const renderer = createRenderer({
  createComment: () => ({ children: [] }), createText: text => ({ text }), createElement: tag => ({ tag, props: {}, children: [] }),
  insert(node, parent) { node.parent = parent; parent.children.push(node) }, remove(node) { node.parent.children = node.parent.children.filter(child => child !== node) },
  parentNode: node => node.parent, nextSibling: () => null, setText(node, text) { node.text = text }, setElementText(node, text) { node.text = text }, patchProp(node, key, _prev, value) { node.props[key] = value },
})
const flush = async () => { await nextTick(); await new Promise(resolve => setImmediate(resolve)); await nextTick() }
const job = (jobId, extra = {}) => ({
  schemaVersion: 2, jobId, command: 'audio_tools', source: 'manual', status: 'completed', owner: null,
  fraction: 1, message: '', error: null, createdAt: 1700000000, updatedAt: 1700000002, startedAt: 1700000001, finishedAt: 1700000002,
  runtime: { stage: 'done', progress: null, nodes: {}, order: [] }, logs: [],
  payload: { operation: 'media_image', inputs: ['/input/source.png'], outputDir: '/out' }, result: { outputs: [{ path: `/out/${jobId}.png` }] }, ...extra,
})
const removals = () => fixture.calls.filter(call => call.command === 'remove_result_jobs')
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done }); return { promise, resolve } }
async function mount(t, records = [job('media'), job('agent', { source: 'agent' })], hook, template = false) {
  fixture.reset(records, hook)
  const pinia = createPinia(), store = useMediaJobsStore(pinia)
  await store.initialize()
  const View = template ? { ...Component, render: viewModule.render } : Component
  const app = renderer.createApp({ setup() { return () => { const results = buildJournalResults(store.jobs); return h(View, { results, total: results.length, loading: store.loading, error: store.error }) } } })
  app.config.warnHandler = () => {}
  for (const name of ['NButton', 'NTag', 'NIcon', 'NCheckbox', 'NAlert', 'NPagination']) {
    app.component(name, { inheritAttrs: false, setup(_props, { attrs, slots }) { return () => h('fixture-' + name.toLowerCase(), attrs, slots.default?.()) } })
  }
  const root = { children: [] }
  app.use(pinia); app.mount(root)
  t.after(() => { if (app._instance) app.unmount(); disposePinia(pinia) })
  await flush()
  return { store, app, root, state: app._instance.subTree.component.setupState }
}
const allNodes = node => [node, ...(node.children ?? []).flatMap(allNodes)]
const visibleText = root => allNodes(root).map(node => node.text ?? '').join(' ')
function open(state, ids = ['media']) {
  state.handleRemove(state.pageResults.filter(row => ids.includes(row.id)))
  return fixture.dialogs.at(-1)
}
function chooseFiles(dialog, checked) {
  const checkbox = dialog.content().children.find(node => node.type?.name === 'FixtureCheckbox')
  assert.equal(checkbox.props.checked, false)
  checkbox.props['onUpdate:checked'](checked)
}

test('record-only removal is unchecked by default and forgets only acknowledged jobs without resurrecting on a late refresh', async t => {
  const records = [job('media'), job('agent', { source: 'agent' })]
  const { store, state } = await mount(t, records)
  const dialog = open(state)
  chooseFiles(dialog, false)
  assert.equal(removals().length, 0)
  await dialog.onPositiveClick(); await flush()
  assert.deepEqual(removals().map(call => call.args), [
    { jobIds: ['media'], deleteFiles: false, checkOnly: true }, { jobIds: ['media'], deleteFiles: false, checkOnly: false },
  ])
  assert.deepEqual(store.jobs.map(row => row.jobId), ['agent'])
  store.adoptSnapshot({ schemaVersion: 3, full: true, removed: [], jobs: records, dataRoot: '/fixture', jobsDir: '/fixture/agent-jobs' })
  assert.deepEqual(store.jobs.map(row => row.jobId), ['agent'])
  assert.equal(fixture.calls.some(call => ['move_paths_to_trash', 'delete_editor_projects_for_tasks'].includes(call.command)), false)
})

for (const status of ['failed', 'cancelled', 'interrupted']) test(`${status} Agent partial outputs remain removable through the same safe boundary`, async t => {
  const { store, state } = await mount(t, [job('partial', { source: 'agent', status })])
  await open(state, ['partial']).onPositiveClick()
  assert.equal(store.jobs.length, 0)
  assert.deepEqual(removals()[1].args.jobIds, ['partial'])
})

test('selected recycling releases previews and sends only job identities and explicit booleans', async t => {
  let state
  const mounted = await mount(t, undefined, args => {
    if (args.checkOnly) assert.equal(state.previews.media, undefined)
  })
  state = mounted.state
  state.togglePreview('media', '/out/media.png')
  const dialog = open(state)
  chooseFiles(dialog, true)
  await dialog.onPositiveClick()
  assert.ok(removals().every(call => call.args.deleteFiles === true && !('paths' in call.args)))
  assert.equal(mounted.store.jobs.some(row => row.jobId === 'media'), false)
})

test('busy lease preflight prevents removal and leaves the selected record available for retry', async t => {
  const { store, state } = await mount(t, undefined, () => { throw new Error('lease busy') })
  state.toggleSelecting(); state.toggleSelection('media', true)
  const dialog = open(state)
  assert.equal(await dialog.onPositiveClick(), false)
  assert.equal(removals().length, 1)
  assert.equal(removals()[0].args.checkOnly, true)
  assert.equal(store.jobs.length, 2)
  assert.deepEqual(state.selectedIds, ['media'])
  assert.ok(fixture.messages.some(message => message.level === 'warning' && message.text === 'results.removeNotReady'))
})

test('recycling failures retain all records until the separate record-only confirmation succeeds', async t => {
  const { store, state } = await mount(t, undefined, args => args.deleteFiles && !args.checkOnly ? fixture.response(args, {
    removedJobIds: [], trashed: ['/out/earlier.png'], failed: [{ path: '/out/media.png', error: 'locked' }],
    retained: [{ path: '/input/shared.png', reason: 'library_index_unavailable' }],
  }) : undefined)
  const dialog = open(state)
  chooseFiles(dialog, true)
  const pending = dialog.onPositiveClick(); await flush()
  assert.equal(fixture.dialogs.length, 2)
  assert.equal(store.jobs.length, 2)
  assert.equal(removals().length, 2)
  fixture.dialogs[1].onPositiveClick()
  await pending
  assert.deepEqual(removals().map(call => [call.args.checkOnly, call.args.deleteFiles]), [[true, true], [false, true], [true, false], [false, false]])
  assert.deepEqual(store.jobs.map(row => row.jobId), ['agent'])
  assert.ok(fixture.messages.some(message => message.text.startsWith('results.journal.filesRetained') && message.text.includes('1')))
  assert.ok(fixture.messages.every(message => !message.text.includes('library_index_unavailable')))
})

test('declining record-only fallback preserves the record and its selection after partial recycling', async t => {
  const { store, state } = await mount(t, undefined, args => args.deleteFiles && !args.checkOnly ? fixture.response(args, { removedJobIds: [], failed: [{ path: '/out/media.png', error: 'locked' }] }) : undefined)
  state.toggleSelecting(); state.toggleSelection('media', true)
  const dialog = open(state); chooseFiles(dialog, true)
  const pending = dialog.onPositiveClick(); await flush()
  fixture.dialogs[1].onNegativeClick(); await pending
  assert.equal(store.jobs.length, 2)
  assert.deepEqual(state.selectedIds, ['media'])
  assert.equal(removals().length, 2)
  assert.ok(!fixture.messages.some(message => message.level === 'success'))
})

test('protected files report retention without offering a failure fallback or claiming all files were recycled', async t => {
  const { state } = await mount(t, undefined, args => {
    if (!args.checkOnly) { fixture.remote.delete('media'); return fixture.response(args, { retained: [{ path: '/out/media.png', reason: 'library_index_unavailable' }] }) }
  })
  const dialog = open(state); chooseFiles(dialog, true)
  await dialog.onPositiveClick()
  assert.equal(fixture.dialogs.length, 1)
  assert.ok(fixture.messages.some(message => message.level === 'success' && message.text.startsWith('results.journal.removeSuccess')))
  assert.ok(fixture.messages.some(message => message.level === 'warning' && message.text.startsWith('results.journal.filesRetained')))
  assert.ok(!fixture.messages.some(message => message.text.includes('library_index_unavailable') || message.text.includes('removeFilesSuccess')))
})

test('final IPC rejection preserves records and never reports success', async t => {
  const { store, state } = await mount(t, undefined, args => { if (!args.checkOnly) throw new Error('metadata write failed') })
  assert.equal(await open(state).onPositiveClick(), false)
  assert.equal(store.jobs.length, 2)
  assert.ok(fixture.messages.some(message => message.level === 'error' && message.text === 'metadata write failed'))
  assert.ok(!fixture.messages.some(message => message.level === 'success'))
})

test('duplicate confirms cannot submit twice and unmount still installs acknowledged deletion tombstones without notifications', async t => {
  const gate = deferred()
  const { store, state, app } = await mount(t, undefined, async args => { if (!args.checkOnly) { await gate.promise; fixture.remote.delete('media'); return fixture.response(args) } })
  const dialog = open(state)
  const pending = dialog.onPositiveClick(); await flush()
  assert.equal(await dialog.onPositiveClick(), false)
  state.handleRemove(state.pageResults)
  assert.equal(fixture.dialogs.length, 1)
  assert.equal(removals().length, 2)
  app.unmount(); gate.resolve(); await pending
  assert.equal(store.jobs.some(row => row.jobId === 'media'), false)
  assert.equal(fixture.messages.length, 0)
  assert.equal(dialog.destroyed, true)
})

test('unmount while preflight is pending never starts the final removal request', async t => {
  for (const deleteFiles of [false, true]) {
    const gate = deferred()
    const { store, state, app } = await mount(t, undefined, async args => {
      if (args.checkOnly) await gate.promise
    })
    const dialog = open(state)
    chooseFiles(dialog, deleteFiles)
    const pending = dialog.onPositiveClick(); await flush()
    assert.equal(removals().length, 1)
    assert.equal(removals()[0].args.checkOnly, true)
    assert.equal(removals()[0].args.deleteFiles, deleteFiles)
    app.unmount()
    gate.resolve()
    assert.equal(await pending, false)
    assert.equal(removals().length, 1)
    assert.equal(store.jobs.length, 2)
    assert.equal(fixture.remote.size, 2)
    assert.equal(fixture.messages.length, 0)
    assert.equal(dialog.destroyed, true)
  }
})

test('page selection can remove a multi-page batch without sending unrelated records', async t => {
  const { store, state } = await mount(t, Array.from({ length: 14 }, (_, index) => job('media-' + index)))
  state.toggleSelecting(); state.selectPage()
  assert.equal(state.selectedIds.length, 12)
  state.page = 2; await flush(); state.toggleSelection('media-12', true)
  state.removeSelected()
  await fixture.dialogs[0].onPositiveClick(); await flush()
  assert.deepEqual(store.jobs.map(row => row.jobId), ['media-13'])
  assert.equal(removals()[1].args.jobIds.length, 13)
  assert.equal(state.selecting, false)
})

test('loading or a failed journal prevents opening a removal confirmation', async t => {
  const { state, store } = await mount(t)
  store.loading = true; await flush()
  state.handleRemove(state.pageResults)
  state.toggleSelecting()
  assert.equal(state.selecting, false)
  store.loading = false; store.error = 'unreadable journal'; await flush()
  state.handleRemove(state.pageResults)
  assert.equal(fixture.dialogs.length, 0)
  assert.equal(removals().length, 0)
})

test('unmount closes a pending file-failure fallback and leaves its records intact', async t => {
  const { state, store, app } = await mount(t, undefined, args => args.deleteFiles && !args.checkOnly ? fixture.response(args, {
    removedJobIds: [], failed: [{ path: '/out/media.png', error: 'locked' }],
  }) : undefined)
  const dialog = open(state); chooseFiles(dialog, true)
  const pending = dialog.onPositiveClick(); await flush()
  const fallback = fixture.dialogs[1]
  app.unmount(); await pending
  assert.equal(fallback.destroyed, true)
  assert.equal(store.jobs.length, 2)
  assert.equal(removals().length, 2)
  assert.equal(fixture.messages.length, 0)
})

test('an older dialog leaving cannot detach the current removal handle', async t => {
  const { state } = await mount(t)
  const first = open(state)
  first.onNegativeClick()
  const second = open(state)
  first.onAfterLeave()
  const gate = deferred()
  fixture.setHook(async args => { if (args.checkOnly) { await gate.promise } })
  const pending = second.onPositiveClick(); await flush()
  assert.equal(second.loading, true)
  assert.equal(second.negativeButtonProps.disabled, true)
  gate.resolve(); await pending
})

for (const status of ['completed', 'failed', 'cancelled', 'interrupted']) test(`rendered ${status} fileless processing records remain explicit and removable`, async t => {
  const { root, state, store } = await mount(t, [job('empty', { command: 'media_convert', payload: {}, result: null, status })], undefined, true)
  assert.ok(visibleText(root).includes('results.noFiles'))
  assert.ok(visibleText(root).includes('results.journal.status.' + status))
  assert.equal(allNodes(root).some(node => node.tag === 'details'), false)
  assert.equal(allNodes(root).some(node => node.props?.['aria-label']?.startsWith('results.journal.openOutput')), false)
  await open(state, ['empty']).onPositiveClick()
  assert.equal(store.jobs.length, 0)
  assert.deepEqual(removals()[1].args, { jobIds: ['empty'], deleteFiles: false, checkOnly: false })
})

test('rendered editor mix exports expose the canonical file and use the existing ID-only removal flow', async t => {
  const { root, state, store } = await mount(t, [job('editor', { command: 'export_editor_mix', payload: { project: { name: 'Session', assets: [{ path: '/input/voice.wav' }] } }, result: { path: '/exports/Mix.wav' } })], undefined, true)
  assert.ok(visibleText(root).includes('results.journal.editorExport'))
  assert.ok(visibleText(root).includes('Mix.wav'))
  assert.equal(state.pageResults[0].outputs[0].path, '/exports/Mix.wav')
  const dialog = open(state, ['editor']); chooseFiles(dialog, true)
  await dialog.onPositiveClick()
  assert.equal(store.jobs.length, 0)
  assert.deepEqual(removals()[1].args, { jobIds: ['editor'], deleteFiles: true, checkOnly: false })
})
