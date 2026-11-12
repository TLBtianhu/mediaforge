// 本模块会被 Node 原生测试直接导入，内部导入必须用相对路径加 .ts 扩展名；
// 不要改成 @/ 别名。
import {
  agentJobs,
  enqueueAudioJobs as enqueueAudioJobsNative,
  removeAudioJobs as removeAudioJobsNative,
  removeResultJobs as removeResultJobsNative,
} from '../../api/native.ts'
import { isTauriRuntime } from '../../utils/appStore.ts'
import { CURRENT_INFERENCE_PARAMS_VERSION } from '../../stores/taskNormalize.ts'
import { RESULT_JOB_COMMANDS } from './resultCommands.ts'

export type AudioJobRequest = { input: string | null; output: string; inputIndex?: number; inputs?: Record<string, unknown>; workbenchOutputs?: Record<string, unknown> }
export type AudioPresentation = { workflowId?: string; sourceUpdatedAt?: number; workbench?: { nativeGraph: boolean } }
export type JournalJob = {
  schemaVersion: 2; jobId: string; command: string; source: 'manual' | 'agent'; status: string
  cancelTaskId?: string; fraction: number; message: string; error: string | null
  createdAt: number; updatedAt: number; startedAt: number | null; finishedAt: number | null
  payload?: Record<string, unknown>; payloadRef?: string; presentation?: AudioPresentation
  result: Record<string, unknown> | null; owner?: { kind?: string; [key: string]: unknown } | null
  runtime: { stage: string | null; progress: Record<string, unknown> | null; nodes: Record<string, any>; order: string[] }
  logs: Record<string, unknown>[]
  audio?: { groupId: string; primaryJobId: string; index: number; request: AudioJobRequest; memberJobIds?: string[] }
}
export type JournalSnapshot = {
  schemaVersion: 3
  /** True for the authoritative full catalog read; false for an incremental reply. */
  full: boolean
  jobs: JournalJob[]
  /** Ids the caller already knew but the disk no longer has. */
  removed: string[]
  dataRoot: string
  jobsDir: string
}
export type AudioEnqueue = { schemaVersion: 2; command: 'infer' | 'infer_workflow'; groupId: string; primaryJobId: string; payload: Record<string, unknown>; presentation?: AudioPresentation; tasks: { jobId: string; request: AudioJobRequest }[] }
export const isRecord = (value: unknown): value is Record<string, any> => Boolean(value) && typeof value === 'object' && !Array.isArray(value)
const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value)
const text = (value: unknown): value is string => typeof value === 'string' && Boolean(value.trim())
const STATES = new Set(['queued', 'running', 'completed', 'failed', 'cancelled', 'interrupted'])
const invalid = (): never => { throw new Error('Unsupported task journal schema; original files were preserved') }

const COMMON = ['schemaVersion','jobId','command','source','status','fraction','message','error','result','createdAt','updatedAt','startedAt','finishedAt','owner','runtime','logs']
const OPTIONAL = ['payload','payloadRef','audio','presentation','cancelTaskId']
const keys = (value: Record<string, unknown>, allowed: readonly string[]) => Object.keys(value).every(key => allowed.includes(key))
const identifier = (value: unknown): value is string => typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9_.-]{0,199}$/.test(value)
  && value.toLowerCase() !== 'index' && !/^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)/i.test(value)
const finiteJson = (value: unknown): boolean => typeof value === 'number' ? Number.isFinite(value)
  : Array.isArray(value) ? value.every(finiteJson) : isRecord(value) ? Object.values(value).every(finiteJson) : value === null || ['string','boolean'].includes(typeof value)

