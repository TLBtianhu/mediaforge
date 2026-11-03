import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const source = readFileSync(new URL('../src/components/media/ProcessingPlan.vue', import.meta.url), 'utf8')

test('capability selector remains available when another model is usable', () => {
  assert.match(source, /v-else-if="!available\.length"/)
  assert.doesNotMatch(source, /v-else-if="!hasUsableModel"/)
})
