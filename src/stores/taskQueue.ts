/**
 * 队列任务构造：输出路径、单模型任务与工作流任务。
 * 任务 store 提供状态与持久化动作；模块只负责创建并入队。
 */
import type { WorkflowEntry } from './workflow.ts'
import type { OutputLayout, OutputNamingConfig, SeparationTask } from './task.ts'

type Deps = Record<string, any>

export function makeTaskQueue(deps: Deps) {
  const {
    useSettingsStore, windowsPaths, resolveOutputDirectory, joinOutputPath,
    inputStemSegment, createRunId, STAGE_META, localLogRecord,
    buildRunConfig, buildWorkflowRunConfig, tasks, rememberSessionTasks,
    activeTaskId, queuePersist,
  } = deps

function resolveTaskOutputPath(jobOutput: string, input: string, resultId: string, outputLayout: OutputLayout) {
    return outputLayout === 'flat'
      ? jobOutput
      : joinOutputPath(jobOutput, inputStemSegment(input, resultId, windowsPaths.value), windowsPaths.value)
  }

  function createQueuedTask(input: string, model: string, inferenceParams: Record<string, unknown>, modelType?: string | null, jobId?: string, jobOutput?: string, outputLayout: OutputLayout = 'folders', outputNaming?: OutputNamingConfig, batchIndex?: number) {
    const settings = useSettingsStore()
    const id = createRunId('sep')
    const resultId = createRunId('result')
    const resolvedJobOutput = jobOutput || resolveOutputDirectory(settings.outputDir, undefined, windowsPaths.value)
    const task: SeparationTask = {
      id,
      jobId,
      resultId,
      ...(typeof batchIndex === 'number' && Number.isSafeInteger(batchIndex) && batchIndex >= 0 ? { batchIndex } : {}),
      model,
      input,
      jobOutput: resolvedJobOutput,
      output: resolveTaskOutputPath(resolvedJobOutput, input, resultId, outputLayout),
      status: 'queued',
      message: 'Queued',
      createdAt: Date.now(),
      updatedAt: Date.now(),
      progress: STAGE_META.queued.progress,
      stageLabel: STAGE_META.queued.label,
      files: [],
      outputs: [],
      logs: [localLogRecord('Queued', 'task.queued', 'info', { taskId: id })],
      runConfig: buildRunConfig(inferenceParams, modelType, outputLayout, outputNaming),
    }
    tasks.value.unshift(task)
    rememberSessionTasks([task])
    activeTaskId.value = id
    queuePersist()
    return task
  }

  function createQueuedWorkflowTask(input: string, workflow: WorkflowEntry, jobId?: string, jobOutput?: string, outputLayout: OutputLayout = 'folders', outputNaming?: OutputNamingConfig, batchIndex?: number) {
    const settings = useSettingsStore()
    const id = createRunId('wf')
    const resultId = createRunId('result')
    const resolvedJobOutput = jobOutput || resolveOutputDirectory(settings.outputDir, undefined, windowsPaths.value)
    const task: SeparationTask = {
      id,
      jobId,
      resultId,
      ...(typeof batchIndex === 'number' && Number.isSafeInteger(batchIndex) && batchIndex >= 0 ? { batchIndex } : {}),
      model: workflow.name,
      input,
      jobOutput: resolvedJobOutput,
      output: resolveTaskOutputPath(resolvedJobOutput, input, resultId, outputLayout),
      status: 'queued',
      message: 'Queued',
      createdAt: Date.now(),
      updatedAt: Date.now(),
      progress: STAGE_META.queued.progress,
      stageLabel: STAGE_META.queued.label,
      files: [],
      outputs: [],
      logs: [localLogRecord(`Queued workflow: ${workflow.name}`, 'task.queued', 'info', { taskId: id })],
      runConfig: buildWorkflowRunConfig(workflow, outputLayout, outputNaming),
    }
    tasks.value.unshift(task)
    rememberSessionTasks([task])
    activeTaskId.value = id
    queuePersist()
    return task
  }

  return { resolveTaskOutputPath, createQueuedTask, createQueuedWorkflowTask }
}
