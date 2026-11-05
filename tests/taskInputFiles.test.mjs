import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'

test('TaskInputFiles is shared by audio and workflow bindings', () => {
  const component = readFileSync(new URL('../src/components/TaskInputFiles.vue', import.meta.url), 'utf8')
  const separate = readFileSync(new URL('../src/views/SeparateView.vue', import.meta.url), 'utf8')
  const bindings = readFileSync(new URL('../src/components/workflow/WorkflowNodeBindings.vue', import.meta.url), 'utf8')
  assert.match(component, /separate.chooseFiles/)
  assert.match(component, /dragHandlers/)
  assert.match(separate, /TaskInputFiles/)
  assert.match(separate, /drag-handlers/)
  assert.match(bindings, /TaskInputFiles/)
})
