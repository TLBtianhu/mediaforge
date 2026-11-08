import type { SeparationTask, SeparationRunConfig } from '../../stores/task.ts'
import { STAGE_META, CURRENT_INFERENCE_PARAMS_VERSION, normalizeStatus } from '../../stores/taskNormalize.ts'
import { makeTaskWorkerEventHandler } from '../../stores/taskWorkerEvents.ts'
import { isTerminalTaskStatus } from './lifecycle.ts'
import { primaryJob, type JournalJob, type AudioEnqueue } from './journal.ts'
import { normalizeStemOutputs } from '../../utils/stemOutputs.ts'
import { normalizeLogRecord, localLogRecord, workerLogRecord, type LogEntry } from '../../utils/logRecord.ts'
import { appendWorkflowNodeLogs } from '../../workflows/nodeLogs.ts'
import { workflowInputNodes, type WorkflowRuntimeBindings } from '../../workflows/workbench.ts'

const clone = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T
const strings = (value: unknown): Record<string, string> => Object.fromEntries(Object.entries(value && typeof value === 'object' ? value : {}).filter((pair): pair is [string, string] => typeof pair[1] === 'string'))
const ms = (value: number | null) => value == null ? undefined : value * 1000
export const isAudioJob = (job: JournalJob) => job.source === 'manual' && Boolean(job.audio) && ['infer', 'infer_workflow'].includes(job.command)

type ErrorFormatter = (code: unknown, message: unknown, input?: string) => string
type AudioJobIndex = { byId: Map<string, JournalJob>; members: Map<string, JournalJob[]> }

function indexAudioJobs(jobs: readonly JournalJob[]): AudioJobIndex {
  const byId = new Map(jobs.map(job => [job.jobId, job]))
  const members = new Map<string, JournalJob[]>()
  for (const job of jobs) {
    if (!job.audio) continue
    const group = members.get(job.audio.primaryJobId) || []
    group.push(job)
    members.set(job.audio.primaryJobId, group)
  }
  for (const group of members.values()) group.sort((a, b) => a.audio!.index - b.audio!.index)
  return { byId, members }
}

