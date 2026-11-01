/**
 * 推理参数编辑器的草稿/开关/保存/重置契约。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { makeInferenceEditor } from '../src/views/models/inferenceEditor.ts'

const computed = source => ({ get value() { return source() } })

function harness(overrides = {}) {
  const calls = []
  const model = {
    name: 'a.ckpt',
    defaultInferenceParams: { batch_size: 2, overlap_size: 4, chunk_size: 512 },
    slots: null,
  }
  const deps = {
    computed,
    watch: () => {},
    modelStore: {
      models: [model],
      selectedInfo: null,
      getModelInferenceOverrides: name => calls.overrides?.[name],
      setModelInferenceOverrides: async (name, draft) => { calls.push(['set', name, draft]) },
      resetModelInferenceOverrides: async name => { calls.push(['reset', name]) },
      getModelBaseInferenceDefaults: () => null,
    },
    taskStore: {
      applySelectedModelDefaults: (...args) => calls.push(['apply', ...args]),
      getSavedModelState: () => null,
    },
    selectedModel: { value: '' },
    message: {
      warning: v => calls.push(['warning', v]),
      success: v => calls.push(['success', v]),
      error: v => calls.push(['error', v]),
    },
    t: (key, params) => (params ? `${key}:${JSON.stringify(params)}` : key),
    studioLog: { error: () => {} },
    inferenceDraft: { value: { batch_size: 1, overlap_size: 0, chunk_size: 0 } },
    showInferenceEditor: { value: false },
    inferenceEditorModel: { value: null },
    inferenceEditorParams: { value: {} },
    ...overrides,
  }
  return { calls, deps, model, editor: makeInferenceEditor(deps) }
}

test('resolveInferenceDraft reads saved params with fallbacks', () => {
  const { editor, model } = harness()
  assert.deepEqual(editor.resolveInferenceDraft(model), { batch_size: 2, overlap_size: 4, chunk_size: 512 })
  assert.deepEqual(editor.resolveInferenceDraft(null), { batch_size: 1, overlap_size: 0, chunk_size: 0 })
})

test('opening the editor syncs the draft and shows the panel', () => {
  const { deps, editor, model } = harness()
  editor.openInferenceEditor(model)
  assert.deepEqual(deps.inferenceDraft.value, { batch_size: 2, overlap_size: 4, chunk_size: 512 })
  assert.equal(deps.showInferenceEditor.value, true)
  assert.equal(deps.inferenceEditorModel.value.name, 'a.ckpt')
})

test('hasInferenceDraftChanges compares against the model defaults', () => {
  const { deps, editor, model } = harness()
  editor.openInferenceEditor(model)
  assert.equal(editor.hasInferenceDraftChanges(model), false)
  deps.inferenceDraft.value.chunk_size = 1024
  assert.equal(editor.hasInferenceDraftChanges(model), true)
  assert.equal(editor.canResetInferenceDefaults(model), true)
})

test('closing clears the editing model', () => {
  const { deps, editor, model } = harness()
  editor.openInferenceEditor(model)
  editor.closeInferenceEditor()
  assert.equal(deps.showInferenceEditor.value, false)
  assert.equal(deps.inferenceEditorModel.value, null)
})

test('a model without slots has no slot interface', () => {
  const { editor, model } = harness()
  editor.openInferenceEditor(model)
  assert.equal(editor.inferenceEditorInterface.value, null)
})

test('a chunk-size issue blocks saving with a warning', async () => {
  const { calls, deps, editor, model } = harness()
  model.inferenceParamMeta = { recommendedSampleStep: 1, chunkSizeConstraint: { step: 64, offset: 0, min: 64 } }
  editor.openInferenceEditor(model)
  deps.inferenceDraft.value.chunk_size = 100 // 100 不是 64 的步进 → 触发 invalidChunkSize
  await editor.saveInferenceDefaults()
  assert.equal(calls.filter(c => c[0] === 'set').length, 0)
  assert.ok(calls.some(c => c[0] === 'warning'))
})

test('a valid save writes overrides, closes and confirms', async () => {
  const { calls, deps, editor, model } = harness()
  editor.openInferenceEditor(model)
  // 元数据为空 → 无 chunk 约束，保存应通过。
  await editor.saveInferenceDefaults()
  const set = calls.find(c => c[0] === 'set')
  assert.ok(set, '应调用 setModelInferenceOverrides')
  assert.equal(set[1], 'a.ckpt')
  assert.deepEqual(set[2], { batch_size: 2, overlap_size: 4, chunk_size: 512 })
  assert.ok(calls.some(c => c[0] === 'success'))
  assert.equal(deps.inferenceEditorModel.value, null)
})

test('reset clears overrides and resyncs the draft', async () => {
  const { calls, deps, editor, model } = harness({
    modelStore: {
      models: [],
      selectedInfo: null,
      getModelInferenceOverrides: () => ({ chunk_size: 9999 }),
      setModelInferenceOverrides: async () => {},
      resetModelInferenceOverrides: async name => { calls.push(['reset', name]) },
      getModelBaseInferenceDefaults: () => null,
    },
  })
  editor.openInferenceEditor(model)
  deps.inferenceDraft.value.chunk_size = 9999
  await editor.resetInferenceDefaults()
  assert.ok(calls.some(c => c[0] === 'reset' && c[1] === 'a.ckpt'))
  assert.deepEqual(deps.inferenceDraft.value, { batch_size: 2, overlap_size: 4, chunk_size: 512 })
})
