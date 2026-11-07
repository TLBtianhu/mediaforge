import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { invoke } from '@tauri-apps/api/core'
import { listen } from '@tauri-apps/api/event'
import { isTaskProgress, isTaskResult } from '@/utils/taskEvents'

export type JournalJob = {
  jobId: string
  command: string
  source?: 'manual' | 'agent'
  status: string
  cancelTaskId?: string
  fraction: number
  message: string
  createdAt: number
  finishedAt?: number | null
  payload?: Record<string, unknown>
  result?: Record<string, unknown> | null
  error?: string | null
  owner?: { kind?: string; [key: string]: unknown }
}
type WorkerEvent = { type?: string; requestId?: string | null; taskId?: string | null; payload?: Record<string, unknown> }
const TERMINAL = new Set(['completed', 'failed', 'cancelled', 'interrupted'])
export const isJournalTerminal = (status: string) => TERMINAL.has(status)
export const journalOperation = (job: JournalJob) => String(job.payload?.operation || job.command)
const now = () => Date.now() / 1000
const snapshot = <T>(value: T): T => JSON.parse(JSON.stringify(value))

// A Pinia lifetime, rather than a route lifetime, owns the event subscription.
// The backend journal remains the durable source of truth across app restarts.
export const useMediaJobsStore = defineStore('mediaJobs', () => {
  const jobs = ref<JournalJob[]>([])
  const loading = ref(false)
  const error = ref('')
  const dataRoot = ref('')
  const jobsDir = ref('')
  const active = computed(() => jobs.value.filter(job => !isJournalTerminal(job.status)
    && (job.source !== 'manual' || trackedManual(job))))
  const revisions = new Map<string, number>()
  const submittedIds = new Set<string>()
  let revision = 0
  let listening = false
  let initializing: Promise<void> | undefined
  let refreshing: Promise<void> | undefined

  function put(job: JournalJob) {
    const index = jobs.value.findIndex(item => item.jobId === job.jobId)
    if (index < 0) jobs.value.push(job)
    else jobs.value[index] = job
    revisions.set(job.jobId, ++revision)
  }

  function onEvent(event: WorkerEvent) {
    const job = jobs.value.find(item => item.jobId === event.taskId)
      || jobs.value.find(item => item.jobId === event.requestId)
    if (!job || isJournalTerminal(job.status)) return
    const payload = event.payload || {}
    if (isTaskProgress(event.type) || event.type === 'task.started' || event.type === 'task.stage') {
      const fraction = Number(payload.fraction ?? payload.overallFraction ?? (Number(payload.total) > 0 ? Number(payload.completed ?? payload.done ?? 0) / Number(payload.total) : job.fraction))
      put({ ...job, status: 'running', fraction: Number.isFinite(fraction) ? Math.max(0, Math.min(0.99, fraction)) : job.fraction,
        message: String(payload.current || payload.message || job.message),
        result: payload.outputs ? snapshot({ ...job.result, ...payload }) : job.result })
    } else if (isTaskResult(event.type)) {
      const failed = Array.isArray(payload.failed) && payload.failed.length > 0
        || journalOperation(job).startsWith('media_') && Array.isArray(payload.outputs) && payload.outputs.length === 0
      put({ ...job, status: failed ? 'failed' : 'completed', fraction: 1, result: snapshot(payload),
        message: String(payload.message || ''), error: failed ? String(payload.message || 'Some inputs failed') : null, finishedAt: now() })
    } else if (event.type === 'error' || event.type === 'task.cancelled') {
      const cancelled = event.type === 'task.cancelled'
      const result = snapshot({ ...job.result, ...payload })
      if (Array.isArray(job.result?.outputs) && (!Array.isArray(payload.outputs) || !payload.outputs.length)) result.outputs = job.result.outputs
      put({ ...job, status: cancelled ? 'cancelled' : 'failed', message: String(payload.message || ''),
        error: cancelled ? null : String(payload.message || 'Task failed'),
        result, finishedAt: now() })
    }
  }

  function refresh(): Promise<void> {
    if (refreshing) return refreshing
    const atStart = revision
    loading.value = true
    refreshing = (async () => {
      try {
        const response = await invoke<{ schemaVersion: number; dataRoot: string; jobsDir: string; jobs: JournalJob[] }>('agent_jobs')
        if (response.schemaVersion !== 1 || !Array.isArray(response.jobs) || typeof response.dataRoot !== 'string' || typeof response.jobsDir !== 'string') {
          throw new Error('Unsupported task journal response schema')
        }
        const incoming = response.jobs
        {
          const merged = new Map(jobs.value.map(job => [job.jobId, job]))
          for (const job of incoming) {
            if (!job || typeof job.jobId !== 'string' || typeof job.status !== 'string') continue
            const current = merged.get(job.jobId)
            // An in-flight snapshot must not overwrite events received meanwhile.
            if ((revisions.get(job.jobId) || 0) > atStart) continue
            if (current && isJournalTerminal(current.status) && !isJournalTerminal(job.status)) continue
            merged.set(job.jobId, snapshot(job))
          }
          jobs.value = [...merged.values()]
        }
        dataRoot.value = response.dataRoot || dataRoot.value
        jobsDir.value = response.jobsDir
        error.value = ''
      } catch (cause) {
        error.value = cause instanceof Error ? cause.message : String(cause)
      } finally {
        loading.value = false
        refreshing = undefined
      }
    })()
    return refreshing
  }

  function initialize(): Promise<void> {
    if (initializing) return initializing
    initializing = (async () => {
      if (!listening) {
        await listen<WorkerEvent>('pymss://worker-event', event => onEvent(event.payload))
        listening = true
      }
      await refresh()
    })().finally(() => { initializing = undefined })
    return initializing
  }

  async function start(payload: Record<string, unknown>): Promise<JournalJob> {
    const request = snapshot(payload)
    await initialize()
    if (error.value) throw new Error(error.value)
    const operation = String(request.operation || '')
    if (!operation) throw new Error('Missing media operation')
    if (active.value.some(job => journalOperation(job) === operation && job.source === 'manual' && trackedManual(job))) {
      throw new Error('This operation is already running')
    }
    const jobId = String(request.requestId || `media-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`)
    if (jobs.value.some(job => job.jobId === jobId)) throw new Error('Task ID already exists')
    request.requestId = jobId
    submittedIds.add(jobId)
    put({ jobId, command: 'audio_tools', source: 'manual', status: 'queued', cancelTaskId: jobId,
      fraction: 0, message: '', createdAt: now(), payload: request, result: null, error: null })
    try {
      // The listener and immutable local snapshot exist before native work starts.
      await invoke('start_audio_tool', { payload: request })
    } catch (cause) {
      const current = jobs.value.find(job => job.jobId === jobId)!
      if (!isJournalTerminal(current.status)) put({ ...current, status: 'failed', finishedAt: now(),
        message: cause instanceof Error ? cause.message : String(cause), error: String(cause) })
      throw cause
    }
    return jobs.value.find(job => job.jobId === jobId)!
  }

  function trackedManual(job: JournalJob) {
    return submittedIds.has(job.jobId) || job.owner?.kind === 'tauri-worker'
  }
  function latest(operation: string) {
    // Old route-owned running rows have no lease, so their liveness is unknown.
    // Keep them visible in the queue without permanently locking a media page.
    return [...jobs.value].reverse().filter(job => job.source === 'manual' && journalOperation(job) === operation
      && (isJournalTerminal(job.status) || trackedManual(job)))
      .sort((a, b) => b.createdAt - a.createdAt)[0]
  }
  return { jobs, loading, error, dataRoot, jobsDir, active, initialize, refresh, start, latest }
})
