import type { SeparationJob, SeparationTask } from '../stores/task.ts'
import { isTerminalTaskStatus, resolveJobStatus } from '../features/tasks/lifecycle.ts'
import { workflowSourceDefinition, visibleWorkflowNodeStates, workflowOutputNodes, type WorkflowNodeState } from './workbench.ts'
import { visibleWorkflowNodeLogs, type WorkflowNodeLogs } from './nodeLogs.ts'

export function workflowTaskLabel(task: SeparationTask): string {
  return task.input.split(/[\\/]/).pop() || task.model
}

export function activeWorkflowTask(tasks: readonly SeparationTask[]): SeparationTask | null {
  const status = resolveJobStatus(tasks)
  return isTerminalTaskStatus(status) ? null : tasks.find(task => task.status === status) || null
}

export function selectedWorkflowJob(jobs: readonly SeparationJob[], id: string): SeparationJob | null {
  return jobs.find(job => job.id === id) || jobs.reduce<SeparationJob | null>((latest, job) => (
    !latest || job.createdAt > latest.createdAt ? job : latest
  ), null)
}

export type WorkflowVisibleNode = { id: string; label: string; kind: 'input' | 'save' | 'step' | 'node' }
export function workflowVisibleNodes(definition: Record<string, unknown>): WorkflowVisibleNode[] {
  if (Array.isArray(definition.nodes)) return definition.nodes.map(node => ({ id: String(node.id), label: String(node.title || node.type), kind: 'node' }))
  return [
    { id: 'input', label: '', kind: 'input' },
    ...(Array.isArray(definition.steps) ? definition.steps : []).map(step => ({ id: String(step.id), label: String(step.model || step.id), kind: 'step' as const })),
    ...(Array.isArray(definition.ensembles) ? definition.ensembles : []).map(item => ({ id: String(item.id), label: String(item.output_stem || item.algorithm), kind: 'node' as const })),
    ...(workflowOutputNodes(definition).length ? [{ id: 'save', label: '', kind: 'save' as const }] : []),
  ]
}

/** Derive display data without merging or mutating individual task snapshots. */
export function workflowBatchExecution(tasks: readonly SeparationTask[]) {
  const executions = tasks.map(task => {
    const definition = workflowSourceDefinition(task.runConfig)
    const nodes = workflowVisibleNodes(definition)
    if (Array.isArray(definition.nodes) && task.nodeExecutionOrder?.length) {
      // Graph serialization follows editing order, not the kernel's execution order.
      const order = new Map(task.nodeExecutionOrder.map((id, index) => [id, index]))
      const pending = task.nodeExecutionOrder.length
      nodes.sort((a, b) => (order.get(a.id) ?? pending) - (order.get(b.id) ?? pending))
    }
    return { task, nodes, nodeIds: new Set(nodes.map(node => node.id)), states: visibleWorkflowNodeStates(definition, task.nodeStates),
      logs: visibleWorkflowNodeLogs(definition, task.nodeLogs) }
  })
  const nodes = [...new Map(executions.flatMap(item => item.nodes.map(node => [node.id, node] as const))).values()]
  const states: Record<string, WorkflowNodeState> = Object.create(null)
  const logs: WorkflowNodeLogs = Object.create(null)
  for (const node of nodes) {
    const relevant = executions.filter(item => item.nodeIds.has(node.id))
    const values = relevant.map(({ task, states: taskStates }) => {
      const state = taskStates[node.id]
      if (!state) return { status: task.status === 'done' ? 'unknown' : isTerminalTaskStatus(task.status) ? 'skipped' : 'queued', progress: 0 }
      if (state.status === 'running' && isTerminalTaskStatus(task.status)) {
        return { ...state, status: task.status === 'done' ? 'done' : task.status }
      }
      return state
    })
    const completed = values.filter(state => state.status === 'done').length
    const failed = values.filter(state => state.status === 'failed').length
    const cancelled = values.filter(state => state.status === 'cancelled').length
    const skipped = values.filter(state => state.status === 'skipped').length
    const unknown = values.filter(state => state.status === 'unknown').length
    const status = failed ? 'failed' : cancelled ? 'cancelled'
      : completed === values.length ? 'done' : values.some(state => state.status === 'running') ? 'running'
        : values.some(state => state.status === 'queued') ? 'queued' : skipped ? 'skipped' : 'unknown'
    states[node.id] = { status, progress: values.reduce((total, state) => total + (state.status === 'done' ? 100 : state.progress), 0) / Math.max(1, values.length),
      summary: { completed, total: values.length, failed, cancelled, skipped, unknown } }
    logs[node.id] = relevant.flatMap(({ task, logs: taskLogs }, index) => {
      const lines = taskLogs[node.id] || []
      return lines.length ? [`[info] ── ${index + 1}. ${workflowTaskLabel(task)} ──`, ...lines] : []
    })
  }
  return { nodes, states, logs }
}
