/**
 * 简单工作流编辑器的几何与布局契约。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import {
  stepHeight, ensembleHeight, nodesOverlap, outputPortKey, ensembleInputPortKey,
  savePortKey, computeAutoLayout,
} from '../src/components/workflow/simpleLayout.ts'

const simpleOutputRef = (stepId, stem) => `${stepId}.${stem}`
const simpleSourceStepId = source => String(source).split('.')[0]
const isAudio = algorithm => algorithm === 'separate'

test('stepHeight grows with the number of stems', () => {
  assert.equal(stepHeight({ stems: ['a', 'b'] }), 166)
  assert.equal(stepHeight({ stems: ['a', 'b', 'c', 'd'] }), 166 + 2 * 26)
})

test('ensembleHeight distinguishes audio operations and input count', () => {
  assert.equal(ensembleHeight({ algorithm: 'separate', inputs: [] }, isAudio), 174)
  assert.equal(ensembleHeight({ algorithm: 'other', inputs: [{}] }, isAudio), 206 + 42)
})

test('nodesOverlap detects overlap with a gap', () => {
  assert.equal(nodesOverlap({ x: 0, y: 0 }, 100, 100, { x: 50, y: 50 }, 100, 100), true)
  assert.equal(nodesOverlap({ x: 0, y: 0 }, 100, 100, { x: 500, y: 500 }, 100, 100), false)
})

test('port keys follow the output/ensemble-input/save formats', () => {
  assert.equal(outputPortKey('s1', 'vocals', simpleOutputRef), 'output:s1.vocals')
  assert.equal(ensembleInputPortKey('e1', 2), 'ensemble-input:e1:2')
  assert.equal(savePortKey('s1', 'vocals', simpleOutputRef), 'save:s1.vocals')
})

test('computeAutoLayout assigns increasing depth and positions', () => {
  const draft = {
    steps: [
      { id: 'a', input: 'input', stems: ['vocals'] },
      { id: 'b', input: 'a.vocals', stems: ['vocals'] },
    ],
    ensembles: [],
  }
  const nodes = computeAutoLayout(draft, simpleSourceStepId, isAudio)
  assert.deepEqual(nodes.input, { x: 64, y: 250 })
  // a 在 input 之后（depth 1），b 在 a 之后（depth 2），x 随深度递增。
  assert.equal(nodes.a.x, 360)
  assert.equal(nodes.b.x, 720)
  assert.ok(nodes.save.x > nodes.b.x)
})
