import assert from 'node:assert/strict'
import test, { after } from 'node:test'
import { fileURLToPath } from 'node:url'
import { createRenderer, h, nextTick } from 'vue'
import { createServer, transformWithEsbuild } from 'vite'
import { parse, compileScript, compileTemplate } from 'vue/compiler-sfc'

// Mount the complete view script; batch expansion uses the production helper.
const id = '\0audio-result-removal-fixture'
const stubs = {
  'vue-i18n': `export const useI18n=()=>({t:(key,values)=>key+(values?JSON.stringify(values):'')})`,
  'naive-ui': `export {useMessage,useDialog,NCheckbox} from '${id}'`,
  '@vicons/ionicons5': `export const CheckmarkOutline={},ChevronDownOutline={},FolderOpenOutline={},FolderOutline={},DocumentTextOutline={},ColorWandOutline={},SearchOutline={},SwapVerticalOutline={},TimeOutline={},TrashOutline={},RefreshOutline={}`,
}
const vite = await createServer({
  configFile: false, appType: 'custom', optimizeDeps: { noDiscovery: true },
  server: { middlewareMode: true, watch: null, hmr: false, ws: false, preTransformRequests: false },
  resolve: { alias: { ...Object.fromEntries(Object.keys(stubs).map(name => [name, '\0audio-result-stub:' + name])), '@': fileURLToPath(new URL('../src', import.meta.url)) } },
  plugins: [{
    name: 'audio-results-native-boundaries', enforce: 'pre',
    resolveId(name) { return name === id || name.startsWith('\0audio-result-stub:') ? name : null },
    load(name) {
      if (name.startsWith('\0audio-result-stub:')) return stubs[name.slice('\0audio-result-stub:'.length)]
      if (name === id) return `
        import {reactive} from 'vue';
        import {getRemovalTasks} from '/src/stores/taskResultCleanup.ts';
        import {buildJobs} from '/src/stores/taskJobs.ts';
        export const calls=[],dialogs=[],messages=[],editorCalls=[],rows=reactive([]);let hook;
        export const NCheckbox={name:'FixtureCheckbox'};
        export function reset(items,nextHook){calls.length=dialogs.length=messages.length=editorCalls.length=0;rows.splice(0,rows.length,...structuredClone(items));hook=nextHook}
        export function response(ids,extra={}){return {schemaVersion:2,checkedJobIds:[...ids],removedJobIds:[...ids],trashed:[],failed:[],retained:[],...extra}}
        function related(ids){return getRemovalTasks(rows,ids,'results.removeRunningBatch')}
        export const task={initialized:true,focusedResultTaskId:null,get tasks(){return rows},get resultTasks(){return rows.filter(row=>['done','failed','cancelled'].includes(row.status))},
          get resultJobs(){return buildJobs(rows).filter(group=>group.tasks.some(row=>['done','failed','cancelled'].includes(row.status)))},
          getRemovalTasks:related,
          async checkRemoval(ids,deleteFiles){const expanded=related(ids).map(item=>item.id);const call={kind:'preflight',ids:[...expanded],deleteFiles};calls.push(call);await hook?.(call);return response(expanded,{removedJobIds:[]})},
          async removeResults(ids,options){const call={kind:'remove',ids:[...ids],...options};calls.push(call);const result=await hook?.(call)??response(ids);const removed=new Set(result.removedJobIds);for(let i=rows.length-1;i>=0;i--)if(removed.has(rows[i].id))rows.splice(i,1);return result},
          revealPath(){},focusResultTask(){},
        };
        export const editor={async listOrphanedProjects(...args){editorCalls.push({kind:'list-orphans',args});return []},async deleteOrphanedProjects(...args){editorCalls.push({kind:'delete-orphans',args});return {deletedProjectIds:[],blockedProjectIds:[],failedProjectIds:[]}},async deleteProjectsForTasks(){calls.push({kind:'editor-delete'});throw Error('Result removal must preserve editor projects')},async listOpenProjectsForTasks(){calls.push({kind:'editor-check'});throw Error('Native reference protection owns this decision')}};
        export const mediaJobs={jobs:[],loading:false,error:null,initialize:async()=>{},refresh:async()=>{}};
        export const useTaskStore=()=>task,useEditorStore=()=>editor,useMediaJobsStore=()=>mediaJobs;
        export const useMessage=()=>Object.fromEntries(['success','warning','error'].map(level=>[level,text=>messages.push({level,text})]));
        export const useDialog=()=>({warning(options){const entry=reactive({...options,destroyed:false,destroy(){entry.destroyed=true;options.onAfterLeave?.()}});dialogs.push(entry);return entry}});
      `
    },
    async transform(code, filename) {
      filename = filename.replaceAll('\\', '/')
      if (filename.endsWith('/src/views/ResultsView.vue')) {
        const { descriptor } = parse(code, { filename })
        const script = compileScript(descriptor, { id: filename })
        const template = compileTemplate({ source: descriptor.template.content, filename, id: filename, compilerOptions: { bindingMetadata: script.bindings } })
        assert.deepEqual(template.errors, [])
        return transformWithEsbuild(script.content + '\n' + template.code, filename + '.ts', { loader: 'ts', tsconfigRaw: {} })
      }
      if (filename.endsWith('/src/views/results/JournalResults.vue')) return 'export default {}'
      if (filename.endsWith('/src/stores/task.ts')) return `export {useTaskStore} from '${id}'`
      if (filename.endsWith('/src/stores/editor.ts')) return `export {useEditorStore} from '${id}'`
      if (filename.endsWith('/src/stores/mediaJobs.ts')) return `export {useMediaJobsStore} from '${id}'`
    },
  }],
})
after(() => vite.close())
globalThis.window = { addEventListener() {}, removeEventListener() {} }
globalThis.document = { documentElement: { classList: { remove() {}, add() {} } } }
const fixture = await vite.ssrLoadModule(id)
const viewModule = await vite.ssrLoadModule('/src/views/ResultsView.vue')
const Component = viewModule.default
Component.render = () => null
const renderer = createRenderer({
  createComment: () => ({ children: [] }), createText: text => ({ text }), createElement: tag => ({ tag, props: {}, children: [] }),
  insert(node, parent, anchor) {
    if (node.parent) node.parent.children = node.parent.children.filter(child => child !== node)
    const index = anchor ? parent.children.indexOf(anchor) : -1
    if (index < 0) parent.children.push(node); else parent.children.splice(index, 0, node)
    node.parent = parent
  },
  remove(node) { if (node.parent) node.parent.children = node.parent.children.filter(child => child !== node); node.parent = null },
  parentNode: node => node?.parent ?? null,
  nextSibling(node) { const children = node.parent?.children ?? []; return children[children.indexOf(node) + 1] ?? null },
  setText(node, text) { node.text = text }, setElementText(node, text) { node.text = text; node.children = [] }, patchProp(node, key, _prev, value) { node.props[key] = value },
})
const flush = async () => { await nextTick(); await new Promise(resolve => setImmediate(resolve)); await nextTick() }
const item = (id, jobId = id, status = 'done', outputs = true) => ({ id, jobId, status, model: 'Fixture', input: `/input/${id}.wav`, output: `/output/${id}`, createdAt: 1700000000000, updatedAt: 1700000000000,
  outputs: outputs ? [{ stem: 'Vocals', path: `/output/${id}/vocals.wav` }] : [], runConfig: {} })
