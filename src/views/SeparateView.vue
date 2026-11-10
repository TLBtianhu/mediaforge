<script setup lang="ts">
import { createLogger } from '@/utils/logger'
import TaskOutputSettings from '@/components/TaskOutputSettings.vue'
import TaskInputFiles from '@/components/TaskInputFiles.vue'
import ModelParameterField from '@/components/ModelParameterField.vue'
import ModelPanelState from '@/components/ModelPanelState.vue'

import { computed, nextTick, onMounted, onBeforeUnmount, ref, watch, type Ref } from 'vue'
import { storeToRefs } from 'pinia'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { useDialog, useMessage, type DropdownOption } from 'naive-ui'
import { convertFileSrc } from '@tauri-apps/api/core'
import { invoke } from '@tauri-apps/api/core'
import { getCurrentWebview } from '@tauri-apps/api/webview'
import type { UnlistenFn } from '@tauri-apps/api/event'
import {
  CubeOutline,
  CheckmarkCircle,
  PlayOutline,
  MusicalNotesOutline,
  SearchOutline,
  FolderOutline,
  CloudUploadOutline,
  CloseOutline,
  SettingsOutline,
  OpenOutline,
  PauseOutline,
  TerminalOutline,
  TimeOutline,
  ReorderFourOutline,
  ChevronUpOutline,
  ChevronDownOutline,
  GridOutline,
  ListOutline,
  InformationCircleOutline,
} from '@vicons/ionicons5'
import {
  WINDOWS_RESERVED_FILENAMES,
  compareModelNames, formatDateToken, formatLegacyTimeToken, formatPlaybackTime,
  formatTimeToken, getFileName, normalizeFilenameFragment, padNumber, safeFilenamePart,
  stripFileExtension, truncateUtf8,
} from './separate/format'
import { useModelList } from './separate/useModelList'
import { makeInferenceDefaults } from './separate/inferenceDefaults'
import { makePlayback } from './separate/playback'
import { makeStemSelection } from './separate/stemSelection'
import { makeQueue } from './separate/queue'
import { makeOutputDirectory } from './separate/outputDirectory'
import { makeSlotPanel } from './separate/slotPanel'
import { launchTask } from '@/features/tasks/launch'
import { makeTaskPresentation } from './separate/taskPresentation'
import { makeReorder } from './separate/reorder'
import { useOutputNaming } from './separate/useOutputNaming'
import { useTaskStore, type ModelListSortMode, type OutputLayout, type SeparationJob, type SeparationTask, type StemOutput } from '@/stores/task'
import { isTerminalTaskStatus, resolveJobStatus } from '@/features/tasks/lifecycle'
import { inferenceChunkSizeIssue, resolveInferenceChunkStep, resolveInferenceSampleStep } from '@/features/inference/sampleStep'
import type { WorkflowEntry } from '@/stores/workflow'
import { WORKFLOW_FORMAT_VERSION } from '@/workflows/formats'
import { useSettingsStore } from '@/stores/settings'
import { useModelStore, type ModelDefaultInferenceParams, type ModelEntry } from '@/stores/model'
import { useAppStore } from '@/stores/app'
import { buildModelCategoryOptionsFromModels, getModelCategoryLabel } from '@/utils/modelCategory'
import { matchesModelQuery } from '@/utils/modelSearch'
import { sortStemOutputsByOrder } from '@/utils/stemOrder'
import { retainAvailableModelNames } from '@/utils/modelSource'
import { SEPARATE_MODEL_PAGE_SIZES } from '@/utils/pagination'
import MediaPageHeader from '@/components/media/MediaPageHeader.vue'
import WorkbenchSteps from '@/components/media/WorkbenchSteps.vue'
import WorkbenchCard from '@/components/media/WorkbenchCard.vue'
import TaskLogConsole from '@/components/TaskLogConsole.vue'
import AlignedInferenceInputNumber from '@/components/AlignedInferenceInputNumber.vue'
import ModelSlotFields from '@/components/ModelSlotFields.vue'
import SeparateAdvancedPanel from '@/components/separate/SeparateAdvancedPanel.vue'
import SeparateNamingModal from '@/components/separate/SeparateNamingModal.vue'
import SeparateQueueModal from '@/components/separate/SeparateQueueModal.vue'
import SeparateSettingsDrawer from '@/components/separate/SeparateSettingsDrawer.vue'
import SeparateEnsembleModal from '@/components/separate/SeparateEnsembleModal.vue'
import SeparateNoteModal from '@/components/separate/SeparateNoteModal.vue'
import SeparateLogModal from '@/components/separate/SeparateLogModal.vue'
import SeparateReadyStage from '@/components/separate/SeparateReadyStage.vue'
import SeparateTaskStage from '@/components/separate/SeparateTaskStage.vue'
import { findDropZone, toCssPoint } from '@/utils/dropZones.ts'
import InferenceParameterLesson from '@/components/InferenceParameterLesson.vue'
import { parameterHelpTopics } from '@/features/inference/parameterHelp'
import ParameterFieldGuide from '@/components/ParameterFieldGuide.vue'
import { isTutorialFrame } from '@/features/onboarding/demoContext'
import { useBeginnerGuideStore } from '@/stores/beginnerGuide'
import { visibleParameterGuides } from '@/features/onboarding/parameters'

const studioLog = createLogger('ui.views.SeparateView')

const { t, locale } = useI18n()
const {
  shortenMiddle, statusLabel, statusType, progressStatus, normalizeProgressMessage,
  progressTitle, progressDetail, taskSubMessage, taskDuration,
} = makeTaskPresentation(t)
const message = useMessage()
const dialog = useDialog()
const router = useRouter()
const route = useRoute()
const task = useTaskStore()
const model = useModelStore()
const settings = useSettingsStore()
const app = useAppStore()
const beginnerGuide = useBeginnerGuideStore()

const {
  inputFiles,
  separateRunMode: runMode,
  ensembleEnabled,
  ensembleModels,
  ensembleStem,
  ensembleModelStems,
  ensembleType,
  ensembleWeights,
  separateTemporaryOutputDir: temporaryOutputDir,
  separateOutputDir: normalizedOutputDir,
  separateOutputLayout: outputLayout,
  separateOutputNamingTemplate: outputNamingTemplate,
  separateCustomStemOrder: customStemOrder,
  useTta,
  batch_size,
  overlap_size,
  num_overlap,
  chunk_size,
  standardize,
  normalize,
  window_size,
  aggression,
  enable_post_process,
  post_process_threshold,
  high_end_process,
  selectedStems,
  selectedStemsCleared: persistedOutputStemSelectionCleared,
  modelListViewMode,
  modelListSortMode,
  modelListPageSize: modelPageSize,
} = storeToRefs(task)

// The audio workbench is single-model only; legacy workflow mode is retired.
runMode.value = 'model'
const { selectedModel, downloadedModels, models: modelEntries, isLoading, modelsLoaded, detailLoading, modelPreferences, error: modelError } = storeToRefs(model)

