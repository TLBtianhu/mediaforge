import { computed, onScopeDispose, ref } from 'vue'
import { defineStore } from 'pinia'
import { invoke } from '@tauri-apps/api/core'
import { listen } from '@tauri-apps/api/event'
import { isTaskProgress, isTaskResult } from '@/utils/taskEvents'

export type { JournalJob } from '@/features/tasks/journal'
import { readJournal, validateJournalSnapshot, type JournalJob, type JournalSnapshot } from '@/features/tasks/journal'
import { audioJobEvent, isAudioJob } from '@/features/tasks/audioJobs'
import { useSettingsStore } from '@/stores/settings'
import { isTauriRuntime } from '@/utils/appStore'

type WorkerEvent = { type?: string; requestId?: string | null; taskId?: string | null; payload?: Record<string, unknown> }
const TERMINAL = new Set(['completed', 'failed', 'cancelled', 'interrupted'])
export const isJournalTerminal = (status: string) => TERMINAL.has(status)
export const journalOperation = (job: JournalJob) => String(job.payload?.operation || job.command)
const now = () => Date.now() / 1000
const snapshot = <T>(value: T): T => JSON.parse(JSON.stringify(value))
export const JOURNAL_POLL_INTERVAL_MS = 2000

// A Pinia lifetime, rather than a route lifetime, owns the event subscription.
// The backend journal remains the durable source of truth across app restarts.
export const useMediaJobsStore = defineStore('mediaJobs', () => {
  const jobs = ref<JournalJob[]>([])
  const loading = ref(false)
  const error = ref('')
  const dataRoot = ref('')
  const jobsDir = ref('')
  const active = computed(() => jobs.value.filter(job => !isJournalTerminal(job.status)))
  const revisions = new Map<string, number>()
  const pendingIds = new Set<string>()
  const deletedIds = new Set<string>()
  let revision = 0
  let listening = false
  let initializing: Promise<void> | undefined
  let refreshing: Promise<void> | undefined
  let disposed = false
  let pollTimer: ReturnType<typeof setTimeout> | undefined
  let unlisten: (() => void) | undefined

  function schedulePoll() {
    if (disposed || pollTimer !== undefined || !isTauriRuntime()) return
    pollTimer = setTimeout(() => {
      pollTimer = undefined
      void refresh().finally(schedulePoll)
    }, JOURNAL_POLL_INTERVAL_MS)
    // Node component tests have no application event loop to keep alive.
    ;(pollTimer as unknown as { unref?: () => void }).unref?.()
  }

  onScopeDispose(() => {
    disposed = true
    if (pollTimer !== undefined) clearTimeout(pollTimer)
    pollTimer = undefined
    unlisten?.()
    unlisten = undefined
  })

  function put(job: JournalJob) {
    job.updatedAt = Math.max(job.updatedAt, now())
    const index = jobs.value.findIndex(item => item.jobId === job.jobId)
    if (index < 0) jobs.value.push(job)
    else jobs.value[index] = job
    revisions.set(job.jobId, ++revision)
  }

  function handleWorkerEvent(event: WorkerEvent) {
    const job = jobs.value.find(item => item.jobId === event.taskId)
      || jobs.value.find(item => item.jobId === event.requestId)
    if (!job || isJournalTerminal(job.status)) return
    if (isAudioJob(job)) {
      put(audioJobEvent(job, jobs.value, event, useSettingsStore().developerMode))
      return
    }
    const payload = event.payload || {}
    const editorExport = job.command === 'export_editor_mix'
    if (isTaskProgress(event.type) || event.type === 'task.started' || event.type === 'task.stage'
      || editorExport && event.type === 'editor_export_progress') {
      const fraction = Number(payload.fraction ?? payload.overallFraction ?? (Number(payload.total) > 0 ? Number(payload.completed ?? payload.done ?? 0) / Number(payload.total) : job.fraction))
      put({ ...job, status: 'running', fraction: Number.isFinite(fraction) ? Math.max(0, Math.min(0.99, fraction)) : job.fraction,
        message: String(payload.current || payload.message || job.message),
        result: payload.outputs ? snapshot({ ...job.result, ...payload }) : job.result })
    } else if (isTaskResult(event.type) || editorExport && event.type === 'editor_mix_exported') {
      const failed = Array.isArray(payload.failed) && payload.failed.length > 0
        || journalOperation(job).startsWith('media_') && Array.isArray(payload.outputs) && payload.outputs.length === 0
      put({ ...job, status: failed ? 'failed' : 'completed', fraction: 1, result: snapshot(payload),
        message: String(payload.message || ''), error: failed ? String(payload.message || 'Some inputs failed') : null, finishedAt: now() })
    } else if (event.type === 'error' && payload.recoverable === true) {
      // Match the durable backend: a diagnostic can be followed by a valid result.
      put({ ...job, message: String(payload.message || job.message) })
    } else if (event.type === 'error' || event.type === 'task.cancelled') {
      const cancelled = event.type === 'task.cancelled'
      const result = snapshot({ ...job.result, ...payload })
      if (Array.isArray(job.result?.outputs) && (!Array.isArray(payload.outputs) || !payload.outputs.length)) result.outputs = job.result.outputs
      put({ ...job, status: cancelled ? 'cancelled' : 'failed', message: String(payload.message || ''),
        error: cancelled ? null : String(payload.message || 'Task failed'),
        result, finishedAt: now() })
    }
  }

  function adoptSnapshot(raw: JournalSnapshot, atStart = revision) {
    const response = validateJournalSnapshot(raw)
    const current = new Map(jobs.value.map(job => [job.jobId, job]))
    const merged = new Map<string, JournalJob>()
    for (const incoming of response.jobs) {
      if (deletedIds.has(incoming.jobId)) continue
      const previous = current.get(incoming.jobId)
      const updated = (revisions.get(incoming.jobId) || 0) > atStart
        || previous && (previous.updatedAt > incoming.updatedAt
          || previous.updatedAt === incoming.updatedAt && previous.status === incoming.status && (revisions.get(incoming.jobId) || 0) > 0
          || isJournalTerminal(previous.status) && !isJournalTerminal(incoming.status))
      merged.set(incoming.jobId, previous && updated ? previous : snapshot(incoming))
    }
    for (const previous of jobs.value) {
      if (!deletedIds.has(previous.jobId) && !merged.has(previous.jobId)
        && (pendingIds.has(previous.jobId) || (revisions.get(previous.jobId) || 0) > atStart)) merged.set(previous.jobId, previous)
    }
    jobs.value = [...merged.values()]
    dataRoot.value = response.dataRoot
    jobsDir.value = response.jobsDir
  }

  function acceptJobs(raw: JournalSnapshot) {
    const response = validateJournalSnapshot(raw)
    for (const job of response.jobs) {
      deletedIds.delete(job.jobId)
      const index = jobs.value.findIndex(item => item.jobId === job.jobId)
      if (index < 0) jobs.value.push(snapshot(job))
      else jobs.value[index] = snapshot(job)
      revisions.set(job.jobId, ++revision)
    }
    dataRoot.value = response.dataRoot
    jobsDir.value = response.jobsDir
  }

  function forget(ids: readonly string[]) {
    ids.forEach(id => { deletedIds.add(id); revisions.set(id, ++revision); pendingIds.delete(id) })
    jobs.value = jobs.value.filter(job => !deletedIds.has(job.jobId))
  }

  function refresh(): Promise<void> {
    if (disposed) return Promise.resolve()
    if (refreshing) return refreshing
    const atStart = revision
    loading.value = true
    refreshing = (async () => {
      try {
        const response = await readJournal()
        if (!disposed) {
          adoptSnapshot(response, atStart)
          error.value = ''
        }
      } catch (cause) {
        if (!disposed) error.value = cause instanceof Error ? cause.message : String(cause)
      } finally {
        loading.value = false
        refreshing = undefined
      }
    })()
    return refreshing
  }

  function initialize(): Promise<void> {
    if (disposed) return Promise.resolve()
    if (initializing) return initializing
    initializing = (async () => {
      if (!listening && isTauriRuntime()) {
        const stop = await listen<WorkerEvent>('pymss://worker-event', event => { if (!disposed) handleWorkerEvent(event.payload) })
        if (disposed) { stop(); return }
        unlisten = stop
        listening = true
      }
      await refresh()
      schedulePoll()
    })().finally(() => { initializing = undefined })
    return initializing
  }

  async function start(payload: Record<string, unknown>): Promise<JournalJob> {
    const request = snapshot(payload)
    await initialize()
    if (error.value) throw new Error(error.value)
    const operation = String(request.operation || '')
    if (!operation) throw new Error('Missing media operation')
    if (active.value.some(job => journalOperation(job) === operation && job.source === 'manual')) {
      throw new Error('This operation is already running')
    }
    const jobId = String(request.requestId || `media-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`)
    if (jobs.value.some(job => job.jobId === jobId)) throw new Error('Task ID already exists')
    request.requestId = jobId
    pendingIds.add(jobId)
    put({ schemaVersion: 2, jobId, command: 'audio_tools', source: 'manual', status: 'queued', cancelTaskId: jobId,
      fraction: 0, message: '', createdAt: now(), updatedAt: now(), startedAt: null, finishedAt: null, owner: null,
      runtime: { stage: null, progress: null, nodes: {}, order: [] }, logs: [], payload: request, result: null, error: null })
    try {
      // The listener and immutable local snapshot exist before native work starts.
      await invoke('start_audio_tool', { payload: request })
    } catch (cause) {
      // An IPC acknowledgement can be lost after the backend accepted a waiter.
      // Resolve against disk before deciding whether the submission was rejected.
      pendingIds.delete(jobId)
      await refresh()
      const current = jobs.value.find(job => job.jobId === jobId)
      if (!error.value && current && !isJournalTerminal(current.status)) return current
      throw cause
    } finally {
      pendingIds.delete(jobId)
    }
    return jobs.value.find(job => job.jobId === jobId)!
  }

  function latest(operation: string) {
    return [...jobs.value].reverse().filter(job => job.source === 'manual' && journalOperation(job) === operation)
      .sort((a, b) => b.createdAt - a.createdAt)[0]
  }
  return { jobs, loading, error, dataRoot, jobsDir, active, initialize, refresh, adoptSnapshot, acceptJobs, forget, handleWorkerEvent, start, latest }
})
