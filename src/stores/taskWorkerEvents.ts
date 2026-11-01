/**
 * Worker 事件归约：任务进度、节点生命周期、日志、失败与终态。
 *
 * 运行时依赖全部由 store 注入，便于离线契约测试，不在模块内创建单例。
 */
import type { TaskStatus } from '../features/tasks/lifecycle.ts'
import type { StemOutput } from '../utils/stemOutputs.ts'
import type { SeparationTask } from './task.ts'

type Deps = Record<string, any>

function clamp(value: number, minimum: number, maximum: number) {
  return Math.max(minimum, Math.min(maximum, value))
}

export function makeTaskWorkerEventHandler(deps: Deps) {
  const { tasks, setTaskStatus, normalizeStatus, STAGE_META, isTerminalTaskStatus,
    markTaskStarted, markTaskFinished, touch, queueProgressPersist, queuePersist, scheduleQueue,
    appendWorkflowNodeLogs, localLogRecord, workerLogRecord, normalizeLogRecord, appendTaskLogs,
    useSettingsStore, normalizeStemOutputs, outputsFromFiles, windowsPaths, resolveTaskErrorMessage, } = deps

  function resolveStageProgress(status: TaskStatus, current?: number, total?: number) {
    const meta = STAGE_META[status]
    if (status !== 'separating') return meta.progress
    if (
      typeof current !== 'number'
      || typeof total !== 'number'
      || !Number.isFinite(current)
      || !Number.isFinite(total)
      || total <= 0
    ) {
      return meta.progress
    }
    return Math.round(clamp(current / total, 0, 1) * 100)
  }

  function handleWorkerEvent(event: any) {
    const taskId = event?.taskId
    if (!taskId) return
    const task = tasks.value.find((item: SeparationTask) => item.id === taskId)
    if (!task) return
    if (event.type === 'task.started') {
      setTaskStatus(taskId, 'preparing', 'Task started', 6)
    } else if (event.type === 'task.stage') {
      const stage = normalizeStatus(event.payload?.stage)
      setTaskStatus(taskId, stage, event.payload?.message || STAGE_META[stage].label, event.payload?.progress)
    } else if (event.type === 'workflow_execution') {
      // Native graph execution supplies explicit lifecycle events. Do not infer
      // node completion from a progress fraction: output collection still runs
      // after execution_completed, and task.done remains the task terminal event.
      if (isTerminalTaskStatus(task.status) || task.runConfig?.runMode !== 'workflow') return
      const payload = event.payload || {}
      const executionEvent = typeof payload.event === 'string' ? payload.event : ''
      const knownExecutionEvent = [
        'execution_started', 'execution_completed', 'execution_failed', 'execution_cancelled',
        'node_started', 'node_progress', 'node_completed', 'node_failed', 'node_cancelled', 'node_skipped',
      ].includes(executionEvent)
      if (payload.version !== 1 || !knownExecutionEvent) return
      const nodeEvent = executionEvent.startsWith('node_')
      const nodeId = typeof payload.nodeId === 'string' && payload.nodeId.trim() ? payload.nodeId : undefined
      const message = typeof payload.message === 'string' ? payload.message : ''
      const fraction = typeof payload.overallFraction === 'number' && Number.isFinite(payload.overallFraction)
        ? clamp(payload.overallFraction, 0, 1) : undefined
      task.status = 'separating'
      markTaskStarted(task)
      task.stageLabel = STAGE_META.separating.label
      task.message = message || STAGE_META.separating.label
      if (fraction !== undefined) task.progress = Math.max(task.progress || 0, Math.min(90, 35 + fraction * 55))
      const done = Number(payload.done)
      const total = Number(payload.total)
      if (payload.unit === 'seconds' && Number.isFinite(done) && Number.isFinite(total)) {
        task.progressCurrent = done
        task.progressTotal = total
        task.progressDetail = message || undefined
      } else {
        task.progressCurrent = undefined
        task.progressTotal = undefined
        task.progressDetail = undefined
      }
      if (nodeId && nodeEvent) {
        task.nodeStates ||= {}
        const previous = task.nodeStates[nodeId]
        if (!previous) (task.nodeExecutionOrder ||= []).push(nodeId)
        const nodeFraction = typeof payload.nodeProgress === 'number' && Number.isFinite(payload.nodeProgress)
          ? clamp(payload.nodeProgress, 0, 1) : undefined
        const nodeStatus = executionEvent === 'node_completed' ? 'done'
          : executionEvent === 'node_failed' ? 'failed'
            : executionEvent === 'node_cancelled' ? 'cancelled'
              : executionEvent === 'node_skipped' ? 'skipped'
                : 'running'
        const progress = nodeStatus === 'done' ? 100
          : nodeFraction === undefined ? (previous?.progress || 0) : nodeFraction * 100
        task.nodeStates[nodeId] = { status: nodeStatus, progress, ...(message ? { message } : {}) }
        if (nodeStatus === 'running' || nodeStatus === 'failed') task.currentNodeId = nodeId
        else if (task.currentNodeId === nodeId) task.currentNodeId = undefined
        if (message && ['node_started', 'node_completed', 'node_failed', 'node_cancelled', 'node_skipped'].includes(executionEvent)) {
          const prefix = executionEvent === 'node_completed' ? 'Node completed: '
            : executionEvent === 'node_failed' ? 'Node failed: '
              : executionEvent === 'node_cancelled' ? 'Node cancelled: '
                : executionEvent === 'node_skipped' ? 'Node skipped: ' : 'Node started: '
          appendWorkflowNodeLogs(task, nodeId, localLogRecord(`${prefix}${message}`, executionEvent, nodeStatus === 'failed' ? 'error' : 'info', { taskId: task.id, nodeId }))
        }
      }
      touch(task)
      queueProgressPersist()
    } else if (event.type === 'task.progress') {
      // Terminal states are monotonic. A worker may flush a progress line after
      // cancellation or failure has already been observed; never resurrect it.
      if (isTerminalTaskStatus(task.status)) return
      const stage = normalizeStatus(event.payload?.stage)
      const done = Number(event.payload?.done)
      const total = Number(event.payload?.total)
      const detail = typeof event.payload?.message === 'string' ? event.payload.message : undefined
      const nodeId = typeof event.payload?.nodeId === 'string' ? event.payload.nodeId : undefined
      const nodeProgress = event.payload?.nodeProgress
      if (nodeId && typeof nodeProgress === 'number' && Number.isFinite(nodeProgress)) {
        const fraction = clamp(nodeProgress, 0, 1)
        task.nodeStates ||= {}
        const previous = task.nodeStates[nodeId]
        if (!previous) (task.nodeExecutionOrder ||= []).push(nodeId)
        const complete = fraction >= 1 && event.payload?.audioIndex == null && event.payload?.unit == null
        const status = complete ? 'done' : 'running'
        if (detail && (!previous || previous.status !== status || previous.message !== detail)) {
          const transition = status === 'done' && previous?.status !== 'done' ? 'Node completed: ' : !previous ? 'Node started: ' : ''
          appendWorkflowNodeLogs(task, nodeId, localLogRecord(`${transition}${detail}`, `node.${status}`, 'info', { taskId: task.id, nodeId }))
        }
        task.nodeStates[nodeId] = { status, progress: fraction * 100, message: detail }
        task.currentNodeId = complete ? undefined : nodeId
      }
      task.status = stage
      if (stage !== 'queued') markTaskStarted(task)
      task.stageLabel = STAGE_META[stage].label
      task.message = detail || STAGE_META[stage].label
      task.progressCurrent = Number.isFinite(done) ? done : undefined
      task.progressTotal = Number.isFinite(total) ? total : undefined
      task.progressDetail = detail
      const explicitProgress = event.payload?.progress
      const resolvedProgress = typeof explicitProgress === 'number' && Number.isFinite(explicitProgress)
        ? clamp(explicitProgress, 0, 99)
        : Math.min(99, resolveStageProgress(stage, task.progressCurrent, task.progressTotal))
      task.progress = stage === 'separating'
        ? resolvedProgress
        : Math.max(task.progress || 0, resolvedProgress)
      touch(task)
      queueProgressPersist()
    } else if (event.type === 'task.log' || event.type === 'worker_log') {
      const record = workerLogRecord(event)
      if ((record.level === 'debug' || record.level === 'trace') && !useSettingsStore().developerMode) return
      appendTaskLogs(task, record)
      appendWorkflowNodeLogs(task, record.nodeId, record)
    } else if (event.type === 'error') {
      if (isTerminalTaskStatus(task.status)) return
      if (task.runConfig?.workbench && Array.isArray(event.payload?.outputs)) {
        task.outputs = normalizeStemOutputs(event.payload.outputs, task.input, windowsPaths.value)
        task.files = task.outputs.map((output: StemOutput) => output.path)
      }
      if (typeof event.payload?.nodeId === 'string' && task.nodeStates?.[event.payload.nodeId]) {
        task.nodeStates[event.payload.nodeId].status = 'failed'
      }
      if (task.currentNodeId && task.nodeStates?.[task.currentNodeId]) {
        task.nodeStates[task.currentNodeId].status = 'failed'
      }
      const codeValue = event.payload?.code
      const code = codeValue ? `[${codeValue}] ` : ''
      const message = resolveTaskErrorMessage(codeValue, event.payload?.message, task.input)
      const detail = event.payload?.detail || ''
      const errorRecord = event.payload?.logRecord ? workerLogRecord(event) : normalizeLogRecord({
        schemaVersion: 1, id: `${task.id}-error-${task.logs.length}`, timestamp: event.timestamp || new Date().toISOString(),
        source: 'worker', level: 'error', logger: 'worker', event: 'task.failed', message: `${code}${message}`,
        detail: String(detail), taskId: task.id, nodeId: event.payload?.nodeId || task.currentNodeId, context: {},
      })
      appendWorkflowNodeLogs(task, typeof event.payload?.nodeId === 'string' ? event.payload.nodeId : task.currentNodeId,
        errorRecord)
      const recoverable = Boolean(event.payload?.recoverable)
      task.error = message
      task.message = message
      task.progressCurrent = undefined
      task.progressTotal = undefined
      task.progressDetail = undefined
      if (recoverable) {
        task.stageLabel = task.stageLabel || STAGE_META.failed.label
        task.progress = Math.min(99, Math.max(task.progress || 0, 1))
      } else {
        task.status = 'failed'
        task.stageLabel = STAGE_META.failed.label
        task.progress = 100
      }
      if (!recoverable) markTaskFinished(task)
      appendTaskLogs(task, errorRecord)
    } else if (event.type === 'task.result') {
      if (isTerminalTaskStatus(task.status)) return
      task.status = 'done'
      markTaskStarted(task)
      task.message = 'Done'
      task.stageLabel = STAGE_META.done.label
      task.progress = 100
      task.progressCurrent = undefined
      task.progressTotal = undefined
      task.progressDetail = undefined
      task.files = event.payload?.files || []
      if (Array.isArray(event.payload?.nodeValues)) task.nodeValues = event.payload.nodeValues
      if (event.payload?.outputDir) {
        task.output = event.payload.outputDir
      }
      if (task.runConfig?.workbench && Array.isArray(event.payload?.outputDirectories)) {
        task.outputDirectories = event.payload.outputDirectories.filter((path: unknown): path is string => typeof path === 'string' && Boolean(path.trim()))
        if (task.outputDirectories?.length) task.output = task.outputDirectories[0]
      }
      if (typeof event.payload?.outputRoot === 'string' && event.payload.outputRoot) {
        task.jobOutput = event.payload.outputRoot
      } else if (task.runConfig?.outputLayout === 'flat' && event.payload?.outputDir) {
        task.jobOutput = event.payload.outputDir
      }
      const normalizedOutputs = normalizeStemOutputs(event.payload?.outputs, task.input, windowsPaths.value)
      task.outputs = normalizedOutputs.length
        ? normalizedOutputs
        : outputsFromFiles(task.output, task.files, event.payload?.outputFormat || 'wav', task.outputPrefix, task.input, windowsPaths.value)
      task.error = undefined
      touch(task)
      markTaskFinished(task, task.updatedAt)
      queuePersist()
    } else if (event.type === 'task.cancelled') {
      if (isTerminalTaskStatus(task.status)) return
      if (task.currentNodeId && task.nodeStates?.[task.currentNodeId]) task.nodeStates[task.currentNodeId].status = 'cancelled'
      appendWorkflowNodeLogs(task, task.currentNodeId, localLogRecord(event.payload?.message || 'Cancelled', 'task.cancelled', 'info', { taskId: task.id, nodeId: task.currentNodeId }))
      task.status = 'cancelled'
      markTaskStarted(task)
      task.message = event.payload?.message || 'Cancelled'
      task.stageLabel = STAGE_META.cancelled.label
      task.progress = 100
      task.progressCurrent = undefined
      task.progressTotal = undefined
      task.progressDetail = undefined
      touch(task)
      markTaskFinished(task, task.updatedAt)
      queuePersist()
    }
    if (isTerminalTaskStatus(task.status)) scheduleQueue()
  }

  return handleWorkerEvent
}
