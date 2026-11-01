// SeparateView 的任务队列：分离任务筛选、各状态列表、聚焦与取消。
import { isTerminalTaskStatus } from '../../features/tasks/lifecycle.ts'
import type { SeparationJob } from '@/stores/task'

type Deps = Record<string, any>

export function makeQueue(deps: Deps) {
  function isSeparationJob(job: SeparationJob) {
    return !job.primary.runConfig?.workbench
  }
  function isNoResultSeparationJob(job: SeparationJob) {
    return isSeparationJob(job) && job.tasks.every(item => isTerminalTaskStatus(item.status))
      && !job.tasks.some((item: SeparationJob['tasks'][number]) => item.status === 'done' && !item.resultHidden && (item.outputs.length || item.files.length))
  }
  const separationJobs = deps.computed(() => deps.task.allJobs.filter(isSeparationJob))
  const separationSessionJobs = deps.computed(() => deps.task.sessionJobs.filter(isSeparationJob))
  const noResultSeparationJobs = deps.computed(() => separationJobs.value.filter(isNoResultSeparationJob)
    .sort((left: SeparationJob, right: SeparationJob) => right.updatedAt - left.updatedAt))
  const newestRunningJob = deps.computed(() => {
    return [...separationJobs.value]
      .filter((job: SeparationJob) => job.tasks.some((item: SeparationJob['tasks'][number]) => !isTerminalTaskStatus(item.status)))
      .sort((a, b) => b.createdAt - a.createdAt)[0] || null
  })
  const backgroundRunningJobs = deps.computed(() => separationSessionJobs.value
    .filter((job: SeparationJob) => !['queued', 'done', 'failed', 'cancelled'].includes(job.status))
    .sort((left: SeparationJob, right: SeparationJob) => right.updatedAt - left.updatedAt))
  const backgroundQueuedJobs = deps.computed(() => separationSessionJobs.value
    .filter((job: SeparationJob) => job.status === 'queued')
    .sort((left: SeparationJob, right: SeparationJob) => left.createdAt - right.createdAt))
  const completedSessionJobs = deps.computed(() => separationSessionJobs.value
    .filter((job: SeparationJob) => job.status === 'done')
    .sort((left: SeparationJob, right: SeparationJob) => right.updatedAt - left.updatedAt))
  const recentFailedJobs = deps.computed(() => separationSessionJobs.value
    .filter((job: SeparationJob) => job.status === 'failed')
    .sort((left: SeparationJob, right: SeparationJob) => right.updatedAt - left.updatedAt))
  const cancelledSessionJobs = deps.computed(() => separationSessionJobs.value
    .filter((job: SeparationJob) => job.status === 'cancelled')
    .sort((left: SeparationJob, right: SeparationJob) => right.updatedAt - left.updatedAt))
  const queueJobs = deps.computed(() => deps.queueScope.value === 'no-results' ? noResultSeparationJobs.value : [
    ...backgroundRunningJobs.value,
    ...backgroundQueuedJobs.value,
    ...recentFailedJobs.value,
    ...completedSessionJobs.value,
    ...cancelledSessionJobs.value,
  ])
  const focusedJob = deps.computed(() => separationJobs.value.find((job: SeparationJob) => job.id === deps.focusedSeparationJobId.value) || null)
  const currentJob = deps.computed(() => deps.composingNewJob.value ? null : (focusedJob.value || newestRunningJob.value))

  function focusQueueJob(target: SeparationJob) {
    if (!isSeparationJob(target)) return false
    deps.composingNewJob.value = false
    deps.focusedSeparationJobId.value = target.id
    deps.showQueueModal.value = false
    return true
  }
  function dismissQueueJob(target: SeparationJob) {
    if (!isSeparationJob(target)) return
    deps.task.removeSessionJob(target.id)
    if (deps.focusedSeparationJobId.value === target.id) deps.focusedSeparationJobId.value = null
  }
  function clearFinishedQueueJobs() {
    deps.task.clearFinishedSessionJobs(separationSessionJobs.value.map((job: SeparationJob) => job.id))
  }
  async function cancelQueueJob(job: SeparationJob) {
    if (!isSeparationJob(job) || deps.cancellingQueueJobId.value) return false
    const targets = job.tasks.filter((item: SeparationJob['tasks'][number]) => !isTerminalTaskStatus(item.status))
    if (!targets.length) return false
    deps.cancellingQueueJobId.value = job.id
    try {
      let cancelled = 0
      for (const item of targets) {
        // eslint-disable-next-line no-await-in-loop
        if (await deps.task.cancelTask(item.id)) cancelled += 1
      }
      return cancelled > 0 || targets.every(item => isTerminalTaskStatus(item.status))
    } finally {
      deps.cancellingQueueJobId.value = null
    }
  }

  return {
    isSeparationJob, isNoResultSeparationJob, separationJobs, separationSessionJobs,
    noResultSeparationJobs, newestRunningJob, backgroundRunningJobs, backgroundQueuedJobs,
    completedSessionJobs, recentFailedJobs, cancelledSessionJobs, queueJobs, focusedJob, currentJob,
    focusQueueJob, dismissQueueJob, clearFinishedQueueJobs, cancelQueueJob,
  }
}
