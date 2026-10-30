import assert from 'node:assert/strict'
import test from 'node:test'
import { isTaskEvent, isTaskProgress, isTaskResult } from '../src/utils/taskEvents.ts'

test('canonical task events are recognised', () => {
  assert.equal(isTaskProgress('task.progress'), true)
  assert.equal(isTaskResult('task.result'), true)
  assert.equal(isTaskEvent('task.failed'), true)
})

test('legacy names stay recognised until P7 removes them', () => {
  assert.equal(isTaskProgress('task.progress'), true)
  assert.equal(isTaskResult('task.result'), true)
})

test('other domains are not mistaken for task events', () => {
  assert.equal(isTaskProgress('download_progress'), false)
  assert.equal(isTaskResult('runtime_info'), false)
  assert.equal(isTaskEvent(undefined), false)
  assert.equal(isTaskEvent(null), false)
})
