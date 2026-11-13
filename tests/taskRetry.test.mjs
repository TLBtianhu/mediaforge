import assert from 'node:assert/strict'
import test from 'node:test'
import { resolveRetryOutput } from '../src/stores/taskRetry.ts'

const task = (overrides = {}) => ({
  id: 'task-1',
  input: '/input/song.wav',
  output: '/out/song/result.wav',
  jobOutput: '',
  runConfig: { outputLayout: 'folders' },
  ...overrides,
})

test('retry output keeps an absolute recorded job root', () => {
  const result = resolveRetryOutput(task({ jobOutput: '/out/job' }), false, 'unknown')
  assert.equal(result.jobOutput, '/out/job')
  assert.equal(result.outputLayout, 'folders')
})

test('retry rejects missing canonical roots instead of guessing from folder names', () => {
  assert.throws(() => resolveRetryOutput(task({ output: '/out/song' }), false, 'unknown'), /unknown/)
})

test('flat retry keeps the recorded output directory', () => {
  const result = resolveRetryOutput(task({ jobOutput: '/out', output: '/out', runConfig: { outputLayout: 'flat' } }), false, 'unknown')
  assert.equal(result.jobOutput, '/out')
  assert.equal(result.outputLayout, 'flat')
})

test('retry rejects an unrecoverable relative output', () => {
  assert.throws(() => resolveRetryOutput(task({ output: 'relative/out' }), false, 'unknown'), /unknown/)
})