const isDragging = ref(false)
const showSettingsDrawer = ref(false)
const parameterHelpPage = ref<number | null>(null)
const expandedInferenceSections = ref<string[]>([])
const settingsCardRef = ref<{ $el: HTMLElement } | null>(null)
const fieldGuideRef = ref<{ syncPosition: () => void } | null>(null)
const guideTarget = ref<HTMLElement | null>(null)
let guideOpenedSettings = false
const helpTopics = computed(() => parameterHelpTopics(currentModelInfo.value?.modelType))
const guidedParameter = computed(() => beginnerGuide.parameterStep && (!beginnerGuide.demoActive || isTutorialFrame()) ? beginnerGuide.currentParameter : undefined)
const guideRequiresAdvanced = computed(() => guidedParameter.value?.group === 'inference' && guidedParameter.value.id !== 'tta')
watch(showSettingsDrawer, () => { parameterHelpPage.value = null })
const showEnsembleModal = ref(false)
const showNamingModal = ref(false)
const showLogModal = ref(false)
const contextMenuX = ref(0)
const contextMenuY = ref(0)
const contextMenuVisible = ref(false)
const contextMenuKind = ref<'model' | null>(null)
const contextModel = ref<ModelEntry | null>(null)
const showModelNoteEditor = ref(false)
const noteEditorModel = ref<ModelEntry | null>(null)
const noteDraft = ref('')
const showQueueModal = ref(false)
const queueScope = ref<'session' | 'no-results'>('session')
const removingRunRecords = ref(false)
watch(showQueueModal, show => { if (!show) queueScope.value = 'session' })
if (route.query.mode === 'workflow') void router.replace('/workflows')
const focusedSeparationJobId = ref<string | null>(null)
const composingNewJob = ref(false)
const submittingJob = ref(false)
const cancellingTaskId = ref<string | null>(null)
const cancellingQueueJobId = ref<string | null>(null)
const {
  isSeparationJob, isNoResultSeparationJob, separationJobs, separationSessionJobs,
  noResultSeparationJobs, newestRunningJob, backgroundRunningJobs, backgroundQueuedJobs,
  completedSessionJobs, recentFailedJobs, cancelledSessionJobs, queueJobs, focusedJob, currentJob,
  focusQueueJob, dismissQueueJob, clearFinishedQueueJobs, cancelQueueJob,
} = makeQueue({ task, focusedSeparationJobId, composingNewJob, showQueueModal, queueScope, cancellingQueueJobId, computed })
const audioElements = new Map<string, HTMLAudioElement>()
const audioAccessOrder: string[] = []
const playingOutputPath = ref('')
const outputPlayback = ref<Record<string, { currentTime: number; duration: number }>>({})
const PREVIEW_AUDIO_CACHE_LIMIT = 8
const {
  getOutputPlayback, setOutputPlayback, touchPreviewAudio, releasePreviewAudio,
  trimPreviewAudioCache, getAudio, syncOutputPreviewAudio,
} = makePlayback({ outputPlayback, playingOutputPath, audioAccessOrder, audioElements, PREVIEW_AUDIO_CACHE_LIMIT, Audio, convertFileSrc })
let unlistenDragDrop: UnlistenFn | null = null

const formatOptions = [
  { label: 'WAV', value: 'wav' },
  { label: 'FLAC', value: 'flac' },
  { label: 'MP3', value: 'mp3' },
  { label: 'M4A', value: 'm4a' },
]

function getFileKindLabel(path: string) {
  const ext = path.split('.').pop()?.toLowerCase() || ''
  if (['mp4', 'mkv', 'mov', 'avi', 'webm', 'flv'].includes(ext)) return t('separate.videoFile')
  return t('separate.audioFile')
}

const wavBitDepthOptions = computed(() => [
  { label: t('audio.pcm16'), value: 'PCM_16' },
  { label: t('audio.pcm24'), value: 'PCM_24' },
  { label: t('audio.float'), value: 'FLOAT' },
])
const flacBitDepthOptions = computed(() => [
  { label: t('audio.pcm16'), value: 'PCM_16' },
  { label: t('audio.pcm24'), value: 'PCM_24' },
])
const bitRateOptions = computed(() => [
  { label: t('audio.bitrate128'), value: '128k' },
  { label: t('audio.bitrate192'), value: '192k' },
  { label: t('audio.bitrate256'), value: '256k' },
  { label: t('audio.bitrate320'), value: '320k' },
  { label: t('audio.bitrate512'), value: '512k' },
])
const mp3BitRateOptions = computed(() => bitRateOptions.value.filter(option => parseInt(option.value, 10) <= 320))
const m4aCodecOptions = computed(() => [
  { label: t('audio.codecAac'), value: 'aac' },
])
const selectedModelName = computed(() => String(selectedModel.value || ''))

const {
  modelSearch, modelCategoryFilter, modelPage, modelSortOptions, listedDownloadedModels,
  selectedModelListItem, modelDownloaded,
  modelCategoryOptions, filteredDownloadedModels, pagedDownloadedModels, modelPageSizeOptions,
} = useModelList({
  t, locale, downloadedModels, selectedModelName, modelPreferences, modelListSortMode, modelPageSize,
  isLoading, modelsLoaded, modelNote, app, modelError,
})
// 这两个 computed 被 separateViewUx 在 VM 里按名字取用，保留组件内定义。
const modelPanelHasModels = computed(() => downloadedModels.value.length > 0)
const modelPanelLoading = computed(() => !modelPanelHasModels.value && !modelsLoaded.value
  && (isLoading.value || app.envLoading) && !modelError.value)
const ensembleTypeOptions = [
  'avg_wave', 'median_wave', 'min_wave', 'max_wave',
  'avg_fft', 'median_fft', 'min_fft', 'max_fft',
].map(value => ({ label: value, value }))
const saveAsFolder = computed({
  get: () => outputLayout.value === 'folders',
  set: (value: boolean) => {
    outputLayout.value = value ? 'folders' : 'flat'
  },
})
const effectiveOutputLayout = computed<OutputLayout>(() => outputLayout.value)
const compareModelName = (a: string, b: string) => compareModelNames(a, b, locale.value)

const currentModelInfo = computed(() => {
  if (model.selectedInfo?.name === selectedModelName.value) return model.selectedInfo
  return selectedModelListItem.value
})
const currentInferenceSampleStep = computed(() => currentModelInfo.value?.inferenceParamMeta?.recommendedSampleStep)
const overlapSizeStep = computed(() => resolveInferenceSampleStep(currentModelInfo.value?.inferenceParamMeta, 1))
const chunkSizeStep = computed(() => resolveInferenceChunkStep(currentModelInfo.value?.inferenceParamMeta, 1024))
const currentChunkConstraint = computed(() => currentModelInfo.value?.inferenceParamMeta?.chunkSizeConstraint)
const currentChunkIssue = computed(() => inferenceChunkSizeIssue(chunk_size.value, currentModelInfo.value?.inferenceParamMeta))


function updateOverlapSize(value: number | null) {
  overlap_size.value = value
}

function updateChunkSize(value: number | null) {
  chunk_size.value = value
}

const currentModelDefaults = computed(() => currentModelInfo.value?.defaultInferenceParams || {})
const currentModelDefaultsResolved = computed(() => Boolean(currentModelInfo.value?.defaultInferenceParamsResolved))
const currentModelType = computed(() => String(currentModelInfo.value?.modelType || '').trim().toLowerCase())
const isVrModel = computed(() => currentModelType.value === 'vr')
// 槽位面板：接口来自后端（模型的 slots），布局来自前端布局档。
// 现有 refs 用的就是原生键，所以 bind 直接映射到 ref，不需要额外状态。
const slotRefs: Record<string, Ref<unknown>> = {
  batch_size, overlap_size, num_overlap, chunk_size, window_size, aggression,
  enable_post_process, high_end_process, post_process_threshold, standardize,
}

const { slotPanelPlan, slotPanelValues, updateSlotPanel } = makeSlotPanel({
  computed, currentModelInfo, currentChunkConstraint, slotRefs,
})

const isApolloModel = computed(() => currentModelType.value === 'apollo')
const showStandardizeField = computed(() => Boolean(currentModelInfo.value) && !isVrModel.value)
const showNormalizeField = computed(() => Boolean(currentModelInfo.value))
const {
  collectCurrentModelInferenceDefaults, applyCurrentModelInferenceDefaults,
  saveCurrentModelInferenceDefaults, resetCurrentModelInferenceDefaults,
} = makeInferenceDefaults({
  currentModelDefaults, batch_size, overlap_size, num_overlap, chunk_size, window_size, aggression,
  enable_post_process, post_process_threshold, high_end_process, standardize, normalize,
  isApolloModel, showStandardizeField, showNormalizeField, currentModelInfo, currentChunkIssue,
  model, task, message, t, studioLog,
})
const hasVisibleAdvancedFields = computed(() => (
  Object.keys(currentModelDefaults.value).some(key => !['standardize', 'normalize'].includes(key))
))
const shouldPrefetchAdvancedParams = computed(() => (
  Boolean(currentModelInfo.value?.downloaded)
  && (!currentModelDefaultsResolved.value || !String(currentModelInfo.value?.configInstruments || '').trim())
))
const advancedParamsLoading = computed(() => shouldPrefetchAdvancedParams.value && detailLoading.value)
function hasInferenceField(key: string) {
  if (key === 'standardize' || key === 'normalize') return false
  if (key === 'num_overlap' && isApolloModel.value) return false
  return Object.prototype.hasOwnProperty.call(currentModelDefaults.value, key)
}

