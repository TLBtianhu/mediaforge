import { createLogger } from '@/utils/logger'
import { defineStore } from 'pinia'
import { computed, ref, watch } from 'vue'
import { invoke } from '@tauri-apps/api/core'
import { cancelTask as cancelNativeTask } from '@/api/native'
import {
  pickInputFolder as pickInputFolderDialog,
  pickMediaFiles,
  scanMediaPaths,
} from '@/api/native'
import i18n from '@/i18n'
import { loadAppStore, saveAppStore } from '@/utils/appStore'
import { useSettingsStore } from '@/stores/settings'
import { useModelStore, type ModelDefaultInferenceParams } from '@/stores/model'
import { useAppStore } from '@/stores/app'
import type { WorkflowEntry } from '@/stores/workflow'
import { detectWorkflowFormat, WORKFLOW_FORMAT_VERSION } from '@/workflows/formats'
import {
  prepareWorkflowDefinitionForRun,
  resolveWorkflowRuntimeDefaults,
} from '@/workflows/runtimeDefinition'
import {
  isTerminalTaskStatus,
  taskJobId,
} from '@/features/tasks/lifecycle'
import { resolveOutputDirectory, joinOutputPath, createRunId, inputStemSegment } from '@/stores/taskOutputPaths'
import { CURRENT_INFERENCE_PARAMS_VERSION, validateSeparateState, STAGE_META, normalizeOutputLayout, normalizeOutputNaming } from '@/stores/taskNormalize'
import { useMediaJobsStore } from '@/stores/mediaJobs'
import { audioTasksFromJobs, audioEnqueueFromTasks } from '@/features/tasks/audioJobs'
import { enqueueAudioJobs, removeAudioJobs, type ResultRemovalResponse } from '@/features/tasks/journal'
import { makeTaskRunConfig } from '@/stores/taskRunConfig'
import { makeTaskQueue } from '@/stores/taskQueue'
import { finalizeTaskBatch, prepareTaskBatch } from '@/stores/taskBatch'
import { validateSeparateWorkflowInput, workflowDefinitionValidationError } from '@/stores/taskValidation'
import { resolveRetryOutput } from '@/stores/taskRetry'
import { buildInferenceParams as buildInferenceParamsFromValues, modelInferenceParamsFromState as modelInferenceParamsFromValues } from '@/stores/taskInferenceParams'
import { buildJobs, findJobById } from '@/stores/taskJobs'
import { makeTaskInputActions } from '@/stores/taskInputActions'
import { makeTaskQueueScheduler } from '@/stores/taskQueueScheduler'
import { makeTaskInferenceDefaults } from '@/stores/taskInferenceDefaults'
import { makeTaskCancelActions } from '@/stores/taskCancelActions'
import { makeTaskInferenceParams } from '@/stores/taskInferenceActions'
import { makeTaskResultHelpers } from '@/stores/taskResultHelpers'
import { normalizePersistedModelState, resolveTaskErrorMessage } from '@/stores/taskHelpers'
import { getRemovalTasks as getRemovalTasksFromState, primaryRevealPath } from '@/stores/taskResultCleanup'
import { isMainTaskOwner } from '@/utils/taskOwner'
import { cloneWorkflow, prepareWorkbenchRun, workflowSourceDefinition, type WorkflowRuntimeBindings } from '@/workflows/workbench'
import { SEPARATE_MODEL_PAGE_SIZES, normalizePageSize } from '@/utils/pagination'

const studioLog = createLogger('ui.stores.task')

import {
  type OutputLayout, type ModelListSortMode, type OutputNamingConfig,
  type SeparationTask,
  type PersistedSeparateModelState, type PersistedSeparateState,
  type ModelInferenceUiDefaults,
} from '@/stores/taskTypes'

