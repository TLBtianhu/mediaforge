import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import vm from 'node:vm'
import ts from 'typescript'
import { isTerminalTaskStatus, taskJobId } from '../src/features/tasks/lifecycle.ts'
import { getRemovalTasks as getRemovalTasksFromState } from '../src/stores/taskResultCleanup.ts'
import { TASK_HISTORY_VERSION } from '../src/stores/taskNormalize.ts'

const source = ts.createSourceFile('task.ts', readFileSync(new URL('../src/stores/task.ts', import.meta.url), 'utf8'), ts.ScriptTarget.Latest, true)
const names = new Set(['persistTasks', 'hasResultPresence', 'getRemovalTasks', 'removeResults', 'removeResult', 'clearResults', 'removeWorkflowRun', 'removeSeparationRuns'])
const selected = []
function collect(node) {
  if (ts.isFunctionDeclaration(node) && names.has(node.name?.text)) selected.push(node.getText(source))
  ts.forEachChild(node, collect)
}
collect(source)
assert.equal(selected.length, names.size)
const code = ts.transpileModule(selected.join('\n'), { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText
const plain = value => JSON.parse(JSON.stringify(value))
const ref = value => ({ value })
function task(id, jobId, status = 'done') {
  return { id, jobId, status, logs: ['Run log'], outputs: status === 'done' ? [{ path: `D:/out/${id}.wav` }] : [], files: [],
    runConfig: { workflowId: 'workflow', workbench: { sourceDefinition: { nodes: [], links: [] } } } }
}
function environment(items) {
  const writes = []
  let saveHandler, retries = 0
  const state = {
    tasks: ref(items), initialized: ref(true), isTerminalTaskStatus, taskJobId, getRemovalTasksFromState, TASK_HISTORY_VERSION,
    activeTaskId: ref(items[0]?.id), focusedTaskId: ref(items[0]?.id), focusedResultTaskId: ref(items[0]?.id),
    sessionTaskIds: ref(new Set(items.map(item => item.id))),
    i18n: { global: { t: key => key } }, queuePersist: () => { retries++ },
    async saveAppStore(name, data) { writes.push({ name, data: plain(data) }); if (saveHandler) await saveHandler(data) },
    getJobById(id) { const tasks = state.tasks.value.filter(item => taskJobId(item) === id); return tasks.length ? { id, tasks } : null },
  }
  state.taskBoardTasks = { get value() { return state.tasks.value.filter(item => !item.taskHidden) } }
  state.resultTasks = { get value() { return state.tasks.value.filter(item => item.status === 'done' && !item.resultHidden && (item.outputs.length || item.files.length)) } }
  const store = vm.runInNewContext(`${code}\n({persistTasks,getRemovalTasks,removeResults,removeResult,clearResults,removeWorkflowRun,removeSeparationRuns})`, state)
  return { state, store, writes, retries: () => retries, setSaveHandler(handler) { saveHandler = handler } }
}

test('result deletion removes the entire finished batch and its logs from persistent and session state', async () => {
  const env = environment([task('success', 'batch'), task('failure', 'batch', 'failed'), task('cancelled', 'batch', 'cancelled'), task('other', 'other')])
  assert.equal(await env.store.removeResult('success'), 3)
  assert.deepEqual(Array.from(env.state.tasks.value, item => item.id), ['other'])
  assert.deepEqual(env.writes.at(-1).data.tasks.map(item => item.id), ['other'])
  assert.deepEqual([...env.state.sessionTaskIds.value], ['other'])
  assert.equal(env.state.focusedResultTaskId.value, null)
  assert.equal(env.state.focusedTaskId.value, null)
  assert.equal(env.state.activeTaskId.value, 'other')
  assert.equal(await env.store.removeResult('success'), 0)
  assert.equal(env.writes.length, 1)
})

test('running or queued siblings block deletion before any records are changed', async () => {
  for (const status of ['queued', 'preparing', 'loading_model', 'separating', 'writing_output']) {
    const env = environment([task('success', 'batch'), task('pending', 'batch', status)])
    await assert.rejects(env.store.removeResult('success'), /results.removeRunningBatch/)
    assert.equal(env.state.tasks.value.length, 2)
    assert.equal(env.writes.length, 0)
  }
})

test('clearing an empty selection does nothing and clearing results retains unrelated failures', async () => {
  const env = environment([task('one', 'first'), task('two', 'second'), task('failed', 'third', 'failed')])
  assert.equal(await env.store.clearResults([]), 0)
  assert.equal(env.writes.length, 0)
  assert.equal(await env.store.clearResults(), 2)
  assert.deepEqual(Array.from(env.state.tasks.value, item => item.id), ['failed'])
})

test('a failed history write restores removed records without losing concurrent task updates', async () => {
  const env = environment([task('one', 'first'), task('active', 'second', 'separating')])
  env.setSaveHandler(async () => {
    env.state.tasks.value[0].status = 'done'
    env.state.tasks.value.unshift(task('new', 'new', 'queued'))
    throw new Error('Disk write failed')
  })
  await assert.rejects(env.store.removeResult('one'), /Disk write failed/)
  assert.deepEqual(Array.from(env.state.tasks.value, item => [item.id, item.status]), [['new', 'queued'], ['one', 'done'], ['active', 'done']])
  assert.equal(env.state.focusedResultTaskId.value, 'one')
  assert.equal(env.retries(), 1)
})

test('workflow log cleanup accepts only finished runs with no visible result and validates ownership', async () => {
  const env = environment([task('done', 'result'), task('failed', 'no-output', 'failed'), task('queued', 'pending', 'queued')])
  await assert.rejects(env.store.removeWorkflowRun('workflow', 'result'), /workbench.removeRunHasResults/)
  await assert.rejects(env.store.removeWorkflowRun('other-workflow', 'no-output'), /workbench.runNotFound/)
  await assert.rejects(env.store.removeWorkflowRun('workflow', 'pending'), /results.removeRunningBatch/)
  await env.store.removeWorkflowRun('workflow', 'no-output')
  assert.deepEqual(Array.from(env.state.tasks.value, item => item.id), ['done', 'queued'])
})

test('persistence retains batches larger than 80 tasks until explicitly deleted', async () => {
  const env = environment(Array.from({ length: 105 }, (_, i) => task(String(i), 'large-batch', i === 104 ? 'queued' : 'done')))
  await env.store.persistTasks()
  assert.equal(env.writes[0].data.tasks.length, 105)
  assert.equal(env.writes[0].data.tasks[104].status, 'queued')
})

test('old failed model and Ensemble runs can be cleared even after leaving the current-session queue', async () => {
  const items = Array.from({ length: 100 }, (_, index) => ({
    ...task(`task-${index}`, `job-${index}`, index % 2 ? 'failed' : 'cancelled'),
    runConfig: { runMode: index % 2 ? 'model' : 'workflow' },
  }))
  const env = environment(items)
  env.state.sessionTaskIds.value.clear()
  assert.equal(await env.store.removeSeparationRuns(items.map(item => item.jobId)), 100)
  assert.equal(env.state.tasks.value.length, 0)
  assert.equal(env.writes.at(-1).data.tasks.length, 0)
})

test('separation record cleanup preserves workflow-editor runs, active batches and results', async () => {
  const workflow = task('workflow', 'workflow', 'failed')
  const model = { ...task('model', 'model', 'failed'), runConfig: { runMode: 'model' } }
  const result = { ...task('result', 'result'), runConfig: { runMode: 'model' } }
  const active = { ...task('active', 'active', 'separating'), runConfig: { runMode: 'model' } }
  const env = environment([workflow, model, result, active])
  await assert.rejects(env.store.removeSeparationRuns(['model', 'workflow']), /workbench.runNotFound/)
  await assert.rejects(env.store.removeSeparationRuns(['model', 'result']), /workbench.removeRunHasResults/)
  await assert.rejects(env.store.removeSeparationRuns(['model', 'active']), /results.removeRunningBatch/)
  assert.equal(env.writes.length, 0)
  assert.equal(await env.store.removeSeparationRuns([]), 0)
  await env.store.removeSeparationRuns(['model'])
  assert.deepEqual(Array.from(env.state.tasks.value, item => item.id), ['workflow', 'result', 'active'])
})
