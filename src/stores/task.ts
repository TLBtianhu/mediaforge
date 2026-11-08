import { createLogger } from '@/utils/logger'
import { defineStore } from 'pinia'
import { computed, nextTick, ref, watch } from 'vue'
import { invoke } from '@tauri-apps/api/core'
import i18n from '@/i18n'
import { isTauriRuntime, loadAppStore, saveAppStore } from '@/utils/appStore'
import { useSettingsStore } from '@/stores/settings'
import { useModelStore, type ModelDefaultInferenceParams } from '@/stores/model'
import { useAppStore } from '@/stores/app'
import type { WorkflowEntry } from '@/stores/workflow'
import type { WorkflowNodeLogs } from '@/workflows/nodeLogs'
import type { LogEntry } from '@/utils/logRecord'
import { detectWorkflowFormat, WORKFLOW_FORMAT_VERSION } from '@/workflows/formats'
import {
  prepareWorkflowDefinitionForRun,
  resolveWorkflowRuntimeDefaults,
} from '@/workflows/runtimeDefinition'
import {
  isInterruptedTaskStatus,
  isTerminalTaskStatus,
  selectQueuedJobGroups,
  taskJobId,
  type TaskStatus,
} from '@/features/tasks/lifecycle'
import { normalizeStemOutputs, stemFromOutputPath, type StemOutput } from '@/utils/stemOutputs'
import { normalizeOutputPath, resolveOutputDirectory, joinOutputPath, createRunId, inputStemSegment } from '@/stores/taskOutputPaths'
import { CURRENT_INFERENCE_PARAMS_VERSION, validateSeparateState, STAGE_META, normalizeOutputLayout, normalizeOutputNaming } from '@/stores/taskNormalize'
import { JOURNAL_POLL_INTERVAL_MS, useMediaJobsStore } from '@/stores/mediaJobs'
import { audioTasksFromJobs, audioEnqueueFromTasks } from '@/features/tasks/audioJobs'
import { enqueueAudioJobs, removeAudioJobs, type ResultRemovalResponse } from '@/features/tasks/journal'
import { makeTaskRunConfig } from '@/stores/taskRunConfig'
import { makeTaskQueue } from '@/stores/taskQueue'
import { completeTaskBatch, finalizeTaskBatch, prepareTaskBatch } from '@/stores/taskBatch'
import { validateSeparateWorkflowInput, workflowDefinitionValidationError } from '@/stores/taskValidation'
import { resolveRetryOutput } from '@/stores/taskRetry'
import { buildInferenceParams as buildInferenceParamsFromValues, modelInferenceParamsFromState as modelInferenceParamsFromValues } from '@/stores/taskInferenceParams'
import { buildJobs, findJobById } from '@/stores/taskJobs'
import { getRemovalTasks as getRemovalTasksFromState, primaryRevealPath } from '@/stores/taskResultCleanup'
import { ZERO_AS_UNSET_FIELDS, normalizeEditableNumberValue, resolveNumberOverride, resolveBooleanOverride, preferCurrentOverride, applyModelDefaultsToUi, isVrModelType, isApolloModelType, type InferenceNumberField } from '@/stores/taskInference'
import { isMainTaskOwner } from '@/utils/taskOwner'
import { cloneWorkflow, prepareWorkbenchRun, workflowSourceDefinition, type WorkflowRuntimeBindings, type WorkflowWorkbenchSnapshot, type WorkflowNodeState } from '@/workflows/workbench'
import { SEPARATE_MODEL_PAGE_SIZES, normalizePageSize } from '@/utils/pagination'

const studioLog = createLogger('ui.stores.task')

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


