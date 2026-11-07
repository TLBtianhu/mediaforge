/** Read-only file results from the persistent task journal; never audio-store tasks. */
export type JournalResultJob = {
  jobId: string
  command: string
  status: string
  source?: string
  cancelTaskId?: string
  createdAt?: number
  updatedAt?: number
  finishedAt?: number | null
  message?: string
  error?: string | null
  payload?: Record<string, unknown>
  result?: Record<string, unknown> | null
}

export type JournalOutput = { path: string; name: string; size?: number; preview: 'image' | 'video' | 'audio' | null }
export type JournalResult = {
  id: string
  title: string
  operation: string
  source: 'manual' | 'agent'
  status: string
  createdAt: number
  updatedAt: number
  message: string
  inputs: string[]
  outputs: JournalOutput[]
}
export type JournalResultSort = 'time_desc' | 'time_asc' | 'name_asc' | 'name_desc'

const TERMINAL = new Set(['completed', 'done', 'failed', 'cancelled', 'interrupted'])
const AUDIO_RUNS = new Set(['infer', 'infer_workflow'])
const RESULT_COMMANDS = new Set(['infer', 'infer_workflow', 'audio_tools', 'media_tool', 'media_image', 'media_video', 'media_workflow', 'media_convert', 'run_operation'])
const NON_RESULT_OPERATIONS = new Set(['media_probe', 'probe', 'inspect', 'model_download', 'runtime_install'])
const IMAGE = new Set(['png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp', 'avif'])
const VIDEO = new Set(['mp4', 'mkv', 'mov', 'webm', 'avi', 'm4v'])
const AUDIO = new Set(['wav', 'mp3', 'flac', 'm4a', 'aac', 'ogg', 'opus'])
const record = (value: unknown): value is Record<string, unknown> => Boolean(value) && typeof value === 'object' && !Array.isArray(value)
const text = (value: unknown) => typeof value === 'string' ? value : ''
export const journalFileName = (path: string) => path.split(/[\\/]/).pop() || path
const timestamp = (value: unknown) => typeof value === 'number' && Number.isFinite(value) && value > 0 ? value < 1e12 ? value * 1000 : value : 0

function outputFiles(result: Record<string, unknown>, payload: Record<string, unknown>): JournalOutput[] {
  const raw = result.outputs
  const values = Array.isArray(raw) ? raw : record(raw) ? Object.values(raw) : []
  const directories = new Set([text(result.outputDir), text(result.outputDirectory), text(payload.outputDir)].filter(Boolean))
  const seen = new Set<string>()
  return values.flatMap(value => {
    // Only explicit output descriptors count; a scalar/path-looking value or
    // an output directory does not prove that a file was produced.
    if (!record(value)) return []
    const path = text(value.path)
    const kind = text(value.kind).toLowerCase()
    if (!path.trim() || /[\\/]$/.test(path) || directories.has(path) || ['directory', 'folder', 'scalar', 'array', 'object'].includes(kind)) return []
    if (/^[a-z][a-z0-9+.-]*:\/\//i.test(path)) return []
    const name = journalFileName(path)
    if (!kind.endsWith('file') && !/\.[^./\\]+$/.test(name)) return []
    if (seen.has(path)) return []
    seen.add(path)
    const extension = name.split('.').pop()?.toLowerCase() || ''
    const preview = IMAGE.has(extension) ? 'image' : VIDEO.has(extension) ? 'video' : AUDIO.has(extension) ? 'audio' : null
    return [{ path, name, ...(typeof value.size === 'number' && Number.isFinite(value.size) && value.size >= 0 ? { size: value.size } : {}), preview }]
  })
}

export function buildJournalResults(jobs: readonly JournalResultJob[], audioTaskIds: ReadonlySet<string> = new Set()): JournalResult[] {
  const results = new Map<string, JournalResult>()
  for (const job of jobs) {
    if (!TERMINAL.has(job.status) || !RESULT_COMMANDS.has(job.command)) continue
    const payload = job.payload || {}
    const result = job.result || {}
    const operation = text(result.operation) || text(payload.operation) || job.command
    if (NON_RESULT_OPERATIONS.has(operation)) continue
    const source = job.source === 'manual' ? 'manual' : 'agent'
    if (AUDIO_RUNS.has(job.command)) {
      // Manual audio owns deletion in task-history; showing its journal mirror
      // would resurrect a result after the user deleted the audio record.
      if (source === 'manual') continue
      const identities = [job.jobId, job.cancelTaskId, text(payload.taskId), text(payload.jobId)]
      if (identities.some(id => id && audioTaskIds.has(id))) continue
    }
    const outputs = outputFiles(result, payload)
    if (!outputs.length) continue
    const rawInputs = payload.inputs
    const inputs = Array.isArray(rawInputs) ? rawInputs.filter((value): value is string => typeof value === 'string' && Boolean(value.trim()))
      : record(rawInputs) ? Object.values(rawInputs).flatMap(value => record(value) && typeof value.path === 'string' ? [value.path] : [])
        : [text(payload.input) || text(payload.inputPath)].filter(Boolean)
    const createdAt = timestamp(job.createdAt)
    const updatedAt = timestamp(job.finishedAt) || timestamp(job.updatedAt) || createdAt
    const row: JournalResult = {
      id: job.jobId, title: journalFileName(inputs[0] || outputs[0].path), operation, source,
      status: job.status, createdAt, updatedAt, message: text(job.error) || text(job.message), inputs, outputs,
    }
    const previous = results.get(row.id)
    if (!previous || previous.updatedAt <= updatedAt) results.set(row.id, row)
  }
  return [...results.values()]
}

export function filterJournalResults(results: readonly JournalResult[], query: string, sort: JournalResultSort): JournalResult[] {
  const keyword = query.trim().toLowerCase()
  const filtered = results.filter(row => !keyword || [row.id, row.title, row.operation, row.source, row.status, row.message,
    ...row.inputs, ...row.outputs.flatMap(output => [output.name, output.path])].join(' ').toLowerCase().includes(keyword))
  return [...filtered].sort((left, right) => {
    if (sort === 'name_asc') return left.title.localeCompare(right.title) || right.updatedAt - left.updatedAt
    if (sort === 'name_desc') return right.title.localeCompare(left.title) || right.updatedAt - left.updatedAt
    return sort === 'time_asc' ? left.updatedAt - right.updatedAt : right.updatedAt - left.updatedAt
  })
}
