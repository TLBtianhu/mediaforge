/**
 * 工作流列表派生与筛选契约。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { makeWorkflowListing } from '../src/views/workflows/listing.ts'

const computed = source => ({ get value() { return source() } })
const t = (key, params) => (params ? `${key}:${JSON.stringify(params)}` : key)

const simpleDef = { steps: [{ id: 's1' }] }
const graphDef = { nodes: [{ id: 'n1' }], links: [] }

function harness(workflows, overrides = {}) {
  const deps = {
    computed, t,
    workflows: { value: workflows },
    query: { value: '' },
    typeFilter: { value: 'all' },
    workflow: { editorFor: () => false, hasAnyEditorOpen: false },
    selectedWorkflowId: { value: '' },
    // 这些 utils 的实现模块在 Node 下不可解析（内部用 @/ 别名），测试注入等价桩。
    isSimpleWorkflowDefinition: def => Array.isArray(def?.steps),
    isGraphWorkflowDefinition: def => Array.isArray(def?.nodes) && (def?.links == null || Array.isArray(def.links)) && !Array.isArray(def?.steps),
    analyzeComfyOverview: () => ({ nodeCount: 1, outputCount: 1 }),
    hydrateSimpleWorkflow: () => ({ steps: [{ id: 's1' }], ensembles: [] }),
    countWorkflowSaveOutputs: () => 1,
    getWorkflowDefinitionIssue: def => (def && Object.keys(def).length ? undefined : 'invalid-format'),
    ...overrides,
  }
  return { deps, listing: makeWorkflowListing(deps) }
}

test('normalizeMode maps null/true/false', () => {
  const { listing } = harness([])
  assert.equal(listing.normalizeMode(null), 'global')
  assert.equal(listing.normalizeMode(true), 'enabled')
  assert.equal(listing.normalizeMode(false), 'disabled')
})

test('workflowKind and type counts classify by definition shape', () => {
  const { listing } = harness([
    { id: 'a', name: 'A', definition: simpleDef },
    { id: 'b', name: 'B', definition: graphDef },
    { id: 'c', name: 'C', definition: {} },
  ])
  assert.equal(listing.workflowKind({ definition: simpleDef }), 'simple')
  assert.equal(listing.workflowKind({ definition: graphDef }), 'advanced')
  assert.equal(listing.workflowKind({ definition: {} }), 'invalid')
  assert.deepEqual(listing.workflowTypeCounts.value, { all: 3, simple: 1, advanced: 1 })
})

test('filteredWorkflows applies query and type filter', () => {
  const workflows = [
    { id: 'a', name: 'Vocals', description: 'vocal split', definition: simpleDef },
    { id: 'b', name: 'Master', description: 'mastering', definition: graphDef },
  ]
  const { deps, listing } = harness(workflows)
  deps.query.value = 'voc'
  assert.deepEqual(listing.filteredWorkflows.value.map(i => i.id), ['a'])
  deps.query.value = ''
  deps.typeFilter.value = 'advanced'
  assert.deepEqual(listing.filteredWorkflows.value.map(i => i.id), ['b'])
})

test('workflowGroups drops empty groups', () => {
  const { listing } = harness([{ id: 'a', name: 'A', definition: simpleDef }])
  assert.deepEqual(listing.workflowGroups.value.map(g => g.kind), ['simple'])
})

test('workflowDefinitionError flags invalid definitions', () => {
  const { listing } = harness([])
  assert.equal(listing.workflowDefinitionError({}), 'workflows.workflowFormatInvalid')
  assert.equal(listing.workflowRunBlocked({}), true)
})

test('editor-open helpers reflect the store', () => {
  const { listing } = harness([], {
    workflow: { editorFor: () => true, hasAnyEditorOpen: true },
  })
  assert.equal(listing.isNodeEditorOpen.value, true)
  assert.equal(listing.isAnyEditorOpen.value, true)
})