type ScanMediaPathsResult = {
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

type PersistedSeparateModelState = {
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

type PersistedSeparateState = {
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

type ModelInferenceUiDefaults = {
  [K in keyof ModelDefaultInferenceParams]: ModelDefaultInferenceParams[K] | null
}

const AUDIO_EXTENSIONS = ['wav', 'mp3', 'flac', 'm4a', 'aac', 'ogg', 'opus']
const VIDEO_EXTENSIONS = ['mp4', 'mkv', 'mov', 'avi', 'webm', 'flv']


function isSupportedInputPath(path: string) {
  const ext = path.split('.').pop()?.toLowerCase() || ''
  return AUDIO_EXTENSIONS.includes(ext) || VIDEO_EXTENSIONS.includes(ext)
}



function normalizePersistedModelState(value: unknown): PersistedSeparateModelState {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {}
  const raw = value as Record<string, unknown>
  const numberFields = ['batch_size', 'overlap_size', 'num_overlap', 'chunk_size', 'window_size', 'aggression', 'post_process_threshold']
  const next: PersistedSeparateModelState = {}
  numberFields.forEach((key) => {
    const item = raw[key]
    if (item === null) (next as Record<string, unknown>)[key] = null
    else if (typeof item === 'number' && Number.isFinite(item)) (next as Record<string, unknown>)[key] = item
  })
  ;['standardize', 'normalize', 'enable_post_process', 'high_end_process'].forEach((key) => {
    if (typeof raw[key] === 'boolean') (next as Record<string, unknown>)[key] = raw[key]
  })
  if (Array.isArray(raw.selectedStems)) {
    next.selectedStems = raw.selectedStems.map(item => String(item || '').trim()).filter(Boolean)
  }
  if (typeof raw.selectedStemsCleared === 'boolean') {
    next.selectedStemsCleared = raw.selectedStemsCleared
  }
  return next
}





function resolveTaskErrorMessage(code: unknown, message: unknown, input?: string) {
  const detail = typeof message === 'string' ? message : ''
  const path = typeof input === 'string' ? input : ''
  if (code === 'INPUT_AUDIO_STREAM_MISSING') {
    return i18n.global.t('separate.inputAudioStreamMissing', { path })
  }
  if (code === 'INPUT_MEDIA_UNSUPPORTED') {
    return i18n.global.t('separate.inputMediaUnsupported', { path })
  }
  return detail || i18n.global.t('toast.taskFailed')
}

export const useTaskStore = defineStore('task', () => {
  const initialized = ref(false)
  const loadError = ref('')
  const tasks = ref<SeparationTask[]>([])
  const journal = useMediaJobsStore()
  const acceptedGroupIds = new Set<string>()
  const inFlightGroupIds = new Set<string>()
  const retryDispatchAfter = new Map<string, number>()
  function syncJournalTasks() {
    const previous = new Map(tasks.value.map(task => [task.id,task]))
    tasks.value = audioTasksFromJobs(journal.jobs, windowsPaths.value, resolveTaskErrorMessage).map(projected => {
      const current = previous.get(projected.id)
      if (!current) return projected
      for (const key of Object.keys(current)) if (!(key in projected)) delete (current as unknown as Record<string, unknown>)[key]
      return Object.assign(current,projected)
    })
    const activeGroups = new Set(tasks.value.filter(task => !isTerminalTaskStatus(task.status)).map(taskJobId))
    for (const id of acceptedGroupIds) if (!activeGroups.has(id)) acceptedGroupIds.delete(id)
    for (const id of retryDispatchAfter.keys()) if (!activeGroups.has(id)) retryDispatchAfter.delete(id)
  }
  watch(() => journal.jobs, () => {
    if (!initialized.value) return
    syncJournalTasks()
    scheduleQueue()
  }, { deep: true })
  const activeTaskId = ref<string | null>(null)
  // Session-only queue visibility. Persisted task/result history remains the
  // source for Results and recovery, while this set intentionally starts empty
  // on every application launch.
  const sessionTaskIds = ref<Set<string>>(new Set())
  const focusedResultTaskId = ref<string | null>(null)
  const focusedTaskId = ref<string | null>(null)
  const inputFiles = ref<string[]>([])
  const separateRunMode = ref<'model' | 'workflow'>('model')
  const ensembleEnabled = ref(false)
  const ensembleModels = ref<string[]>([])
  const ensembleStem = ref('')
  const ensembleModelStems = ref<Record<string, string>>({})
  const ensembleType = ref('avg_wave')
  const ensembleWeights = ref<Record<string, number>>({})
  // Retain directory overrides across route changes, but inherit Settings after restarting.
  const separateTemporaryOutputDir = ref('')
  const separateOutputDir = computed(() => resolveOutputDirectory(separateTemporaryOutputDir.value, useSettingsStore().outputDir, windowsPaths.value))
  const windowsPaths = computed(() => {
    const app = useAppStore()
    const platform = app.envInfo?.platform || app.runtimeInfo?.platform
      || (typeof navigator !== 'undefined' ? navigator.platform : '')
    return /^win/i.test(platform)
  })
  const separateOutputLayout = ref<OutputLayout>('flat')
  const separateOutputNamingTemplate = ref('%index%_%filename%_%stem%')
  const separateCustomStemOrder = ref<string[]>([])
  const modelListViewMode = ref<'card' | 'list'>('card')
  const modelListSortMode = ref<ModelListSortMode>('usage')
  const modelListPageSize = ref(12)
  const useTta = ref(false)
  const batch_size = ref<number | null>(1)
  const overlap_size = ref<number | null>(0)
  const num_overlap = ref<number | null>(0)
  const chunk_size = ref<number | null>(0)
  const standardize = ref(false)
  const normalize = ref(false)
  const selectedStems = ref<string[]>([])
  const window_size = ref<number | null>(0)
  const aggression = ref<number | null>(0)
  const enable_post_process = ref(false)
  const post_process_threshold = ref<number | null>(0)
  const high_end_process = ref(false)
  const selectedModelDefaults = ref<ModelDefaultInferenceParams>({})
  const selectedModelUiDefaults = ref<ModelInferenceUiDefaults>({})
  const selectedModelOverrides = ref<ModelDefaultInferenceParams>({})
  const selectedModelType = ref<string | null>(null)
  const selectedStemsCleared = ref(false)
  const inferenceParamsDirty = ref(false)
  const persistedSeparateModelState = ref<Record<string, PersistedSeparateModelState>>({})

  const inputPath = computed(() => inputFiles.value[0] || '')
  const activeTask = computed(() => tasks.value.find((task) => task.id === activeTaskId.value) || null)
  const completedTasks = computed(() => tasks.value.filter((task) => task.status === 'done'))
  const runningTasks = computed(() => tasks.value.filter((task) => !isTerminalTaskStatus(task.status)))
  const activeWorkerTasks = computed(() => tasks.value.filter((task) => !isTerminalTaskStatus(task.status) && task.status !== 'queued'))
  // 任务页可见任务：排除已在任务页隐藏的记录（结果页隐藏不影响任务页）
  const taskBoardTasks = computed(() => tasks.value)
  // 结果页可见结果：仅含有输出且未在结果页隐藏的任务（任务页隐藏不影响结果页）
  const resultTasks = computed(() => completedTasks.value.filter((task) => task.outputs.length > 0))
  const queuedTasks = computed(() => tasks.value.filter((task) => task.status === 'queued'))
  const failedTasks = computed(() => tasks.value.filter((task) => task.status === 'failed'))
  const allJobs = computed(() => buildJobs(tasks.value))
  const resultJobs = computed(() => buildJobs(resultTasks.value))
  const sessionJobs = computed(() => buildJobs(tasks.value.filter(task => sessionTaskIds.value.has(task.id))))

  let separateStateSaveTimer: ReturnType<typeof setTimeout> | null = null
  let applyingModelDefaults = false

  function separateStateSnapshot(): PersistedSeparateState {
    return {
      version: 1,
      runMode: separateRunMode.value,
      ensembleEnabled: ensembleEnabled.value,
      ensembleModels: [...ensembleModels.value],
      ensembleStem: ensembleStem.value,
      ensembleModelStems: { ...ensembleModelStems.value },
      ensembleType: ensembleType.value,
      ensembleWeights: { ...ensembleWeights.value },
      outputLayout: separateOutputLayout.value,
      outputNamingTemplate: separateOutputNamingTemplate.value,
      customStemOrder: [...separateCustomStemOrder.value],
      modelListViewMode: modelListViewMode.value,
      modelListSortMode: modelListSortMode.value,
      modelListPageSize: modelListPageSize.value,
      useTta: useTta.value,
      inferenceParamsByModel: JSON.parse(JSON.stringify(persistedSeparateModelState.value)),
    }
  }

  function persistSeparateState() {
    if (!initialized.value) return
    void saveAppStore('separate-state', separateStateSnapshot()).catch((error) => {
      studioLog.warn('Failed to persist separate settings', error)
    })
  }

  function queueSeparateStatePersist() {
    if (!initialized.value) return
    if (separateStateSaveTimer) clearTimeout(separateStateSaveTimer)
    separateStateSaveTimer = setTimeout(() => {
      separateStateSaveTimer = null
      persistSeparateState()
    }, 120)
  }

  function saveCurrentModelState(modelName: string) {
    const name = String(modelName || '').trim()
    if (!name) return
    const current = persistedSeparateModelState.value[name] || {}
    persistedSeparateModelState.value[name] = {
      ...current,
      selectedStems: [...selectedStems.value],
      selectedStemsCleared: selectedStemsCleared.value,
    }
    queueSeparateStatePersist()
  }

  function getSavedModelState(modelName: string) {
    return persistedSeparateModelState.value[String(modelName || '').trim()]
  }

  function getJobById(id: string | null | undefined) {
    return findJobById(allJobs.value, id)
  }

  function rememberSessionTasks(items: readonly Pick<SeparationTask, 'id'>[]) {
    if (!items.length) return
    const next = new Set(sessionTaskIds.value)
    items.forEach(item => next.add(item.id))
    sessionTaskIds.value = next
  }

  function removeSessionJob(jobId: string) {
    const next = new Set(sessionTaskIds.value)
    let removed = 0
    tasks.value.forEach((task) => {
      if (taskJobId(task) === jobId && next.delete(task.id)) removed += 1
    })
    if (removed) sessionTaskIds.value = next
    return removed
  }

  function clearFinishedSessionJobs(jobIds?: readonly string[]) {
    const allowedIds = jobIds ? new Set(jobIds) : null
    const finishedJobIds = new Set(sessionJobs.value
      .filter(job => isTerminalTaskStatus(job.status) && (!allowedIds || allowedIds.has(job.id)))
      .map(job => job.id))
    if (!finishedJobIds.size) return 0
    const next = new Set(sessionTaskIds.value)
    tasks.value.forEach((task) => {
      if (finishedJobIds.has(taskJobId(task))) next.delete(task.id)
    })
    sessionTaskIds.value = next
    return finishedJobIds.size
  }

  async function enqueueTasks(drafts: SeparationTask[]): Promise<SeparationTask[]> {
    if (!drafts.length) return []
    if (!initialized.value) await initialize()
    if (loadError.value || journal.error) throw new Error(loadError.value || journal.error)
    const accepted = await enqueueAudioJobs(audioEnqueueFromTasks(drafts))
    journal.acceptJobs(accepted)
    syncJournalTasks()
    const ids = new Set(drafts.map(task => task.id))
    const confirmed = tasks.value.filter(task => ids.has(task.id)).sort((a,b) => (a.batchIndex || 0) - (b.batchIndex || 0))
    rememberSessionTasks(confirmed)
    activeTaskId.value = confirmed[0]?.id || activeTaskId.value
    return confirmed
  }

  let initializationPromise: Promise<void> | null = null
  async function initialize(): Promise<void> {
    if (initialized.value) return
    if (initializationPromise) return initializationPromise
    initializationPromise = (async () => {
    try {
    const [, separateStored] = await Promise.all([journal.initialize(), loadAppStore<PersistedSeparateState>('separate-state')])
    if (journal.error) throw new Error(journal.error)
    const currentTasks = audioTasksFromJobs(journal.jobs, windowsPaths.value, resolveTaskErrorMessage)
    validateSeparateState(separateStored)
    tasks.value = currentTasks
    activeTaskId.value = tasks.value[0]?.id || null
    separateRunMode.value = 'model'
    ensembleEnabled.value = separateStored?.ensembleEnabled === true
    ensembleModels.value = Array.isArray(separateStored?.ensembleModels)
      ? separateStored.ensembleModels.map(item => String(item || '').trim()).filter(Boolean)
      : []
    ensembleStem.value = String(separateStored?.ensembleStem || '')
    ensembleModelStems.value = Object.fromEntries(
      Object.entries(separateStored?.ensembleModelStems || {}).flatMap(([name, value]) => {
        const stem = String(value || '').trim()
        return stem ? [[name, stem]] : []
      }),
    )
    ensembleType.value = String(separateStored?.ensembleType || 'avg_wave')
    ensembleWeights.value = Object.fromEntries(
      Object.entries(separateStored?.ensembleWeights || {}).flatMap(([name, value]) => {
        const weight = Number(value)
        return Number.isFinite(weight) && weight >= 0 ? [[name, weight]] : []
      }),
    )
    separateOutputLayout.value = normalizeOutputLayout(separateStored?.outputLayout)
    separateOutputNamingTemplate.value = String(separateStored?.outputNamingTemplate || '%index%_%filename%_%stem%')
    separateCustomStemOrder.value = Array.isArray(separateStored?.customStemOrder)
      ? separateStored.customStemOrder.map(item => String(item || '').trim()).filter(Boolean)
      : []
    modelListViewMode.value = separateStored?.modelListViewMode === 'list' ? 'list' : 'card'
    modelListSortMode.value = ['recent', 'favorite', 'name-asc', 'name-desc'].includes(String(separateStored?.modelListSortMode))
      ? separateStored?.modelListSortMode as ModelListSortMode
      : 'usage'
    modelListPageSize.value = normalizePageSize(separateStored?.modelListPageSize, SEPARATE_MODEL_PAGE_SIZES, 12)
    useTta.value = separateStored?.useTta === true
    persistedSeparateModelState.value = Object.fromEntries(
      Object.entries(separateStored?.inferenceParamsByModel || {})
        .map(([name, value]) => {
          const normalized = normalizePersistedModelState(value)
          const state: PersistedSeparateModelState = {}
          if (Array.isArray(normalized.selectedStems)) state.selectedStems = [...normalized.selectedStems]
          if (typeof normalized.selectedStemsCleared === 'boolean') state.selectedStemsCleared = normalized.selectedStemsCleared
          return [name, state]
        }),
    )
    initialized.value = true
    loadError.value = ''
    } catch (error) {
      loadError.value = error instanceof Error ? error.message : String(error)
      throw error
    }
    })().finally(() => { initializationPromise = null })
    return initializationPromise
  }

  watch(
    [separateRunMode, ensembleEnabled, ensembleModels, ensembleStem, ensembleModelStems, ensembleType, ensembleWeights,
      separateOutputLayout, separateOutputNamingTemplate, separateCustomStemOrder, modelListViewMode, modelListSortMode, modelListPageSize, useTta],
    () => {
      if (applyingModelDefaults) return
      queueSeparateStatePersist()
    },
    { deep: true },
  )

  watch(
    [batch_size, overlap_size, num_overlap, chunk_size, standardize, normalize, window_size, aggression, enable_post_process,
      post_process_threshold, high_end_process],
    () => {
      if (applyingModelDefaults) return
      inferenceParamsDirty.value = true
      const modelStore = useModelStore()
      if (modelStore.selectedModel) saveCurrentModelState(modelStore.selectedModel)
    },
  )

  watch(
    [selectedStems, selectedStemsCleared],
    () => {
      if (applyingModelDefaults) return
      const modelStore = useModelStore()
      if (modelStore.selectedModel) saveCurrentModelState(modelStore.selectedModel)
      else queueSeparateStatePersist()
    },
    { deep: true },
  )

  watch(() => useSettingsStore().maxConcurrentSeparations, () => {
    scheduleQueue()
  })

  function applySelectedModelDefaults(
    baseDefaults: ModelDefaultInferenceParams | undefined,
    modelType?: string | null,
    saved?: PersistedSeparateModelState,
    modelOverrides?: ModelDefaultInferenceParams,
    options: { force?: boolean } = {},
  ) {
    selectedModelDefaults.value = baseDefaults ? { ...baseDefaults } : {}
    selectedModelOverrides.value = modelOverrides ? { ...modelOverrides } : {}
    selectedModelType.value = modelType ?? null
    const next = applyModelDefaultsToUi(baseDefaults, modelType)
    const modelOverrideValue = <K extends keyof ModelDefaultInferenceParams>(key: K) => (
      modelOverrides && Object.prototype.hasOwnProperty.call(modelOverrides, key) ? modelOverrides[key] : undefined
    )
    const modelDefaultValue = <K extends keyof ModelDefaultInferenceParams>(key: K) => (
      baseDefaults && Object.prototype.hasOwnProperty.call(baseDefaults, key) ? baseDefaults[key] : undefined
    )
    const savedValue = <K extends keyof ModelDefaultInferenceParams & keyof PersistedSeparateModelState>(key: K, fallback: PersistedSeparateModelState[K]) => (
      saved && Object.prototype.hasOwnProperty.call(saved, key) ? saved[key] : fallback
    )
    const modelOrSavedValue = <K extends keyof ModelDefaultInferenceParams & keyof PersistedSeparateModelState>(key: K) => {
      const override = modelOverrideValue(key)
      if (override !== undefined) return override
      const modelDefault = modelDefaultValue(key)
      if (modelDefault !== undefined) return modelDefault
      return savedValue(key, next[key])
    }
    const valueOrDefault = <T>(value: T | undefined, fallback: T): T => value === undefined ? fallback : value
    const resolvedDefaults = {
      batch_size: valueOrDefault(modelOrSavedValue('batch_size'), 1),
      overlap_size: valueOrDefault(modelOrSavedValue('overlap_size'), 0),
      num_overlap: valueOrDefault(modelOrSavedValue('num_overlap'), 0),
      chunk_size: valueOrDefault(modelOrSavedValue('chunk_size'), 0),
      standardize: valueOrDefault(modelOrSavedValue('standardize'), false),
      normalize: valueOrDefault(modelOrSavedValue('normalize'), false),
      window_size: valueOrDefault(modelOrSavedValue('window_size'), 0),
      aggression: valueOrDefault(modelOrSavedValue('aggression'), 0),
      enable_post_process: valueOrDefault(modelOrSavedValue('enable_post_process'), false),
      post_process_threshold: valueOrDefault(modelOrSavedValue('post_process_threshold'), 0),
      high_end_process: valueOrDefault(modelOrSavedValue('high_end_process'), false),
    }
    selectedModelUiDefaults.value = { ...resolvedDefaults }
    if (inferenceParamsDirty.value && !options.force) return

    applyingModelDefaults = true
    batch_size.value = resolvedDefaults.batch_size
    overlap_size.value = resolvedDefaults.overlap_size
    num_overlap.value = resolvedDefaults.num_overlap
    chunk_size.value = resolvedDefaults.chunk_size
    standardize.value = resolvedDefaults.standardize
    normalize.value = resolvedDefaults.normalize
    window_size.value = resolvedDefaults.window_size
    aggression.value = resolvedDefaults.aggression
    enable_post_process.value = resolvedDefaults.enable_post_process
    post_process_threshold.value = resolvedDefaults.post_process_threshold
    high_end_process.value = resolvedDefaults.high_end_process
    selectedStems.value = Array.isArray(saved?.selectedStems) ? [...saved.selectedStems] : []
    selectedStemsCleared.value = saved?.selectedStemsCleared === true
    inferenceParamsDirty.value = false
    void nextTick(() => {
      applyingModelDefaults = false
    })
  }

  function getCurrentUiInferenceDefaults() {
    const defaults = Object.fromEntries(
      Object.entries(selectedModelUiDefaults.value).map(([key, value]) => [key, value === null ? undefined : value]),
    ) as ModelDefaultInferenceParams
    return applyModelDefaultsToUi(defaults, selectedModelType.value)
  }

  function restoreInferenceNumberFallback(field: InferenceNumberField) {
    if (!ZERO_AS_UNSET_FIELDS.has(field)) return
    const defaults = getCurrentUiInferenceDefaults()
    const nextValue = defaults[field]
    if (typeof nextValue !== 'number' || !Number.isFinite(nextValue)) return
    switch (field) {
      case 'batch_size':
        if (normalizeEditableNumberValue(batch_size.value, { zeroMeansUnset: true }) === null) batch_size.value = nextValue
        break
      case 'overlap_size':
        if (normalizeEditableNumberValue(overlap_size.value, { zeroMeansUnset: true }) === null) overlap_size.value = nextValue
        break
      case 'num_overlap':
        if (normalizeEditableNumberValue(num_overlap.value, { zeroMeansUnset: true }) === null) num_overlap.value = nextValue
        break
      case 'chunk_size':
        if (normalizeEditableNumberValue(chunk_size.value, { zeroMeansUnset: true }) === null) chunk_size.value = nextValue
        break
      case 'window_size':
        if (normalizeEditableNumberValue(window_size.value, { zeroMeansUnset: true }) === null) window_size.value = nextValue
        break
      case 'aggression':
      case 'post_process_threshold':
        break
    }
  }

  function normalizeInferenceInputsBeforeSubmit() {
    restoreInferenceNumberFallback('batch_size')
    restoreInferenceNumberFallback('overlap_size')
    restoreInferenceNumberFallback('num_overlap')
    restoreInferenceNumberFallback('chunk_size')
    restoreInferenceNumberFallback('window_size')
  }

  const { buildRunConfig, workflowModelInferenceParams, buildWorkflowRunConfig } = makeTaskRunConfig({
    useSettingsStore, useAppStore, useModelStore, selectedStems, useTta,
    normalizeOutputNaming, CURRENT_INFERENCE_PARAMS_VERSION,
    detectWorkflowFormat, resolveWorkflowRuntimeDefaults, prepareWorkflowDefinitionForRun,
  })
  const { resolveTaskOutputPath, createQueuedTask, createQueuedWorkflowTask } = makeTaskQueue({
    useSettingsStore, windowsPaths, resolveOutputDirectory, joinOutputPath,
    inputStemSegment, createRunId, STAGE_META,
    buildRunConfig, buildWorkflowRunConfig, tasks, rememberSessionTasks,
    activeTaskId,
  })
  async function startQueuedTask(taskId: string) {
    const task = tasks.value.find(item => item.id === taskId)
    if (!task || task.status !== 'queued') return false
    return startQueuedGroup([task])
  }

  async function startQueuedGroup(group: SeparationTask[]) {
    if (!group.length) return false
    const groupId = taskJobId(group[0])
    if (acceptedGroupIds.has(groupId) || inFlightGroupIds.has(groupId) || (retryDispatchAfter.get(groupId) || 0) > Date.now()) return false
    inFlightGroupIds.add(groupId)
    const primary = group[0].id
    try {
      await invoke(group[0].runConfig?.runMode === 'workflow' ? 'start_workflow_inference' : 'start_separation', { payload: { jobId: primary } })
      // A queued ACK means the backend owns the waiter, not that a worker started.
      acceptedGroupIds.add(groupId)
      retryDispatchAfter.delete(groupId)
      await journal.refresh()
      if (journal.error) throw new Error(journal.error)
      syncJournalTasks()
      return true
    } catch (error) {
      // A queued record predates start, so a lost ACK is ambiguous. Retrying the
      // idempotent start is safe, but only after the resident polling interval.
      retryDispatchAfter.set(groupId, Date.now() + JOURNAL_POLL_INTERVAL_MS)
      studioLog.warn('Failed to start an enqueued audio job', error)
      await journal.refresh()
      syncJournalTasks()
      if (tasks.value.some(task => taskJobId(task) === groupId && task.status !== 'queued' && !isTerminalTaskStatus(task.status))) acceptedGroupIds.add(groupId)
      return false
    } finally {
      inFlightGroupIds.delete(groupId)
    }
  }

  const startBatchWorker = startQueuedGroup
  const startWorkflowBatchWorker = startQueuedGroup

  let schedulingSuppressed = 0
  let schedulingQueue = false
  let queueScheduleRequested = false

  function scheduleQueue() {
    // Browser-only previews cannot start a Rust worker. Keep restored queue items
    // intact until the desktop runtime is available instead of failing them on boot.
    if (!isTauriRuntime() || !initialized.value || loadError.value || journal.error || schedulingSuppressed) return
    if (!isMainTaskOwner()) return
    if (schedulingQueue) {
      queueScheduleRequested = true
      return
    }
    schedulingQueue = true
    try {
      do {
        queueScheduleRequested = false
        // Admission and the shared UI/MCP/media cap belong to the backend. Submit
        // every unaccepted group once; a batch still maps to one backend waiter.
        const eligible = tasks.value.filter(task => task.status !== 'queued' || !acceptedGroupIds.has(taskJobId(task))
          && !inFlightGroupIds.has(taskJobId(task)) && (retryDispatchAfter.get(taskJobId(task)) || 0) <= Date.now())
        const nextJobs = selectQueuedJobGroups(eligible, Math.max(1, tasks.value.length))
        nextJobs.forEach((jobTasks) => {
          if (jobTasks.length === 1) {
            void startQueuedTask(jobTasks[0].id)
            return
          }
          if (jobTasks[0].runConfig?.runMode === 'workflow') {
            void startWorkflowBatchWorker(jobTasks)
          } else {
            void startBatchWorker(jobTasks)
          }
        })
        // Synchronous validation failures request another pass after the current selection is consumed.
      } while (queueScheduleRequested)
    } finally {
      schedulingQueue = false
    }
  }

  function addInputFiles(paths: string[]) {
    const valid = paths.filter((p) => p?.trim() && isSupportedInputPath(p))
    if (!valid.length) return 0
    const existing = new Set(inputFiles.value)
    const additions = valid.filter((p) => !existing.has(p))
    if (additions.length) inputFiles.value = [...inputFiles.value, ...additions]
    return additions.length
  }

  function removeInputFile(path: string) {
    inputFiles.value = inputFiles.value.filter((p) => p !== path)
  }

  function clearInputFiles() {
    inputFiles.value = []
  }

  function moveInputFile(fromIndex: number, toIndex: number) {
    const from = Math.trunc(Number(fromIndex))
    const to = Math.trunc(Number(toIndex))
    if (from === to || from < 0 || to < 0 || from >= inputFiles.value.length || to >= inputFiles.value.length) return false
    const next = [...inputFiles.value]
    const [item] = next.splice(from, 1)
    next.splice(to, 0, item)
    inputFiles.value = next
    return true
  }

  async function pickFiles() {
    const files = await invoke<string[]>('pick_media_files')
    addInputFiles(files || [])
    return files?.length || 0
  }

  async function scanAndAddPaths(paths: string[]) {
    if (!paths?.length) return { added: 0, warnings: [] as string[] }
    const result = await invoke<ScanMediaPathsResult>('scan_media_paths', { paths })
    return { added: addInputFiles(result.files || []), warnings: result.warnings || [] }
  }

  async function pickInputFolder() {
    const folder = await invoke<string | null>('pick_input_folder')
    if (!folder) return 0
    const result = await scanAndAddPaths([folder])
    return result.added
  }

  async function addPaths(paths: string[]) {
    const result = await scanAndAddPaths(paths)
    return result.added
  }

  async function revealPath(path: string) {
    await invoke('reveal_path', { path })
  }

  async function trashPaths(paths: string[]) {
    const valid = paths.filter((p) => p?.trim())
    if (!valid.length) return { trashed: [] as string[], failed: [] as string[] }
    return invoke<{ trashed: string[]; failed: string[] }>('move_paths_to_trash', { paths: valid, emptyDirs: [] })
  }

  function getTaskById(id: string) {
    return tasks.value.find((task) => task.id === id) || null
  }

  function focusResultTask(id: string | null) {
    focusedResultTaskId.value = id
  }

  function focusTask(id: string | null) {
    focusedTaskId.value = id
  }

  // 任务是否还会出现在结果页（done + 有输出 + 未被结果页隐藏）。
  // 不会出现在结果页的任务，其“结果维度”视为已不存在。
  function hasResultPresence(task: SeparationTask) {
    return task.status === 'done'
      && (task.outputs.length > 0 || task.files.length > 0)
  }

  function getRemovalTasks(ids: string[]) {
    return getRemovalTasksFromState(tasks.value, ids, i18n.global.t('results.removeRunningBatch'))
  }

  // The native transaction owns the complete group and optional file recycling.
  async function removeResult(id: string) {
    return (await removeResults([id])).removedJobIds.length
  }

  async function checkRemoval(ids: string[], deleteFiles = false) {
    const related = getRemovalTasks(ids)
    const response = await removeAudioJobs(related.map(task => task.id), { deleteFiles, checkOnly: true })
    if (response.checkedJobIds.length !== related.length || related.some(task => !response.checkedJobIds.includes(task.id))) throw new Error('Result removal preflight did not confirm the exact group')
    return response
  }

  async function removeResults(ids: string[], options: { deleteFiles?: boolean } = {}): Promise<ResultRemovalResponse> {
    const related = getRemovalTasks(ids)
    if (!related.length) return { schemaVersion: 2, checkedJobIds: [], removedJobIds: [], trashed: [], failed: [], retained: [] }
    const response = await removeAudioJobs(related.map(task => task.id), options)
    if (response.checkedJobIds.length !== related.length || related.some(task => !response.checkedJobIds.includes(task.id))) throw new Error('Result removal did not confirm the exact group')
    const removed = response.removedJobIds
    if (!removed.length) return response
    journal.forget(removed)
    syncJournalTasks()
    const removedIds = new Set(removed)
    sessionTaskIds.value = new Set([...sessionTaskIds.value].filter(id => !removedIds.has(id)))
    if (activeTaskId.value && removedIds.has(activeTaskId.value)) activeTaskId.value = tasks.value[0]?.id || null
    if (focusedTaskId.value && removedIds.has(focusedTaskId.value)) focusedTaskId.value = null
    if (focusedResultTaskId.value && removedIds.has(focusedResultTaskId.value)) focusedResultTaskId.value = null
    return response
  }

  async function removeWorkflowRun(workflowId: string, jobId: string) {
    const job = getJobById(jobId)
    if (!job) return
    if (!job.tasks.every(task => task.runConfig?.workbench && task.runConfig.workflowId === workflowId)) {
      throw new Error(i18n.global.t('workbench.runNotFound'))
    }
    if (job.tasks.some(hasResultPresence)) throw new Error(i18n.global.t('workbench.removeRunHasResults'))
    await removeResults(job.tasks.map(task => task.id))
  }

  async function removeSeparationRuns(jobIds: string[]) {
    const selected = new Set(jobIds)
    const related = tasks.value.filter(task => selected.has(taskJobId(task)))
    if (related.some(task => task.runConfig?.workbench)) throw new Error(i18n.global.t('workbench.runNotFound'))
    if (related.some(hasResultPresence)) throw new Error(i18n.global.t('workbench.removeRunHasResults'))
    return (await removeResults(related.map(task => task.id))).removedJobIds.length
  }

  // Empty selections must not turn into a request to remove all results.
  async function clearResults(ids?: string[]) {
    return (await removeResults(ids ?? resultTasks.value.map(task => task.id))).removedJobIds.length
  }

  async function cancelAllTasks() {
    const current = [...runningTasks.value]
    let cancelled = 0
    schedulingSuppressed++
    try {
      for (const task of current) if (await cancelTask(task.id)) cancelled++
    } finally {
      schedulingSuppressed--
      scheduleQueue()
    }
    return { total: current.length, cancelled }
  }

  async function cancelTask(id: string) {
    const task = tasks.value.find(item => item.id === id)
    if (!task || isTerminalTaskStatus(task.status)) return false
    const cancelled = await invoke<boolean>('cancel_task', { taskId: id })
    if (cancelled) {
      await journal.refresh()
      syncJournalTasks()
      scheduleQueue()
    }
    return cancelled
  }

  async function cancelWorkbenchJob(jobId: string) {
    const targets = tasks.value.filter(task => taskJobId(task) === jobId && !isTerminalTaskStatus(task.status))
    schedulingSuppressed++
    try {
      for (const target of targets) await invoke('cancel_task', { taskId: target.id })
      await journal.refresh()
      syncJournalTasks()
    } finally {
      schedulingSuppressed--
      scheduleQueue()
    }
  }

  function buildInferenceParams(modelType?: string | null): Record<string, unknown> {
    return buildInferenceParamsFromValues({
      modelType,
      defaults: selectedModelDefaults.value,
      modelOverrides: selectedModelOverrides.value,
      dirty: inferenceParamsDirty.value,
      values: {
        batch_size: batch_size.value,
        overlap_size: overlap_size.value,
        num_overlap: num_overlap.value,
        chunk_size: chunk_size.value,
        window_size: window_size.value,
        aggression: aggression.value,
        enable_post_process: enable_post_process.value,
        post_process_threshold: post_process_threshold.value,
        high_end_process: high_end_process.value,
        standardize: standardize.value,
        normalize: normalize.value,
      },
    })
  }

  function modelInferenceParamsFromState(modelName: string, modelType?: string | null): Record<string, unknown> {
    const modelStore = useModelStore()
    if (modelStore.selectedModel === modelName) return buildInferenceParams(modelType)
    const entry = modelStore.models.find(item => item.name === modelName) || null
    const defaults = modelStore.hasKnownModelInferenceBase(modelName)
      ? (modelStore.getModelBaseInferenceDefaults(modelName) || {})
      : (entry?.defaultInferenceParams || {})
    const overrides = modelStore.getModelInferenceOverrides(modelName) || {}
    const saved = getSavedModelState(modelName) || {}
    return modelInferenceParamsFromValues({ modelType, defaults, overrides, saved })
  }

  function submitOne(input: string, model: string, inferenceParams: Record<string, unknown>, modelType?: string | null, jobId?: string, jobOutput?: string, outputLayout: OutputLayout = 'folders', outputNaming?: OutputNamingConfig, batchIndex?: number) {
    return createQueuedTask(input, model, inferenceParams, modelType, jobId, jobOutput, outputLayout, outputNaming, batchIndex)
  }

  async function startSeparation(options: { outputDir?: string; outputLayout?: OutputLayout; outputNaming?: OutputNamingConfig } = {}) {
    if (loadError.value) throw new Error(loadError.value)
    const modelStore = useModelStore()
    if (!inputFiles.value.length) {
      throw new Error('Input file is required')
    }
    if (!modelStore.selectedModel.trim()) {
      throw new Error('Model is required')
    }
    const model = modelStore.selectedModel
    const selectedEntry = modelStore.selectedInfo?.name === model
      ? modelStore.selectedInfo
      : modelStore.models.find((item) => item.name === model) || null
    const modelType = selectedEntry?.modelType || null
    normalizeInferenceInputsBeforeSubmit()
    const inferenceParams = buildInferenceParams(modelType)
    const targets = [...inputFiles.value]
    const batch = prepareTaskBatch(options, separateOutputDir.value, windowsPaths.value)
    const createdTasks = targets.map((input, batchIndex) => submitOne(
      input,
      model,
      inferenceParams,
      modelType,
      batch.jobId,
      batch.jobOutput,
      batch.outputLayout,
      batch.outputNaming,
      batchIndex,
    ))
    modelStore.recordModelUse(model)
    return finalizeTaskBatch(batch, await enqueueTasks(createdTasks), { schedule: scheduleQueue })
  }

  async function startWorkflowInference(workflow: WorkflowEntry, options: { outputDir?: string; outputLayout?: OutputLayout; outputNaming?: OutputNamingConfig } = {}) {
    if (loadError.value) throw new Error(loadError.value)
    if (!workflow?.id) {
      throw new Error('Workflow is required')
    }
    const validationError = workflowDefinitionValidationError(workflow.definition)
    if (validationError) throw new Error(validationError)
    validateSeparateWorkflowInput(workflow.definition)
    const targets = [...inputFiles.value]
    if (!targets.length) {
      throw new Error(i18n.global.t('separate.startHintNoInput'))
    }
    const batch = prepareTaskBatch(options, separateOutputDir.value, windowsPaths.value)
    const createdTasks = targets.map((input, batchIndex) => createQueuedWorkflowTask(
      input,
      workflow,
      batch.jobId,
      batch.jobOutput,
      batch.outputLayout,
      batch.outputNaming,
      batchIndex,
    ))
    return finalizeTaskBatch(batch, await enqueueTasks(createdTasks), { schedule: scheduleQueue })
  }
  async function startWorkbenchInference(workflow: WorkflowEntry, bindings: WorkflowRuntimeBindings) {
    if (loadError.value) throw new Error(loadError.value)
    if (!isMainTaskOwner()) throw new Error('workbench.mainWindowOnly')
    const validationError = workflowDefinitionValidationError(workflow.definition)
    if (validationError) throw new Error(validationError)
    const nativeGraph = detectWorkflowFormat(workflow.definition) === 'graph'
    const batch = prepareTaskBatch(
      { outputDir: bindings.outputDirectory, outputLayout: bindings.outputLayout },
      useSettingsStore().outputDir,
      windowsPaths.value,
    )
    const directory = batch.jobOutput
    const prepared = prepareWorkbenchRun(workflow.definition, bindings, directory, windowsPaths.value)
    const runtimeEntry = { ...workflow, definition: prepared.definition }
    const createdTasks = prepared.runs.map((run, batchIndex) => {
      const created = createQueuedWorkflowTask(run.input, runtimeEntry, batch.jobId, directory, nativeGraph ? 'flat' : bindings.outputLayout, undefined, batchIndex)
      const config = created.runConfig!
      config.useTta = false
      if (nativeGraph) config.workflowDefinition = cloneWorkflow(workflow.definition)
      const snapshot = {
        version: 2 as const, sourceUpdatedAt: workflow.updatedAt, bindings: cloneWorkflow(bindings),
        inputs: cloneWorkflow(run.inputs), outputs: cloneWorkflow(run.outputs),
      }
      config.workbench = { ...snapshot, nativeGraph }
      return created
    })
    return finalizeTaskBatch(batch, await enqueueTasks(createdTasks), { schedule: scheduleQueue })
  }

  async function retryTask(taskId: string) {
    if (loadError.value) throw new Error(loadError.value)
    const existing = tasks.value.find((t) => t.id === taskId)
    if (!existing) return
    if (existing.runConfig?.workbench && existing.runConfig.workflowDefinition) {
      const config = cloneWorkflow(existing.runConfig)
      const snapshot = config.workbench!
      const created = createQueuedWorkflowTask(existing.input, {
        id: config.workflowId!, name: config.workflowName || existing.model, description: '',
        definition: workflowSourceDefinition(config), format: detectWorkflowFormat(workflowSourceDefinition(config)),
        formatVersion: WORKFLOW_FORMAT_VERSION, createdAt: existing.createdAt, updatedAt: snapshot.sourceUpdatedAt,
      }, undefined, existing.jobOutput || existing.output, config.outputLayout, config.outputNaming)
      const queued = created
      queued.runConfig = config
      queued.output = existing.output
      const [confirmed] = await enqueueTasks([created])
      scheduleQueue()
      return confirmed
    }
    const { jobOutput, outputLayout } = resolveRetryOutput(existing, windowsPaths.value, i18n.global.t('separate.retryOutputDirectoryUnknown'))
    if (existing.runConfig?.runMode === 'workflow' && existing.runConfig.workflowId && existing.runConfig.workflowName && existing.runConfig.workflowDefinition) {
      validateSeparateWorkflowInput(existing.runConfig.workflowDefinition)
      const task = createQueuedWorkflowTask(existing.input, {
        id: existing.runConfig.workflowId,
        name: existing.runConfig.workflowName,
        description: '',
        definition: existing.runConfig.workflowDefinition,
        format: detectWorkflowFormat(existing.runConfig.workflowDefinition),
        formatVersion: WORKFLOW_FORMAT_VERSION,
        createdAt: existing.createdAt,
        updatedAt: existing.updatedAt,
      }, undefined, jobOutput, outputLayout, existing.runConfig.outputNaming)
      task.runConfig = cloneWorkflow(existing.runConfig!)
      const [confirmed] = await enqueueTasks([task])
      scheduleQueue()
      return confirmed
    }
    const modelStore = useModelStore()
    modelStore.selectedModel = existing.model
    const retryParams = existing.runConfig?.inferenceParams || {}
    const persistedModelType = existing.runConfig?.modelType ?? null
    const modelEntry = modelStore.models.find((item) => item.name === existing.model) || null
    const modelType = modelEntry?.modelType || persistedModelType || null
    const task = createQueuedTask(existing.input, existing.model, retryParams, modelType, undefined, jobOutput, outputLayout, existing.runConfig?.outputNaming)
    task.runConfig = cloneWorkflow(existing.runConfig!)
    const [confirmed] = await enqueueTasks([task])
    scheduleQueue()
    return confirmed
  }

  return {
    cancelWorkbenchJob,
    startWorkbenchInference,
    initialized,
    loadError,
    tasks,
    activeTaskId,
    activeTask,
    completedTasks,
    runningTasks,
    activeWorkerTasks,
    taskBoardTasks,
    resultTasks,
    allJobs,
    resultJobs,
    sessionJobs,
    queuedTasks,
    failedTasks,
    focusedResultTaskId,
    focusedTaskId,
    inputFiles,
    inputPath,
    separateRunMode,
    ensembleEnabled,
    ensembleModels,
    ensembleStem,
    ensembleModelStems,
    ensembleType,
    ensembleWeights,
    separateTemporaryOutputDir,
    separateOutputDir,
    separateOutputLayout,
    separateOutputNamingTemplate,
    separateCustomStemOrder,
    modelListViewMode,
    modelListSortMode,
    modelListPageSize,
    useTta,
    batch_size,
    overlap_size,
    num_overlap,
    chunk_size,
    standardize,
    normalize,
    selectedStems,
    selectedStemsCleared,
    window_size,
    aggression,
    enable_post_process,
    post_process_threshold,
    high_end_process,
    restoreInferenceNumberFallback,
    initialize,
    pickFiles,
    pickInputFolder,
    addPaths,
    addInputFiles,
    moveInputFile,
    removeInputFile,
    clearInputFiles,
    revealPath,
    trashPaths,
    primaryRevealPath,
    getTaskById,
    getJobById,
    taskJobId,
    focusResultTask,
    focusTask,
    removeSessionJob,
    clearFinishedSessionJobs,
    removeResult,
    removeResults,
    checkRemoval,
    getRemovalTasks,
    removeWorkflowRun,
    removeSeparationRuns,
    clearResults,
    cancelTask,
    cancelAllTasks,
    startSeparation,
    startWorkflowInference,
    retryTask,
    scheduleQueue,
    applySelectedModelDefaults,
    saveCurrentModelState,
    getSavedModelState,
    normalizeInferenceInputsBeforeSubmit,
  }
})
