/**
 * 运行配置规范化：输出布局、输出命名、runConfig 的版本化默认值与旧参数兼容。
 *
 * 纯函数，不碰 store 状态；类型从 task.ts 引入（import type 擦除，无运行时循环）。
 */
import type { OutputLayout, OutputNamingConfig, SeparationRunConfig, SeparationTask } from './task.ts'
import type { TaskStatus } from '../features/tasks/lifecycle.ts'
import { isInterruptedTaskStatus, TERMINAL_TASK_STATUSES } from '../features/tasks/lifecycle.ts'
import { normalizeStemOutputs } from '../utils/stemOutputs.ts'
import { normalizeOutputPath, outputsFromFiles } from './taskOutputPaths.ts'

export const CURRENT_INFERENCE_PARAMS_VERSION = 3

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
  const version = next.inferenceParamsVersion || 0
  if (version >= CURRENT_INFERENCE_PARAMS_VERSION) {
    next.inferenceParamsVersion = CURRENT_INFERENCE_PARAMS_VERSION
    return next
  }

  const params = { ...(next.inferenceParams || {}) }
  const hasStandardize = Object.prototype.hasOwnProperty.call(params, 'standardize')
  const hasNormalize = Object.prototype.hasOwnProperty.call(params, 'normalize')

  if (!hasStandardize && !hasNormalize) {
    // Legacy desktop only omitted `normalize` when the old input-standardize
    // checkbox was enabled. Preserve that behavior on retry/history restore.
    params.standardize = true
    params.normalize = false
  } else if (!hasStandardize && hasNormalize) {
    // Legacy desktop used `normalize` to mean input standardization.
    params.standardize = Boolean(params.normalize)
    params.normalize = false
  } else if (hasStandardize && !hasNormalize) {
    // First compatibility pass already switched to `standardize`, but output
    // normalize had not been made explicit yet.
    params.normalize = false
  }

  next.inferenceParams = params
  next.inferenceParamsVersion = CURRENT_INFERENCE_PARAMS_VERSION
  return next
}


export const TERMINAL_STATUSES: TaskStatus[] = [...TERMINAL_TASK_STATUSES]

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
  const normalizedFiles = task.files || []
  const workerOutputs = normalizeStemOutputs(task.outputs, task.input, windows)
  const normalizedOutputs = workerOutputs.length
    ? workerOutputs
    : outputsFromFiles(normalizedOutput, normalizedFiles, normalizedRunConfig?.outputFormat || 'wav', task.outputPrefix, task.input, windows)
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
    progress: TERMINAL_STATUSES.includes(status) ? 100 : progress,
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