const defaults = () => [item('success', 'batch'), item('failed-sibling', 'batch', 'failed', false), item('other')]
async function mount(t, options = {}) {
  fixture.reset(options.items ?? defaults(), options.hook)
  fixture.mediaJobs.jobs = options.jobs ?? []
  const app = renderer.createApp(options.template ? { ...Component, render: viewModule.render } : Component)
  app.config.warnHandler = () => {}
  for (const name of ['NButton', 'NTag', 'NIcon', 'NCheckbox', 'NInput', 'NSelect', 'NPagination', 'NCollapseTransition', 'MediaPageHeader']) {
    app.component(name, { inheritAttrs: false, setup(_props, { attrs, slots }) { return () => h('fixture-' + name.toLowerCase(), attrs, slots.default?.()) } })
  }
  const root = { children: [] }; app.mount(root)
  t.after(() => { if (app._instance) app.unmount() })
  await flush()
  return { app, root, state: app._instance.setupState }
}
const allNodes = node => [node, ...(node.children ?? []).flatMap(allNodes)]
const visibleText = root => allNodes(root).map(node => node.text ?? '').join(' ')
function open(state, action) {
  if (action === 'single') state.handleRemoveResult(state.resultGroups.find(group => group.id === 'batch'))
  else if (action === 'selected') { state.selecting = true; state.selectedResultIds = ['batch']; state.handleRemoveSelected() }
  else state.handleClearResults()
  return fixture.dialogs.at(-1)
}
function chooseFiles(dialog, checked) {
  const checkbox = dialog.content().children.find(node => node.type?.name === 'FixtureCheckbox')
  assert.equal(checkbox.props.checked, false)
  checkbox.props['onUpdate:checked'](checked)
}
const requested = action => action === 'all' ? ['success', 'failed-sibling', 'other'] : ['success', 'failed-sibling']
for (const action of ['single', 'selected', 'all']) {
  test(`${action}: record-only default removes the full terminal batch and preserves editor projects`, async t => {
    const { state } = await mount(t)
    const dialog = open(state, action); chooseFiles(dialog, false)
    assert.ok(dialog.content().children.some(node => node.children === 'results.removeEditorProjectHint'))
    await dialog.onPositiveClick()
    assert.deepEqual(fixture.calls, [
      { kind: 'preflight', ids: requested(action), deleteFiles: false }, { kind: 'remove', ids: requested(action), deleteFiles: false },
    ])
    assert.ok(fixture.messages.some(message => message.level === 'success'))
    assert.deepEqual(fixture.rows.map(row => row.id), action === 'all' ? [] : ['other'])
    if (action !== 'single') { assert.deepEqual(state.selectedResultIds, []); assert.equal(state.selecting, false) }
  })
  test(`${action}: explicit recycling sends IDs and flags without frontend file or directory planning`, async t => {
    const { state } = await mount(t)
    const dialog = open(state, action); chooseFiles(dialog, true)
    await dialog.onPositiveClick()
    assert.deepEqual(fixture.calls, [
      { kind: 'preflight', ids: requested(action), deleteFiles: true }, { kind: 'remove', ids: requested(action), deleteFiles: true },
    ])
  })
  test(`${action}: a busy native lease prevents every destructive action`, async t => {
    for (const files of [false, true]) {
      const { state } = await mount(t, { hook() { throw new Error('lease busy') } })
      const dialog = open(state, action); chooseFiles(dialog, files)
      assert.equal(await dialog.onPositiveClick(), false)
      assert.deepEqual(fixture.calls, [{ kind: 'preflight', ids: requested(action), deleteFiles: files }])
      assert.equal(fixture.rows.length, 3)
      assert.deepEqual(fixture.messages, [{ level: 'warning', text: 'results.removeNotReady' }])
    }
  })
  test(`${action}: file failures keep all records until an independent record-only confirmation`, async t => {
    const { state } = await mount(t, { hook(call) {
      if (call.kind === 'remove' && call.deleteFiles) return fixture.response(call.ids, { removedJobIds: [], trashed: ['/output/earlier.wav'], failed: [{ path: '/output/locked.wav', error: 'locked' }] })
    } })
    const dialog = open(state, action); chooseFiles(dialog, true)
    const pending = dialog.onPositiveClick(); await flush()
    assert.equal(fixture.rows.length, 3)
    assert.equal(fixture.dialogs.length, 2)
    assert.equal(fixture.calls.length, 2)
    assert.ok(fixture.dialogs[1].content.includes('ConfirmContent'))
    fixture.dialogs[1].onPositiveClick(); await pending
    assert.deepEqual(fixture.calls.map(call => [call.kind, call.deleteFiles]), [['preflight', true], ['remove', true], ['preflight', false], ['remove', false]])
    assert.deepEqual(fixture.rows.map(row => row.id), action === 'all' ? [] : ['other'])
  })
  test(`${action}: declining fallback preserves all records and the current selection`, async t => {
    const { state } = await mount(t, { hook(call) {
      if (call.kind === 'remove') return fixture.response(call.ids, { removedJobIds: [], failed: [{ path: '/output/locked.wav', error: 'locked' }] })
    } })
    state.selecting = true; state.selectedResultIds = ['batch']
    const dialog = open(state, action); chooseFiles(dialog, true)
    const pending = dialog.onPositiveClick(); await flush()
    fixture.dialogs[1].onNegativeClick()
    assert.equal(await pending, false)
    assert.equal(fixture.rows.length, 3)
    assert.deepEqual(state.selectedResultIds, ['batch'])
    assert.equal(fixture.calls.length, 2)
    assert.ok(!fixture.messages.some(message => message.level === 'success'))
  })
  test(`${action}: native IPC failure never offers an automatic record-only fallback`, async t => {
    const { state } = await mount(t, { hook(call) { if (call.kind === 'remove') throw new Error('write transaction failed') } })
    const dialog = open(state, action); chooseFiles(dialog, true)
    assert.equal(await dialog.onPositiveClick(), false)
    assert.equal(fixture.rows.length, 3)
    assert.equal(fixture.dialogs.length, 1)
    assert.deepEqual(fixture.messages, [{ level: 'error', text: 'write transaction failed' }])
  })
}

