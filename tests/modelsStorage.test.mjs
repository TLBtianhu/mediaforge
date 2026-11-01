/**
 * 模型库存储管理区块的派生状态契约。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { makeModelStorage } from '../src/views/models/storage.ts'

const computed = source => ({ get value() { return source() } })
const collator = { compare: (a, b) => String(a).localeCompare(String(b)) }

function harness(overrides = {}) {
  const deps = {
    computed,
    collator: { value: collator },
    modelStorageSummary: { value: { models: [], toolModels: [] } },
    storageSearch: { value: '' },
    storageSort: { value: 'size-desc' },
    storageDownloadedOnly: { value: true },
    selectedStorageModels: { value: [] },
    isDeletingModel: () => false,
    batchDeleteState: { value: { totalModels: 0, completedModels: 0, currentModel: null } },
    deleteTasks: { value: {} },
    ...overrides,
  }
  return { deps, storage: makeModelStorage(deps) }
}

const catalog = (name, sizeBytes, downloaded = true) => ({
  name, sizeBytes, expectedSizeBytes: sizeBytes, downloaded,
  files: [{ exists: true }, { exists: downloaded }],
})
const tool = (id, name, sizeBytes, toolName = 'asr') => ({ id, name, tool: toolName, role: 'runtime', sizeBytes, fileCount: 1 })

test('catalog and tool models merge with distinct selection keys', () => {
  const { storage } = harness({
    modelStorageSummary: { value: { models: [catalog('a.ckpt', 100)], toolModels: [tool('m1', 'whisper', 50)] } },
  })
  const items = storage.allStorageModels.value
  assert.equal(items.length, 2)
  assert.equal(items[0].selectionKey, 'catalog:a.ckpt')
  assert.equal(items[1].selectionKey, 'tool:asr:m1')
  assert.equal(items[1].kind, 'tool')
})

test('storage list filters downloaded, search text, and sorts by size', () => {
  const summary = { models: [catalog('big', 900), catalog('small', 100), catalog('hidden', 50, false)], toolModels: [] }
  const { deps, storage } = harness({ modelStorageSummary: { value: summary } })
  assert.deepEqual(storage.storageModels.value.map(i => i.name), ['big', 'small'])
  deps.storageDownloadedOnly.value = false
  assert.deepEqual(storage.storageModels.value.map(i => i.name), ['big', 'small', 'hidden'])
  deps.storageSort.value = 'size-asc'
  assert.deepEqual(storage.storageModels.value.map(i => i.name), ['hidden', 'small', 'big'])
  deps.storageSort.value = 'size-desc'
  deps.storageSearch.value = 'sma'
  assert.deepEqual(storage.storageModels.value.map(i => i.name), ['small'])
})

test('selected bytes sum only the checked models', () => {
  const { deps, storage } = harness({
    modelStorageSummary: { value: { models: [catalog('a', 100), catalog('b', 200)], toolModels: [] } },
  })
  deps.selectedStorageModels.value = ['catalog:a']
  assert.equal(storage.selectedStorageBytes.value, 100)
  storage.setStorageSelected('catalog:b', true)
  assert.equal(storage.selectedStorageBytes.value, 300)
  storage.setStorageSelected('catalog:a', false)
  assert.equal(storage.selectedStorageBytes.value, 200)
})

test('a catalog model being deleted marks the selection as busy', () => {
  const { storage } = harness({
    modelStorageSummary: { value: { models: [catalog('a', 100)], toolModels: [tool('t', 'x', 50)] } },
    selectedStorageModels: { value: ['catalog:a'] },
    isDeletingModel: name => name === 'a',
  })
  assert.equal(storage.hasDeletingSelectedStorageModels.value, true)
})

test('batch delete progress blends completed models with the current file fraction', () => {
  const { storage } = harness({
    batchDeleteState: { value: { totalModels: 4, completedModels: 1, currentModel: 'a' } },
    deleteTasks: { value: { a: { completedFiles: 1, totalFiles: 2 } } },
  })
  assert.equal(storage.batchDeleteProgress.value, 38) // (1 + 0.5) / 4 = 37.5 → 38
  assert.equal(harness().storage.batchDeleteProgress.value, 0)
})
