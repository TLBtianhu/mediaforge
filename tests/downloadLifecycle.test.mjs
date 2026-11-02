import assert from 'node:assert/strict'
import test from 'node:test'
import {
  TERMINAL_DOWNLOAD_STATUSES,
  isTerminalDeleteTaskStatus,
  isTerminalDownloadStatus,
  isTerminalOperationStatus,
} from '../src/features/downloads/lifecycle.ts'

test('terminal download statuses have exactly one definition', () => {
  assert.deepEqual(Array.from(TERMINAL_DOWNLOAD_STATUSES), ['done', 'error', 'cancelled'])
  for (const status of TERMINAL_DOWNLOAD_STATUSES) {
    assert.equal(isTerminalDownloadStatus(status), true)
    assert.equal(isTerminalOperationStatus(status), true)
  }
})

test('paused, interrupted and deleting remain resumable non-terminal states', () => {
  for (const status of ['idle', 'preparing', 'downloading', 'paused', 'interrupted']) {
    assert.equal(isTerminalDownloadStatus(status), false)
  }
  assert.equal(isTerminalOperationStatus('deleting'), false)
})

test('delete tasks share the terminal vocabulary and reject a missing task', () => {
  assert.equal(isTerminalDeleteTaskStatus({ status: 'done' }), true)
  assert.equal(isTerminalDeleteTaskStatus({ status: 'error' }), true)
  assert.equal(isTerminalDeleteTaskStatus({ status: 'cancelled' }), true)
  assert.equal(isTerminalDeleteTaskStatus({ status: 'deleting' }), false)
  assert.equal(isTerminalDeleteTaskStatus(null), false)
  assert.equal(isTerminalDeleteTaskStatus(undefined), false)
})
