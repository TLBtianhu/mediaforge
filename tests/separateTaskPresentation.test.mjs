import assert from 'node:assert/strict'
import test from 'node:test'
import { makeTaskPresentation } from '../src/views/separate/taskPresentation.ts'

const view = makeTaskPresentation(key => `i18n:${key}`)
const item = overrides => ({
  status: 'separating', createdAt: 1000, startedAt: 2000, updatedAt: 3000,
  ...overrides,
})

test('task status labels and shared visual types preserve unknown states', () => {
  assert.equal(view.statusLabel('done'), 'i18n:tasks.statusDone')
  assert.equal(view.statusLabel('custom'), 'custom')
  assert.equal(view.statusType('done'), 'success')
  assert.equal(view.statusType('failed'), 'error')
  assert.equal(view.statusType('cancelled'), 'warning')
  assert.equal(view.statusType('separating'), 'info')
  for (const status of ['done', 'failed', 'cancelled', 'separating']) {
    assert.equal(view.progressStatus(status), view.statusType(status))
  }
})

test('progress messages normalize known worker text and preserve other text', () => {
  assert.equal(view.normalizeProgressMessage(' Loading Model '), 'i18n:tasks.progressLoadingModel')
  assert.equal(view.normalizeProgressMessage(' custom detail '), 'custom detail')
  assert.equal(view.normalizeProgressMessage(), '')
  assert.equal(view.progressTitle(item()), 'i18n:tasks.progressTitleSeparating')
  assert.equal(view.progressTitle(item({ status: 'done' })), 'i18n:tasks.statusDone')
})

test('progress detail and submessage use only meaningful task data', () => {
  assert.equal(view.progressDetail(item({ progressCurrent: 5.2, progressTotal: 12.4 })), '00:00:05 / 00:00:12')
  assert.equal(view.progressDetail(item({ status: 'done', progressCurrent: 5, progressTotal: 12 })), '')
  assert.equal(view.progressDetail(item({ progressCurrent: 5, progressTotal: 0 })), '')
  assert.equal(view.taskSubMessage(item({ error: 'failed detail', message: 'loading model' })), 'failed detail')
  assert.equal(view.taskSubMessage(item({ message: 'loading model' })), 'i18n:tasks.progressLoadingModel')
})

test('duration uses queue/running/terminal timestamps and clamps invalid values', () => {
  assert.equal(view.taskDuration(item({ status: 'queued' }), 9000), '0s')
  assert.equal(view.taskDuration(item(), 6500), '5s')
  assert.equal(view.taskDuration(item({ status: 'done', durationMs: 65000 }), 99999), '1m 5s')
  assert.equal(view.taskDuration(item({ status: 'done', durationMs: null, finishedAt: 8000 }), 99999), '6s')
  assert.equal(view.taskDuration(item({ status: 'done', durationMs: -1, finishedAt: 1000 }), 99999), '0s')
})

test('middle shortening preserves both path ends', () => {
  assert.equal(view.shortenMiddle('short.wav'), 'short.wav')
  assert.equal(view.shortenMiddle('C:/very/long/path/to/output-file.wav', 24), 'C:/very/lo...t-file.wav')
})