export function validateJournalJob(value: unknown): JournalJob {
  if (!isRecord(value) || value.schemaVersion !== 2 || !COMMON.every(key => key in value) || !keys(value,[...COMMON,...OPTIONAL])
    || !identifier(value.jobId) || !text(value.command) || !['manual','agent'].includes(value.source) || !STATES.has(value.status)
    || !finite(value.fraction) || value.fraction < 0 || value.fraction > 1 || typeof value.message !== 'string'
    || !(value.error === null || typeof value.error === 'string') || !(value.result === null || isRecord(value.result))
    || !finite(value.createdAt) || value.createdAt <= 0 || !finite(value.updatedAt) || value.updatedAt <= 0
    || ![value.startedAt,value.finishedAt].every(time => time === null || finite(time) && time > 0)
    || ['completed','failed','cancelled','interrupted'].includes(value.status) && value.finishedAt === null) return invalid()
  const owner=value.owner
  if (owner !== null && (!isRecord(owner) || !['operation-kernel','tauri-worker'].includes(owner.kind)
    || !Number.isSafeInteger(owner.pid) || owner.pid <= 0 || !keys(owner,['kind','pid','kernelId','workerPid'])
    || owner.kind === 'operation-kernel' && !text(owner.kernelId)
    || 'workerPid' in owner && (!Number.isSafeInteger(owner.workerPid) || owner.workerPid <= 0))) return invalid()
  if (value.status === 'running' && owner === null) return invalid()
  const runtime=value.runtime
  if (!isRecord(runtime) || !keys(runtime,['stage','progress','nodes','order']) || Object.keys(runtime).length !== 4
    || !(runtime.stage === null || typeof runtime.stage === 'string') || !(runtime.progress === null || isRecord(runtime.progress))
    || !isRecord(runtime.nodes) || !Array.isArray(runtime.order) || runtime.order.some((id: unknown) => typeof id !== 'string')
    || !Array.isArray(value.logs) || value.logs.some((log: unknown) => !isRecord(log))) return invalid()
  if ('cancelTaskId' in value && !identifier(value.cancelTaskId)) return invalid()
  if ('presentation' in value) {
    const presentation=value.presentation
    if (!isRecord(presentation) || !keys(presentation,['workflowId','sourceUpdatedAt','workbench'])
      || 'workflowId' in presentation && !text(presentation.workflowId)
      || 'sourceUpdatedAt' in presentation && !finite(presentation.sourceUpdatedAt)
      || 'workbench' in presentation && (!isRecord(presentation.workbench) || Object.keys(presentation.workbench).length !== 1
        || typeof presentation.workbench.nativeGraph !== 'boolean')) return invalid()
  }
  if (value.audio !== undefined) {
    const audio=value.audio
    if (!isRecord(audio) || !keys(audio,['groupId','primaryJobId','index','request','memberJobIds'])
      || !identifier(audio.groupId) || !identifier(audio.primaryJobId) || !Number.isSafeInteger(audio.index) || audio.index < 0
      || !['infer','infer_workflow'].includes(value.command) || value.source !== 'manual') return invalid()
    const request=audio.request
    if (!isRecord(request) || !keys(request,['input','output','inputIndex','inputs','workbenchOutputs'])
      || !(request.input === null || text(request.input)) || !text(request.output)
      || 'inputIndex' in request && (!Number.isSafeInteger(request.inputIndex) || request.inputIndex < 1)
      || ['inputs','workbenchOutputs'].some(key => key in request && (!isRecord(request[key]) || Object.values(request[key]).some(path => typeof path !== 'string')))) return invalid()
    if (audio.primaryJobId === value.jobId) {
      if (!isRecord(value.payload) || value.payloadRef !== undefined || !Array.isArray(audio.memberJobIds) || audio.memberJobIds[0] !== value.jobId
        || audio.index !== 0 || audio.memberJobIds.some((id: unknown) => !identifier(id)) || new Set(audio.memberJobIds).size !== audio.memberJobIds.length
        || ['taskId','requestId','tasks','input','inputs','workbenchOutputs'].some(key => key in value.payload)
        || 'inferenceParamsVersion' in value.payload && value.payload.inferenceParamsVersion !== CURRENT_INFERENCE_PARAMS_VERSION
        || !text(value.payload.output) || value.command === 'infer' && !text(value.payload.model)
        || value.command === 'infer_workflow' && (!isRecord(value.payload.workflow) || !text(value.payload.workflowName))) return invalid()
    } else if (audio.index < 1 || value.payloadRef !== audio.primaryJobId || value.payload !== undefined || value.presentation !== undefined || audio.memberJobIds !== undefined) return invalid()
  } else if (!isRecord(value.payload) || 'audio' in value || 'payloadRef' in value || 'presentation' in value) return invalid()
  if (!finiteJson(value)) return invalid()
  return value as JournalJob
}

/** Group closure is checked on a complete set, never on a delta subset. */
export function validateJournalGroups(jobs: readonly JournalJob[]): void {
  const byId = new Map(jobs.map(job => [job.jobId, job]))
  for (const job of jobs) if (job.audio) {
    const primary = byId.get(job.audio.primaryJobId)
    if (!primary?.audio || primary.audio.primaryJobId !== primary.jobId || primary.audio.groupId !== job.audio.groupId
      || primary.command !== job.command || primary.audio.memberJobIds?.[job.audio.index] !== job.jobId) return invalid()
    if (job.jobId === primary.jobId && primary.audio.memberJobIds.some(id => !byId.get(id)?.audio || byId.get(id)!.audio!.primaryJobId !== job.jobId)) return invalid()
  }
}