export type {
  OutputLayout, ModelListSortMode, OutputNamingConfig, SeparationRunConfig,
  SeparationTask, SeparationJob,
} from '@/stores/taskTypes'
export type { TaskStatus } from '@/features/tasks/lifecycle'
export type { StemOutput } from '@/utils/stemOutputs'

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
const {
  addInputFiles, removeInputFile, clearInputFiles, moveInputFile,
  pickFiles, pickInputFolder, addPaths, revealPath,
} = makeTaskInputActions({
  inputFiles,
  native: {
    pickMediaFiles,
    scanMediaPaths,
    pickInputFolder: pickInputFolderDialog,
  },
})
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
  // Terminal records remain visible even when a run produced no file.
  const resultTasks = computed(() => tasks.value.filter(task => isTerminalTaskStatus(task.status)))
  const queuedTasks = computed(() => tasks.value.filter((task) => task.status === 'queued'))
  const failedTasks = computed(() => tasks.value.filter((task) => task.status === 'failed'))
  const allJobs = computed(() => buildJobs(tasks.value))
  // Keep every sibling so a partial batch cannot appear completed while another
  // member is still active, or omit a failed member that produced no file.
  const resultJobs = computed(() => allJobs.value.filter(job => job.tasks.some(task => isTerminalTaskStatus(task.status))))
  const sessionJobs = computed(() => buildJobs(tasks.value.filter(task => sessionTaskIds.value.has(task.id))))

  let separateStateSaveTimer: ReturnType<typeof setTimeout> | null = null

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
      if (isApplyingModelDefaults()) return
      queueSeparateStatePersist()
    },
    { deep: true },
  )

  watch(
    [batch_size, overlap_size, num_overlap, chunk_size, standardize, normalize, window_size, aggression, enable_post_process,
      post_process_threshold, high_end_process],
    () => {
      if (isApplyingModelDefaults()) return
      inferenceParamsDirty.value = true
      const modelStore = useModelStore()
      if (modelStore.selectedModel) saveCurrentModelState(modelStore.selectedModel)
    },
  )

  watch(
    [selectedStems, selectedStemsCleared],
    () => {
      if (isApplyingModelDefaults()) return
      const modelStore = useModelStore()
      if (modelStore.selectedModel) saveCurrentModelState(modelStore.selectedModel)
      else queueSeparateStatePersist()
    },
    { deep: true },
  )

  watch(() => useSettingsStore().maxConcurrentSeparations, () => {
    scheduleQueue()
  })

  const {
    applySelectedModelDefaults, restoreInferenceNumberFallback,
    normalizeInferenceInputsBeforeSubmit, isApplyingModelDefaults,
  } = makeTaskInferenceDefaults({
    selectedModelDefaults, selectedModelOverrides, selectedModelType, selectedModelUiDefaults,
    inferenceParamsDirty, batch_size, overlap_size, num_overlap, chunk_size, standardize,
    normalize, window_size, aggression, enable_post_process, post_process_threshold,
    high_end_process, selectedStems, selectedStemsCleared,
  })

  const { buildRunConfig, buildWorkflowRunConfig } = makeTaskRunConfig({
    useSettingsStore, useAppStore, useModelStore, selectedStems, useTta,
    normalizeOutputNaming, CURRENT_INFERENCE_PARAMS_VERSION,
    detectWorkflowFormat, resolveWorkflowRuntimeDefaults, prepareWorkflowDefinitionForRun,
  })
  const { createQueuedTask, createQueuedWorkflowTask } = makeTaskQueue({
    useSettingsStore, windowsPaths, resolveOutputDirectory, joinOutputPath,
    inputStemSegment, createRunId, STAGE_META,
    buildRunConfig, buildWorkflowRunConfig,
  })

  const { scheduleQueue, suppressScheduling, resumeScheduling } = makeTaskQueueScheduler({
    tasks, initialized, loadError, journal, acceptedGroupIds, inFlightGroupIds,
    retryDispatchAfter, invoke, syncJournalTasks, studioLog,
  })
  const {
    getTaskById, focusResultTask, focusTask, hasResultPresence, getRemovalTasks, checkRemoval, removeResult,
  } = makeTaskResultHelpers({
    tasks, focusedTaskId, focusedResultTaskId, getRemovalTasksFromState, i18n, removeAudioJobs,
    removeResults: (ids) => removeResults(ids),
  })

  // The native transaction owns the complete group and optional file recycling.
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
    const terminalGroups = resultJobs.value.filter(job => job.tasks.every(task => isTerminalTaskStatus(task.status)))
    return (await removeResults(ids ?? terminalGroups.flatMap(job => job.tasks.map(task => task.id)))).removedJobIds.length
  }

  const {
    cancelAllTasks, cancelTask, cancelWorkbenchJob,
  } = makeTaskCancelActions({
    tasks, runningTasks, journal, cancelTaskRequest: cancelNativeTask, syncJournalTasks, scheduleQueue,
    suppressScheduling, resumeScheduling,
  })

  const { buildInferenceParams } = makeTaskInferenceParams({
    selectedModelDefaults, selectedModelOverrides, inferenceParamsDirty,
    batch_size, overlap_size, num_overlap, chunk_size, window_size, aggression,
    enable_post_process, post_process_threshold, high_end_process, standardize, normalize,
    getSavedModelState, useModelStore, buildInferenceParamsFromValues, modelInferenceParamsFromValues,
  })

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
    primaryRevealPath,
    getTaskById,
    getJobById,
    taskJobId,
    focusResultTask,
    focusTask,
    removeSessionJob,
    clearFinishedSessionJobs,
    removeResults,
    removeResult,
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
