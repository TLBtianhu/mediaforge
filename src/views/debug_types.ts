// DebugView 的本地数据结构（从组件抽出，便于复用与独立演进）。
import type { LogRecord } from '@/utils/logRecord'

export type DebugTab = 'overview' | 'runtime' | 'activity' | 'models' | 'events' | 'logs' | 'runtime-tools' | 'updates'
export type CatalogEditMode = 'simple' | 'advanced'
export type DebugLogLevelFilter = 'all' | 'error' | 'warn' | 'info' | 'debug' | 'unclassified'
export type DebugLogCategoryFilter = 'all' | 'issues' | 'traceback' | 'pymss' | 'worker' | 'app' | 'runtime' | 'store' | 'other'
export type DebugCatalogInfo = {
  baseCatalogPath: string
  debugCatalogPath: string
  debugDir: string
  baseCatalog: Record<string, unknown>
  debugCatalog: Record<string, unknown>
  effectiveCatalog: Record<string, unknown>
  status: Record<string, any>
}
export type DebugModelConfig = {
  model: string
  source: string
  readOnly: boolean
  baseConfigPath: string | null
  effectiveConfigPath: string | null
  baseContent: string
  effectiveContent: string
  downloadedConfigExists: boolean
}
export type DebugRuntimeFileInfo = {
  kind: string
  source: string
  path: string
  exists: boolean
  editable: boolean
  content?: string | null
  backupPath: string
  backupExists: boolean
}
export type DebugRuntimePointers = {
  runtimeEnvsDir: string
  activeRuntimeFile: string
  files: DebugRuntimeFileInfo[]
}
export type RuntimeTreeChild = {
  name: string
  role: string
  path: string
}
export type RuntimeTreeNode = RuntimeTreeChild & {
  source: string
  children: RuntimeTreeChild[]
}
export type RuntimeDebugEnvironmentRow = {
  backend: string
  pythonPath: string
  editablePath: string
  editableFile: DebugRuntimeFileInfo | null
}
export type DebugWorkerEvent = {
  type?: string
  taskId?: string
  timestamp?: string
  payload?: unknown
}
export type DebugWorkerEventGroup = {
  key: string
  taskId: string | null
  latestType: string
  firstTimestamp?: string
  lastTimestamp?: string
  eventCount: number
  latestPayload?: unknown
  typeCounts: Record<string, number>
  events: DebugWorkerEvent[]
}
export type DebugLogInfo = {
  path: string
  logsDir: string
  exists: boolean
  sizeBytes: number
  persistentPath: string
  persistentExists: boolean
  persistentSizeBytes: number
  maxBytes: number
  persistentMaxBytes: number
  reportPath: string
  reportExists: boolean
  reportSizeBytes: number
}
export type DebugLogContent = {
  path: string
  content: string
  sizeBytes: number
  truncated: boolean
}
export type DebugLogReport = {
  path: string
  exists: boolean
  sizeBytes: number
}
export type ParsedDebugLogLine = {
  record: LogRecord
  id: string
  timestamp: string
  severity: DebugLogLevelFilter
  level: string
  source: string
  sourceKind: string
  category: DebugLogCategoryFilter
  taskId: string
  command: string
  stage: string
  message: string
  messageContext: string
  details: string
  location: string
  traceback: string
  hasIssue: boolean
  fields: Record<string, string>
  raw: string
}

