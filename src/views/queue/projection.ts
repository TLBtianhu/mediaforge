import type { SeparationTask } from '../../stores/task.ts'

export type QueueJob = {
  jobId: string
  command: string
  status: string
  source?: 'manual' | 'agent'
  cancelTaskId?: string
  fraction?: number
  message?: string
  createdAt?: number
  finishedAt?: number | null
  payload?: Record<string, unknown>
  result?: Record<string, unknown> | null
  error?: string | null
}

export type QueueEntry = QueueJob & { cancelOwner: 'audio' | 'backend' }

export function isQueueActive(job: Pick<QueueJob, 'status'>) {
  return job.status === 'running' || job.status === 'queued'
}

export function isQueueFailed(job: Pick<QueueJob, 'status'>) {
  return job.status === 'failed' || job.status === 'interrupted'
}

function audioStatus(status: SeparationTask['status']) {
  if (status === 'done') return 'completed'
  if (status === 'queued' || status === 'failed' || status === 'cancelled') return status
  return 'running'
}

/** Audio owns pending scheduling/history; the shared journal owns media/agent work. */
export function mergeQueueJobs(records: readonly QueueJob[], audioTasks: readonly SeparationTask[]): QueueEntry[] {
  const jobs = new Map<string, QueueEntry>()
  for (const record of records) {
    if (!record.jobId) continue
    // UI audio history can be removed independently of the backend journal.
    // Reintroducing its old rows here would undo the user's clear-history action.
    if (record.source === 'manual' && ['infer', 'infer_workflow'].includes(record.command)) continue
    jobs.set(record.jobId, { ...record, cancelOwner: 'backend' })
  }
  for (const task of audioTasks) {
    const status = audioStatus(task.status)
    jobs.delete(task.id)
    if (task.taskHidden && !isQueueActive({ status })) continue
    jobs.set(task.id, {
      jobId: task.id,
      command: task.runConfig?.runMode === 'workflow' || task.runConfig?.workbench ? 'infer_workflow' : 'infer',
      status,
      source: 'manual',
      cancelOwner: 'audio',
      cancelTaskId: task.id,
      fraction: status === 'queued' ? 0 : status === 'completed' ? 1 : Math.max(0, Math.min(1, Number(task.progress || 0) / 100)),
      message: task.message || task.stageLabel || '',
      createdAt: task.createdAt / 1000,
      finishedAt: task.finishedAt == null ? null : task.finishedAt / 1000,
      payload: { input: task.input, inputs: [task.input], model: task.model, outputDir: task.output, batchId: task.jobId || task.batchId },
      result: { outputs: task.outputs, files: task.files, ...(task.nodeValues ? { nodeValues: task.nodeValues } : {}) },
      error: task.error || null,
    })
  }
  return [...jobs.values()].sort((left, right) => Number(right.createdAt || 0) - Number(left.createdAt || 0))
}

export function queueOutputCount(job: Pick<QueueJob, 'result'>) {
  const outputs = job.result?.outputs
  if (Array.isArray(outputs)) return outputs.length
  if (outputs && typeof outputs === 'object') return Object.keys(outputs).length
  const downloaded = job.result?.downloaded
  return Array.isArray(downloaded) ? downloaded.length : 0
}

type CancelActions = {
  audio: (taskId: string) => Promise<unknown>
  manual: (taskId: string) => Promise<unknown>
  agent: (jobId: string) => Promise<unknown>
}

/** Never send unspawned audio work directly to the backend: its scheduler owns it. */
export async function cancelQueueJob(job: QueueEntry, actions: CancelActions): Promise<boolean> {
  if (!isQueueActive(job)) return false
  const response = job.cancelOwner === 'audio'
    ? await actions.audio(job.jobId)
    : job.source === 'manual'
      ? await actions.manual(job.cancelTaskId || job.jobId)
      : await actions.agent(job.jobId)
  return response !== false && !(response && typeof response === 'object' && 'ok' in response && response.ok === false)
}