export function validateJournalSnapshot(value: unknown): JournalSnapshot {
  if (!isRecord(value) || value.schemaVersion !== 3 || typeof value.full !== 'boolean'
    || !Array.isArray(value.jobs) || !Array.isArray(value.removed) || typeof value.dataRoot !== 'string' || typeof value.jobsDir !== 'string'
    || value.removed.some((id: unknown) => !identifier(id)) || new Set(value.removed).size !== value.removed.length) return invalid()
  const jobs = value.jobs.map(validateJournalJob)
  if (new Set(jobs.map(job => job.jobId)).size !== jobs.length) return invalid()
  // A delta carries only what changed, so closure is only knowable in full mode.
  if (value.full) validateJournalGroups(jobs)
  return { ...value, jobs } as JournalSnapshot
}

export function primaryJob(job: JournalJob, jobs: readonly JournalJob[]): JournalJob {
  if (!job.audio || job.audio.primaryJobId === job.jobId) return job
  const primary = jobs.find(item => item.jobId === job.audio!.primaryJobId)
  if (!primary) return invalid()
  return primary
}

// Browser previews keep the same individual job records, never a second audio-history blob.
const PREFIX = 'mediaforge:job:'
function previewJobs(): JournalJob[] {
  const jobs: unknown[] = []
  for (let index = 0; index < localStorage.length; index++) {
    const key = localStorage.key(index)
    if (key?.startsWith(PREFIX)) {
      const job = validateJournalJob(JSON.parse(localStorage.getItem(key)!))
      if (key !== PREFIX + job.jobId) return invalid()
      jobs.push(job)
    }
  }
  return validateJournalSnapshot({ schemaVersion: 3, full: true, jobs, removed: [], dataRoot: '', jobsDir: '' }).jobs
}

/** Ask the backend for the catalog; `known` turns the reply into a delta. */
export async function readJournal(known?: Readonly<Record<string, number>>): Promise<JournalSnapshot> {
  if (!isTauriRuntime()) return validateJournalSnapshot({ schemaVersion: 3, full: true, jobs: previewJobs(), removed: [], dataRoot: '', jobsDir: '' })
  return validateJournalSnapshot(await agentJobs(known && Object.keys(known).length ? known : undefined))
}

export async function enqueueAudioJobs(payload: AudioEnqueue): Promise<JournalSnapshot> {
  if (isTauriRuntime()) return validateJournalSnapshot(await enqueueAudioJobsNative(payload))
  const current = previewJobs()
  if (payload.schemaVersion !== 2 || !payload.tasks.length || payload.primaryJobId !== payload.tasks[0].jobId) return invalid()
  const now = Date.now() / 1000
  const jobs: JournalJob[] = payload.tasks.map((task, index) => ({
    schemaVersion: 2, jobId: task.jobId, command: payload.command, source: 'manual', status: 'queued',
    cancelTaskId: payload.primaryJobId, fraction: 0, message: 'queued', error: null, result: null,
    createdAt: now, updatedAt: now, startedAt: null, finishedAt: null, owner: null,
    runtime: { stage: null, progress: null, nodes: {}, order: [] }, logs: [],
    audio: { groupId: payload.groupId, primaryJobId: payload.primaryJobId, index, request: task.request,
      ...(index === 0 ? { memberJobIds: payload.tasks.map(item => item.jobId) } : {}) },
    ...(index === 0 ? { payload: payload.payload, ...(payload.presentation ? { presentation: payload.presentation } : {}) } : { payloadRef: payload.primaryJobId }),
  }))
  if (jobs.some(job => current.some(item => item.jobId === job.jobId))) throw new Error('Task ID already exists')
  validateJournalSnapshot({ schemaVersion: 3, full: true, jobs: [...current, ...jobs], removed: [], dataRoot: '', jobsDir: '' })
  jobs.forEach(job => localStorage.setItem(PREFIX + job.jobId, JSON.stringify(job)))
  return validateJournalSnapshot({ schemaVersion: 3, full: false, jobs, removed: [], dataRoot: '', jobsDir: '' })
}

export type ResultRemovalResponse = {
  schemaVersion: 2
  checkedJobIds: string[]
  removedJobIds: string[]
  trashed: string[]
  failed: { path: string; error: string }[]
  retained: { path: string; reason: string }[]
}
export type ResultRemovalOptions = { deleteFiles?: boolean; checkOnly?: boolean }