test('retained native references report a count without raw reasons or claiming every file was recycled', async t => {
  const { state } = await mount(t, { hook(call) { if (call.kind === 'remove') return fixture.response(call.ids, { retained: [{ path: '/output/shared.wav', reason: 'library_index_unavailable' }] }) } })
  const dialog = open(state, 'single'); chooseFiles(dialog, true); await dialog.onPositiveClick()
  assert.ok(fixture.messages.some(message => message.level === 'warning' && message.text === 'results.removeFilesRetained{"count":1}'))
  assert.ok(!fixture.messages.some(message => /library_index_unavailable|removeFilesSuccess/.test(message.text)))
  assert.equal(fixture.dialogs.length, 1)
})
test('retained references survive partial recycling followed by record-only removal', async t => {
  const { state } = await mount(t, { hook(call) { if (call.kind === 'remove' && call.deleteFiles) return fixture.response(call.ids, { removedJobIds: [], failed: [{ path: '/output/locked.wav', error: 'locked' }], retained: [{ path: '/output/shared.wav', reason: 'editor_project' }] }) } })
  const dialog = open(state, 'single'); chooseFiles(dialog, true)
  const pending = dialog.onPositiveClick(); await flush(); fixture.dialogs[1].onPositiveClick(); await pending
  assert.ok(fixture.messages.some(message => message.text === 'results.removeFilesRetained{"count":1}'))
})
test('clear uses the entire audio snapshot even when the search matches no results', async t => {
  const { state } = await mount(t)
  state.search = 'no matching file'; await flush()
  assert.equal(state.filteredResults.length, 0)
  await open(state, 'all').onPositiveClick()
  assert.deepEqual(fixture.calls[1].ids, ['success', 'failed-sibling', 'other'])
})
test('an active batch is rejected before opening a dialog', async t => {
  const { state } = await mount(t, { items: [item('success', 'batch'), item('active', 'batch', 'separating', false)] })
  open(state, 'single')
  assert.equal(fixture.dialogs.length, 0)
  assert.equal(fixture.calls.length, 0)
})
test('duplicate confirmation cannot duplicate the native request', async t => {
  let release; const gate = new Promise(resolve => { release = resolve })
  const { state } = await mount(t, { hook: call => call.kind === 'remove' ? gate : undefined })
  const dialog = open(state, 'single')
  const pending = dialog.onPositiveClick(); await flush()
  assert.equal(await dialog.onPositiveClick(), false)
  state.handleClearResults(); assert.equal(fixture.dialogs.length, 1)
  assert.equal(dialog.closable, false)
  assert.equal(fixture.calls.length, 2)
  release(); await pending
})
test('unmount during fallback closes both dialogs and keeps current records', async t => {
  const { app, state } = await mount(t, { hook: call => call.kind === 'remove' ? fixture.response(call.ids, { removedJobIds: [], failed: [{ path: '/output/locked.wav', error: 'locked' }] }) : undefined })
  const dialog = open(state, 'single'); chooseFiles(dialog, true)
  const pending = dialog.onPositiveClick(); await flush()
  app.unmount(); assert.equal(await pending, false)
  assert.ok(fixture.dialogs.every(entry => entry.destroyed))
  assert.equal(fixture.rows.length, 3)
})

