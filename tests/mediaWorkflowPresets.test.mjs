import assert from 'node:assert/strict'
import test from 'node:test'
import { normalizeMediaWorkflowDefinition, normalizeMediaWorkflowState, applyMediaWorkflowMutation } from '../src/features/media/workflowPresets.ts'

const recipe = () => ({ version: 1, modality: 'image', steps: [{ capability: 'upscale', bindings: { upscaler: 'media:real-esrgan-x2' }, params: { scale: 2, tile: 0 } }] })
const create = id => ({ action: 'upsert', id, name: '  海报  ', definition: recipe() })

test('a saved recipe excludes task-local paths and keeps zero-valued parameters in an independent snapshot', () => {
  const input = { ...recipe(), inputs: ['private.png'], outputDir: 'private-out', modelDir: 'private-models', maskPath: 'private-mask', requestId: 'old-job' }
  input.steps[0].outputDir = 'step-output'
  const definition = normalizeMediaWorkflowDefinition(input)
  assert.deepEqual(Object.keys(definition), ['version', 'modality', 'steps'])
  assert.deepEqual(Object.keys(definition.steps[0]), ['capability', 'bindings', 'params'])
  input.steps[0].params.tile = 128
  assert.equal(definition.steps[0].params.tile, 0)
})

test('audio definitions, empty steps, broken bindings and newer schemas are rejected', () => {
  for (const input of [{ nodes: [], links: [] }, { ...recipe(), version: 2 }, { ...recipe(), modality: 'audio' }, { ...recipe(), steps: [] },
    { ...recipe(), steps: [{ capability: 'upscale', bindings: {}, params: {} }] }]) assert.throws(() => normalizeMediaWorkflowDefinition(input))
})

test('modality must be an exact string and cannot be accepted through coercion', () => {
  for (const modality of [['image'], ['video'], { toString: () => 'image' }]) {
    assert.throws(() => normalizeMediaWorkflowDefinition({ ...recipe(), modality }))
  }
})

test('independent recipes with the same name do not overwrite each other', () => {
  const first = applyMediaWorkflowMutation(null, create('one'), 100)
  const second = applyMediaWorkflowMutation(first.state, create('two'), 101)
  assert.equal(second.state.presets.length, 2)
  assert.equal(second.state.presets[0].name, '海报')
  assert.equal(first.state.presets.length, 1)
})

test('stale updates and deletes preserve the latest state; a missing recipe cannot be recreated by an update', () => {
  const saved = applyMediaWorkflowMutation(null, create('one'), 100).state
  const updated = applyMediaWorkflowMutation(saved, { ...create('one'), name: '最新', expectedUpdatedAt: 100 }, 99).state
  assert.equal(updated.presets[0].updatedAt, 101)
  assert.equal(updated.presets[0].createdAt, 100)
  for (const mutation of [{ ...create('one'), expectedUpdatedAt: 100 }, { action: 'delete', id: 'one', expectedUpdatedAt: 100 }, create('one')]) {
    const response = applyMediaWorkflowMutation(updated, mutation, 102)
    assert.ok(response.conflict)
    assert.deepEqual(response.state, updated)
  }
  const removed = applyMediaWorkflowMutation(updated, { action: 'delete', id: 'one', expectedUpdatedAt: 101 }, 103).state
  assert.equal(removed.presets.length, 0)
  assert.ok(applyMediaWorkflowMutation(removed, { ...create('one'), expectedUpdatedAt: 101 }, 104).conflict)
})

test('corrupt, duplicate or future state is never silently replaced with an empty library', () => {
  const saved = applyMediaWorkflowMutation(null, create('one'), 100).state
  for (const state of [{ version: 2, presets: [] }, { version: 1, presets: 'broken' }, { version: 1, presets: [...saved.presets, ...saved.presets] }]) {
    assert.throws(() => normalizeMediaWorkflowState(state))
    assert.throws(() => applyMediaWorkflowMutation(state, create('two'), 101))
  }
  for (const id of ['', '../escape', 'a/b']) assert.throws(() => applyMediaWorkflowMutation(null, create(id), 100))
})

test('oversized recipes and libraries are rejected instead of growing settings without a bound', () => {
  assert.throws(() => normalizeMediaWorkflowDefinition({ ...recipe(), steps: Array.from({ length: 65 }, () => recipe().steps[0]) }))
  const definition = recipe(); definition.steps[0].params.text = 'x'.repeat(256 * 1024)
  assert.throws(() => normalizeMediaWorkflowDefinition(definition))
  assert.throws(() => normalizeMediaWorkflowState({ version: 1, presets: Array(1001).fill({}) }))
})
