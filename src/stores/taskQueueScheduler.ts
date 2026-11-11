import type { Ref } from 'vue'
import { isTerminalTaskStatus, selectQueuedJobGroups, taskJobId } from '@/features/tasks/lifecycle'
import { isTauriRuntime } from '@/utils/appStore'
import { isMainTaskOwner } from '@/utils/taskOwner'
import { JOURNAL_POLL_INTERVAL_MS } from '@/stores/mediaJobs'
import type { SeparationTask } from '@/stores/taskTypes'

type Deps = {
  tasks: Ref<SeparationTask[]>
  initialized: Ref<boolean>
  loadError: Ref<string>
  journal: {
    error: string | null
    refresh: () => Promise<void>
  }
  acceptedGroupIds: Set<string>
  inFlightGroupIds: Set<string>
  retryDispatchAfter: Map<string, number>
  invoke: <T>(command: string, args?: Record<string, unknown>) => Promise<T>
  syncJournalTasks: () => void
  studioLog: { warn: (message: string, error: unknown) => void }
}

/** Admission and scheduling for queued audio jobs. */
export function makeTaskQueueScheduler({
  tasks, initialized, loadError, journal, acceptedGroupIds, inFlightGroupIds,
  retryDispatchAfter, invoke, syncJournalTasks, studioLog,
}: Deps) {
  let schedulingSuppressed = 0
  let schedulingQueue = false
  let queueScheduleRequested = false

  async function startQueuedTask(taskId: string) {
    const task = tasks.value.find(item => item.id === taskId)
    if (!task || task.status !== 'queued') return false
    return startQueuedGroup([task])
  }

  async function startQueuedGroup(group: SeparationTask[]) {
    if (!group.length) return false
    const groupId = taskJobId(group[0])
    if (acceptedGroupIds.has(groupId) || inFlightGroupIds.has(groupId) || (retryDispatchAfter.get(groupId) || 0) > Date.now()) return false
    inFlightGroupIds.add(groupId)
    const primary = group[0].id
    try {
      await invoke(group[0].runConfig?.runMode === 'workflow' ? 'start_workflow_inference' : 'start_separation', { payload: { jobId: primary } })
      // A queued ACK means the backend owns the waiter, not that a worker started.
      acceptedGroupIds.add(groupId)
      retryDispatchAfter.delete(groupId)
      await journal.refresh()
      if (journal.error) throw new Error(journal.error)
      syncJournalTasks()
      return true
    } catch (error) {
      // A queued record predates start, so a lost ACK is ambiguous. Retrying the
      // idempotent start is safe, but only after the resident polling interval.
      retryDispatchAfter.set(groupId, Date.now() + JOURNAL_POLL_INTERVAL_MS)
      studioLog.warn('Failed to start an enqueued audio job', error)
      await journal.refresh()
      syncJournalTasks()
      if (tasks.value.some(task => taskJobId(task) === groupId && task.status !== 'queued' && !isTerminalTaskStatus(task.status))) acceptedGroupIds.add(groupId)
      return false
    } finally {
      inFlightGroupIds.delete(groupId)
    }
  }

  function scheduleQueue() {
    // Browser-only previews cannot start a Rust worker. Keep restored queue items
    // intact until the desktop runtime is available instead of failing them on boot.
    if (!isTauriRuntime() || !initialized.value || loadError.value || journal.error || schedulingSuppressed) return
    if (!isMainTaskOwner()) return
    if (schedulingQueue) {
      queueScheduleRequested = true
      return
    }
    schedulingQueue = true
    try {
      do {
        queueScheduleRequested = false
        // Admission and the shared UI/MCP/media cap belong to the backend. Submit
        // every unaccepted group once; a batch still maps to one backend waiter.
        const eligible = tasks.value.filter(task => task.status !== 'queued' || !acceptedGroupIds.has(taskJobId(task))
          && !inFlightGroupIds.has(taskJobId(task)) && (retryDispatchAfter.get(taskJobId(task)) || 0) <= Date.now())
        const nextJobs = selectQueuedJobGroups(eligible, Math.max(1, tasks.value.length))
        nextJobs.forEach((jobTasks) => {
          if (jobTasks.length === 1) {
            void startQueuedTask(jobTasks[0].id)
            return
          }
          void startQueuedGroup(jobTasks)
        })
        // Synchronous validation failures request another pass after the current selection is consumed.
      } while (queueScheduleRequested)
    } finally {
      schedulingQueue = false
    }
  }

  return {
    scheduleQueue,
    startQueuedTask,
    startQueuedGroup,
    suppressScheduling: () => { schedulingSuppressed++ },
    resumeScheduling: () => { schedulingSuppressed-- },
  }
}
