import type { SeparationTask } from './task.ts'
import { isTerminalTaskStatus, taskJobId } from '../features/tasks/lifecycle.ts'

/** Result removal includes the whole batch and must never race an active sibling. */
export function getRemovalTasks(sourceTasks: readonly SeparationTask[], ids: readonly string[], runningMessage: string): SeparationTask[] {
  const selected = new Set(ids)
  if ([...selected].some(id => !sourceTasks.some(task => task.id === id))) throw new Error(runningMessage)
  const jobs = new Set(sourceTasks.filter(task => selected.has(task.id)).map(taskJobId))
  const related = sourceTasks.filter(task => jobs.has(taskJobId(task)))
  if (related.some(task => !isTerminalTaskStatus(task.status))) throw new Error(runningMessage)
  return related
}

export function primaryRevealPath(task: SeparationTask): string {
  return task.runConfig?.workbench ? task.outputs[0]?.path || task.output : task.output
}
