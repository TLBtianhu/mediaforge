// DebugView 的执行日志：下载快照、日志/错误派生、返回任务与诊断开关。
import type { DownloadTask } from '@/stores/model'

type Deps = Record<string, any>

export function makeExecutionLog(deps: Deps) {
  const diagnosticDetailsOpen = deps.ref(false)
  const executionLogVisible = deps.ref(false)
  const executionLogTaskId = deps.ref('')
  const executionDownloadSnapshot = deps.ref(null)

  const executionLogTask = deps.computed(() => deps.task.tasks.find((item: any) => item.id === executionLogTaskId.value))
  const liveExecutionDownload = deps.computed(() => (Object.values(deps.downloadTasks.value) as DownloadTask[]).find(item => item.taskId === executionLogTaskId.value))
  deps.watch(liveExecutionDownload, (current: DownloadTask | undefined, previous: DownloadTask | undefined) => {
    if (current) executionDownloadSnapshot.value = current
    else if (previous && previous.taskId === executionLogTaskId.value && deps.modelStore.downloadStates[previous.model] === 'done') {
      executionDownloadSnapshot.value = { ...previous, status: 'done', progress: 100, speedBytesPerSecond: 0 }
    }
  }, { flush: 'sync' })
  const executionLogDownload = deps.computed(() => liveExecutionDownload.value || executionDownloadSnapshot.value)
  const executionLogName = deps.computed(() => executionLogTask.value?.runConfig?.workflowName || executionLogTask.value?.model || executionLogDownload.value?.model || executionLogTaskId.value)
  const executionLogs = deps.computed(() => executionLogTask.value?.logs || (executionLogDownload.value?.logs || []).map((entry: any) => entry.record || deps.normalizeLogRecord({
    message: entry.message, level: entry.level, timestamp: Number.isFinite(entry.ts) ? new Date(entry.ts).toISOString() : '',
    source: 'worker', taskId: executionLogTaskId.value,
  })))
  const executionNodeLogs = deps.computed(() => executionLogTask.value?.nodeLogs || {})
  const executionLogError = deps.computed(() => executionLogTask.value?.error
    || (executionLogTask.value?.status === 'failed' ? executionLogTask.value.message : '')
    || executionLogDownload.value?.errorMessage
    || (executionLogDownload.value?.status === 'error' ? executionLogDownload.value.message : '')
    || '')
  const canReturnToTask = deps.computed(() => executionLogTaskId.value && (executionLogTask.value || executionLogDownload.value))
  const diagnosticSummary = deps.computed(() => deps.summarizeDiagnosticChecks(deps.diagnostics.value))

  deps.watch([deps.developerMode, () => diagnosticSummary.value.state], ([enabled, state]: any[], [wasEnabled]: any) => {
    if (!enabled) diagnosticDetailsOpen.value = false
    else if (!wasEnabled) diagnosticDetailsOpen.value = state === 'error' || state === 'warn'
    else if (state === 'error' || state === 'warn') diagnosticDetailsOpen.value = true
  })

  function showExecutionLogs(taskId: string) {
    executionLogTaskId.value = taskId
    executionDownloadSnapshot.value = (Object.values(deps.downloadTasks.value) as DownloadTask[]).find(item => item.taskId === taskId)
      || (executionDownloadSnapshot.value?.taskId === taskId ? executionDownloadSnapshot.value : null)
    executionLogVisible.value = true
  }

  return {
    diagnosticDetailsOpen, executionLogVisible, executionLogTaskId, executionDownloadSnapshot,
    executionLogTask, liveExecutionDownload, executionLogDownload, executionLogName, executionLogs,
    executionNodeLogs, executionLogError, canReturnToTask, diagnosticSummary, showExecutionLogs,
  }
}
