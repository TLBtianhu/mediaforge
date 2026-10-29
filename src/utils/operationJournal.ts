import { invoke } from '@tauri-apps/api/core'

export type JournalStatus = 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'

export type JournalJobInput = {
  jobId: string
  command: string
  source?: 'manual' | 'agent'
  status: JournalStatus
  fraction?: number
  message?: string
  createdAt?: number
  finishedAt?: number | null
  payload?: Record<string, unknown>
  result?: Record<string, unknown> | null
  error?: string | null
}

const lastSent = new Map<string, number>()

/** Best-effort shared task journal for manual operations. */
export async function journalJob(job: JournalJobInput): Promise<void> {
  if (typeof window === 'undefined' || !('__TAURI_INTERNALS__' in window)) return
  const now = Date.now()
  const previous = lastSent.get(job.jobId) || 0
  if (job.status === 'running' && now - previous < 250) return
  lastSent.set(job.jobId, now)
  if (job.status !== 'running' && job.status !== 'queued') lastSent.delete(job.jobId)
  try {
    await invoke('journal_job', { job: { ...job, source: job.source || 'manual' } })
  } catch {
    // The journal must never break the actual media operation.
  }
}

export function journalNow(): number {
  return Date.now() / 1000
}