const advancedPanelContext = {
  slotPanelPlan, slotPanelValues, updateSlotPanel, advancedParamsLoading,
  guidedParameter, hasInferenceField, hasVisibleAdvancedFields,
  batch_size, overlap_size, num_overlap, chunk_size, window_size, aggression,
  post_process_threshold, standardize, enable_post_process, high_end_process,
  overlapSizeStep, currentInferenceSampleStep, chunkSizeStep,
  currentChunkConstraint, currentChunkIssue, updateOverlapSize, updateChunkSize,
  showStandardizeField, currentModelInfo, currentModelDefaultsResolved,
  resetCurrentModelInferenceDefaults, saveCurrentModelInferenceDefaults,
  restoreInferenceNumberFallback: (key: string) => task.restoreInferenceNumberFallback(key as never),
}

watch(() => beginnerGuide.parameterStep, active => {
  if (beginnerGuide.demoActive && !isTutorialFrame()) return
  if (active && !ensembleEnabled.value) {
    guideOpenedSettings = true
    showSettingsDrawer.value = true
    parameterHelpPage.value = null
    expandedInferenceSections.value = ['inference']
  } else if (guideOpenedSettings) {
    guideOpenedSettings = false
    showSettingsDrawer.value = false
  }
}, { immediate: true })
watch(showSettingsDrawer, show => {
  if (!show && guideOpenedSettings && beginnerGuide.parameterStep) beginnerGuide.pause()
})
watch(expandedInferenceSections, names => {
  if (guideRequiresAdvanced.value && showSettingsDrawer.value && parameterHelpPage.value === null && !names.includes('inference')) beginnerGuide.pause()
})

async function locateGuidedParameter() {
  await nextTick()
  if (!beginnerGuide.parameterStep || !showSettingsDrawer.value || parameterHelpPage.value !== null) return
  if (guideRequiresAdvanced.value && !expandedInferenceSections.value.includes('inference')) {
    expandedInferenceSections.value = ['inference']
    await nextTick()
  }
  const field = settingsCardRef.value?.$el.querySelector<HTMLElement>(`[data-parameter-guide="${guidedParameter.value?.id}"]`)
  guideTarget.value = field || null
  field?.scrollIntoView({ block: 'nearest', behavior: 'instant' })
  const input = field?.querySelector<HTMLInputElement>('input')
  if (input && !input.readOnly) input.focus({ preventScroll: true })
  fieldGuideRef.value?.syncPosition()
}
watch([guidedParameter, parameterHelpPage, expandedInferenceSections], () => {
  void locateGuidedParameter()
}, { flush: 'post' })

function openParameterHelp() {
  if (beginnerGuide.parameterStep) return
  parameterHelpPage.value = 0
}

