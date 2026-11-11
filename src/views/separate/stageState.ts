import type { ComputedRef, Ref } from 'vue'
import type { SeparationJob, SeparationTask, StemOutput } from '@/stores/task'
import { isTerminalTaskStatus, resolveJobStatus } from '@/features/tasks/lifecycle'
import { getFileName } from './format'
import { sortStemOutputsByOrder } from '@/utils/stemOrder'
import type { ModelEntry } from '@/stores/model'

type Deps = {
  computed: <T>(source: () => T) => ComputedRef<T>
  t: (key: string, params?: Record<string, unknown>) => string
  currentJob: Ref<SeparationJob | null>
  inputFiles: Ref<string[]>
  selectedModelName: Ref<string>
  currentModelInfo: Ref<ModelEntry | null>
  normalizedOutputDir: Ref<string>
  modelEntries: Ref<ModelEntry[]>
  categoryLabel: (item: ModelEntry) => string
  parseModelInstruments: (value: unknown) => string[]
  statusLabel: (status: string) => string
  shortenMiddle: (value: string, max: number) => string
}

/**
 * Derives the running/terminal/ready stage from the focused job. Keeping this
 * out of the view makes the batch semantics testable and leaves the template
 * with a small, explicit state object.
 */
export function makeStageState({
  computed, t, currentJob, inputFiles, selectedModelName, currentModelInfo,
  normalizedOutputDir, modelEntries, categoryLabel, parseModelInstruments, statusLabel, shortenMiddle,
}: Deps) {
  const focusedBatchTasks = computed<SeparationTask[]>(() => currentJob.value?.tasks || [])
  const activeFocusedBatchTask = computed<SeparationTask | null>(() => {
    return [...focusedBatchTasks.value]
      .filter((item: SeparationTask) => !isTerminalTaskStatus(item.status))
      .sort((a: SeparationTask, b: SeparationTask) => {
        const aQueued = a.status === 'queued' ? 1 : 0
        const bQueued = b.status === 'queued' ? 1 : 0
        if (aQueued !== bQueued) return aQueued - bQueued
        return Number(a.createdAt) - Number(b.createdAt)
      })[0] || null
  })
  const currentTask = computed(() => {
    if (focusedBatchTasks.value.length) return activeFocusedBatchTask.value || focusedBatchTasks.value[0] || null
    return null
  })
  const completedActionOptions = computed(() => [
    { key: 'retry', label: t('separate.runAgain') },
    ...(currentTask.value?.logs.length ? [{ key: 'logs', label: t('tasks.logs') }] : []),
  ])
  const currentBatchTasks = computed(() => focusedBatchTasks.value.length ? focusedBatchTasks.value : currentTask.value ? [currentTask.value] : [])
  const currentBatchTotal = computed(() => currentBatchTasks.value.length)
  const currentBatchDoneCount = computed(() => currentBatchTasks.value.filter(item => item.status === 'done').length)
  const currentBatchFailedCount = computed(() => currentBatchTasks.value.filter(item => item.status === 'failed').length)
  const currentBatchCancelledCount = computed(() => currentBatchTasks.value.filter(item => item.status === 'cancelled').length)
  const currentBatchFinishedCount = computed(() => currentBatchDoneCount.value + currentBatchFailedCount.value + currentBatchCancelledCount.value)
  const currentBatchIsMulti = computed(() => currentBatchTotal.value > 1)
  const currentBatchIsQueued = computed(() => currentBatchTasks.value.length > 0 && currentBatchTasks.value.every(item => item.status === 'queued'))
  const taskPanelState = computed<'ready' | 'running' | 'done' | 'failed' | 'cancelled'>(() => {
    const items = currentBatchTasks.value
    if (!items.length) return 'ready'
    const status = resolveJobStatus(items)
    return isTerminalTaskStatus(status) ? status as 'done' | 'failed' | 'cancelled' : 'running'
  })
  const isConfigCompact = computed(() => taskPanelState.value !== 'ready')
  const isTerminalState = computed(() => currentBatchTasks.value.length > 0 && currentBatchTasks.value.every(item => isTerminalTaskStatus(item.status)))
  const isRunModeLocked = computed(() => taskPanelState.value === 'running')
  const inputCompactLine = computed(() => {
    if (currentBatchIsMulti.value && currentBatchTotal.value) return t('separate.batchInputCompact', { count: currentBatchTotal.value })
    if (currentTask.value) return getFileName(currentTask.value.input)
    if (!inputFiles.value.length) return t('separate.noInputSelected')
    const first = getFileName(inputFiles.value[0])
    if (inputFiles.value.length === 1) return first
    return t('separate.inputCompactMultiple', { count: inputFiles.value.length, name: first })
  })
  const modelCompactLine = computed(() => {
    if (currentTask.value?.model) return currentTask.value.model
    const name = selectedModelName.value || t('separate.noModelSelected')
    const category = currentModelInfo.value ? categoryLabel(currentModelInfo.value) : ''
    return category ? `${name} · ${category}` : name
  })
  const currentTaskFileName = computed(() => currentTask.value ? getFileName(currentTask.value.input) : '')
  const currentTaskOutputPath = computed(() => currentJob.value?.output || currentTask.value?.output || normalizedOutputDir.value)
  const currentTaskOutputSummary = computed(() => shortenMiddle(currentTaskOutputPath.value, 72))

  function configuredStemOrder(item: SeparationTask) {
    if (item.runConfig?.outputNaming?.stemOrder?.length) return item.runConfig.outputNaming.stemOrder
    if (item.runConfig?.runMode === 'workflow') return []
    const modelEntry = modelEntries.value.find(modelItem => modelItem.name === item.model)
    const configured = parseModelInstruments(modelEntry?.configInstruments)
    const selected = item.runConfig?.selectedStems || []
    if (!configured.length) return selected
    if (!selected.length) return configured
    const selectedKeys = new Set(selected.map((stem: string) => stem.toLowerCase()))
    const configuredKeys = new Set(configured.map((stem: string) => stem.toLowerCase()))
    return [
      ...configured.filter((stem: string) => selectedKeys.has(stem.toLowerCase())),
      ...selected.filter((stem: string) => !configuredKeys.has(stem.toLowerCase())),
    ]
  }

  const playableOutputGroups = computed(() => currentBatchTasks.value
    .filter(item => item.status === 'done')
    .map(item => ({
      taskId: item.id,
      input: item.input,
      outputs: sortStemOutputsByOrder(
        item.outputs.filter((output: StemOutput) => Boolean(output.path)),
        configuredStemOrder(item),
      ),
    }))
    .filter(group => group.outputs.length > 0))
  const playableOutputs = computed(() => playableOutputGroups.value.flatMap(group => group.outputs))
  const currentBatchProgress = computed(() => {
    const items = currentBatchTasks.value
    if (!items.length) return 0
    const total = items.reduce((sum, item) => {
      if (isTerminalTaskStatus(item.status)) return sum + 100
      if (item.status === 'queued') return sum
      return sum + Math.max(0, Math.min(99, Number(item.progress || 0)))
    }, 0)
    return Math.round(total / items.length)
  })
  const currentBatchActiveIndex = computed(() => {
    if (!currentBatchTotal.value) return 0
    if (taskPanelState.value !== 'running') return currentBatchFinishedCount.value
    return Math.min(currentBatchTotal.value, currentBatchFinishedCount.value + 1)
  })
  const currentBatchTitle = computed(() => {
    if (currentBatchIsQueued.value) return t('separate.batchQueuedTitle')
    if (!currentBatchIsMulti.value) return t('separate.taskRunningTitle')
    if (taskPanelState.value === 'running') {
      return t('separate.batchRunningTitle', { current: currentBatchActiveIndex.value, total: currentBatchTotal.value })
    }
    if (taskPanelState.value === 'done') return t('separate.batchDoneTitle', { count: currentBatchDoneCount.value })
    return statusLabel(taskPanelState.value)
  })
  const currentBatchLine = computed(() => {
    if (currentBatchIsQueued.value) return t('separate.batchQueuedHint')
    if (!currentBatchIsMulti.value) return currentTaskFileName.value
    if (taskPanelState.value === 'running' && currentTask.value) {
      return t('separate.batchCurrentInput', { name: getFileName(currentTask.value.input) })
    }
    return t('separate.batchFinishedSummary', {
      done: currentBatchDoneCount.value,
      failed: currentBatchFailedCount.value,
      cancelled: currentBatchCancelledCount.value,
      total: currentBatchTotal.value,
    })
  })
  const currentBatchOutputSummary = computed(() => {
    if (!currentBatchIsMulti.value) return currentTaskOutputSummary.value
    return t('separate.batchResultSummary', { count: currentBatchDoneCount.value, total: currentBatchTotal.value })
  })

  return {
    focusedBatchTasks, activeFocusedBatchTask, currentTask, completedActionOptions,
    currentBatchTasks, currentBatchTotal, currentBatchDoneCount, currentBatchFailedCount,
    currentBatchCancelledCount, currentBatchFinishedCount, currentBatchIsMulti, currentBatchIsQueued,
    taskPanelState, isConfigCompact, isTerminalState, isRunModeLocked, inputCompactLine,
    modelCompactLine, currentTaskFileName, currentTaskOutputPath, currentTaskOutputSummary,
    configuredStemOrder, playableOutputGroups, playableOutputs, currentBatchProgress,
    currentBatchActiveIndex, currentBatchTitle, currentBatchLine, currentBatchOutputSummary,
  }
}
