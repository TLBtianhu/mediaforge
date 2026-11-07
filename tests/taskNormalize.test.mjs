/**
 * 运行配置规范化契约：默认值、字段拷贝、旧 inferenceParams 版本迁移。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { normalizeOutputLayout, normalizeOutputNaming, normalizeRunConfig, normalizeStatus, normalizeTask, readTaskHistory, validateSeparateState, CURRENT_INFERENCE_PARAMS_VERSION } from '../src/stores/taskNormalize.ts'

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
  const config = normalizeRunConfig({ inferenceParamsVersion: CURRENT_INFERENCE_PARAMS_VERSION })
  assert.equal(config.downloadMethod, 'aria2c')
  assert.equal(config.outputLayout, 'folders')
  assert.deepEqual(config.selectedStems, [])
  assert.deepEqual(config.inferenceParams, {})
  assert.equal(config.inferenceParamsVersion, CURRENT_INFERENCE_PARAMS_VERSION)
})

test('missing, old and future inference schemas are rejected without mutating the input', () => {
  for (const version of [undefined, 0, 1, 2, 4, 99]) {
    const input = { inferenceParamsVersion: version, inferenceParams: { normalize: true } }
    const original = JSON.stringify(input)
    assert.throws(() => normalizeRunConfig(input), /Unsupported/)
    assert.equal(JSON.stringify(input), original)
  }
})

test('current-version configs are not migrated again', () => {
  const config = normalizeRunConfig({ inferenceParamsVersion: CURRENT_INFERENCE_PARAMS_VERSION, inferenceParams: { standardize: false } })
  assert.equal(config.inferenceParams.standardize, false)
  assert.equal(config.inferenceParams.normalize, undefined)
})


test('normalizeStatus maps unknown and non-string statuses', () => {
  assert.equal(normalizeStatus('done'), 'done')
  assert.equal(normalizeStatus('nonsense'), 'preparing')
  assert.equal(normalizeStatus(undefined), 'queued')
  assert.equal(normalizeStatus(42), 'queued')
})

test('normalizeTask fills defaults and marks interrupted runs as failed', () => {
  const fresh = normalizeTask({})
  assert.equal(fresh.status, 'queued')
  assert.equal(fresh.progress, 2)
  assert.equal(fresh.output, 'outputs')

  const interrupted = normalizeTask({ status: 'separating' })
  assert.equal(interrupted.status, 'failed')
  assert.equal(interrupted.stageLabel, '已中断')
  assert.equal(interrupted.message, '上次运行未完成')
  assert.ok(interrupted.logs.some(line => line.includes('中断')))
  assert.equal(interrupted.progress, 100)

  const done = normalizeTask({ status: 'done' })
  assert.equal(done.status, 'done')
  assert.equal(done.progress, 100)
})

test('task restore never manufactures missing output metadata from old filename lists', () => {
  const task = normalizeTask({ output: 'D:/out', files: ['vocals.wav'], outputs: [] }, true)
  assert.equal(task.outputs.length, 0)
})

test('history and separate settings accept only their current envelopes', () => {
  assert.deepEqual(readTaskHistory(null), [])
  assert.deepEqual(readTaskHistory({ version: 1, tasks: [] }), [])
  for (const state of [{ tasks: [] }, { version: 0, tasks: [] }, { version: 2, tasks: [] }, { version: 1, tasks: [{}] }]) {
    const original = JSON.stringify(state)
    assert.throws(() => readTaskHistory(state), /Unsupported/)
    assert.equal(JSON.stringify(state), original)
  }
  assert.equal(validateSeparateState(null), null)
  assert.deepEqual(validateSeparateState({ version: 1 }), { version: 1 })
  for (const state of [{}, { version: 0 }, { version: 2 }]) assert.throws(() => validateSeparateState(state), /Unsupported/)
})