test('the rendered audio page preserves a mixed group and each canonical terminal outcome', async t => {
  const { root, state } = await mount(t, { template: true, items: [item('success', 'batch'), item('failed', 'batch', 'failed', false), item('cancelled', 'batch', 'cancelled', false), item('interrupted', 'batch', 'failed', false)],
    jobs: [{ jobId: 'interrupted', status: 'interrupted' }] })
  assert.equal(state.resultGroups[0].items.length, 4)
  assert.equal(state.resultGroups[0].inputCount, 4)
  assert.equal(state.resultGroups[0].outcome, 'mixed')
  const text = visibleText(root)
  for (const key of ['results.mixed', 'results.journal.status.completed', 'results.journal.status.failed', 'results.journal.status.cancelled', 'results.journal.status.interrupted', 'results.noFiles']) assert.ok(text.includes(key), key)
  assert.equal(allNodes(root).filter(node => node.tag === 'fixture-nbutton' && visibleText(node).includes('results.openInEditor')).length, 1)
})

test('rendered fileless terminal records hide output actions and remain deletable by ID', async t => {
  const { state, root } = await mount(t, { template: true, items: [item('failed', 'batch', 'failed', false)] })
  assert.ok(visibleText(root).includes('results.noFiles'))
  assert.equal(allNodes(root).filter(node => node.tag === 'fixture-nbutton' && /results.openInEditor|results.openDirectory/.test(visibleText(node))).length, 0)
  await open(state, 'single').onPositiveClick()
  assert.equal(fixture.rows.length, 0)
  assert.deepEqual(fixture.calls[1], { kind: 'remove', ids: ['failed'], deleteFiles: false })
})

