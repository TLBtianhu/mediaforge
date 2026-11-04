import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'

test('SeparateView passes Vue computed into makeOutputDirectory', () => {
  const source = readFileSync(new URL('../src/views/SeparateView.vue', import.meta.url), 'utf8')
  const match = source.match(/makeOutputDirectory\(\{([\s\S]*?)\}\)/)
  assert.ok(match, 'makeOutputDirectory call not found')
  assert.match(match[1], /\bcomputed\b/, 'computed must be provided to makeOutputDirectory')
  const outputDirectorySource = readFileSync(new URL('../src/views/separate/outputDirectory.ts', import.meta.url), 'utf8')
  assert.match(outputDirectorySource, /computed: typeof computed/, 'computed must be an explicit required dependency')
})
