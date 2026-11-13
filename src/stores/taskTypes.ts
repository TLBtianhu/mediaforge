import type { TaskStatus } from '@/features/tasks/lifecycle'
import type { StemOutput } from '@/utils/stemOutputs'
import type { WorkflowNodeLogs } from '@/workflows/nodeLogs'
import type { LogEntry } from '@/utils/logRecord'
import type { WorkflowWorkbenchSnapshot, WorkflowNodeState } from '@/workflows/workbench'
import type { ModelDefaultInferenceParams } from '@/stores/model'

export type { TaskStatus } from '@/features/tasks/lifecycle'
export type { StemOutput } from '@/utils/stemOutputs'

export type OutputLayout = 'folders' | 'flat'
export type ModelListSortMode = 'usage' | 'recent' | 'favorite' | 'name-asc' | 'name-desc'

export type OutputNamingConfig = {
  enabled: boolean
  template: string
  stemOrder: string[]
}

export type SeparationRunConfig = {
  runMode?: 'model' | 'workflow'
  modelDir: string | null
  downloadSource: string
  downloadMethod: string
  workflowId?: string
  workflowName?: string
  workflowDefinition?: Record<string, unknown>
  workbench?: WorkflowWorkbenchSnapshot
  modelType?: string | null
  outputLayout: OutputLayout
  outputNaming?: OutputNamingConfig
  device: string
  deviceIds: number[]
  outputFormat: string
  selectedStems: string[]
  useTta: boolean
  debug: boolean
  audioParams: Record<string, string | number>
  inferenceParamsVersion?: number
  inferenceParams: Record<string, unknown>
}


export type ScanMediaPathsResult = {
  files: string[]
  warnings: string[]
}

export type SeparationTask = {
  id: string
  jobId?: string
  batchIndex?: number
  model: string
  input: string
  jobOutput?: string
  output: string
  outputDirectories?: string[]
  status: TaskStatus
  message: string
  createdAt: number
  updatedAt: number
  startedAt?: number
  finishedAt?: number
  durationMs?: number
  progress: number
  stageLabel: string
  progressCurrent?: number
  progressTotal?: number
  progressDetail?: string
  files: string[]
  outputs: StemOutput[]
  /** Plugin graph leaf values (ASR text, measurements, MIDI paths) by node. */
  nodeValues?: { nodeId: number; nodeType: string; slot: number; value: string }[]
  logs: LogEntry[]
  nodeLogs?: WorkflowNodeLogs
  error?: string
  runConfig?: SeparationRunConfig
  nodeStates?: Record<string, WorkflowNodeState>
  nodeExecutionOrder?: string[]
  currentNodeId?: string
}

export type SeparationJob = {
  id: string
  output: string
  tasks: SeparationTask[]
  primary: SeparationTask
  model: string
  inputCount: number
  outputCount: number
  createdAt: number
  updatedAt: number
  startedAt?: number
  finishedAt?: number
  durationMs?: number
  status: TaskStatus
  progress: number
}

export type PersistedSeparateModelState = {
  batch_size?: number | null
  overlap_size?: number | null
  num_overlap?: number | null
  chunk_size?: number | null
  standardize?: boolean
  normalize?: boolean
  window_size?: number | null
  aggression?: number | null
  enable_post_process?: boolean
  post_process_threshold?: number | null
  high_end_process?: boolean
  selectedStems?: string[]
  selectedStemsCleared?: boolean
}

export type PersistedSeparateState = {
  version?: number
  runMode?: 'model' | 'workflow'
  ensembleEnabled?: boolean
  ensembleModels?: string[]
  ensembleStem?: string
  ensembleModelStems?: Record<string, string>
  ensembleType?: string
  ensembleWeights?: Record<string, number>
  outputLayout?: OutputLayout
  outputNamingTemplate?: string
  customStemOrder?: string[]
  modelListViewMode?: 'card' | 'list'
  modelListSortMode?: ModelListSortMode
  modelListPageSize?: number
  useTta?: boolean
  inferenceParamsByModel?: Record<string, PersistedSeparateModelState>
}

export type ModelInferenceUiDefaults = {
  [K in keyof ModelDefaultInferenceParams]: ModelDefaultInferenceParams[K] | null
}

