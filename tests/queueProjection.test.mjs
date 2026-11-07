import assert from 'node:assert/strict'
import test from 'node:test'
import { cancelQueueJob, isQueueActive, isQueueFailed, mergeQueueJobs, queueOutputCount } from '../src/views/queue/projection.ts'

const audio = (id, overrides = {}) => ({
  id, jobId: 'audio-batch', model: 'model', input: `/inputs/${id}.wav`, output: '/outputs',
  status: 'queued', progress: 0, message: 'waiting', stageLabel: 'Queued',
  createdAt: 1700000000000, updatedAt: 1700000001000, files: [], outputs: [], logs: [], ...overrides,
})
const record = (jobId, overrides = {}) => ({ jobId, command: 'audio_tools', source: 'manual', status: 'running', createdAt: 1700000000, ...overrides })

test('unspawned audio tasks appear independently and completed audio preserves outputs and timestamps', () => {
  const rows = mergeQueueJobs([], [
    audio('queued'),
    audio('done', { status: 'done', progress: 100, createdAt: 1700000002000, finishedAt: 1700000003000, outputs: [{ path: '/out.wav' }] }),
  ])
  assert.deepEqual(rows.map(row => row.jobId), ['done', 'queued'])
  assert.equal(rows[1].status, 'queued')
  assert.equal(rows[1].cancelOwner, 'audio')
  assert.equal(rows[0].status, 'completed')
  assert.equal(rows[0].fraction, 1)
  assert.equal(rows[0].createdAt, 1700000002)
  assert.equal(rows[0].finishedAt, 1700000003)
  assert.equal(queueOutputCount(rows[0]), 1)
})

test('audio task IDs replace stale duplicate journal rows without collapsing their shared batch', () => {
  const rows = mergeQueueJobs([
    record('a', { command: 'infer', status: 'completed' }),
    record('b', { command: 'infer_workflow', status: 'failed', source: 'agent' }),
  ], [audio('a', { status: 'separating', progress: 35 }), audio('b')])
  assert.equal(rows.length, 2)
  assert.equal(rows.find(row => row.jobId === 'a').status, 'running')
  assert.equal(rows.find(row => row.jobId === 'a').fraction, 0.35)
  assert.equal(rows.find(row => row.jobId === 'b').status, 'queued')
  assert.ok(rows.every(row => row.cancelOwner === 'audio'))
})

test('removed or hidden manual audio history cannot be resurrected from journal records', () => {
  const rows = mergeQueueJobs([
    record('removed', { command: 'infer' }),
    record('hidden', { command: 'infer_workflow' }),
    record('agent-audio', { command: 'infer', source: 'agent' }),
    record('media'),
  ], [audio('hidden', { status: 'done', taskHidden: true }), audio('active', { taskHidden: true })])
  assert.deepEqual(new Set(rows.map(row => row.jobId)), new Set(['agent-audio', 'media', 'active']))
})

test('all active audio stages are normalized for filtering and progress', () => {
  for (const status of ['preparing', 'validating_input', 'downloading_model', 'ensuring_model', 'loading_model', 'separating', 'writing_output']) {
    const [row] = mergeQueueJobs([], [audio('active', { status, progress: 150 })])
    assert.equal(row.status, 'running')
    assert.equal(row.fraction, 1)
    assert.equal(isQueueActive(row), true)
  }
  assert.equal(isQueueFailed({ status: 'interrupted' }), true)
  assert.equal(isQueueActive({ status: 'interrupted' }), false)
})

test('media/agent journals preserve full payloads and support named operation outputs', () => {
  const operation = record('operation', { command: 'run_operation', result: { outputs: { audio: { path: 'out.wav' }, score: 0.7 } } })
  const [row] = mergeQueueJobs([operation], [])
  assert.equal(row.result, operation.result)
  assert.equal(queueOutputCount(row), 2)
  assert.equal(queueOutputCount({ result: { downloaded: ['model.bin'] } }), 1)
})

test('queued/running audio cancellation always routes through the audio scheduler', async () => {
  const calls = []
  const actions = Object.fromEntries(['audio', 'manual', 'agent'].map(name => [name, async id => { calls.push([name, id]); return true }]))
  for (const status of ['queued', 'separating']) {
    const [row] = mergeQueueJobs([], [audio('audio', { status })])
    assert.equal(await cancelQueueJob(row, actions), true)
  }
  assert.deepEqual(calls, [['audio', 'audio'], ['audio', 'audio']])
})

test('manual media and agent cancellation keep their backend routes and terminal jobs are untouched', async () => {
  const calls = []
  const actions = Object.fromEntries(['audio', 'manual', 'agent'].map(name => [name, async id => { calls.push([name, id]); return true }]))
  const rows = mergeQueueJobs([
    record('media', { cancelTaskId: 'worker-id' }),
    record('agent', { source: 'agent' }),
    record('finished', { status: 'completed' }),
  ], [])
  for (const row of rows) await cancelQueueJob(row, actions)
  assert.deepEqual(calls, [['manual', 'worker-id'], ['agent', 'agent']])
  const [row] = mergeQueueJobs([], [audio('queued')])
  assert.equal(await cancelQueueJob(row, { ...actions, audio: async () => false }), false)
})
