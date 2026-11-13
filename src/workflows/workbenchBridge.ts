import type { SeparationJob } from '../stores/task.ts'
import type { WorkflowEntry } from '../stores/workflow.ts'
import type { WorkflowWorkbenchSession } from '../stores/workbench.ts'
import type { WorkflowRuntimeBindings } from './workbench.ts'

export const WORKBENCH_REQUEST = 'pymss://workflow-workbench-request'
export const WORKBENCH_STATE = 'pymss://workflow-workbench-state'
export type WorkbenchState = { workflowKey: string; jobs: SeparationJob[]; session?: WorkflowWorkbenchSession }
export type WorkbenchAction = {
  action: 'attach' | 'run' | 'cancel' | 'retry' | 'remove-run' | 'remember' | 'detach'
  workflowKey: string
  entry?: WorkflowEntry
  bindings?: WorkflowRuntimeBindings
  session?: WorkflowWorkbenchSession
  jobId?: string
  taskId?: string
}
type Request = WorkbenchAction & { requestId?: string; windowLabel: string }
export type WorkbenchActionResult = { jobId?: string; taskId?: string; total?: number }
type Response = { requestId?: string; state?: WorkbenchState; result?: WorkbenchActionResult; error?: string }
type Transport = {
  listen: <T>(event: string, handler: (payload: T) => void) => Promise<() => void>
  emitTo: (label: string, event: string, payload: unknown) => Promise<void>
}
type Host = {
  snapshot: (key: string, includeSession: boolean) => WorkbenchState
  remember: (key: string, session: WorkflowWorkbenchSession) => void
  perform: (action: WorkbenchAction) => Promise<WorkbenchActionResult>
  watchTasks: (notify: () => void) => () => void
}

/** The main window owns tasks; editor windows receive read-only job mirrors. */
export async function createWorkbenchBridge(options: {
  label: string
  transport: Transport
  host?: Host
  applyState?: (state: WorkbenchState) => void
  bindingFingerprint?: (key: string) => string
  timeoutMs?: number
}) {
  const { label, transport, host } = options
  const subscribers = new Map<string, string>()
  const removers: (() => void)[] = []
  const pending = new Map<string, { resolve: (result: WorkbenchActionResult) => void; reject: (error: Error) => void; timer: ReturnType<typeof setTimeout>; bindingFingerprint?: string }>()
  let activeKey = ''
  let disposed = false
  let sequence = 0
  const clientId = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`
  let broadcastTimer: ReturnType<typeof setTimeout> | undefined
  async function send(windowLabel: string, response: Response) {
    try { await transport.emitTo(windowLabel, WORKBENCH_STATE, response) }
    catch { subscribers.delete(windowLabel) }
  }
  try {
    if (host) {
      removers.push(await transport.listen(WORKBENCH_REQUEST, async (request: Request) => {
        if (disposed || !request.windowLabel || request.windowLabel === label) return
        if (request.action === 'detach') { subscribers.delete(request.windowLabel); return }
        if (request.action === 'remember') {
          if (request.session) host.remember(request.workflowKey, request.session)
          return
        }
        try {
          const result = request.action === 'attach' ? {} : await host.perform(request)
          if (request.action === 'attach') subscribers.set(request.windowLabel, request.workflowKey)
          await send(request.windowLabel, { requestId: request.requestId, result, state: host.snapshot(request.workflowKey, request.action === 'attach') })
        } catch (error) {
          await send(request.windowLabel, { requestId: request.requestId, error: error instanceof Error ? error.message : String(error) })
        }
      }))
      for (const kind of ['node', 'simple']) {
        removers.push(await transport.listen(`pymss://workflow-${kind}-editor-closed`, (payload: { label?: string }) => {
          if (payload.label) subscribers.delete(payload.label)
        }))
      }
      removers.push(host.watchTasks(() => {
        if (broadcastTimer || disposed) return
        broadcastTimer = setTimeout(() => {
          broadcastTimer = undefined
          for (const [target, key] of subscribers) void send(target, { state: host.snapshot(key, false) })
        }, 100)
      }))
    } else {
      removers.push(await transport.listen(WORKBENCH_STATE, (response: Response) => {
        if (disposed) return
        const waiting = response.requestId ? pending.get(response.requestId) : undefined
        if (response.requestId && !waiting) return
        if (response.state?.workflowKey === activeKey) {
          const changed = waiting?.bindingFingerprint !== undefined
            && waiting.bindingFingerprint !== options.bindingFingerprint?.(activeKey)
          options.applyState?.(changed ? { ...response.state, session: undefined } : response.state)
        }
        if (!waiting) return
        pending.delete(response.requestId!)
        clearTimeout(waiting.timer)
        if (response.error) waiting.reject(new Error(response.error))
        else waiting.resolve(response.result || {})
      }))
    }
  } catch (error) {
    removers.splice(0).forEach(remove => remove())
    throw error
  }
  async function request(action: WorkbenchAction) {
    if (disposed) throw new Error('workbench.hostUnavailable')
    const requestId = `${label}:${clientId}:${++sequence}`
    return new Promise<WorkbenchActionResult>((resolve, reject) => {
      const timer = setTimeout(() => {
        pending.delete(requestId)
        reject(new Error('workbench.hostUnavailable'))
      }, options.timeoutMs ?? 30000)
      pending.set(requestId, { resolve, reject, timer,
        ...(action.action === 'attach' ? { bindingFingerprint: options.bindingFingerprint?.(action.workflowKey) } : {}),
      })
      transport.emitTo('main', WORKBENCH_REQUEST, { ...action, requestId, windowLabel: label }).catch(error => {
        clearTimeout(timer)
        pending.delete(requestId)
        reject(error)
      })
    })
  }
  async function attach(workflowKey: string) {
    activeKey = workflowKey
    return request({ action: 'attach', workflowKey })
  }
  function remember(workflowKey: string, session: WorkflowWorkbenchSession) {
    return transport.emitTo('main', WORKBENCH_REQUEST, { action: 'remember', workflowKey, session, windowLabel: label })
  }
  async function dispose() {
    if (disposed) return
    disposed = true
    if (broadcastTimer) clearTimeout(broadcastTimer)
    removers.splice(0).forEach(remove => remove())
    for (const waiting of pending.values()) { clearTimeout(waiting.timer); waiting.reject(new Error('workbench.hostUnavailable')) }
    pending.clear()
    if (!host) await transport.emitTo('main', WORKBENCH_REQUEST, { action: 'detach', workflowKey: activeKey, windowLabel: label }).catch(() => undefined)
  }
  return { request, attach, remember, dispose }
}
