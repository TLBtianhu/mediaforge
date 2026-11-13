import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test, { after } from 'node:test'
import { fileURLToPath } from 'node:url'
import vm from 'node:vm'
import ts from 'typescript'
import { parse } from 'vue/compiler-sfc'
import { createServer } from 'vite'

const vite = await createServer({ configFile: false, server: { middlewareMode: true, watch: null, hmr: false }, optimizeDeps: { noDiscovery: true }, resolve: { alias: { '@': fileURLToPath(new URL('../src', import.meta.url)) } } })
after(() => vite.close())
const { storeGraphDefaults } = await vite.ssrLoadModule('/src/workflows/graphDefaults.ts')
const { COMFY_WORKFLOW_VERSION } = await vite.ssrLoadModule('/src/workflows/formats.ts')

const descriptor = parse(readFileSync(new URL('../src/views/WorkflowNodeEditorView.vue', import.meta.url), 'utf8')).descriptor
const source = ts.createSourceFile('view.ts', descriptor.scriptSetup.content, ts.ScriptTarget.Latest, true)
const names = new Set(['snapshot', 'captureInitialSnapshot', 'syncCurrentDefinition', 'createFreshDefinition', 'loadWorkflow', 'persistDefinition', 'save', 'saveRevisionConflictCopy', 'overwriteRevisionConflict', 'canSave',
  'cancelAutosave', 'scheduleAutosave', 'autosaveTimer', 'saveQueue', 'pendingSaves', 'saveGeneration', 'disposed'])
const statements = source.statements.filter(statement => ts.isFunctionDeclaration(statement)
  ? names.has(statement.name?.text)
  : ts.isVariableStatement(statement) && statement.declarationList.declarations.some(item => names.has(item.name.getText(source))))
assert.equal(statements.length, names.size)
const mounted = source.statements.find(statement => ts.isExpressionStatement(statement) && ts.isCallExpression(statement.expression) && statement.expression.expression.getText(source) === 'onMounted')
const code = ts.transpileModule([...statements, mounted].map(item => item.getText(source)).join('\n'), { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText
const ref = value => ({ value })
const plain = value => JSON.parse(JSON.stringify(value))
const graph = model => ({ version: COMFY_WORKFLOW_VERSION, nodes: [{ id: 1, type: 'mss_separate', widgets_values: [model] }], links: [] })
class ConflictError extends Error {}

function environment(query = {}) {
  const entry = { id: 'existing', name: 'Existing', description: '', updatedAt: 1, definition: graph('Original') }
  const calls = []
  let mounted, canvas = graph('Original')
  const state = {
    computed: read => ({ get value() { return read() } }),
    t: (key, data) => data?.name ? data.name + ' copy' : key,
    route: { query }, workflows: ref([entry]),
    name: ref('Current'), description: ref(''), definition: ref(graph('Original')),
    defaultDevice: ref('auto'), defaultFormat: ref('wav'),
    editingId: ref('existing'), loadedUpdatedAt: ref(1), loaded: ref(true), canvasInitialized: ref(true),
    saving: ref(false), formError: ref(''), formatError: ref(''), canvasError: ref(''), initialSnapshot: ref(''),
    saveError: ref(''), closing: ref(false), submitting: ref(false), editingName: ref(false), showClosePrompt: ref(false), dirty: ref(true),
    pendingDefinition: ref(null), showRevisionConflict: ref(false), editorKey: ref(0),
    editorRef: ref({ snapshotDefinition: () => canvas }),
    cloneWorkflow: plain, storeGraphDefaults, COMFY_WORKFLOW_VERSION,
    isGraphWorkflowDefinition: value => Array.isArray(value.nodes),
    WorkflowRevisionConflictError: ConflictError, WorkflowEditorInUseError: class extends Error {},
    model: { initialize: async () => {} },
    workflow: { selectedWorkflowId: 'existing', initialize: async () => {}, selectWorkflow() {}, async saveWorkflow(input) {
      calls.push(plain(input))
      return { ...plain(input), id: input.id || 'copy', updatedAt: 2 }
    } },
    workbench: { adoptWorkflowKey() {} }, message: { success() {}, error() {} },
    refreshMaximized: async () => {}, currentWindow: null,
    setTimeout: () => 0, clearTimeout() {},
    onMounted: callback => { mounted = callback },
  }
  const result = vm.runInNewContext(`${code}\n({ save, saveRevisionConflictCopy, overwriteRevisionConflict, persistDefinition })`, state)
  return { state, result, calls, mount: () => mounted(), setCanvas: value => { canvas = value } }
}

test('an unavailable advanced workflow ID cannot fall back to a different stored workflow', async () => {
  const env = environment({ workflowId: 'deleted' })
  await env.mount()
  assert.equal(env.state.editingId.value, 'deleted')
  assert.equal(env.state.formatError.value, 'workflows.workflowNotFound')
  assert.equal(env.state.definition.value.nodes.length, 0)
  env.state.formError.value = env.state.formatError.value
  assert.equal(await env.result.save(), null)
  assert.deepEqual(env.calls, [])
})

test('a valid advanced workflow ID opens that workflow and explicit new starts empty', async () => {
  const existing = environment({ workflowId: 'existing' })
  await existing.mount()
  assert.equal(existing.state.editingId.value, 'existing')
  assert.equal(existing.state.name.value, 'Existing')
  const fresh = environment({ new: '1', workflowId: 'existing' })
  await fresh.mount()
  assert.equal(fresh.state.editingId.value, '')
  assert.equal(fresh.state.definition.value.nodes.length, 0)
})

for (const action of ['saveRevisionConflictCopy', 'overwriteRevisionConflict']) {
  test(`${action} saves the live graph after edits made while the conflict dialog is open`, async () => {
    const env = environment()
    env.state.pendingDefinition.value = graph('Stale')
    env.state.showRevisionConflict.value = true
    env.state.name.value = 'Latest name'
    env.state.definition.value = graph('Latest')
    env.setCanvas(graph('Latest'))
    env.result[action]()
    await new Promise(resolve => setImmediate(resolve))
    assert.equal(env.calls.length, 1)
    assert.equal(env.calls[0].definition.nodes[0].widgets_values[0], 'Latest')
    assert.equal(env.calls[0].name, action === 'saveRevisionConflictCopy' ? 'Latest name copy' : 'Latest name')
    assert.equal(env.calls[0].force, action === 'overwriteRevisionConflict' ? true : undefined)
    assert.equal(env.state.pendingDefinition.value, null)
  })
}
