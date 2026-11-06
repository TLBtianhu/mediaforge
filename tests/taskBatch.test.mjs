import assert from 'node:assert/strict'
import test from 'node:test'
import { completeTaskBatch, finalizeTaskBatch, prepareTaskBatch } from '../src/stores/taskBatch.ts'

test('prepareTaskBatch centralizes output preparation and completeTaskBatch centralizes counts', () => {
  const batch = prepareTaskBatch({ outputDir: '/tmp/out', outputLayout: 'flat' }, '/tmp/fallback', false)
  assert.match(batch.jobId, /^job_/)
  assert.equal(batch.jobOutput, '/tmp/out')
  assert.equal(batch.outputLayout, 'flat')
  const tasks = [{ id: 'task-1' }, { id: 'task-2' }]
  const result = completeTaskBatch(batch.jobId, tasks)
  assert.equal(result.jobId, batch.jobId)
  assert.equal(result.total, 2)
  assert.equal(result.succeeded, 2)
  assert.equal(result.failed, 0)
  assert.equal(result.tasks, tasks)
})

test('finalizeTaskBatch centralizes persistence, scheduling and result normalization', () => {
  const batch = prepareTaskBatch({ outputDir: '/tmp/out' }, '/tmp/fallback', false)
  const tasks = [{ id: 'task-1' }]
  const calls = []
  const result = finalizeTaskBatch(batch, tasks, {
    persist: () => calls.push('persist'),
    schedule: () => calls.push('schedule'),
  })
  assert.deepEqual(calls, ['persist', 'schedule'])
  assert.equal(result.total, 1)
  assert.equal(result.jobId, batch.jobId)
})
