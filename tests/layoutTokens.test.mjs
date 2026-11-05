import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'

const read = (path) => readFileSync(new URL(`../${path}`, import.meta.url), 'utf8')

test('layout tokens define spacing and radius primitives', () => {
  const source = read('src/styles/global.scss')
  for (const token of ['--space-xs', '--space-sm', '--space-md', '--space-lg', '--space-xl', '--radius-sm', '--radius-md', '--radius-lg']) {
    assert.match(source, new RegExp(token.replace('-', '\\-')))
  }
})

test('workflow modals use shared width tokens', () => {
  for (const [path, token] of [
    ['src/views/WorkflowSimpleEditorView.vue', '--modal-width-sm'],
    ['src/views/WorkflowNodeEditorView.vue', '--modal-width-sm'],
    ['src/components/workflow/WorkflowRunPanel.vue', '--modal-width-lg'],
    ['src/components/workflow/WorkflowNodeLog.vue', '--modal-width-lg'],
    ['src/views/WorkflowsView.vue', '--modal-width-md'],
    ['src/views/PluginsView.vue', '--modal-width-md'],
  ]) {
    assert.match(read(path), new RegExp(token.replace('-', '\\-')))
  }
})