test('active mixed groups preserve produced files but cannot enter selection or a clear transaction', async t => {
  const { state, root } = await mount(t, { template: true, items: [item('success', 'batch'), item('active', 'batch', 'separating', false), item('finished')] })
  assert.equal(state.resultGroups.find(group => group.id === 'batch').outcome, 'running')
  assert.ok(visibleText(root).includes('results.running'))
  state.selecting = true; state.toggleSelectAllResults(true)
  assert.deepEqual(state.selectedResultIds, ['finished'])
  state.toggleResultSelection('batch')
  assert.deepEqual(state.selectedResultIds, ['finished'])
  await open(state, 'all').onPositiveClick()
  await flush()
  assert.deepEqual(fixture.calls[1].ids, ['finished'])
  assert.deepEqual(fixture.rows.map(row => row.id), ['success', 'active'])
})

test('orphan listing and explicit cleanup delegate existence checks without a frontend visible-ID allowlist', async t => {
  const { state } = await mount(t, { items: [item('active', 'batch', 'separating', false), item('empty', 'other', 'failed', false)] })
  assert.deepEqual(fixture.editorCalls, [{ kind: 'list-orphans', args: [] }])
  state.orphanedEditorProjects = [{ projectId: 'orphan', name: 'Orphan' }]
  state.handleCleanupOrphanedEditorProjects()
  await fixture.dialogs.at(-1).onPositiveClick()
  assert.ok(fixture.editorCalls.some(call => call.kind === 'delete-orphans' && call.args.length === 0))
})
