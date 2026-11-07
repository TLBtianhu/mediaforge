/**
 * 当前运行配置与历史文件校验；不转换旧格式或吞并未来版本。
 *
 * 纯函数，不碰 store 状态；类型从 task.ts 引入（import type 擦除，无运行时循环）。
 */
import type { OutputLayout, OutputNamingConfig, SeparationRunConfig, SeparationTask } from './task.ts'
import type { TaskStatus } from '../features/tasks/lifecycle.ts'
import { isInterruptedTaskStatus, isTerminalTaskStatus } from '../features/tasks/lifecycle.ts'
import { normalizeStemOutputs } from '../utils/stemOutputs.ts'
import { normalizeOutputPath } from './taskOutputPaths.ts'

export const CURRENT_INFERENCE_PARAMS_VERSION = 3
export const TASK_HISTORY_VERSION = 1
const record = (value: unknown): value is Record<string, unknown> => Boolean(value) && typeof value === 'object' && !Array.isArray(value)
const incompatible = (name: string): never => { throw new Error(`Unsupported ${name} schema; the original file was preserved`) }

export function readTaskHistory(value: unknown, windows = false): SeparationTask[] {
  if (value === null) return []
  if (!record(value) || value.version !== TASK_HISTORY_VERSION || !Array.isArray(value.tasks)) return incompatible('task-history')
  return value.tasks.map(item => {
    if (!record(item) || typeof item.id !== 'string' || !item.id.trim() || typeof item.status !== 'string' || !(item.status in STAGE_META)
      || !record(item.runConfig) || !Array.isArray(item.outputs) || 'files' in item || !Array.isArray(item.logs)
      || typeof item.input !== 'string' || typeof item.output !== 'string' || typeof item.model !== 'string'
      || typeof item.createdAt !== 'number' || !Number.isFinite(item.createdAt) || typeof item.updatedAt !== 'number' || !Number.isFinite(item.updatedAt)) return incompatible('task-history')
    return normalizeTask(item as Partial<SeparationTask>, windows)
  })
}

export function validateSeparateState<T>(value: T): T {
  if (value !== null && (!record(value) || value.version !== 1)) return incompatible('separate-state')
  return value
}

export function normalizeOutputLayout(value: unknown): OutputLayout {
  return value === 'flat' ? 'flat' : 'folders'
}

export function normalizeOutputNaming(value: unknown): OutputNamingConfig | undefined {
  if (!value || typeof value !== 'object') return undefined
  const raw = value as Partial<OutputNamingConfig>
  const template = typeof raw.template === 'string' ? raw.template.trim() : ''
  const stemOrder = Array.isArray(raw.stemOrder)
    ? raw.stemOrder.map(item => String(item || '').trim()).filter(Boolean)
    : []
  return {
    enabled: raw.enabled === true,
    template,
    stemOrder,
  }
}

export function normalizeRunConfig(runConfig?: SeparationRunConfig): SeparationRunConfig | undefined {
  if (!runConfig) return undefined
  if (runConfig.inferenceParamsVersion !== CURRENT_INFERENCE_PARAMS_VERSION) return incompatible('inference parameters')
  if (runConfig.workbench && runConfig.workbench.version !== 2) return incompatible('workbench snapshot')
  if (runConfig.workbench?.nativeGraph && ('sourceDefinition' in runConfig.workbench || !record(runConfig.workflowDefinition))) return incompatible('workbench definition')
  const next: SeparationRunConfig = {
    ...runConfig,
    downloadMethod: runConfig.downloadMethod || 'aria2c',
    outputLayout: normalizeOutputLayout(runConfig.outputLayout),
    outputNaming: normalizeOutputNaming(runConfig.outputNaming),
    selectedStems: Array.isArray(runConfig.selectedStems)
      ? runConfig.selectedStems.map(item => String(item || '').trim()).filter(Boolean)
      : [],
    audioParams: { ...(runConfig.audioParams || {}) },
    inferenceParams: { ...(runConfig.inferenceParams || {}) },
  }
  return next
}

export const STAGE_META: Record<TaskStatus, { progress: number; label: string }> = {
  queued: { progress: 2, label: 'Queued' },
  preparing: { progress: 8, label: 'Preparing' },
  validating_input: { progress: 12, label: 'Validating input' },
  downloading_model: { progress: 22, label: 'Preparing model' },
  ensuring_model: { progress: 22, label: 'Preparing model' },
  loading_model: { progress: 35, label: 'Loading model' },
  separating: { progress: 0, label: 'Separating' },
  writing_output: { progress: 92, label: 'Collecting outputs' },
  done: { progress: 100, label: 'Done' },
  failed: { progress: 100, label: 'Failed' },
  cancelled: { progress: 100, label: 'Cancelled' },
}