function parseModelInstruments(value?: unknown) {
  const seen = new Set<string>()
  const rawItems = Array.isArray(value)
    ? value
    : (() => {
        const text = String(value || '').trim()
        if (!text) return []
        if (text.startsWith('[')) {
          try {
            const parsed = JSON.parse(text)
            if (Array.isArray(parsed)) return parsed
          } catch {
            // Fall through to delimiter parsing for Python-style list strings.
          }
        }
        return text.split(/[,，;；/|\n]+/)
      })()
  return rawItems
    .map(item => String(item || '').trim().replace(/^[\s"'[\](){}]+|[\s"'[\](){}]+$/g, ''))
    .filter((item) => {
      if (!item) return false
      const key = item.toLowerCase()
      if (seen.has(key)) return false
      seen.add(key)
      return true
    })
}
const availableStemNames = computed(() => parseModelInstruments(currentModelInfo.value?.configInstruments))
const ensembleModelEntries = computed(() => ensembleModels.value
  .map(name => listedDownloadedModels.value.find(item => item.name === name))
  .filter((item): item is (typeof listedDownloadedModels.value)[number] => Boolean(item)))
const ensembleStemOptionsByModel = computed(() => Object.fromEntries(
  ensembleModelEntries.value.map(item => [
    item.name,
    parseModelInstruments(item.configInstruments).map(stem => ({ label: stem, value: stem })),
  ]),
))
const ensembleReady = computed(() => ensembleEnabled.value
  && ensembleModels.value.length >= 2
  && ensembleModelEntries.value.length === ensembleModels.value.length
  && Boolean(ensembleStem.value.trim())
  && ensembleModels.value.every((name) => {
    const selectedStem = String(ensembleModelStems.value[name] || '').trim()
    const options = ensembleStemOptionsByModel.value[name] || []
    return Boolean(selectedStem) && options.some(option => option.value.toLowerCase() === selectedStem.toLowerCase())
  }))
const {
  outputStemSelectionCleared, checkedOutputStems, selectedOutputStemCount,
  hasOutputStemSelection, selectAllOutputStems, clearOutputStems,
} = makeStemSelection({ selectedStems, persistedOutputStemSelectionCleared, availableStemNames, computed, ref })
const selectedStemSummary = computed(() => {
  if (outputStemSelectionCleared.value) return t('separate.noStemsSelected')
  if (!selectedStems.value.length) return t('separate.allStems')
  return selectedStems.value.join(', ')
})
const selectedStemDetail = computed(() => {
  if (selectedStems.value.length > 6) {
    return t('separate.selectedStemCount', {
      count: selectedOutputStemCount.value,
      total: availableStemNames.value.length,
    })
  }
  return selectedStemSummary.value
})


function normalizeStemOrder(order: string[], stems: string[]) {
  const byKey = new Map(stems.map(stem => [stem.toLowerCase(), stem]))
  const used = new Set<string>()
  const next: string[] = []
  order.forEach((stem) => {
    const key = stem.toLowerCase()
    const resolved = byKey.get(key)
    if (!resolved || used.has(key)) return
    used.add(key)
    next.push(resolved)
  })
  stems.forEach((stem) => {
    const key = stem.toLowerCase()
    if (!used.has(key)) next.push(stem)
  })
  return next
}

// 合奏模式下输出只有一个 stem（用用户起的名字）；但切到 workflow 模式时是在编排
// 每个模型各自的输出，这时仍然是按模型 stem 命名，所以要看 runMode。
const orderedOutputStems = computed(() => (ensembleEnabled.value && runMode.value !== 'workflow')
  ? [ensembleStem.value.trim()].filter(Boolean)
  : normalizeStemOrder(customStemOrder.value, checkedOutputStems.value))
const outputNamingConfig = computed(() => ({
  enabled: Boolean(outputNamingTemplate.value.trim()),
  template: outputNamingTemplate.value.trim() || '%index%_%filename%_%stem%',
  stemOrder: orderedOutputStems.value,
}))
const outputNamingSummary = computed(() => outputNamingConfig.value.enabled
  ? outputNamingConfig.value.template
  : t('separate.namingDefaultSummary'))
const usesIndexToken = computed(() => outputNamingTemplate.value.includes('%index%'))
const contextMenuOptions = computed<DropdownOption[]>(() => {
  if (contextMenuKind.value === 'model' && contextModel.value) {
    return [{ key: 'edit-model-note', label: t('models.editNote') }]
  }
  return []
})

function openSeparateContextMenu(event: MouseEvent, kind: 'model', target: ModelEntry) {
  contextMenuKind.value = kind
  contextModel.value = target
  contextMenuVisible.value = false
  contextMenuX.value = event.clientX
  contextMenuY.value = event.clientY
  window.requestAnimationFrame(() => {
    contextMenuVisible.value = true
  })
}

function closeSeparateContextMenu() {
  contextMenuVisible.value = false
  contextMenuKind.value = null
  contextModel.value = null
}

function openModelNoteEditor(item: ModelEntry) {
  noteEditorModel.value = item
  noteDraft.value = modelNote(item.name)
  showModelNoteEditor.value = true
}

function closeModelNoteEditor() {
  showModelNoteEditor.value = false
  noteEditorModel.value = null
}

function handleModelNoteEditorUpdate(value: boolean) {
  showModelNoteEditor.value = value
  if (!value) noteEditorModel.value = null
}

function saveModelNote() {
  const item = noteEditorModel.value
  if (!item) return
  model.setModelNote(item.name, noteDraft.value)
  closeModelNoteEditor()
  message.success(t('models.noteSaved'))
}

function handleSeparateContextMenuSelect(key: string | number) {
  const item = contextModel.value
  closeSeparateContextMenu()
  if (!item) return
  if (key === 'edit-model-note') openModelNoteEditor(item as ModelEntry)
}

const startStatusText = computed(() => {
  if (outputDirectoryError.value) return outputDirectoryError.value
  if (!inputFiles.value.length) return t('separate.outputDirectorySummary')
  if (ensembleEnabled.value && !ensembleReady.value) return t('separate.ensembleNotReady')
  if (!ensembleEnabled.value && !modelDownloaded.value) return t('separate.startHintModelMissing')
  if (!ensembleEnabled.value && !hasOutputStemSelection.value) {
    return t('separate.startHintNoOutputStems')
  }
  return t('separate.outputDirectorySummary')
})

const effectiveFormat = computed(() => String(settings.defaultFormat || 'wav').trim().toLowerCase() || 'wav')

const {
  namingTemplateInputRef, namingTokens, outputNamingPreviewFiles, outputNamingPreviewParts, insertNamingToken,
} = useOutputNaming({
  t, inputFiles, ensembleEnabled, selectedModelName, orderedOutputStems, outputNamingTemplate,
  outputNamingConfig, effectiveFormat,
})
const formatLabel = computed(() => effectiveFormat.value.toUpperCase())
const outputSummaryPath = computed(() => outputPreview.value)
const finishedSessionJobCount = computed(() => recentFailedJobs.value.length + completedSessionJobs.value.length + cancelledSessionJobs.value.length)
const hasQueueEntries = computed(() => queueJobs.value.length > 0 || noResultSeparationJobs.value.length > 0)
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
const completedActionOptions = computed<DropdownOption[]>(() => [
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
  return isTerminalTaskStatus(status)
    ? status as 'done' | 'failed' | 'cancelled'
    : 'running'
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
const taskClockNow = ref(Date.now())
let taskClockTimer: ReturnType<typeof setInterval> | null = null
const currentTaskDuration = computed(() => currentTask.value ? taskDuration(currentTask.value, taskClockNow.value) : '')

function stopTaskClock() {
  if (taskClockTimer === null) return
  clearInterval(taskClockTimer)
  taskClockTimer = null
}

function syncTaskClock(running: boolean) {
  taskClockNow.value = Date.now()
  if (!running) {
    stopTaskClock()
  } else if (taskClockTimer === null) {
    taskClockTimer = setInterval(() => { taskClockNow.value = Date.now() }, 1000)
  }
}

watch(
  () => Boolean(currentTask.value && !['queued', 'done', 'failed', 'cancelled'].includes(currentTask.value.status)),
  syncTaskClock,
  { immediate: true },
)

function configuredStemOrder(item: SeparationTask) {
  if (item.runConfig?.outputNaming?.stemOrder?.length) return item.runConfig.outputNaming.stemOrder
  if (item.runConfig?.runMode === 'workflow') return []
  const modelEntry = modelEntries.value.find(modelItem => modelItem.name === item.model)
  const configured = parseModelInstruments(modelEntry?.configInstruments)
  const selected = item.runConfig?.selectedStems || []
  if (!configured.length) return selected
  if (!selected.length) return configured

  const selectedKeys = new Set(selected.map(stem => stem.toLowerCase()))
  const configuredKeys = new Set(configured.map(stem => stem.toLowerCase()))
  return [
    ...configured.filter(stem => selectedKeys.has(stem.toLowerCase())),
    ...selected.filter(stem => !configuredKeys.has(stem.toLowerCase())),
  ]
}

const playableOutputGroups = computed(() => currentBatchTasks.value
  .filter(item => item.status === 'done')
  .map(item => ({
    taskId: item.id,
    input: item.input,
    outputs: sortStemOutputsByOrder(
      item.outputs.filter(output => Boolean(output.path)),
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


watch(playableOutputs, syncOutputPreviewAudio, { immediate: true })

async function toggleOutputPlayback(output: StemOutput) {
  if (!output.path) return
  const audio = getAudio(output.path)
  if (playingOutputPath.value === output.path && !audio.paused) {
    audio.pause()
    playingOutputPath.value = ''
    return
  }

  audioElements.forEach((item, path) => {
    if (path !== output.path) item.pause()
  })
  try {
    await audio.play()
    playingOutputPath.value = output.path
  } catch (error) {
    message.error(error instanceof Error ? error.message : t('separate.previewPlayFailed'))
  }
}

function seekOutput(path: string, value: number) {
  const audio = getAudio(path)
  const next = Number(value || 0)
  audio.currentTime = next
  setOutputPlayback(path, { currentTime: next, duration: audio.duration || getOutputPlayback(path).duration })
}

function stopAllPreviewAudio() {
  for (const path of [...audioElements.keys()]) releasePreviewAudio(path)
  audioAccessOrder.length = 0
  playingOutputPath.value = ''
  outputPlayback.value = {}
}





const {
  draggedInputIndex, draggedStemIndex, moveStem,
  onInputPointerDown, onInputPointerMove, finishInputPointerDrag,
  onStemPointerDown, onStemPointerMove, finishStemPointerDrag,
} = makeReorder({
  isRunModeLocked,
  orderedOutputStems,
  customStemOrder,
  moveInputFile: (fromIndex, toIndex) => task.moveInputFile(fromIndex, toIndex),
})


function resetOutputNaming() {
  outputNamingTemplate.value = '%index%_%filename%_%stem%'
  customStemOrder.value = []
}

function categoryLabel(item: { categoryCn?: string; category?: string; primaryCategoryCn?: string; primaryCategory?: string } | null | undefined) {
  return getModelCategoryLabel(item, locale.value, t('common.notSet'))
}

function modelTargetLabel(item: {
  targetStem?: string
  configTargetInstrument?: string
} | null | undefined) {
  return item?.targetStem || item?.configTargetInstrument || t('common.notSet')
}

function modelArchitectureLabel(item: {
  architecture?: string
  modelType?: string | null
} | null | undefined) {
  return item?.architecture || item?.modelType || t('common.notSet')
}

/** The user's own note for a model, kept beside the catalog entry rather than in it. */
function modelNote(name: string) {
  return modelPreferences.value[name]?.note || ''
}

function modelUseCount(name: string) {
  return model.getModelUseCount(name)
}

function modelMetaLine(item: {
  targetStem?: string
  configTargetInstrument?: string
  architecture?: string
  modelType?: string | null
}) {
  return `${modelTargetLabel(item)} · ${modelArchitectureLabel(item)}`
}

function handleSelectModel(item: (typeof listedDownloadedModels.value)[number]) {
  if (ensembleEnabled.value) {
    const next = ensembleModels.value.includes(item.name)
      ? ensembleModels.value.filter(name => name !== item.name)
      : [...ensembleModels.value, item.name]
    ensembleModels.value = next
    return
  }
  model.selectModel(item).catch(() => {})
}

function prefetchSelectedModelAdvancedParams() {
  if (!shouldPrefetchAdvancedParams.value || detailLoading.value || isLoading.value || !selectedModelName.value) return
  model.selectModel(selectedModelName.value).catch(() => {})
}

watch(selectedModelName, (name, previousName) => {
  if (previousName && previousName !== name) task.saveCurrentModelState(previousName)
})

watch(
  persistedOutputStemSelectionCleared,
  (cleared) => {
    outputStemSelectionCleared.value = cleared
  },
  { immediate: true },
)

watch(ensembleModels, (models) => {
  const next = { ...ensembleWeights.value }
  const nextStems = { ...ensembleModelStems.value }
  models.forEach(name => {
    if (!Number.isFinite(Number(next[name])) || Number(next[name]) < 0) next[name] = 1
    const options = ensembleStemOptionsByModel.value[name] || []
    if (!options.length) delete nextStems[name]
    else {
      const savedStem = String(nextStems[name] || '').trim()
      const savedOption = options.find(option => option.value.toLowerCase() === savedStem.toLowerCase())
      const legacyStem = ensembleStem.value
      const legacyOption = options.find(option => option.value.toLowerCase() === legacyStem.toLowerCase())
      nextStems[name] = savedOption?.value || legacyOption?.value || options[0].value
    }
  })
  Object.keys(next).forEach(name => {
    if (!models.includes(name)) delete next[name]
  })
  Object.keys(nextStems).forEach(name => {
    if (!models.includes(name)) delete nextStems[name]
  })
  ensembleWeights.value = next
  ensembleModelStems.value = nextStems
  if (!ensembleStem.value.trim()) {
    const firstStem = models.map(name => nextStems[name]).find(Boolean)
    if (firstStem) ensembleStem.value = firstStem
  }
}, { deep: true, immediate: true })

watch(
  [downloadedModels, modelsLoaded],
  ([availableModels, loaded]) => {
    if (!loaded) return
    const validModels = retainAvailableModelNames(
      ensembleModels.value,
      availableModels.map(item => item.name),
    )
    if (validModels.length !== ensembleModels.value.length) ensembleModels.value = validModels
  },
  { deep: true, immediate: true },
)

watch(ensembleStemOptionsByModel, () => {
  const models = ensembleModels.value
  if (!models.length) return
  const nextStems = { ...ensembleModelStems.value }
  let changed = false
  models.forEach((name) => {
    const options = ensembleStemOptionsByModel.value[name] || []
    if (!options.length) {
      if (name in nextStems) {
        delete nextStems[name]
        changed = true
      }
      return
    }
    const savedStem = String(nextStems[name] || '').trim()
    const savedOption = options.find(option => option.value.toLowerCase() === savedStem.toLowerCase())
    if (!savedOption || savedOption.value !== nextStems[name]) {
      const legacyStem = ensembleStem.value
      const legacyOption = options.find(option => option.value.toLowerCase() === legacyStem.toLowerCase())
      nextStems[name] = savedOption?.value || legacyOption?.value || options[0].value
      changed = true
    }
  })
  if (changed) ensembleModelStems.value = nextStems
}, { deep: true, immediate: true })

watch([modelSearch, modelCategoryFilter, modelPageSize], () => {
  modelPage.value = 1
})

watch(() => filteredDownloadedModels.value.length, (count) => {
  const pageCount = Math.max(1, Math.ceil(count / modelPageSize.value))
  if (modelPage.value > pageCount) modelPage.value = pageCount
})

watch(
  availableStemNames,
  (stems) => {
    if (!selectedStems.value.length) return
    const allowed = new Set(stems)
    selectedStems.value = selectedStems.value.filter(stem => allowed.has(stem))
  },
  { immediate: true },
)

watch(
  checkedOutputStems,
  (stems) => {
    customStemOrder.value = normalizeStemOrder(customStemOrder.value, stems)
  },
  { immediate: true },
)

watch(
  [selectedModelName, currentModelInfo],
  ([name, info], previous) => {
    if (!info || !name || info.name !== name) return
    const modelChanged = Boolean(
      (previous?.[0] && previous[0] !== name)
      || (previous?.[1]?.name && previous[1].name !== info.name),
    )
    task.applySelectedModelDefaults(
      model.getModelBaseInferenceDefaults(info.name) || info.defaultInferenceParams,
      info.modelType,
      task.getSavedModelState(info.name),
      model.getModelInferenceOverrides(info.name),
      {
        force: modelChanged,
      },
    )
    if (modelChanged) outputStemSelectionCleared.value = persistedOutputStemSelectionCleared.value
  },
  { immediate: true },
)

watch(
  shouldPrefetchAdvancedParams,
  (shouldPrefetch) => {
    if (!shouldPrefetch) return
    prefetchSelectedModelAdvancedParams()
  },
  { immediate: true },
)

watch(
  [listedDownloadedModels, selectedModel, isLoading],
  ([list, current, loading]) => {
    if (loading) return
    if (!list.length) return
    const valid = current && list.some((item) => item.name === current)
    if (!valid) {
      selectedModel.value = list[0].name
      model.selectModel(list[0]).catch(() => {})
    }
  },
  { immediate: true },
)

const availableParameterGuides = computed(() => visibleParameterGuides({
  modelType: currentModelType.value,
  hasModel: Boolean(currentModelInfo.value),
  defaults: currentModelDefaults.value,
  format: effectiveFormat.value,
  canSaveDefaults: Boolean(currentModelInfo.value && currentModelDefaultsResolved.value && !advancedParamsLoading.value),
}))
watch(availableParameterGuides, items => { if (!beginnerGuide.demoActive || isTutorialFrame()) beginnerGuide.registerParameters(items) }, { immediate: true })

onMounted(async () => {
  if (import.meta.env.DEV && route.query.preview === 'results') {
    const previewJob = task.resultJobs.filter(isSeparationJob).sort((a, b) => b.updatedAt - a.updatedAt)[0]
    if (previewJob) focusedSeparationJobId.value = previewJob.id
  }
  if (!app.envInfo && !app.envLoading) {
    app.checkEnvInBackground().catch(() => {})
  }
  // 别的页面可能刚下载过模型；这里补一次刷新，避免"已下载但本页看不到"。
  if (!modelsLoaded.value) void model.loadModels()
  try {
    unlistenDragDrop = await getCurrentWebview().onDragDropEvent(async (event) => {
      if (beginnerGuide.demoActive) { isDragging.value = false; return }
      const type = event.payload.type
      if (type === 'over' || type === 'enter') {
        isDragging.value = true
      } else if (type === 'drop') {
        isDragging.value = false
        const paths = (event.payload as { paths?: string[] }).paths || []
        // 拖到「临时输出目录」输入框上：单个文件夹 = 引用它，别当成音频文件加入。
        // 落点判定依赖 DOM/原生命令，取不到就当作普通文件拖放（不阻断主流程）。
        try {
          if (typeof document !== 'undefined' && typeof window !== 'undefined') {
            const zones = Array.from(document.querySelectorAll<HTMLElement>('[data-drop-zone]')).map(element => {
              const rect = element.getBoundingClientRect()
              return {
                id: element.dataset.dropZone || '',
                left: rect.left,
                top: rect.top,
                right: rect.right,
                bottom: rect.bottom,
              }
            })
            const point = toCssPoint(
              (event.payload as { position?: { x: number; y: number } }).position,
              window.devicePixelRatio,
            )
            if (findDropZone(point, zones) === 'outputDir' && paths.length === 1) {
              const kinds = await invoke<Array<[string, string]>>('path_kinds', { paths })
              const folder = kinds.length === 1 && kinds[0][1] === 'directory' ? kinds[0][0] : ''
              if (folder) {
                temporaryOutputDir.value = folder
                message.success(folder)
                return
              }
              message.warning(t('image.dropFolderHere'))
              return
            }
          }
        } catch {
          // 判定失败：按普通文件处理
        }
        const added = await task.addPaths(paths)
        if (added > 0) message.success(t('separate.addedFiles', { count: added }))
        else message.warning(t('separate.noAudioAdded'))
      } else {
        isDragging.value = false
      }
    })
  } catch {
    // 非 Tauri 环境静默降级
  }
})

onBeforeUnmount(() => {
  stopTaskClock()
  if (unlistenDragDrop) unlistenDragDrop()
  finishInputPointerDrag()
  finishStemPointerDrag()
  stopAllPreviewAudio()
})

async function handlePickFiles() {
  const before = inputFiles.value.length
  await task.pickFiles()
  const added = inputFiles.value.length - before
  if (added > 0) message.success(t('separate.addedFiles', { count: added }))
}

async function handlePickFolder() {
  const count = await task.pickInputFolder()
  if (count > 0) message.success(t('separate.folderScanned', { count }))
  else message.warning(t('separate.folderEmpty'))
}

async function pickTemporaryOutputDir() {
  const folder = await settings.pickFolder()
  if (folder) temporaryOutputDir.value = folder
}

function positiveInferenceNumber(value: number | null | undefined) {
  return typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : null
}

function nonNegativeInferenceNumber(value: number | null | undefined) {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null
}

function buildEnsembleInferenceParams(modelType?: string | null) {
  const normalizedModelType = String(modelType || '').trim().toLowerCase()
  const vrModel = normalizedModelType === 'vr'
  const apolloModel = normalizedModelType === 'apollo'
  const params: Record<string, unknown> = {
    standardize: standardize.value,
    normalize: normalize.value,
  }
  const batchSizeValue = positiveInferenceNumber(batch_size.value)
  if (batchSizeValue !== null) params.batch_size = batchSizeValue

  if (vrModel) {
    const windowSizeValue = positiveInferenceNumber(window_size.value)
    const aggressionValue = nonNegativeInferenceNumber(aggression.value)
    const postProcessThresholdValue = nonNegativeInferenceNumber(post_process_threshold.value)
    if (windowSizeValue !== null) params.window_size = windowSizeValue
    if (aggressionValue !== null) params.aggression = aggressionValue
    params.enable_post_process = enable_post_process.value
    if (postProcessThresholdValue !== null) params.post_process_threshold = postProcessThresholdValue
    params.high_end_process = high_end_process.value
    return params
  }

  const overlapSizeValue = positiveInferenceNumber(overlap_size.value)
  const numOverlapValue = apolloModel ? null : positiveInferenceNumber(num_overlap.value)
  const chunkSizeValue = positiveInferenceNumber(chunk_size.value)
  if (overlapSizeValue !== null) params.overlap_size = overlapSizeValue
  if (numOverlapValue !== null) params.num_overlap = numOverlapValue
  if (chunkSizeValue !== null) params.chunk_size = chunkSizeValue
  return params
}

function buildEnsembleWorkflow(): WorkflowEntry {
  const outputStem = ensembleStem.value.trim()
  const runtimeDevice = settings.getRuntimeDeviceConfig(app.envInfo)
  const device = runtimeDevice.device || 'auto'
  const fmt = effectiveFormat.value || 'wav'
  const models = ensembleModels.value
  const nodes: any[] = []
  const links: any[] = []
  let nodeId = 1
  let linkId = 1
  const nextId = () => nodeId++
  const link = (srcNode: number, srcSlot: number, dstNode: number, dstSlot: number, type: string) => {
    const id = linkId++
    links.push([id, srcNode, srcSlot, dstNode, dstSlot, type])
    return id
  }

  // 1. load_audio: outputs AUDIO(0), STRING(1)
  const loadId = nextId()
  nodes.push({ id: loadId, type: 'pymss_load_audio', pos: [72, 210], size: [220, 80], flags: {}, order: nodes.length, mode: 0, properties: { audio: 'input.wav' }, widgets_values: ['input.wav'], inputs: [{ name: 'audio', type: 'COMBO', widget: { name: 'audio' }, link: null }], outputs: [{ name: 'audio', type: 'AUDIO', links: [] }, { name: 'audio_name', type: 'STRING', links: [] }] })

  // 2. per model: mss_params + mss_separate
  const separateIds: number[] = []
  const separateStemOutSlots: number[] = [] // the AUDIO slot for the chosen stem
  models.forEach((modelName, index) => {
    const entry = ensembleModelEntries.value.find(item => item.name === modelName) || null
    const stem = String(ensembleModelStems.value[modelName] || '').trim() || 'vocals'
    const paramsId = nextId()
    nodes.push({ id: paramsId, type: 'pymss_mss_params', pos: [300, 100 + index * 160], size: [220, 120], flags: {}, order: nodes.length, mode: 0, properties: {}, widgets_values: [1, 'Default', 'Default', false, false, false], inputs: [], outputs: [{ name: 'mss_params', type: 'PYMSS_MSS_PARAMS', links: [] }] })
    const sepId = nextId()
    // stem outputs: pairs of (stem Audio, stem String); find the Audio slot index
    const audioSlot = index * 2 // we connect load.audio(0) -> sep.audio(0), params(0) -> sep.params(1)
    const sepOutputs: any[] = [
      { name: `${stem} (Audio)`, type: 'AUDIO', links: [] },
      { name: `${stem} (String)`, type: 'STRING', links: [] },
    ]
    nodes.push({ id: sepId, type: 'mss_separate', pos: [560, 100 + index * 160], size: [240, 100], flags: {}, order: nodes.length, mode: 0, properties: {}, widgets_values: [modelName, device, true, settings.downloadSource, String(runtimeDevice.deviceIds.join(',') || '0'), false], inputs: [{ name: 'audio', type: 'AUDIO', link: null }, { name: 'params', type: 'PYMSS_MSS_PARAMS', shape: 7, link: null }], outputs: sepOutputs })
    separateIds.push(sepId)
    separateStemOutSlots.push(index) // stem Audio is output slot 0 of each sep (only one stem)
    // wire load.audio -> sep.audio, params -> sep.params
    const la = link(loadId, 0, sepId, 0, 'AUDIO')
    nodes[nodes.length - 1].inputs[0].link = la
    const lp = link(paramsId, 0, sepId, 1, 'PYMSS_MSS_PARAMS')
    nodes[nodes.length - 1].inputs[1].link = lp
    nodes[nodes.length - 1].outputs[0].links = []
    void audioSlot
  })

  // 3. audio_ensemble: inputs audio_1..audio_N (slot 0..N-1), output audio(0)
  const ensId = nextId()
  const ensInputs: any[] = []
  models.forEach((_, index) => ensInputs.push({ name: `audio_${index + 1}`, type: 'AUDIO', link: null }))
  nodes.push({ id: ensId, type: 'pymss_audio_ensemble', pos: [900, 210], size: [220, 120], flags: {}, order: nodes.length, mode: 0, properties: {}, widgets_values: [models.length, ensembleType.value, ...models.map(m => { const w = Number(ensembleWeights.value[m]); return Number.isFinite(w) ? w : 1 })], inputs: ensInputs, outputs: [{ name: 'audio', type: 'AUDIO', links: [] }] })
  // wire each sep stem Audio -> ensemble input slot index
  models.forEach((_, index) => {
    const lid = link(separateIds[index], 0, ensId, index, 'AUDIO')
    nodes[nodes.length - 1].inputs[index].link = lid
    // record outgoing link on sep output[0]
    const sepNode = nodes.find((n) => n.id === separateIds[index])
    if (sepNode) sepNode.outputs[0].links = [lid]
  })

  // 4. save_audio: input audio(0), filename(1)
  const saveId = nextId()
  nodes.push({ id: saveId, type: 'pymss_save_audio', pos: [1180, 210], size: [240, 120], flags: {}, order: nodes.length, mode: 0, properties: {}, widgets_values: [fmt, 'Default', '44100', 'FLOAT', 'PCM_24', '320k'], inputs: [{ name: 'audio', type: 'AUDIO', link: null }, { name: 'filename', type: 'STRING', shape: 7, link: null }], outputs: [] })
  const lsave = link(ensId, 0, saveId, 0, 'AUDIO')
  nodes[nodes.length - 1].inputs[0].link = lsave
  nodes[nodes.length - 1].outputs = []
  nodes.find((n) => n.id === ensId)!.outputs[0].links = [lsave]

  return {
    id: 'temporary-ensemble',
    name: t('separate.ensembleWorkflowName'),
    description: t('separate.ensembleWorkflowDescription'),
    format: 'graph',
    formatVersion: WORKFLOW_FORMAT_VERSION,
    createdAt: Date.now(),
    updatedAt: Date.now(),
    definition: {
      last_node_id: nodeId - 1,
      last_link_id: linkId - 1,
      nodes,
      links,
      version: 1,
      extra: { appDefaults: { device, output_format: fmt }, studioEnsemble: { outputStem } },
    } as Record<string, unknown>,
  }
}


const {
  outputDirectoryInput, outputDirectoryPlatform, outputDirectoryError, outputPreview,
  validateOutputDirectory, canStart, start,
} = makeOutputDirectory({
  temporaryOutputDir, normalizedOutputDir, app, settings, outputNamingPreviewFiles, effectiveOutputLayout, t,
  submittingJob, ensembleEnabled, ensembleReady, modelDownloaded, inputFiles, hasOutputStemSelection,
  currentChunkIssue, task, focusedSeparationJobId, newestRunningJob, composingNewJob, ensembleModels, model,
  buildEnsembleWorkflow, outputNamingConfig, WINDOWS_RESERVED_FILENAMES, computed, launchTask,
})
function beginNextSeparation() {
  stopAllPreviewAudio()
  showLogModal.value = false
  focusedSeparationJobId.value = null
  composingNewJob.value = true
}

function viewQueueJobLogs(target: SeparationJob) {
  if (!focusQueueJob(target)) return
  void nextTick(() => { showLogModal.value = true })
}

function removeSeparationRunRecords(jobs: SeparationJob[]) {
  if (removingRunRecords.value || !jobs.length || jobs.some(job => !isNoResultSeparationJob(job))) return
  const ids = jobs.map(job => job.id)
  dialog.warning({
    title: t('workbench.removeRun'), content: t('separate.removeRunRecordsContent', { count: ids.length }),
    positiveText: t('results.removeAction'), negativeText: t('common.cancel'), positiveButtonProps: { type: 'error' },
    onPositiveClick: async () => {
      removingRunRecords.value = true
      try {
        await task.removeSeparationRuns(ids)
        if (focusedSeparationJobId.value && ids.includes(focusedSeparationJobId.value)) {
          showLogModal.value = false
          focusedSeparationJobId.value = null
        }
      } catch (error) { message.error(error instanceof Error ? error.message : String(error)); return false }
      finally { removingRunRecords.value = false }
    },
  })
}

function queueJobInputSummary(job: SeparationJob) {
  const first = getFileName(job.primary.input)
  return job.inputCount > 1
    ? t('separate.queueBatchInputs', { count: job.inputCount, name: first })
    : first
}

function requestCancelQueueJob(job: SeparationJob) {
  if (!isSeparationJob(job)) return
  dialog.warning({
    title: t('separate.cancelBatchTitle'),
    content: t('separate.cancelBatchHint', { name: job.model }),
    positiveText: t('tasks.cancelAction'),
    negativeText: t('common.cancel'),
    positiveButtonProps: { type: 'error' },
    onPositiveClick: () => cancelQueueJob(job),
  })
}

function goToResults() {
  router.push('/results')
}

function openCurrentLogs() {
  if (!currentTask.value) return
  showLogModal.value = true
}


function handleCompletedAction(key: string | number) {
  if (key === 'retry') {
    void retryCurrentTask()
  } else if (key === 'logs') {
    openCurrentLogs()
  }
}

function handleCancelCurrentTask() {
  const targets = currentBatchTasks.value.filter(item => !isTerminalTaskStatus(item.status))
  if (!targets.length) return
  dialog.warning({
    title: t('tasks.cancelConfirmTitle'),
    content: t('tasks.cancelConfirmContent'),
    positiveText: t('tasks.cancelAction'),
    negativeText: t('common.cancel'),
    positiveButtonProps: { type: 'error' },
    negativeButtonProps: { secondary: true },
    onPositiveClick: async () => {
      if (cancellingTaskId.value) return
      cancellingTaskId.value = targets.length > 1 ? 'batch' : targets[0].id
      try {
        const results = await Promise.all(targets.map(item => task.cancelTask(item.id)))
        if (results.some(Boolean)) message.success(t('tasks.cancelSuccess'))
      } catch (error) {
        message.error(error instanceof Error ? error.message : String(error))
      } finally {
        cancellingTaskId.value = null
      }
    },
  })
}

async function retryCurrentTask() {
  const item = currentTask.value
  if (!item) return
  dialog.warning({
    title: t('tasks.confirmRetryTitle'),
    content: t('tasks.confirmRetry'),
    positiveText: t('common.retry'),
    negativeText: t('common.cancel'),
    negativeButtonProps: { secondary: true },
    onPositiveClick: async () => {
      stopAllPreviewAudio()
      try {
        const next = await task.retryTask(item.id)
        focusedSeparationJobId.value = next?.jobId || next?.id || focusedSeparationJobId.value
        message.success(t('toast.taskRetried'))
      } catch (error) {
        message.error(error instanceof Error ? error.message : String(error))
      }
    },
  })
}
const audioSteps = computed(() => [
  { title: t('separate.input'), text: t('separate.stepInputHint') },
  { title: t('separate.output'), text: t('separate.stepOutputHint') },
  { title: t('separate.model'), text: t('separate.stepRunHint') },
])
const namingModalContext = {
  namingTemplateInputRef, outputNamingTemplate, outputNamingPreviewParts, namingTokens,
  insertNamingToken, usesIndexToken, orderedOutputStems, draggedStemIndex,
  onStemPointerDown, onStemPointerMove, finishStemPointerDrag, padNumber,
  moveStem, resetOutputNaming,
}

const queueModalContext = {
  openQueuePage: () => router.push('/queue'),
  finishedSessionJobCount, clearFinishedQueueJobs, noResultSeparationJobs, removingRunRecords,
  removeSeparationRunRecords, queueJobs, statusLabel, queueJobInputSummary,
  isNoResultSeparationJob, focusQueueJob, viewQueueJobLogs, dismissQueueJob,
  cancellingQueueJobId, requestCancelQueueJob,
}

const settingsDrawerContext = {
  parameterHelpPage, helpTopics, settingsCardRef, fieldGuideRef, guidedParameter, guideTarget,
  beginnerGuide, openParameterHelp, useTta, expandedInferenceSections, advancedPanelContext,
  effectiveFormat, formatLabel, settings, wavBitDepthOptions, flacBitDepthOptions,
  mp3BitRateOptions, bitRateOptions, m4aCodecOptions, showNormalizeField, normalize,
  locateGuidedParameter,
}

const ensembleModalContext = {
  ensembleModels, ensembleStem, ensembleType, ensembleTypeOptions,
  ensembleModelStems, ensembleStemOptionsByModel, ensembleWeights,
}

const noteModalContext = {
  noteEditorModel, noteDraft, handleModelNoteEditorUpdate,
  closeModelNoteEditor, saveModelNote,
}

const readyStageContext = {
  ensembleEnabled, hasQueueEntries, queueJobs, showQueueModal, modelPanelHasModels,
  modelSearch, modelCategoryFilter, modelCategoryOptions, modelListSortMode, modelSortOptions,
  modelListViewMode, ensembleModels, showEnsembleModal, pagedDownloadedModels, selectedModelName,
  handleSelectModel, openSeparateContextMenu, categoryLabel, modelMetaLine, modelUseCount, modelNote,
  filteredDownloadedModels, modelPage, modelPageSize, modelPageSizeOptions, modelDownloaded,
  modelPanelLoading, openModelsPage: () => router.push('/models'), canStart, startStatusText,
  outputSummaryPath, revealOutput: () => task.revealPath(currentTask.value?.outputs[0]?.path || normalizedOutputDir.value),
  submittingJob, start,
}

const taskStageContext = {
  currentTask, taskPanelState, statusLabel, showQueueModal, queueJobs, openCurrentLogs,
  currentBatchTitle, currentBatchLine, currentBatchProgress, currentBatchIsMulti,
  progressTitle, progressDetail, taskSubMessage, currentTaskDuration,
  currentTaskOutputPath, currentTaskOutputSummary, cancellingTaskId,
  handleCancelCurrentTask, beginNextSeparation, hasQueueEntries, currentBatchOutputSummary,
  playableOutputs, playableOutputGroups, getFileName, toggleOutputPlayback,
  playingOutputPath, getOutputPlayback, seekOutput, formatPlaybackTime,
  completedActionOptions, handleCompletedAction, goToResults, retryCurrentTask,
  revealOutput: () => task.revealPath(currentTask.value?.outputs[0]?.path || currentTask.value?.output || normalizedOutputDir.value),
}

</script>

<template>
  <div class="page separate-page mf-page" data-tone="blue">
    <MediaPageHeader
      :title="t('separate.title')"
      :subtitle="t('separate.subtitle')"
      :icon="MusicalNotesOutline"
      eyebrow="AUDIO WORKSPACE"
      tone="blue"
    >
      <div class="console-topbar__controls" data-guide="separation-modes">
        <n-tag size="small" :bordered="false">{{ t('separate.runModeModel') }}</n-tag>
        <n-button text class="console-topbar__manage" @click="router.push('/workflows')">
          {{ t('separate.manageWorkflowsInline') }}
        </n-button>
        <n-button text class="console-topbar__manage" @click="router.push('/models')">
          {{ t('separate.manageModelsInline') }}
        </n-button>
      </div>
    </MediaPageHeader>

    <WorkbenchSteps :steps="audioSteps" class="separate-step-strip" />

    <div class="console" :class="[`console--${taskPanelState}`, { 'console--busy': isConfigCompact }]">
      <aside class="console__rail">
        <WorkbenchCard class="rail-card--input" data-guide="input" index="01" :title="t('separate.input')" :subtitle="t('separate.candidateCount', { count: inputFiles.length })">
          <template #actions>
            <div class="rail-card__actions rail-card__actions--input">
              <label class="input-retention-toggle input-retention-toggle--head">
                <span>{{ t('separate.clearInputAfterSubmit') }}</span>
                <n-switch v-model:value="settings.clearInputAfterSubmit" size="small" :disabled="isRunModeLocked" />
              </label>
              <n-button v-if="inputFiles.length" text size="tiny" class="rail-card__clear" :disabled="isRunModeLocked" @click="task.clearInputFiles()">{{ t('separate.clearAll') }}</n-button>
            </div>
          </template>

            <div
              class="dropzone"
              :class="{ 'dropzone--dragging': isDragging, 'dropzone--filled': inputFiles.length, 'dropzone--clickable': !inputFiles.length && !isRunModeLocked }"
              @click="(!inputFiles.length && !isRunModeLocked) ? handlePickFiles() : undefined"
            >
              <TaskInputFiles
                v-if="inputFiles.length"
                :files="inputFiles"
                :busy="isRunModeLocked"
                reorderable
                :dragged-index="draggedInputIndex"
                :drag-handlers="{ down: onInputPointerDown, move: onInputPointerMove, up: finishInputPointerDrag, cancel: finishInputPointerDrag }"
                :kind-for="getFileKindLabel"
                :shorten-for="value => shortenMiddle(value, 60)"
                @add="handlePickFiles"
                @folder="handlePickFolder"
                @remove="task.removeInputFile"
                @clear="task.clearInputFiles"
              />
              <div v-else class="dropzone__empty">
                <div class="dropzone__glyph"><n-icon :component="CloudUploadOutline" /></div>
                <strong>{{ isDragging ? t('separate.dropHere') : t('separate.candidateEmpty') }}</strong>
              </div>
            </div>
        </WorkbenchCard>

        <WorkbenchCard class="rail-card--output" data-guide="output" index="02" :title="t('separate.output')" :subtitle="t('separate.outputSummaryHint')">
          <template #actions>
            <n-button text size="tiny" class="rail-card__more" :disabled="isRunModeLocked" @click="showSettingsDrawer = true">
              <template #icon><n-icon :component="SettingsOutline" /></template>
              {{ t('separate.configParams') }}
            </n-button>
          </template>

            <TaskOutputSettings
              v-model:model-value="outputDirectoryInput"
              v-model:layout="outputLayout"
              :error="outputDirectoryError"
              :preview="outputPreview"
              :disabled="isRunModeLocked"
              @browse="pickTemporaryOutputDir"
            />

            <label class="ofield" data-guide="output-format">
              <span class="ofield__label">{{ t('settings.defaultFormat') }}</span>
              <n-select v-model:value="settings.defaultFormat" size="small" :options="formatOptions" :disabled="isRunModeLocked" />
            </label>

            <div class="ofield naming-field" data-guide="output-naming">
              <span class="ofield__label">{{ t('separate.namingRule') }}</span>
              <button type="button" class="naming-summary" :disabled="isRunModeLocked" @click="showNamingModal = true">
                <span>{{ outputNamingSummary }}</span>
                <small>{{ t('separate.namingRuleAction') }}</small>
              </button>
            </div>

            <div class="ofield ofield--stems" data-guide="output-stems">
              <div class="ofield__heading">
                <span class="ofield__label">{{ t('separate.outputStems') }}</span>
                <div v-if="!ensembleEnabled && availableStemNames.length > 3" class="stem-selection-actions">
                  <button
                    type="button"
                    :disabled="isRunModeLocked || selectedOutputStemCount === availableStemNames.length"
                    @click="selectAllOutputStems"
                  >{{ t('separate.selectAllStems') }}</button>
                  <span aria-hidden="true">·</span>
                  <button
                    type="button"
                    :disabled="isRunModeLocked || selectedOutputStemCount === 0"
                    @click="clearOutputStems"
                  >{{ t('separate.clearStemSelection') }}</button>
                </div>
              </div>
              <div v-if="ensembleEnabled" class="ofield__static">{{ ensembleStem || '—' }}</div>
              <div v-else-if="availableStemNames.length" class="stem-chips">
                <n-checkbox-group v-model:value="checkedOutputStems" :disabled="isRunModeLocked">
                  <n-checkbox
                    v-for="stem in availableStemNames"
                    :key="stem"
                    :value="stem"
                    :label="stem"
                  />
                </n-checkbox-group>
              </div>
              <div v-else class="ofield__static ofield__static--muted">{{ t('separate.allStems') }}</div>
            </div>
        </WorkbenchCard>
      </aside>

      <main class="console__stage">
        <transition name="stage-swap" mode="out-in">
          <SeparateTaskStage
            v-if="currentTask && (taskPanelState === 'running' || isTerminalState)"
            :context="taskStageContext"
          />
          <SeparateReadyStage v-else :context="readyStageContext" />
        </transition>
      </main>
    </div>

    <n-dropdown
      placement="bottom-start"
      trigger="manual"
      :x="contextMenuX"
      :y="contextMenuY"
      :options="contextMenuOptions"
      :show="contextMenuVisible"
      @clickoutside="closeSeparateContextMenu"
      @select="handleSeparateContextMenuSelect"
    />

    <SeparateNoteModal v-model:show="showModelNoteEditor" :context="noteModalContext" />
    <SeparateEnsembleModal v-model:show="showEnsembleModal" :context="ensembleModalContext" />
    <SeparateSettingsDrawer v-model:show="showSettingsDrawer" :context="settingsDrawerContext" />
    <SeparateNamingModal v-model:show="showNamingModal" :context="namingModalContext" />
    <SeparateQueueModal v-model:show="showQueueModal" v-model:scope="queueScope" :context="queueModalContext" />
    <SeparateLogModal v-model:show="showLogModal" :current-task="currentTask" :current-task-file-name="currentTaskFileName" />
  </div>
</template>


<style scoped src="./SeparateView.css"></style>