export function audioTaskFromJob(job: JournalJob, jobs: readonly JournalJob[], windows = false, formatError: ErrorFormatter = (_code,message) => String(message || 'Task failed'), index?: AudioJobIndex): SeparationTask {
  const primary = index ? index.byId.get(job.audio!.primaryJobId)! : primaryJob(job, jobs)
  const payload = primary.payload!
  const request = job.audio!.request
  const result = job.result || {}
  const presentation = primary.presentation || {}
  const workflow = job.command === 'infer_workflow'
  const definition = workflow ? payload.workflow as Record<string, unknown> : undefined
  const input = request.input || ''
  const status = job.status === 'completed' ? 'done' : job.status === 'interrupted' ? 'failed'
    : job.status === 'running' ? (job.runtime.stage && job.runtime.stage in STAGE_META && !isTerminalTaskStatus(job.runtime.stage as SeparationTask['status']) && job.runtime.stage !== 'queued' ? job.runtime.stage as SeparationTask['status'] : 'preparing')
      : job.status as SeparationTask['status']
  const members = index
    ? index.members.get(primary.jobId) || []
    : jobs.filter(item => item.audio?.primaryJobId === primary.jobId).sort((a, b) => a.audio!.index - b.audio!.index)
  const inputNodes = definition ? workflowInputNodes(definition) : []
  const bindings: WorkflowRuntimeBindings = {
    inputs: Object.fromEntries(inputNodes.map(node => [node.id, { files: [...new Set(members.map(item => item.audio!.request.input || node.embeddedPath || '').filter(Boolean))] }])),
    outputs: {}, primaryInput: inputNodes[0]?.id || '', outputDirectory: String(payload.output || ''),
    outputLayout: payload.outputLayout === 'flat' ? 'flat' : 'folders',
  }
  const config: SeparationRunConfig = {
    runMode: workflow ? 'workflow' : 'model', modelDir: typeof payload.modelDir === 'string' ? payload.modelDir : null,
    downloadSource: String(payload.source || ''), downloadMethod: String(payload.downloadMethod || ''),
    ...(workflow ? { workflowId: presentation.workflowId, workflowName: String(payload.workflowName || ''), workflowDefinition: clone(definition!) } : {}),
    ...(presentation.workbench ? { workbench: { version: 2 as const, sourceUpdatedAt: presentation.sourceUpdatedAt ?? primary.createdAt * 1000, nativeGraph: presentation.workbench.nativeGraph,
      bindings, inputs: strings(request.inputs), outputs: strings(request.workbenchOutputs) } } : {}),
    outputLayout: payload.outputLayout === 'flat' ? 'flat' : 'folders',
    ...(payload.outputNaming ? { outputNaming: clone(payload.outputNaming) as SeparationRunConfig['outputNaming'] } : {}),
    device: String(payload.device || 'auto'), deviceIds: Array.isArray(payload.deviceIds) ? [...payload.deviceIds] as number[] : [],
    outputFormat: String(payload.outputFormat || 'wav'), selectedStems: Array.isArray(payload.selectedStems) ? [...payload.selectedStems] as string[] : [],
    useTta: payload.useTta === true, debug: payload.debug === true,
    audioParams: clone(payload.audioParams || {}) as SeparationRunConfig['audioParams'],
    inferenceParamsVersion: CURRENT_INFERENCE_PARAMS_VERSION, inferenceParams: clone(payload.inferenceParams || {}) as Record<string, unknown>,
  }
  const outputs = normalizeStemOutputs(result.outputs, input, windows)
  const directories = Array.isArray(result.outputDirectories) ? result.outputDirectories.filter((path): path is string => typeof path === 'string' && Boolean(path.trim())) : []
  const actualOutput = presentation.workbench && directories.length ? directories[0] : typeof result.outputDir === 'string' && result.outputDir ? result.outputDir : request.output
  const startedAt = ms(job.startedAt), finishedAt = ms(job.finishedAt)
  const progress = job.runtime.progress || {}
  const logs = job.logs.map(log => normalizeLogRecord(typeof log.timestamp === 'number' ? { ...log, timestamp: new Date(log.timestamp * 1000).toISOString() } : log))
  const task: SeparationTask = {
    id: job.jobId, jobId: job.audio!.groupId, batchIndex: job.audio!.index,
    model: String(workflow ? payload.workflowName || 'Workflow' : payload.model || ''), input,
    jobOutput: typeof result.outputRoot === 'string' && result.outputRoot ? result.outputRoot : String(payload.output), output: actualOutput,
    ...(directories.length ? { outputDirectories: directories } : {}),
    status, message: status === 'failed' ? formatError(result.code,job.error || job.message,input) : job.message, error: (job.error ? formatError(result.code,job.error,input) : undefined) || (job.status === 'interrupted' ? job.message : undefined),
    createdAt: job.createdAt * 1000, updatedAt: job.updatedAt * 1000, startedAt, finishedAt,
    ...(finishedAt !== undefined ? { durationMs: Math.max(0, finishedAt - (startedAt || job.createdAt * 1000)) } : {}),
    progress: isTerminalTaskStatus(status) ? 100 : status === 'queued' ? STAGE_META.queued.progress : Math.max(0, Math.min(99, job.fraction * 100)),
    stageLabel: STAGE_META[status].label,
    ...(typeof progress.done === 'number' ? { progressCurrent: progress.done } : {}),
    ...(typeof progress.total === 'number' ? { progressTotal: progress.total } : {}),
    ...(typeof progress.message === 'string' ? { progressDetail: progress.message } : {}),
    outputs, files: outputs.map(output => output.path), logs, runConfig: config,
    nodeStates: clone(job.runtime.nodes), nodeExecutionOrder: [...job.runtime.order],
    currentNodeId: [...job.runtime.order].reverse().find(id => job.runtime.nodes[id]?.status === 'running' || status === 'failed' && job.runtime.nodes[id]?.status === 'failed'),
    ...(Array.isArray(result.nodeValues) ? { nodeValues: clone(result.nodeValues) as SeparationTask['nodeValues'] } : {}),
  }
  logs.forEach(log => appendWorkflowNodeLogs(task, log.nodeId, log))
  return task
}

export function audioTasksFromJobs(jobs: readonly JournalJob[], windows = false, formatError?: ErrorFormatter): SeparationTask[] {
  const index = indexAudioJobs(jobs)
  return jobs.filter(isAudioJob).map(job => audioTaskFromJob(job, jobs, windows, formatError, index)).sort((a, b) => b.createdAt - a.createdAt)
}

