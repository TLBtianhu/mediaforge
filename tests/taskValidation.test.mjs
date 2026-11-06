import assert from 'node:assert/strict'
import test from 'node:test'
import { createServer } from 'vite'
import { fileURLToPath } from 'node:url'

globalThis.window = { matchMedia: () => ({ matches: false }), addEventListener() {}, removeEventListener() {} }

const vite = await createServer({
  configFile: false,
  server: { watch: null, middlewareMode: true, hmr: false },
  optimizeDeps: { noDiscovery: true },
  resolve: { alias: { '@': fileURLToPath(new URL('../src', import.meta.url)) } },
})
const validation = await vite.ssrLoadModule('/src/stores/taskValidation.ts')

test('workflow definition validation is shared outside the task store', () => {
  assert.equal(validation.workflowDefinitionValidationError({}), '工作流定义格式无效。')
})

test('separation input validation rejects multiple primary inputs', () => {
  const definition = {
    nodes: [
      { id: 'in-a', type: 'pymss_load_audio' },
      { id: 'in-b', type: 'pymss_load_audio' },
      { id: 'save', type: 'pymss_save_audio' },
    ],
  }
  assert.throws(() => validation.validateSeparateWorkflowInput(definition))
})

test('workbench run config keeps its native multi-input exception', () => {
  const definition = {
    nodes: [
      { id: 'in-a', type: 'pymss_load_audio' },
      { id: 'in-b', type: 'pymss_load_audio' },
      { id: 'save', type: 'pymss_save_audio' },
    ],
  }
  assert.doesNotThrow(() => validation.validateWorkflowRunConfig({ workflowDefinition: definition, workbench: {} }))
})

await vite.close()
