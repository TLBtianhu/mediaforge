import assert from 'node:assert/strict'
import test from 'node:test'
import { makeTaskQueue } from '../src/stores/taskQueue.ts'

function setup() {
  const tasks = { value: [] }
  const activeTaskId = { value: null }
  const remembered = []
  let persisted = 0
  let sequence = 0
  const factory = makeTaskQueue({
    useSettingsStore: () => ({ outputDir: 'D:/out' }),
    windowsPaths: { value: true },
    resolveOutputDirectory: value => value,
    joinOutputPath: (base, segment) => `${base}/${segment}`,
    inputStemSegment: () => 'input',
    createRunId: prefix => `${prefix}_${++sequence}`,
    STAGE_META: { queued: { progress: 0, label: 'Queued' } },
    localLogRecord: message => ({ message }),
    buildRunConfig: (_params, _type, outputLayout) => ({ runMode: 'model', outputLayout }),
    buildWorkflowRunConfig: (_workflow, outputLayout) => ({ runMode: 'workflow', outputLayout }),
    tasks,
    rememberSessionTasks: items => remembered.push(...items.map(item => item.id)),
    activeTaskId,
    queuePersist: () => { persisted += 1 },
  })
  return { ...factory, tasks, activeTaskId, remembered, persisted: () => persisted }
}

test('folder tasks derive their output directory and enter the session queue', () => {
  const env = setup()
  const task = env.createQueuedTask('D:/audio/song.wav', 'Model', { batch_size: 2 }, 'vr', undefined, undefined, 'folders', undefined, 3)
  assert.equal(task.output, 'D:/out/input')
  assert.equal(task.runConfig.runMode, 'model')
  assert.equal(task.batchIndex, 3)
  assert.equal(env.tasks.value[0], task)
  assert.equal(env.activeTaskId.value, task.id)
  assert.deepEqual(env.remembered, [task.id])
  assert.equal(env.persisted(), 1)
})

test('workflow tasks retain flat output and workflow run configuration', () => {
  const env = setup()
  const workflow = { id: 'wf', name: 'Workflow', definition: {} }
  const task = env.createQueuedWorkflowTask('D:/audio/song.wav', workflow, undefined, undefined, 'flat')
  assert.equal(task.output, 'D:/out')
  assert.equal(task.model, 'Workflow')
  assert.equal(task.runConfig.runMode, 'workflow')
  assert.equal(task.runConfig.outputLayout, 'flat')
  assert.equal(env.tasks.value.length, 1)
})
