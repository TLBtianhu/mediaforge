import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'

test('ModelPanelState is shared by ProcessingPlan and SeparateView', () => {
  const component = readFileSync(new URL('../src/components/ModelPanelState.vue', import.meta.url), 'utf8')
  const plan = readFileSync(new URL('../src/components/media/ProcessingPlan.vue', import.meta.url), 'utf8')
  const separate = readFileSync(new URL('../src/views/SeparateView.vue', import.meta.url), 'utf8')
  assert.match(component, /model-panel-state__glyph/)
  assert.match(plan, /ModelPanelState/)
  assert.match(separate, /ModelPanelState/)
  assert.match(component, /model-panel-state__error/)
})
