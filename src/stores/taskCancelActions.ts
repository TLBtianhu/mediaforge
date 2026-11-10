import type { Ref } from 'vue'
import type { SeparationTask } from '@/stores/taskTypes'
import { isTerminalTaskStatus, taskJobId } from '@/features/tasks/lifecycle'

type Deps = {
  tasks: Ref<SeparationTask[]>
  runningTasks: Ref<SeparationTask[]>
  journal: { refresh: () => Promise<void> }
  invoke: <T>(command: string, args?: Record<string, unknown>) => Promise<T>
  syncJournalTasks: () => void
  scheduleQueue: () => void
  suppressScheduling: () => void
  resumeScheduling: () => void
}

export function makeTaskCancelActions({
  tasks, runningTasks, journal, invoke, syncJournalTasks, scheduleQueue,
  suppressScheduling, resumeScheduling,
}: Deps) {
  async function cancelTask(id: string) {
    const task = tasks.value.find(item => item.id === id)
    if (!task || isTerminalTaskStatus(task.status)) return false
    const cancelled = await invoke<boolean>('cancel_task', { taskId: id })
    if (cancelled) {
      await journal.refresh()
      syncJournalTasks()
      scheduleQueue()
    }
    return cancelled
  }

  async function cancelAllTasks() {
    const current = [...runningTasks.value]
    let cancelled = 0
    suppressScheduling()
    try {
      for (const task of current) if (await cancelTask(task.id)) cancelled++
    } finally {
      resumeScheduling()
      scheduleQueue()
    }
    return { total: current.length, cancelled }
  }

  async function cancelWorkbenchJob(jobId: string) {
    const targets = tasks.value.filter(task => taskJobId(task) === jobId && !isTerminalTaskStatus(task.status))
    suppressScheduling()
    try {
      for (const target of targets) await invoke('cancel_task', { taskId: target.id })
      await journal.refresh()
      syncJournalTasks()
    } finally {
      resumeScheduling()
      scheduleQueue()
    }
  }

  return { cancelAllTasks, cancelTask, cancelWorkbenchJob }
}
