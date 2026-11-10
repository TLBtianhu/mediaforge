import type { Ref } from 'vue'
import type { SeparationTask } from '@/stores/taskTypes'
import { isTerminalTaskStatus } from '@/features/tasks/lifecycle'

type Deps = {
  tasks: Ref<SeparationTask[]>
  focusedTaskId: Ref<string | null>
  focusedResultTaskId: Ref<string | null>
  getRemovalTasksFromState: (tasks: SeparationTask[], ids: string[], message: string) => SeparationTask[]
  i18n: { global: { t: (key: string) => string } }
  removeAudioJobs: (ids: string[], options: { deleteFiles?: boolean; checkOnly?: boolean }) => Promise<any>
  removeResults: (ids: string[]) => Promise<{ removedJobIds: string[] }>
}

export function makeTaskResultHelpers({
  tasks, focusedTaskId, focusedResultTaskId, getRemovalTasksFromState, i18n, removeAudioJobs, removeResults,
}: Deps) {
  function getTaskById(id: string) {
    return tasks.value.find((task) => task.id === id) || null
  }

  function focusResultTask(id: string | null) {
    focusedResultTaskId.value = id
  }

  function focusTask(id: string | null) {
    focusedTaskId.value = id
  }

  function hasResultPresence(task: SeparationTask) {
    return isTerminalTaskStatus(task.status)
      && (task.outputs.length > 0 || task.files.length > 0)
  }

  function getRemovalTasks(ids: string[]) {
    return getRemovalTasksFromState(tasks.value, ids, i18n.global.t('results.removeRunningBatch'))
  }

  async function checkRemoval(ids: string[], deleteFiles = false) {
    const related = getRemovalTasks(ids)
    const response = await removeAudioJobs(related.map(task => task.id), { deleteFiles, checkOnly: true })
    if (response.checkedJobIds.length !== related.length || related.some(task => !response.checkedJobIds.includes(task.id))) throw new Error('Result removal preflight did not confirm the exact group')
    return response
  }

  async function removeResult(id: string) {
    return (await removeResults([id])).removedJobIds.length
  }

  return { getTaskById, focusResultTask, focusTask, hasResultPresence, getRemovalTasks, checkRemoval, removeResult }
}
