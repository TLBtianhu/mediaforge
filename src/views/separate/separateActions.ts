import { nextTick, type Ref } from 'vue'
import type { SeparationJob } from '@/stores/task'

function isSeparationJob(job: SeparationJob) {
  return !job.primary.runConfig?.workbench
}

type Deps = {
  t: (key: string, params?: Record<string, unknown>) => string
  message: { success: (text: string) => void; error: (text: string) => void }
  dialog: any
  task: any
  router: any
  currentTask: Ref<any>
  currentBatchTasks: Ref<any[]>
  cancellingTaskId: Ref<string | null>
  removingRunRecords: Ref<boolean>
  showLogModal: Ref<boolean>
  focusedSeparationJobId: Ref<string | null>
  composingNewJob: Ref<boolean>
  stopAllPreviewAudio: () => void
  focusQueueJob: (job: SeparationJob) => boolean
  cancelQueueJob: (job: SeparationJob) => void
  isNoResultSeparationJob: (job: SeparationJob) => boolean
  isTerminalTaskStatus: (status: any) => boolean
  getFileName: (path: string) => string
}

/** Queue and terminal actions that only need refs, the task store and dialogs. */
export function makeSeparateActions({
  t, message, dialog, task, router, currentTask, currentBatchTasks,
  cancellingTaskId, removingRunRecords, showLogModal, focusedSeparationJobId,
  composingNewJob, stopAllPreviewAudio, focusQueueJob, cancelQueueJob,
  isNoResultSeparationJob, isTerminalTaskStatus, getFileName,
}: Deps) {
  function beginNextSeparation() {
    stopAllPreviewAudio()
    showLogModal.value = false
    focusedSeparationJobId.value = null
    composingNewJob.value = true
  }

  function viewQueueJobLogs(target: SeparationJob) {
    if (!focusQueueJob(target)) return
    void nextTick(() => { showLogModal.value = true })
  }

  function removeSeparationRunRecords(jobs: SeparationJob[]) {
    if (removingRunRecords.value || !jobs.length || jobs.some(job => !isNoResultSeparationJob(job))) return
    const ids = jobs.map(job => job.id)
    dialog.warning({
      title: t('workbench.removeRun'), content: t('separate.removeRunRecordsContent', { count: ids.length }),
      positiveText: t('results.removeAction'), negativeText: t('common.cancel'), positiveButtonProps: { type: 'error' },
      onPositiveClick: async () => {
        removingRunRecords.value = true
        try {
          await task.removeSeparationRuns(ids)
          if (focusedSeparationJobId.value && ids.includes(focusedSeparationJobId.value)) {
            showLogModal.value = false
            focusedSeparationJobId.value = null
          }
        } catch (error) { message.error(error instanceof Error ? error.message : String(error)); return false }
        finally { removingRunRecords.value = false }
      },
    })
  }

  function queueJobInputSummary(job: SeparationJob) {
    const first = getFileName(job.primary.input)
    return job.inputCount > 1
      ? t('separate.queueBatchInputs', { count: job.inputCount, name: first })
      : first
  }

  function requestCancelQueueJob(job: SeparationJob) {
    if (!isSeparationJob(job)) return
    dialog.warning({
      title: t('separate.cancelBatchTitle'),
      content: t('separate.cancelBatchHint', { name: job.model }),
      positiveText: t('tasks.cancelAction'),
      negativeText: t('common.cancel'),
      positiveButtonProps: { type: 'error' },
      onPositiveClick: () => cancelQueueJob(job),
    })
  }

  function goToResults() {
    router.push('/results')
  }

  function openCurrentLogs() {
    if (!currentTask.value) return
    showLogModal.value = true
  }

  function handleCompletedAction(key: string | number) {
    if (key === 'retry') void retryCurrentTask()
    else if (key === 'logs') openCurrentLogs()
  }

  function handleCancelCurrentTask() {
    const targets = currentBatchTasks.value.filter(item => !isTerminalTaskStatus(item.status))
    if (!targets.length) return
    dialog.warning({
      title: t('tasks.cancelConfirmTitle'),
      content: t('tasks.cancelConfirmContent'),
      positiveText: t('tasks.cancelAction'),
      negativeText: t('common.cancel'),
      positiveButtonProps: { type: 'error' },
      negativeButtonProps: { secondary: true },
      onPositiveClick: async () => {
        if (cancellingTaskId.value) return
        cancellingTaskId.value = targets.length > 1 ? 'batch' : targets[0].id
        try {
          const results = await Promise.all(targets.map(item => task.cancelTask(item.id)))
          if (results.some(Boolean)) message.success(t('tasks.cancelSuccess'))
        } catch (error) {
          message.error(error instanceof Error ? error.message : String(error))
        } finally {
          cancellingTaskId.value = null
        }
      },
    })
  }

  async function retryCurrentTask() {
    const item = currentTask.value
    if (!item) return
    dialog.warning({
      title: t('tasks.confirmRetryTitle'),
      content: t('tasks.confirmRetry'),
      positiveText: t('common.retry'),
      negativeText: t('common.cancel'),
      negativeButtonProps: { secondary: true },
      onPositiveClick: async () => {
        stopAllPreviewAudio()
        try {
          const next = await task.retryTask(item.id)
          focusedSeparationJobId.value = next?.jobId || next?.id || focusedSeparationJobId.value
          message.success(t('toast.taskRetried'))
        } catch (error) {
          message.error(error instanceof Error ? error.message : String(error))
        }
      },
    })
  }

  return {
    beginNextSeparation, viewQueueJobLogs, removeSeparationRunRecords, queueJobInputSummary,
    requestCancelQueueJob, goToResults, openCurrentLogs, handleCompletedAction,
    handleCancelCurrentTask, retryCurrentTask,
  }
}
