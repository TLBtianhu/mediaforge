import assert from 'node:assert/strict'
import test from 'node:test'
import { buildJobs, findJobById } from '../src/stores/taskJobs.ts'

const task = (id, overrides = {}) => ({
  id,
  jobId: 'job-1',
  model: 'model-a',
  input: `/input/${id}.wav`,
  output: '/out',
  outputs: [{ path: `/out/${id}.wav` }],
  status: 'done',
  progress: 100,
  createdAt: 1,
  updatedAt: 2,
  startedAt: 1,
  finishedAt: 2,
  ...overrides,
})

test('buildJobs groups tasks and computes shared lifecycle fields', () => {
  const jobs = buildJobs([
    task('a'),
    task('b', { status: 'separating', progress: 50, finishedAt: undefined, updatedAt: 3 }),
  ])
  assert.equal(jobs.length, 1)
  assert.equal(jobs[0].inputCount, 2)
  assert.equal(jobs[0].outputCount, 2)
  assert.equal(jobs[0].status, 'separating')
  assert.equal(jobs[0].progress, 75)
  assert.equal(findJobById(jobs, 'job-1'), jobs[0])
  assert.equal(findJobById(jobs, null), null)
})
