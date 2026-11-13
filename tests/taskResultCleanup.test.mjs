import assert from 'node:assert/strict'
import test from 'node:test'
import { getRemovalTasks, primaryRevealPath } from '../src/stores/taskResultCleanup.ts'

const task = (overrides = {}) => ({
  output: '/out/result.wav',
  jobOutput: '/out/job',
  outputs: [{ path: '/out/a.wav' }, { path: '/out/a.wav' }, { path: '' }],
  runConfig: { outputLayout: 'folders' },
  ...overrides,
})

test('result deletion expands all terminal siblings while keeping unrelated jobs', () => {
  const items = ['done', 'failed', 'cancelled'].map((status, index) => ({ ...task(), id: String(index), jobId: 'batch', status }))
  const unrelated = { ...task(), id: 'other', jobId: 'other', status: 'done' }
  assert.deepEqual(getRemovalTasks([...items, unrelated], ['0'], 'blocked'), items)
  assert.deepEqual(getRemovalTasks([...items, unrelated], ['0', '1', '0'], 'blocked'), items)
})

test('a queued or running sibling rejects the full batch before a native request', () => {
  for (const status of ['queued', 'downloading', 'loading', 'separating', 'saving']) {
    const items = [{ ...task(), id: 'one', jobId: 'batch', status: 'done' }, { ...task(), id: 'two', jobId: 'batch', status }]
    assert.throws(() => getRemovalTasks(items, ['one'], 'blocked'), /blocked/)
  }
})

test('explicit missing task identities reject instead of becoming a successful empty removal', () => {
  const items = [{ ...task(), id: 'one', jobId: 'batch', status: 'done' }]
  assert.deepEqual(getRemovalTasks(items, [], 'changed'), [])
  assert.throws(() => getRemovalTasks(items, ['missing'], 'changed'), /changed/)
  assert.throws(() => getRemovalTasks(items, ['one', 'missing'], 'changed'), /changed/)
})

test('primaryRevealPath keeps the workbench first output', () => {
  assert.equal(primaryRevealPath(task()), '/out/result.wav')
  assert.equal(primaryRevealPath(task({ runConfig: { workbench: {} }, outputs: [{ path: '/out/first.wav' }] })), '/out/first.wav')
})
