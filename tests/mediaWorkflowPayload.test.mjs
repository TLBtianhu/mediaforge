import test from 'node:test'
import assert from 'node:assert/strict'
import { buildMediaWorkflowPayload } from '../src/features/media/workflowPayload.ts'

const base = {
  requestId: 'wf-test',
  modality: 'video',
  inputs: ['input.mp4'],
  outputDir: 'out',
  modelDir: 'models',
  steps: [],
}

test('media workflow payload includes a dynamic mask only when provided', () => {
  const withMask = buildMediaWorkflowPayload({ ...base, maskPath: 'masks' })
  const withoutMask = buildMediaWorkflowPayload({ ...base, maskPath: '' })
  assert.equal(withMask.operation, 'media_workflow')
  assert.equal(withMask.maskPath, 'masks')
  assert.equal('maskPath' in withoutMask, false)
})
