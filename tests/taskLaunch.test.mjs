import assert from 'node:assert/strict'
import test from 'node:test'
import { launchTask } from '../src/features/tasks/launch.ts'

test('launchTask maps every task kind through one result contract', async () => {
  const calls = []
  const result = { jobId: 'job-1', tasks: [], total: 1, succeeded: 1, failed: 0 }
  const store = {
    startSeparation: async (options) => { calls.push(['separation', options]); return result },
    startWorkflowInference: async (workflow, options) => { calls.push(['workflow', workflow.id, options]); return result },
    startWorkbenchInference: async (workflow, bindings) => { calls.push(['workbench', workflow.id, bindings]); return result },
  }
  const workflow = { id: 'wf-1' }
  const options = { outputDir: 'out' }
  const bindings = { input: 'a.wav' }
  assert.deepEqual(await launchTask(store, { kind: 'separation', options }), result)
  assert.deepEqual(await launchTask(store, { kind: 'workflow', workflow, options }), result)
  assert.deepEqual(await launchTask(store, { kind: 'workbench', workflow, bindings }), result)
  assert.deepEqual(calls, [
    ['separation', options],
    ['workflow', 'wf-1', options],
    ['workbench', 'wf-1', bindings],
  ])
})

test('launchTask normalizes workbench counters', async () => {
  const store = {
    startWorkbenchInference: async () => ({ jobId: 'job-2', tasks: [], total: 2 }),
  }
  const result = await launchTask(store, { kind: 'workbench', workflow: { id: 'wf-2' }, bindings: {} })
  assert.equal(result.succeeded, 2)
  assert.equal(result.failed, 0)
})

test('launchTask rejects an unknown task kind', async () => {
  await assert.rejects(() => launchTask({}, { kind: 'unknown' }), /Unsupported task launch request/)
})
