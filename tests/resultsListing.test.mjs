/**
 * 结果分组/筛选/排序契约。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { makeResultListing } from '../src/views/results/listing.ts'

const computed = source => ({ get value() { return source() } })
const t = (key, params) => (params ? `${key}:${JSON.stringify(params)}` : key)

function job(id, input, updatedAt, resultType = 'audio') {
  const task = { id: `task-${id}`, input, output: `D:/out/${id}`, outputs: [{ stem: 'vocals', path: `D:/out/${id}/vocals.wav` }] }
  return { id, model: 'm', output: `D:/out/${id}`, primary: task, tasks: [task], inputCount: 1, updatedAt, resultType }
}

function harness(jobs, overrides = {}) {
  const deps = {
    computed, t,
    task: { resultJobs: jobs },
    search: { value: '' },
    typeFilter: { value: 'all' },
    sortBy: { value: 'time_desc' },
    resultType: () => 'audio',
    ...overrides,
  }
  return { deps, listing: makeResultListing(deps) }
}

test('resultGroups maps result jobs with their result type', () => {
  const { listing } = harness([job('a', '/a/song.wav', 100)])
  assert.equal(listing.resultGroups.value.length, 1)
  assert.equal(listing.resultGroups.value[0].resultType, 'audio')
  assert.equal(listing.resultGroups.value[0].items.length, 1)
})

test('filteredResults matches keywords across id, input, stem and path', () => {
  const { deps, listing } = harness([job('a', '/a/song.wav', 100), job('b', '/a/other.wav', 200)])
  deps.search.value = 'song'
  assert.deepEqual(listing.filteredResults.value.map(g => g.id), ['a'])
  deps.search.value = 'vocals'
  // 两个都含 vocals，按 time_desc 排序（b.updatedAt=200 在前）。
  assert.deepEqual(listing.filteredResults.value.map(g => g.id), ['b', 'a'])
  deps.search.value = 'nomatch'
  assert.deepEqual(listing.filteredResults.value, [])
})

test('filteredResults respects the type filter and sort order', () => {
  const { deps, listing } = harness([job('a', '/a/song.wav', 100), job('b', '/a/other.wav', 200)])
  deps.typeFilter.value = 'video'
  assert.deepEqual(listing.filteredResults.value, [])
  deps.typeFilter.value = 'audio'
  assert.deepEqual(listing.filteredResults.value.map(g => g.id), ['b', 'a']) // time_desc
  deps.sortBy.value = 'time_asc'
  assert.deepEqual(listing.filteredResults.value.map(g => g.id), ['a', 'b'])
})

test('getGroupTitle uses the filename for single-input groups', () => {
  const { listing } = harness([job('a', '/a/song.wav', 100)])
  assert.equal(listing.getGroupTitle(listing.resultGroups.value[0]), 'song.wav')
})