export function normalizeStatus(status: unknown): TaskStatus {
  if (typeof status !== 'string') return 'queued'
  if (status in STAGE_META) return status as TaskStatus
  return 'preparing'
}

export function normalizeTimestamp(value: unknown) {
  return typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : undefined
}

export function normalizeDurationMs(value: unknown) {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? Math.max(0, Math.round(value)) : undefined
}

export function normalizeTask(task: Partial<SeparationTask>, windows = /^[A-Za-z]:|^\\\\/.test(task.output || task.jobOutput || '')): SeparationTask {
  let status = normalizeStatus(task.status)
  let interrupted = false
  if (isInterruptedTaskStatus(status)) {
    status = 'failed'
    interrupted = true
  }
  const normalizedRunConfig = normalizeRunConfig(task.runConfig)
  const normalizedOutput = normalizeOutputPath(task.output)
  const workerOutputs = normalizeStemOutputs(task.outputs, task.input, windows)
  const normalizedFiles = workerOutputs.map(output => output.path)
  const normalizedOutputs = workerOutputs
  const meta = STAGE_META[status]
  const startedAt = normalizeTimestamp(task.startedAt)
  const finishedAt = normalizeTimestamp(task.finishedAt)
  const persistedDurationMs = normalizeDurationMs(task.durationMs)
  const durationMs = persistedDurationMs ?? (startedAt && finishedAt ? Math.max(0, finishedAt - startedAt) : undefined)
  const progress = typeof task.progress === 'number'
    ? Math.max(meta.progress, Math.min(100, task.progress))
    : meta.progress
  return {
    id: task.id || `task_${Date.now()}`,
    jobId: typeof task.jobId === 'string' && task.jobId.trim() ? task.jobId : undefined,
    resultId: typeof task.resultId === 'string' && task.resultId.trim() ? task.resultId : undefined,
    batchId: typeof task.batchId === 'string' && task.batchId.trim() ? task.batchId : undefined,
    ...(typeof task.batchIndex === 'number' && Number.isSafeInteger(task.batchIndex) && task.batchIndex >= 0 ? { batchIndex: task.batchIndex } : {}),
    model: task.model || '',
    input: task.input || '',
    jobOutput: typeof task.jobOutput === 'string' && task.jobOutput.trim() ? normalizeOutputPath(task.jobOutput) : undefined,
    outputPrefix: typeof task.outputPrefix === 'string' && task.outputPrefix.trim() ? task.outputPrefix : undefined,
    output: normalizedOutput,
    ...(Array.isArray(task.outputDirectories) ? { outputDirectories: task.outputDirectories.filter(path => typeof path === 'string' && path.trim()) } : {}),
    status,
    message: interrupted ? '上次运行未完成' : (task.message || meta.label),
    createdAt: task.createdAt || Date.now(),
    updatedAt: task.updatedAt || task.createdAt || Date.now(),
    startedAt,
    finishedAt,
    durationMs,
    progress: isTerminalTaskStatus(status) ? 100 : progress,
    stageLabel: interrupted ? '已中断' : (task.stageLabel || meta.label),
    progressCurrent: typeof task.progressCurrent === 'number' ? task.progressCurrent : undefined,
    progressTotal: typeof task.progressTotal === 'number' ? task.progressTotal : undefined,
    progressDetail: task.progressDetail || undefined,
    files: normalizedFiles,
    outputs: normalizedOutputs,
    logs: interrupted
      ? [...(task.logs || []), `${new Date().toLocaleTimeString()} 应用关闭或重启，任务已标记为中断。`].slice(-300)
      : (task.logs || []),
    error: interrupted ? '应用关闭或重启导致任务中断' : task.error,
    runConfig: normalizedRunConfig,
    nodeStates: task.nodeStates,
    nodeExecutionOrder: Array.isArray(task.nodeExecutionOrder)
      ? [...new Set(task.nodeExecutionOrder.filter(id => typeof id === 'string' && id.trim()))] : undefined,
    nodeLogs: task.nodeLogs,
    currentNodeId: interrupted ? undefined : task.currentNodeId,
    taskHidden: task.taskHidden === true,
    resultHidden: task.resultHidden === true,
  }
}