function validateRemovalRequest(jobIds: string[]) {
  if (!Array.isArray(jobIds) || jobIds.some(id => !identifier(id)) || new Set(jobIds).size !== jobIds.length) return invalid()
}

function removalResponse(value: unknown, jobIds: string[], options: ResultRemovalOptions, audioGroups = false): ResultRemovalResponse {
  if (!isRecord(value) || value.schemaVersion !== 2
    || !keys(value, ['schemaVersion', 'checkedJobIds', 'removedJobIds', 'trashed', 'failed', 'retained'])
    || !['checkedJobIds', 'removedJobIds'].every(key => Array.isArray(value[key])
      && value[key].every((id: unknown) => identifier(id)) && new Set(value[key]).size === value[key].length)
    || jobIds.some(id => !value.checkedJobIds.includes(id))
    || (!audioGroups || jobIds.length === 0) && value.checkedJobIds.length !== jobIds.length
    || value.removedJobIds.some((id: string) => !value.checkedJobIds.includes(id))
    || !Array.isArray(value.trashed) || value.trashed.some((path: unknown) => !text(path))
    || !Array.isArray(value.failed) || value.failed.some((item: unknown) => !isRecord(item) || !keys(item, ['path', 'error']) || !text(item.path) || !text(item.error))
    || !Array.isArray(value.retained) || value.retained.some((item: unknown) => !isRecord(item) || !keys(item, ['path', 'reason']) || !text(item.path) || !text(item.reason))
    || options.checkOnly && (value.removedJobIds.length > 0 || value.trashed.length > 0 || value.failed.length > 0)
    || !options.deleteFiles && (value.trashed.length > 0 || value.failed.length > 0 || value.retained.length > 0)
    || value.failed.length > 0 && value.removedJobIds.length > 0
    || !options.checkOnly && value.failed.length === 0 && value.removedJobIds.length !== value.checkedJobIds.length) return invalid()
  return value as ResultRemovalResponse
}

export async function removeAudioJobs(jobIds: string[], options: ResultRemovalOptions = {}): Promise<ResultRemovalResponse> {
  validateRemovalRequest(jobIds)
  if (isTauriRuntime()) return removalResponse(await removeAudioJobsNative(jobIds, options), jobIds, options, true)
  const jobs = previewJobs()
  const groups = new Set(jobIds.map(id => {
    const job = jobs.find(row => row.jobId === id)
    if (!job?.audio) throw new Error('Audio job is missing or is not an audio group member')
    return job.audio.primaryJobId
  }))
  const removing = jobs.filter(job => job.audio && groups.has(job.audio.primaryJobId))
  if (removing.some(job => !['completed', 'failed', 'cancelled', 'interrupted'].includes(job.status) || job.owner !== null)) throw new Error('Cannot remove an active audio group')
  if (options.deleteFiles && !options.checkOnly) throw new Error('File recycling requires the desktop app')
  if (!options.checkOnly) removing.forEach(job => localStorage.removeItem(PREFIX + job.jobId))
  const checkedJobIds = removing.map(job => job.jobId).sort()
  return removalResponse({ schemaVersion: 2, checkedJobIds, removedJobIds: options.checkOnly ? [] : checkedJobIds, trashed: [], failed: [], retained: [] }, jobIds, options, true)
}

export async function removeResultJobs(jobIds: string[], options: ResultRemovalOptions = {}): Promise<ResultRemovalResponse> {
  validateRemovalRequest(jobIds)
  if (isTauriRuntime()) return removalResponse(await removeResultJobsNative(jobIds, options), jobIds, options)
  const jobs = previewJobs()
  const removing = jobIds.map(id => {
    const job = jobs.find(row => row.jobId === id)
    if (!job || job.audio || !RESULT_JOB_COMMANDS.has(job.command)) throw new Error('Result job is missing or is not a generic result')
    if (!['completed', 'failed', 'cancelled', 'interrupted'].includes(job.status) || job.owner !== null) throw new Error('Cannot remove an active result job')
    return job
  })
  if (options.deleteFiles && !options.checkOnly) throw new Error('File recycling requires the desktop app')
  if (!options.checkOnly) removing.forEach(job => localStorage.removeItem(PREFIX + job.jobId))
  return removalResponse({ schemaVersion: 2, checkedJobIds: jobIds, removedJobIds: options.checkOnly ? [] : jobIds, trashed: [], failed: [], retained: [] }, jobIds, options)
}
