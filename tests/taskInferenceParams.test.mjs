import assert from 'node:assert/strict'
import test from 'node:test'
import { buildInferenceParams, modelInferenceParamsFromState } from '../src/stores/taskInferenceParams.ts'

const values = {
  batch_size: 2,
  overlap_size: 2,
  num_overlap: 3,
  chunk_size: 4,
  window_size: 5,
  aggression: 6,
  enable_post_process: true,
  post_process_threshold: 0.2,
  high_end_process: true,
  standardize: true,
  normalize: true,
}

test('buildInferenceParams uses explicit UI overrides without dropping model defaults', () => {
  const result = buildInferenceParams({ defaults: { batch_size: 4 }, modelOverrides: {}, dirty: true, values })
  assert.equal(result.batch_size, 2)
  assert.equal(result.overlap_size, 2)
  assert.equal(result.normalize, true)
})

test('modelInferenceParamsFromState merges saved and model overrides', () => {
  const result = modelInferenceParamsFromState({ defaults: { batch_size: 4 }, overrides: { batch_size: 2 }, saved: { overlap_size: 3 } })
  assert.deepEqual(result, { batch_size: 2, overlap_size: 3 })
})

test('VR parameters use their own field set', () => {
  const result = buildInferenceParams({ modelType: 'vr', defaults: {}, modelOverrides: {}, dirty: true, values })
  assert.equal(result.window_size, 5)
  assert.equal(result.aggression, 6)
  assert.equal(result.overlap_size, undefined)
})
