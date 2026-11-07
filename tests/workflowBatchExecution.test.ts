import assert from 'node:assert/strict'
import test from 'node:test'
import { activeWorkflowTask, selectedWorkflowJob, workflowBatchExecution } from '../src/workflows/batchExecution.ts'
import type { SeparationTask, SeparationJob } from '../src/stores/task.ts'

const definition = { version: 1, steps: [{ id: 'split', model: 'model.ckpt', stems: ['Vocals'], save: { Vocals: 'Default' } }] }
function task(id: string, status: SeparationTask['status'], nodeStatus?: 'done' | 'failed' | 'running' | 'cancelled'): SeparationTask {
  return { id, input: `D:/Music/${id}.wav`, model: 'Workflow', status, logs: [],
    runConfig: { workflowDefinition: definition, workbench: { nativeGraph: false } },
    nodeStates: nodeStatus ? { 'step:split': { status: nodeStatus, progress: nodeStatus === 'done' ? 100 : 40 } } : {},
    nodeLogs: { 'step:split': [`${id} model log`] },
  } as SeparationTask
}

test('batch summaries retain early failure even when the last file succeeds', () => {
  const tasks = [task('first', 'failed', 'failed'), task('second', 'done', 'done'), task('third', 'done', 'done')]
  const original = JSON.stringify(tasks)
  const result = workflowBatchExecution(tasks)
  assert.equal(result.states.split.status, 'failed')
  assert.deepEqual(result.states.split.summary, { completed: 2, total: 3, failed: 1, cancelled: 0, skipped: 0, unknown: 0 })
  assert.equal(result.states.split.progress, 80)
  assert.deepEqual(result.logs.split, [
    '[info] ── 1. first.wav ──', 'first model log', '[info] ── 2. second.wav ──', 'second model log', '[info] ── 3. third.wav ──', 'third model log',
  ])
  assert.equal(result.states.save.status, 'skipped')
  assert.equal(JSON.stringify(tasks), original)
})

test('queued files do not complete nodes; interrupted node states cannot stay running forever', () => {
  const result = workflowBatchExecution([task('first', 'done', 'done'), task('next', 'queued')])
  assert.equal(result.states.split.status, 'queued')
  assert.equal(result.states.split.progress, 50)
  assert.equal(result.states.split.summary?.completed, 1)
  const interrupted = workflowBatchExecution([task('cancelled', 'cancelled', 'running'), task('failed', 'failed', 'running')])
  assert.equal(interrupted.states.split.status, 'failed')
  assert.equal(interrupted.states.split.summary?.cancelled, 1)
  assert.equal(interrupted.states.split.summary?.failed, 1)
})

test('the active file follows execution instead of a completed or queued file', () => {
  const tasks = [task('done', 'done'), task('queue', 'queued'), task('loading', 'loading_model')]
  assert.equal(activeWorkflowTask(tasks)?.id, 'loading')
  tasks[2].status = 'failed'
  assert.equal(activeWorkflowTask(tasks)?.id, 'queue')
  tasks[1].status = 'cancelled'
  assert.equal(activeWorkflowTask(tasks), null)
})

test('missing batch selections use the latest run while explicit history selections are retained', () => {
  const older = { id: 'older', createdAt: 10 } as SeparationJob
  const newer = { id: 'newer', createdAt: 20 } as SeparationJob
  assert.equal(selectedWorkflowJob([older, newer], '')?.id, 'newer')
  assert.equal(selectedWorkflowJob([older, newer], 'removed')?.id, 'newer')
  assert.equal(selectedWorkflowJob([older, newer], 'older')?.id, 'older')
  assert.equal(selectedWorkflowJob([], ''), null)
})

test('an executing file takes priority over other files marked preparing when the worker starts', () => {
  const waiting = task('waiting', 'preparing')
  const executing = task('executing', 'separating', 'running')
  const tasks = [waiting, task('queued', 'queued'), executing]
  for (const status of ['validating_input', 'downloading_model', 'ensuring_model', 'loading_model', 'separating', 'writing_output'] as const) {
    executing.status = status
    assert.equal(activeWorkflowTask(tasks)?.id, 'executing', status)
  }
  executing.status = 'done'
  assert.equal(activeWorkflowTask(tasks)?.id, 'waiting')
  waiting.status = 'cancelled'
  assert.equal(activeWorkflowTask(tasks)?.id, 'queued')
})

test('native nodes retain their own identities and current records with no node events is not labelled successful', () => {
  const native = task('native', 'done')
  native.runConfig!.workbench!.nativeGraph = true
  native.runConfig!.workflowDefinition = { version: 0.4, nodes: [{ id: 1, type: 'LoadAudio' }, { id: 2, type: 'SaveAudio' }], links: [] }
  native.nodeStates = { '1': { status: 'done', progress: 100 } }
  native.nodeLogs = { '1': ['Read audio'] }
  const result = workflowBatchExecution([native])
  assert.equal(result.states['1'].status, 'done')
  assert.equal(result.states['2'].status, 'unknown')
  assert.equal(result.states['2'].summary?.unknown, 1)
  assert.deepEqual(result.nodes.map(node => node.id), ['1', '2'])
})

test('native node details follow recorded execution rather than JSON array or numeric id order', () => {
  const native = task('native', 'done')
  native.runConfig!.workbench!.nativeGraph = true
  native.runConfig!.workflowDefinition = { version: 0.4, nodes: [
    { id: 3, type: 'pymss_mss_separate' }, { id: 4, type: 'pymss_save_audio' },
    { id: 10, type: 'pymss_load_audio' }, { id: 2, type: 'pymss_mss_params' },
  ], links: [] }
  native.nodeExecutionOrder = ['10', '2', '3', '4']
  const snapshot = JSON.stringify(native)
  const restored = JSON.parse(snapshot)
  const result = workflowBatchExecution([restored])
  assert.deepEqual(result.nodes.map(node => node.id), ['10', '2', '3', '4'])
  assert.equal(JSON.stringify(restored), snapshot)
})

test('native nodes with no execution event follow observed nodes without inventing an execution order', () => {
  const native = task('native', 'failed')
  native.runConfig!.workbench!.nativeGraph = true
  native.runConfig!.workflowDefinition = { version: 0.4, nodes: [
    { id: 3, type: 'pymss_mss_separate' }, { id: 4, type: 'pymss_save_audio' },
    { id: 10, type: 'pymss_load_audio' }, { id: 2, type: 'pymss_mss_params' },
  ], links: [] }
  native.nodeExecutionOrder = ['10', '2', '3']
  assert.deepEqual(workflowBatchExecution([native]).nodes.map(node => node.id), ['10', '2', '3', '4'])
  native.nodeExecutionOrder = ['10']
  assert.deepEqual(workflowBatchExecution([native]).nodes.map(node => node.id), ['10', '3', '4', '2'])
  delete native.nodeExecutionOrder
  assert.deepEqual(workflowBatchExecution([native]).nodes.map(node => node.id), ['3', '4', '10', '2'])
})

test('simple node details keep the grouped input, steps and output layout', () => {
  const simple = task('simple', 'done', 'done')
  simple.nodeExecutionOrder = ['input', 'step:split', 'save:split:Vocals']
  assert.deepEqual(workflowBatchExecution([simple]).nodes.map(node => node.id), ['input', 'split', 'save'])
})
