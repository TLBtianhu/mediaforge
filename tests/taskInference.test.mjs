/**
 * 推理参数覆盖与默认值契约。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import {
  normalizeEditableNumberValue, resolveNumberOverride, resolveBooleanOverride,
  preferCurrentOverride, applyModelDefaultsToUi, isVrModelType, isApolloModelType,
} from '../src/stores/taskInference.ts'

test('normalizeEditableNumberValue rejects non-numbers and unset zeros', () => {
  assert.equal(normalizeEditableNumberValue('x', { zeroMeansUnset: false }), null)
  assert.equal(normalizeEditableNumberValue(0, { zeroMeansUnset: true }), null)
  assert.equal(normalizeEditableNumberValue(0, { zeroMeansUnset: false }), 0)
  assert.equal(normalizeEditableNumberValue(512, { zeroMeansUnset: true }), 512)
})

test('resolveNumberOverride returns null when current matches the default', () => {
  assert.equal(resolveNumberOverride({ chunk_size: 512 }, 'chunk_size', 512), null)
  assert.equal(resolveNumberOverride({ chunk_size: 512 }, 'chunk_size', 1024), 1024)
  assert.equal(resolveNumberOverride(undefined, 'chunk_size', 1024), 1024)
  assert.equal(resolveNumberOverride({ chunk_size: 512 }, 'chunk_size', 0, { zeroMeansUnset: true }), null)
})

test('resolveBooleanOverride distinguishes same, different and missing defaults', () => {
  assert.equal(resolveBooleanOverride({ standardize: true }, 'standardize', true), null)
  assert.equal(resolveBooleanOverride({ standardize: true }, 'standardize', false), false)
  assert.equal(resolveBooleanOverride(undefined, 'standardize', true), true)
  assert.equal(resolveBooleanOverride(undefined, 'standardize', false), null)
})

test('preferCurrentOverride keeps the current override, else falls back to the model', () => {
  const overrides = { chunk_size: 1024 }
  assert.equal(preferCurrentOverride(2048, overrides, 'chunk_size', false), 2048)
  assert.equal(preferCurrentOverride(null, overrides, 'chunk_size', false), null)
  assert.equal(preferCurrentOverride(null, overrides, 'chunk_size', true), 1024)
})

test('applyModelDefaultsToUi applies vr/apollo specific defaults', () => {
  const base = applyModelDefaultsToUi(undefined)
  assert.equal(base.batch_size, 1)
  const vr = applyModelDefaultsToUi(undefined, 'vr')
  assert.equal(vr.batch_size, 2)
  assert.equal(vr.window_size, 512)
  assert.equal(vr.aggression, 5)
  assert.equal(vr.num_overlap, 0)
  const apollo = applyModelDefaultsToUi(undefined, 'apollo')
  assert.equal(apollo.num_overlap, 0)
  assert.equal(isVrModelType('vr'), true)
  assert.equal(isApolloModelType('apollo'), true)
  assert.equal(isVrModelType('demucs'), false)
})
