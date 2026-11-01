import assert from 'node:assert/strict'
import test from 'node:test'
import { isTaskEvent, isTaskProgress, isTaskResult } from '../src/utils/taskEvents.ts'

test('every canonical task event is recognised', () => {
  for (const type of ['task.started', 'task.stage', 'task.progress', 'task.log', 'task.result', 'task.cancelled']) {
    assert.equal(isTaskEvent(type), true, type)
  }
  assert.equal(isTaskProgress('task.progress'), true)
  assert.equal(isTaskResult('task.result'), true)
})

test('the retired dotted terminal name is gone too', () => {
  assert.equal(isTaskEvent('task.done'), false)
})

test('the legacy underscore spellings are gone', () => {
  for (const type of ['task_started', 'task_stage', 'task_progress', 'task_log', 'task_done', 'task_cancelled']) {
    assert.equal(isTaskEvent(type), false, type)
    assert.equal(isTaskProgress(type), false, type)
    assert.equal(isTaskResult(type), false, type)
  }
})

test('other domains are not mistaken for task events', () => {
  assert.equal(isTaskProgress('asset.progress'), false)
  assert.equal(isTaskResult('runtime_info'), false)
  assert.equal(isTaskEvent('asset.done'), false)
  assert.equal(isTaskEvent(undefined), false)
  assert.equal(isTaskEvent(null), false)
})
