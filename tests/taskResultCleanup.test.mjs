import assert from 'node:assert/strict'
import test from 'node:test'
import { primaryRevealPath, resultTrashEmptyDirs, resultTrashTargets } from '../src/stores/taskResultCleanup.ts'

const task = (overrides = {}) => ({
  output: '/out/result.wav',
  jobOutput: '/out/job',
  outputs: [{ path: '/out/a.wav' }, { path: '/out/a.wav' }, { path: '' }],
  runConfig: { outputLayout: 'folders' },
  ...overrides,
})

test('resultTrashTargets deduplicates explicit output paths', () => {
  assert.deepEqual(resultTrashTargets(task()), ['/out/a.wav'])
})

test('flat and workbench tasks do not recycle arbitrary directories', () => {
  assert.deepEqual(resultTrashEmptyDirs(task({ runConfig: { outputLayout: 'flat' } }), false), [])
  assert.deepEqual(resultTrashEmptyDirs(task({ runConfig: { workbench: {} } }), false), [])
})

test('primaryRevealPath keeps the workbench first output', () => {
  assert.equal(primaryRevealPath(task()), '/out/result.wav')
  assert.equal(primaryRevealPath(task({ runConfig: { workbench: {} }, outputs: [{ path: '/out/first.wav' }] })), '/out/first.wav')
})
