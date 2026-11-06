import { isTerminalTaskStatus, resolveJobStatus, sortJobTasks, taskJobId } from '../features/tasks/lifecycle.ts'
import type { SeparationJob, SeparationTask } from './task.ts'

export function buildJobs(sourceTasks: SeparationTask[]): SeparationJob[] {
  const groups = new Map<string, SeparationTask[]>()
  sourceTasks.forEach(task => {
    const id = taskJobId(task)
    groups.set(id, [...(groups.get(id) || []), task])
  })
  return [...groups.entries()].map(([id, items]) => {
    const sorted = sortJobTasks(items)
    const primary = sorted[0]
    const output = primary.jobOutput || primary.output
    const progressTotal = sorted.reduce((sum, item) => {
      if (isTerminalTaskStatus(item.status)) return sum + 100
      if (item.status === 'queued') return sum
      return sum + Math.max(0, Math.min(99, Number(item.progress || 0)))
    }, 0)
    const status = resolveJobStatus(sorted)
    const startedValues = sorted.map(item => item.startedAt || item.createdAt).filter((value): value is number => typeof value === 'number')
    const finishedValues = sorted.map(item => item.finishedAt || item.updatedAt).filter((value): value is number => typeof value === 'number')
    const startedAt = startedValues.length ? Math.min(...startedValues) : undefined
    const finishedAt = isTerminalTaskStatus(status) && finishedValues.length ? Math.max(...finishedValues) : undefined
    return {
      id,
      output,
      tasks: sorted,
      primary,
      model: primary.model,
      inputCount: sorted.length,
      outputCount: sorted.reduce((sum, item) => sum + item.outputs.length, 0),
      createdAt: Math.min(...sorted.map(item => item.createdAt)),
      updatedAt: Math.max(...sorted.map(item => item.updatedAt)),
      startedAt,
      finishedAt,
      durationMs: startedAt && finishedAt ? Math.max(0, finishedAt - startedAt) : undefined,
      status,
      progress: Math.round(progressTotal / sorted.length),
    }
  })
}

export function findJobById(jobs: SeparationJob[], id: string | null | undefined): SeparationJob | null {
  if (!id) return null
  return jobs.find(job => job.id === id) || null
}
