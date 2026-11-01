/**
 * 模型库主列表的排序与分页契约。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { makeModelSorting } from '../src/views/models/sorting.ts'

const computed = source => ({ get value() { return source() } })

const model = (name, sizeBytes, downloaded = true, modelType = 'demucs') => ({ name, sizeBytes, downloaded, modelType })

function harness(models, overrides = {}) {
  const deps = {
    computed,
    locale: { value: 'zh-CN' },
    filteredModels: { value: models },
    downloadedOnly: { value: false },
    modelSort: { value: 'default' },
    page: { value: 1 },
    pageSize: { value: 2 },
    modelPreferences: { value: {} },
    categoryLabel: m => m.modelType,
    ...overrides,
  }
  return { deps, sorting: makeModelSorting(deps) }
}

test('favorites pin to the top only in default/favorite order', () => {
  const models = [model('b', 100), model('a', 100)]
  const { deps, sorting } = harness(models, {
    modelPreferences: { value: { a: { favorite: true } } },
  })
  assert.deepEqual(sorting.sortedModels.value.map(m => m.name), ['a', 'b'])
  deps.modelSort.value = 'size-desc'
  // 非默认排序不置顶，纯按维度（此处同尺寸回落到名字）。
  assert.deepEqual(sorting.sortedModels.value.map(m => m.name), ['a', 'b'])
})

test('size and name sorts are stable and direction-aware', () => {
  const models = [model('b', 100), model('a', 300), model('c', 200)]
  const { deps, sorting } = harness(models)
  deps.modelSort.value = 'size-desc'
  assert.deepEqual(sorting.sortedModels.value.map(m => m.name), ['a', 'c', 'b'])
  deps.modelSort.value = 'size-asc'
  assert.deepEqual(sorting.sortedModels.value.map(m => m.name), ['b', 'c', 'a'])
  deps.modelSort.value = 'name-desc'
  assert.deepEqual(sorting.sortedModels.value.map(m => m.name), ['c', 'b', 'a'])
})

test('downloaded-only filters the list before sorting', () => {
  const models = [model('a', 100, true), model('b', 200, false)]
  const { sorting } = harness(models, { downloadedOnly: { value: true } })
  assert.deepEqual(sorting.sortedModels.value.map(m => m.name), ['a'])
})

test('category and type sorts use the collator key with name fallback', () => {
  const models = [model('b', 100, true, 'demucs'), model('a', 100, true, 'apollo')]
  const { deps, sorting } = harness(models)
  deps.modelSort.value = 'category'
  assert.deepEqual(sorting.sortedModels.value.map(m => m.name), ['a', 'b'])
})

test('paging slices the sorted list', () => {
  const models = [model('a', 100), model('b', 200), model('c', 300)]
  const { deps, sorting } = harness(models, { modelSort: { value: 'size-asc' } })
  assert.deepEqual(sorting.pagedModels.value.map(m => m.name), ['a', 'b'])
  deps.page.value = 2
  assert.deepEqual(sorting.pagedModels.value.map(m => m.name), ['c'])
})
