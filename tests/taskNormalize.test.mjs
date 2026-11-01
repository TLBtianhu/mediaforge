/**
 * 运行配置规范化契约：默认值、字段拷贝、旧 inferenceParams 版本迁移。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { normalizeOutputLayout, normalizeOutputNaming, normalizeRunConfig, CURRENT_INFERENCE_PARAMS_VERSION } from '../src/stores/taskNormalize.ts'

test('output layout and naming normalize their shapes', () => {
  assert.equal(normalizeOutputLayout('flat'), 'flat')
  assert.equal(normalizeOutputLayout('folders'), 'folders')
  assert.equal(normalizeOutputLayout('weird'), 'folders')
  assert.equal(normalizeOutputNaming(undefined), undefined)
  assert.deepEqual(normalizeOutputNaming({ enabled: true, template: ' {stem} ', stemOrder: [' vocals ', '', 'drums'] }), {
    enabled: true, template: '{stem}', stemOrder: ['vocals', 'drums'],
  })
})

test('runConfig fills defaults and clones nested params', () => {
  const config = normalizeRunConfig({})
  assert.equal(config.downloadMethod, 'aria2c')
  assert.equal(config.outputLayout, 'folders')
  assert.deepEqual(config.selectedStems, [])
  // 空 params 也走版本迁移：补 standardize=true、normalize=false。
  assert.deepEqual(config.inferenceParams, { standardize: true, normalize: false })
  assert.equal(config.inferenceParamsVersion, CURRENT_INFERENCE_PARAMS_VERSION)
})

test('legacy configs migrate inferenceParams to standardize/normalize', () => {
  // 两个键都缺：旧桌面默认开 input standardize、关 output normalize。
  const legacy = normalizeRunConfig({ inferenceParams: {} })
  assert.equal(legacy.inferenceParams.standardize, true)
  assert.equal(legacy.inferenceParams.normalize, false)
  // 只有 normalize：旧字段名作 input standardization 用。
  const withNormalize = normalizeRunConfig({ inferenceParams: { normalize: true } })
  assert.equal(withNormalize.inferenceParams.standardize, true)
  assert.equal(withNormalize.inferenceParams.normalize, false)
  // 只有 standardize：已经迁过一轮，只补 normalize=false。
  const withStandardize = normalizeRunConfig({ inferenceParams: { standardize: true } })
  assert.equal(withStandardize.inferenceParams.normalize, false)
})

test('current-version configs are not migrated again', () => {
  const config = normalizeRunConfig({ inferenceParamsVersion: CURRENT_INFERENCE_PARAMS_VERSION, inferenceParams: { standardize: false } })
  assert.equal(config.inferenceParams.standardize, false)
  assert.equal(config.inferenceParams.normalize, undefined)
})