/** Only the effective worker request and its small origin metadata cross the persistence boundary. */
export function audioEnqueueFromTasks(tasks: readonly SeparationTask[]): AudioEnqueue {
  const primary = tasks[0], config = primary.runConfig!
  const workflow = config.runMode === 'workflow'
  return {
    schemaVersion: 2, command: workflow ? 'infer_workflow' : 'infer', groupId: primary.jobId || primary.id, primaryJobId: primary.id,
    payload: {
      ...(workflow ? { workflowName: config.workflowName || primary.model, workflow: clone(config.workflowDefinition || {}) } : { model: primary.model, download: true, endpoint: null, selectedStems: [...config.selectedStems], inferenceParamsVersion: CURRENT_INFERENCE_PARAMS_VERSION, inferenceParams: clone(config.inferenceParams) }),
      output: primary.jobOutput || primary.output, modelDir: config.modelDir, source: config.downloadSource, downloadMethod: config.downloadMethod,
      device: config.device, deviceIds: [...config.deviceIds], outputFormat: config.outputFormat, outputLayout: config.outputLayout,
      ...(config.outputNaming ? { outputNaming: clone(config.outputNaming) } : {}), useTta: config.useTta, debug: config.debug, audioParams: clone(config.audioParams),
      ...(config.workbench && !config.workbench.nativeGraph ? { workbench: true } : {}),
    },
    ...(workflow ? { presentation: { ...(config.workflowId ? { workflowId: config.workflowId } : {}),
      ...(config.workbench ? { ...(Number.isFinite(config.workbench.sourceUpdatedAt) ? { sourceUpdatedAt: config.workbench.sourceUpdatedAt } : {}), workbench: { nativeGraph: config.workbench.nativeGraph === true } } : {}) } } : {}),
    tasks: tasks.map((task, index) => ({ jobId: task.id, request: { input: task.input || null, output: task.output, inputIndex: index + 1,
      ...(task.runConfig?.workbench && !task.runConfig.workbench.nativeGraph ? { inputs: clone(task.runConfig.workbench.inputs), workbenchOutputs: clone(task.runConfig.workbench.outputs) } : {}) } })),
  }
}

/** Live events update the same journal-shaped memory; persistence belongs to the backend observer. */
export function audioJobEvent(job: JournalJob, jobs: readonly JournalJob[], event: Record<string, any>, developerMode = false): JournalJob {
  if (['completed','failed','cancelled','interrupted'].includes(job.status)) return job
  const task = audioTaskFromJob(job, jobs)
  const touch = (item: SeparationTask) => { item.updatedAt = Date.now() }
  const started = (item: SeparationTask) => { item.startedAt ||= Date.now() }
  const finished = (item: SeparationTask, at = Date.now()) => { item.finishedAt = at }
  const handler = makeTaskWorkerEventHandler({
    tasks: { value: [task] }, STAGE_META, normalizeStatus, isTerminalTaskStatus, markTaskStarted: started, markTaskFinished: finished, touch,
    queuePersist() {}, queueProgressPersist() {}, scheduleQueue() {}, appendWorkflowNodeLogs, localLogRecord, workerLogRecord, normalizeLogRecord, normalizeStemOutputs,
    useSettingsStore: () => ({ developerMode }), windowsPaths: { value: /^[A-Za-z]:|^\\/.test(task.output) },
    resolveTaskErrorMessage: (_code: unknown, message: unknown) => String(message || 'Task failed'),
    appendTaskLogs: (item: SeparationTask, value: LogEntry | LogEntry[]) => { item.logs.push(...(Array.isArray(value) ? value : [value])); item.logs = item.logs.slice(-120) },
    setTaskStatus: (_id: string, status: SeparationTask['status'], message?: string, progress?: number) => {
      if (isTerminalTaskStatus(task.status)) return
      task.status = status; task.message = message || STAGE_META[status].label; task.stageLabel = STAGE_META[status].label
      task.progress = progress ?? STAGE_META[status].progress; if (status !== 'queued') started(task); touch(task)
      if (isTerminalTaskStatus(status)) finished(task)
    },
  })
  handler({ ...event, taskId: job.jobId })
  const payload = event.payload || {}
  const result = ['task.result', 'error', 'task.cancelled'].includes(event.type) || payload.outputs ? { ...job.result, ...payload } : job.result
  if (result && 'files' in result) delete result.files
  return { ...job, status: task.status === 'done' ? 'completed' : ['queued', 'failed', 'cancelled'].includes(task.status) ? task.status : 'running',
    fraction: task.progress / 100, message: task.message, error: task.error || null, result,
    startedAt: task.startedAt == null ? null : task.startedAt / 1000, finishedAt: task.finishedAt == null ? null : task.finishedAt / 1000,
    updatedAt: task.updatedAt / 1000,
    runtime: { stage: task.status, progress: event.type === 'task.progress' ? clone(payload) : job.runtime.progress, nodes: task.nodeStates || {}, order: task.nodeExecutionOrder || [] },
    logs: task.logs.map(log => typeof log === 'string' ? normalizeLogRecord(log) as unknown as Record<string, unknown> : log as unknown as Record<string, unknown>),
  }
}
