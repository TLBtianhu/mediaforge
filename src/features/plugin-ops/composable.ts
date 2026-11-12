/**
 * Plugin-operation runtime bridge (round 5 of the pymss plugin redesign).
 *
 * Discovery uses one-shot worker commands (list_operations /
 * describe_operation). Execution runs as a background task through
 * `start_operation_task`; canonical task events resolve the pending request.
 *
 * Every start is tracked per request id, so concurrent operations cannot
 * overwrite one another, and every waiting caller ends on a terminal event:
 * `task.result` (with an empty `failed[]`), `error`, or `task.cancelled`.
 * `stop()` ends the waits that a closed page can no longer observe.
 */
import { startOperationTask } from '@/api/native'
import { reactive, ref } from 'vue'
import { invokeWorkerCommand, invokeWorkerQuery } from '@/api/worker'
import { listen, type UnlistenFn } from '@tauri-apps/api/event'
import { isTaskProgress, isTaskResult } from '@/utils/taskEvents'

export type OperationParamSchema = {
  type: 'number' | 'integer' | 'string' | 'boolean'
  default?: unknown
  minimum?: number
  maximum?: number
  enum?: (string | number)[]
  description?: string
}

export type OperationSpec = {
  id: string
  schemaVersion: number
  title: string
  description?: string
  inputs: Record<string, { type: string }>
  params: {
    type: 'object'
    properties: Record<string, OperationParamSchema>
    required?: string[]
    additionalProperties?: boolean
  }
  outputs: Record<string, { type: string }>
}

export type OperationRunResult = {
  operation: string
  outputs: Record<string, { kind: string; path?: string; value?: unknown; sampleRate?: number }>
  outputDir: string
}

export type OperationProgress = { fraction: number; message: string }

type WorkerEvent = {
  requestId?: string
  taskId?: string
  type: string
  payload?: Record<string, unknown>
}

type PendingRun = {
  resolve: (value: OperationRunResult) => void
  reject: (error: Error) => void
  onProgress?: (progress: OperationProgress) => void
  fraction: number
}

const state = reactive({
  available: false,
  unavailableReason: '' as string | undefined,
  specs: [] as OperationSpec[],
})
const loading = ref(false)
const error = ref<string | null>(null)

let unlistenWorker: UnlistenFn | undefined
let startInFlight: Promise<void> | undefined
let requestSequence = 0
const pendingRuns = new Map<string, PendingRun>()

/** A closed page cannot observe results; its waiters must still settle. */
export const PLUGIN_OPS_CLOSED = 'PLUGIN_OPS_CLOSED'

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function failureMessages(payload: Record<string, unknown>): string[] {
  const failed = payload.failed
  if (!Array.isArray(failed)) return []
  return failed.map((item) => {
    if (isRecord(item)) return String(item.error || item.message || '')
    return String(item ?? '')
  }).filter(Boolean)
}

function handleWorkerEvent(workerEvent: WorkerEvent) {
  if (!isRecord(workerEvent)) return
  const eventId = workerEvent.requestId || workerEvent.taskId || ''
  const pending = pendingRuns.get(eventId)
  if (!pending) return
  const payload = isRecord(workerEvent.payload) ? workerEvent.payload : {}
  if (isTaskResult(workerEvent.type)) {
    pendingRuns.delete(eventId)
    // Media results may finish "successfully" with failed entries; reporting
    // them as success would claim work the worker did not do.
    const failures = failureMessages(payload)
    if (failures.length) pending.reject(new Error(failures[0]))
    else pending.resolve(payload as unknown as OperationRunResult)
    return
  }
  if (workerEvent.type === 'error') {
    if (payload.recoverable === true) {
      // A recoverable diagnostic keeps the operation running, exactly like the
      // durable journal: it is not the terminal result.
      pending.onProgress?.({ fraction: pending.fraction, message: String(payload.message || '') })
      return
    }
    pendingRuns.delete(eventId)
    pending.reject(new Error(String(payload.message || 'Operation failed')))
    return
  }
  if (workerEvent.type === 'task.cancelled') {
    pendingRuns.delete(eventId)
    pending.reject(new Error('cancelled'))
    return
  }
  if (isTaskProgress(workerEvent.type)) {
    const fraction = Number(payload.fraction)
    const value = Number.isFinite(fraction) ? Math.max(0, Math.min(1, fraction)) : pending.fraction
    pending.fraction = value
    pending.onProgress?.({ fraction: value, message: String(payload.message || '') })
  }
}

async function start() {
  if (typeof window === 'undefined' || !('__TAURI_INTERNALS__' in window)) return
  if (unlistenWorker) return
  if (!startInFlight) {
    startInFlight = listen<WorkerEvent>('pymss://worker-event', ({ payload }) => handleWorkerEvent(payload))
      .then((unlisten) => {
        unlistenWorker = unlisten
      })
      .finally(() => {
        startInFlight = undefined
      })
  }
  await startInFlight
}

/** End every waiting request; a later run still starts a fresh listener. */
export function stopPluginOps(reason: string = PLUGIN_OPS_CLOSED) {
  const waiting = [...pendingRuns.values()]
  pendingRuns.clear()
  for (const pending of waiting) pending.reject(new Error(reason))
}

export function usePluginOps() {
  async function refresh() {
    loading.value = true
    error.value = null
    try {
      const response = (await invokeWorkerQuery('list_operations')) as unknown as {
        operations: OperationSpec[]
        pymssOperationsAvailable: boolean
        error?: string
      }
      state.available = response.pymssOperationsAvailable !== false
      state.unavailableReason = response.error
      state.specs = response.operations || []
    } catch (err) {
      state.available = false
      state.specs = []
      error.value = err instanceof Error ? err.message : String(err)
    } finally {
      loading.value = false
    }
  }

  async function describe(operationId: string): Promise<OperationSpec | null> {
    try {
      return (await invokeWorkerCommand('describe_operation', { operation: operationId })) as unknown as OperationSpec
    } catch {
      return null
    }
  }

  async function run(options: {
    operation: string
    params: Record<string, unknown>
    inputs: Record<string, { kind: 'file'; path: string }>
    outputDir?: string
    onProgress?: (p: OperationProgress) => void
  }): Promise<OperationRunResult> {
    await start()
    const requestId = `operation_${options.operation}_${Date.now()}_${++requestSequence}`
    const completion = new Promise<OperationRunResult>((resolve, reject) => {
      pendingRuns.set(requestId, { resolve, reject, onProgress: options.onProgress, fraction: 0 })
    })
    try {
      await startOperationTask({
        operation: options.operation,
        params: options.params,
        inputs: options.inputs,
        outputDir: options.outputDir,
        requestId,
      })
      return await completion
    } finally {
      pendingRuns.delete(requestId)
    }
  }

  return { state, loading, error, refresh, describe, run, stop: stopPluginOps }
}
