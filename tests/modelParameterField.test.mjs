import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'

test('ModelParameterField is shared by ProcessingPlan and SeparateView', () => {
  const component = readFileSync(new URL('../src/components/ModelParameterField.vue', import.meta.url), 'utf8')
  const plan = readFileSync(new URL('../src/components/media/ProcessingPlan.vue', import.meta.url), 'utf8')
  const separate = readFileSync(new URL('../src/views/SeparateView.vue', import.meta.url), 'utf8')
  assert.match(component, /n-input-number|n-select|n-input/)
  assert.match(plan, /ModelParameterField/)
  assert.match(separate, /ModelParameterField/)
  assert.match(separate, /AlignedInferenceInputNumber/)
})
