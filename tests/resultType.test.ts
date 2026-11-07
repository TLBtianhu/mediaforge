import assert from 'node:assert/strict'
import test from 'node:test'
import { resultType } from '../src/utils/resultType.ts'
import type { SeparationRunConfig } from '../src/stores/task.ts'

const classify = (config?: unknown) => resultType({ runConfig: config as SeparationRunConfig })
const graph = { version: 0.4, nodes: [{ type: 'pymss_audio_ensemble' }], links: [], extra: { studioEnsemble: { outputStem: 'Vocals' } } }
const simple = { version: 1, steps: [], ensembles: [{ id: 'mix', algorithm: 'avg_wave' }] }

test('results classify single models, quick Ensemble and both workflow types from saved configuration', () => {
  assert.equal(classify(), 'model')
  assert.equal(classify({ runMode: 'model' }), 'model')
  assert.equal(classify({ runMode: 'workflow', workflowId: 'temporary-ensemble', workflowDefinition: graph }), 'ensemble')
  assert.equal(classify({ runMode: 'workflow', workflowDefinition: simple }), 'simple')
  assert.equal(classify({ runMode: 'workflow', workflowDefinition: graph }), 'advanced')
})

test('an editor workflow containing Ensemble never becomes a quick Ensemble result', () => {
  for (const [definition, kind] of [[simple, 'simple'], [graph, 'advanced']] as const) {
    const config = { runMode: 'workflow', workflowId: 'temporary-ensemble', workflowDefinition: definition, workbench: { nativeGraph: kind === 'advanced' } }
    assert.equal(classify(config), kind)
    assert.equal(classify(JSON.parse(JSON.stringify(config))), kind)
  }
})

test('incomplete workflow history is not misclassified as an advanced graph or single model', () => {
  assert.equal(classify({ runMode: 'workflow' }), 'unknown')
  assert.equal(classify({ runMode: 'workflow', workflowDefinition: { nodes: [], steps: [] } }), 'unknown')
  assert.equal(classify({ workflowDefinition: {}, workbench: { nativeGraph: false } }), 'unknown')
  assert.equal(classify({ workflowDefinition: {}, workbench: { nativeGraph: true } }), 'advanced')
})
