/**
 * 模型目录迁移的视图投影与对话框分派。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { makeModelDirMigration } from '../src/views/settings/modelDirMigration.ts'

const computed = source => ({ get value() { return source() } })
const t = key => key

function harness(state, overrides = {}) {
  const calls = []
  const messages = []
  const settings = {
    pickModelDir: async () => 'D:/new-models',
    prepareModelDirChange: async () => ({ outcome: 'switched' }),
    cancelModelDirChangeConfirmation: () => calls.push('cancel'),
    clearModelDirMigrationState: () => calls.push('clear'),
    confirmModelDirMigration: async () => { calls.push('confirm') },
    resolveModelDirConflict: async action => { calls.push(['resolve', action]) },
    pickOutputDir: async () => { calls.push('pickOutputDir') },
    ...(overrides.settings || {}),
  }
  const deps = {
    computed, t, settings,
    message: {
      warning: value => messages.push(['warning', value]),
      info: value => messages.push(['info', value]),
      success: value => messages.push(['success', value]),
      error: value => messages.push(['error', value]),
    },
    modelDirMigrationState: { value: state },
    hasActiveModelDirUsage: { value: false },
    isCheckingModelDir: { value: false },
    ...overrides.deps,
  }
  return { calls, messages, deps, migration: makeModelDirMigration(deps) }
}

test('the migration dialog is hidden while idle or awaiting confirmation', () => {
  for (const status of ['idle', 'confirm']) {
    assert.equal(harness({ status }).migration.modelDirMigrationVisible.value, false, status)
  }
  for (const status of ['copying', 'conflict', 'success', 'failed']) {
    assert.equal(harness({ status }).migration.modelDirMigrationVisible.value, true, status)
  }
})

test('progress prefers bytes, falls back to files, and pins 100 at the end states', () => {
  assert.equal(harness({ status: 'copying', totalBytes: 200, copiedBytes: 50, totalFiles: 4, completedFiles: 3 }).migration.modelDirMigrationProgress.value, 25)
  assert.equal(harness({ status: 'copying', totalBytes: 0, totalFiles: 4, completedFiles: 1 }).migration.modelDirMigrationProgress.value, 25)
  assert.equal(harness({ status: 'copying', totalBytes: 0, totalFiles: 0 }).migration.modelDirMigrationProgress.value, 0)
  for (const status of ['ready_to_switch', 'finalizing_cleanup', 'success']) {
    assert.equal(harness({ status }).migration.modelDirMigrationProgress.value, 100, status)
  }
})

test('conflict and result flags follow the state machine', () => {
  assert.equal(harness({ status: 'conflict', conflict: { target: 'D:/x' } }).migration.modelDirMigrationHasConflict.value, true)
  assert.equal(harness({ status: 'conflict', conflict: null }).migration.modelDirMigrationHasConflict.value, false)
  assert.equal(harness({ status: 'copying' }).migration.modelDirMigrationHasConflict.value, false)
  for (const status of ['success', 'failed', 'aborted']) {
    assert.equal(harness({ status }).migration.modelDirMigrationHasResult.value, true, status)
  }
  assert.equal(harness({ status: 'copying' }).migration.modelDirMigrationHasResult.value, false)
})

test('closing the dialog cancels a pending confirmation and clears a finished result', () => {
  const confirm = harness({ status: 'confirm' })
  confirm.migration.closeModelDirMigrationDialog()
  assert.deepEqual(confirm.calls, ['cancel'])
  const done = harness({ status: 'success' })
  done.migration.closeModelDirMigrationDialog()
  assert.deepEqual(done.calls, ['clear'])
  const copying = harness({ status: 'copying' })
  copying.migration.closeModelDirMigrationDialog()
  assert.deepEqual(copying.calls, [])
})

test('changing the model dir is blocked while models are in use', async () => {
  const { calls, messages, migration } = harness({ status: 'idle' }, { deps: { hasActiveModelDirUsage: { value: true } } })
  await migration.changeModelDir()
  assert.deepEqual(messages, [['warning', 'settings.modelDirChangeBlocked']])
  assert.equal(calls.length, 0)
})

test('a no-op change reports the same path and never opens the checker twice', async () => {
  const { messages, deps, migration } = harness({ status: 'idle' }, {
    settings: { prepareModelDirChange: async () => ({ outcome: 'noop' }) },
  })
  await migration.changeModelDir()
  assert.deepEqual(messages, [['info', 'settings.modelDirSamePath']])
  assert.equal(deps.isCheckingModelDir.value, false)
})

test('a successful change reports success and always releases the checking flag', async () => {
  const { messages, deps, migration } = harness({ status: 'idle' })
  await migration.changeModelDir()
  assert.deepEqual(messages, [['success', 'settings.modelDirChanged']])
  assert.equal(deps.isCheckingModelDir.value, false)
})

test('conflict resolution forwards the chosen action', async () => {
  const { calls, migration } = harness({ status: 'conflict', conflict: {} })
  await migration.resolveModelDirConflict('skip')
  assert.deepEqual(calls, [['resolve', 'skip']])
})
