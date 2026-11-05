import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'

test('TaskOutputSettings is shared by audio and workflow bindings', () => {
  const component = readFileSync(new URL('../src/components/TaskOutputSettings.vue', import.meta.url), 'utf8')
  const separate = readFileSync(new URL('../src/views/SeparateView.vue', import.meta.url), 'utf8')
  const bindings = readFileSync(new URL('../src/components/workflow/WorkflowNodeBindings.vue', import.meta.url), 'utf8')
  assert.match(component, /separate.outputDirectorySummary/)
  assert.match(separate, /TaskOutputSettings/)
  assert.match(bindings, /TaskOutputSettings/)
  assert.match(separate, /settings.defaultFormat/)
  assert.match(component, /--on-surface-muted/)
})
