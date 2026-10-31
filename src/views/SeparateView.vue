<script setup lang="ts">
import { createLogger } from '@/utils/logger'

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
  compareModelNames, formatDateToken, formatLegacyTimeToken, formatPlaybackTime, formatProgressTime,
  formatTimeToken, normalizeFilenameFragment, padNumber, truncateUtf8,
} from './separate/format'
import { useTaskStore, type ModelListSortMode, type OutputLayout, type SeparationJob, type SeparationTask, type StemOutput } from '@/stores/task'
import { resolveJobStatus } from '@/features/tasks/lifecycle'
import { inferenceChunkSizeIssue, resolveInferenceChunkStep, resolveInferenceSampleStep } from '@/features/inference/sampleStep'
import type { WorkflowEntry } from '@/stores/workflow'
import { WORKFLOW_FORMAT_VERSION } from '@/workflows/formats'
import { useSettingsStore } from '@/stores/settings'
import { journalJob } from '@/utils/operationJournal'
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
import { findDropZone, toCssPoint } from '@/utils/dropZones.ts'
import { buildRenderPlan, type ModelInterface } from '@/litegraph/renderPlan.ts'
import { layoutFor } from '@/litegraph/layouts.ts'
import InferenceParameterLesson from '@/components/InferenceParameterLesson.vue'
import { parameterHelpTopics } from '@/features/inference/parameterHelp'
import ParameterFieldGuide from '@/components/ParameterFieldGuide.vue'
import { isTutorialFrame } from '@/features/onboarding/demoContext'
import { useBeginnerGuideStore } from '@/stores/beginnerGuide'
import { visibleParameterGuides } from '@/features/onboarding/parameters'

const studioLog = createLogger('ui.views.SeparateView')

const { t, locale } = useI18n()
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
const modelSearch = ref('')
const modelCategoryFilter = ref('')
const modelPage = ref(1)
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
const audioElements = new Map<string, HTMLAudioElement>()
const audioAccessOrder: string[] = []
const playingOutputPath = ref('')
const outputPlayback = ref<Record<string, { currentTime: number; duration: number }>>({})
const draggedInputIndex = ref<number | null>(null)
const inputDragPointerId = ref<number | null>(null)
const draggedStemIndex = ref<number | null>(null)
const stemDragPointerId = ref<number | null>(null)
const namingTemplateInputRef = ref<any>(null)
const STEM_ORDER_ROW_HEIGHT = 43
const WINDOWS_RESERVED_FILENAMES = new Set([
  'CON',
  'PRN',
  'AUX',
  'NUL',
  ...Array.from({ length: 9 }, (_, index) => `COM${index + 1}`),
  ...Array.from({ length: 9 }, (_, index) => `LPT${index + 1}`),
])
const MAX_FILENAME_PART_BYTES = 200
const PREVIEW_AUDIO_CACHE_LIMIT = 8
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
const ensembleTypeOptions = [
  'avg_wave', 'median_wave', 'min_wave', 'max_wave',
  'avg_fft', 'median_fft', 'min_fft', 'max_fft',
].map(value => ({ label: value, value }))
const modelSortOptions = computed(() => [
  { label: t('separate.modelSortUsage'), value: 'usage' },
  { label: t('separate.modelSortRecent'), value: 'recent' },
  { label: t('separate.modelSortFavorite'), value: 'favorite' },
  { label: t('separate.modelSortNameAsc'), value: 'name-asc' },
  { label: t('separate.modelSortNameDesc'), value: 'name-desc' },
] as Array<{ label: string; value: ModelListSortMode }>)
const saveAsFolder = computed({
  get: () => outputLayout.value === 'folders',
  set: (value: boolean) => {
    outputLayout.value = value ? 'folders' : 'flat'
  },
})
const effectiveOutputLayout = computed<OutputLayout>(() => outputLayout.value)
const compareModelName = (a: string, b: string) => compareModelNames(a, b, locale.value)

const listedDownloadedModels = computed(() => {
  return [...downloadedModels.value].sort((a, b) => {
    const prefA = modelPreferences.value[a.name] || {}
    const prefB = modelPreferences.value[b.name] || {}
    const favoriteDelta = Number(Boolean(prefB.favorite)) - Number(Boolean(prefA.favorite))
    const useDelta = Number(prefB.useCount || 0) - Number(prefA.useCount || 0)
    const recentDelta = Number(prefB.lastUsedAt || 0) - Number(prefA.lastUsedAt || 0)
    if (modelListSortMode.value === 'favorite') return favoriteDelta || recentDelta || compareModelName(a.name, b.name)
    if (modelListSortMode.value === 'recent') return recentDelta || useDelta || favoriteDelta || compareModelName(a.name, b.name)
    if (modelListSortMode.value === 'name-desc') return compareModelName(b.name, a.name)
    if (modelListSortMode.value === 'name-asc') return compareModelName(a.name, b.name)
    return useDelta || recentDelta || favoriteDelta || compareModelName(a.name, b.name)
  })
})
const selectedModelListItem = computed(() => listedDownloadedModels.value.find(item => item.name === selectedModelName.value) || null)
const modelDownloaded = computed(() => Boolean(selectedModelListItem.value))
const modelPanelHasModels = computed(() => downloadedModels.value.length > 0)
const modelPanelLoading = computed(() => !modelPanelHasModels.value && !modelsLoaded.value && (isLoading.value || app.envLoading) && !modelError.value)
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

const slotPanelInterface = computed<ModelInterface | null>(() => {
  const info = currentModelInfo.value
  const slots = (info as { slots?: ModelInterface['slots'] } | null)?.slots
  if (!info?.name || !slots || Object.keys(slots).length === 0) return null
  const task = (info as { slotTask?: string }).slotTask || 'separation'
  const interface_: ModelInterface = { modelId: info.name, task, slots: { ...slots } }
  // 派生约束（分块对齐）由后端给出，这里只把参数透传给滑块吸附
  const constraint = currentChunkConstraint.value
  const chunk = interface_.slots.chunk_size
  if (chunk && constraint) {
    interface_.slots.chunk_size = {
      ...chunk,
      align: { step: constraint.step, offset: constraint.offset, min: constraint.min },
    }
  }
  return interface_
})

const slotPanelPlan = computed(() => {
  const current = slotPanelInterface.value
  return current ? buildRenderPlan(current, layoutFor(current.task)) : null
})

const slotPanelValues = computed<Record<string, unknown>>(() => {
  const current = slotPanelInterface.value
  if (!current) return {}
  const values: Record<string, unknown> = {}
  for (const [slot, binding] of Object.entries(current.slots)) {
    const target = slotRefs[binding.bind]
    if (target && target.value !== undefined) values[slot] = target.value
  }
  return values
})

function updateSlotPanel(next: Record<string, unknown>) {
  const current = slotPanelInterface.value
  if (!current) return
  for (const [slot, value] of Object.entries(next)) {
    const binding = current.slots[slot]
    const target = binding ? slotRefs[binding.bind] : undefined
    if (target) target.value = value
  }
}

const isApolloModel = computed(() => currentModelType.value === 'apollo')
const showStandardizeField = computed(() => Boolean(currentModelInfo.value) && !isVrModel.value)
const showNormalizeField = computed(() => Boolean(currentModelInfo.value))
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

function collectCurrentModelInferenceDefaults(): ModelDefaultInferenceParams {
  const defaults = currentModelDefaults.value
  const hasField = (key: keyof ModelDefaultInferenceParams) => Object.prototype.hasOwnProperty.call(defaults, key)
  const next: ModelDefaultInferenceParams = {}

  if (hasField('batch_size') && typeof batch_size.value === 'number' && Number.isFinite(batch_size.value)) {
    next.batch_size = batch_size.value
  }
  if (hasField('overlap_size') && typeof overlap_size.value === 'number' && Number.isFinite(overlap_size.value)) {
    next.overlap_size = overlap_size.value
  }
  if (hasField('num_overlap') && !isApolloModel.value && typeof num_overlap.value === 'number' && Number.isFinite(num_overlap.value)) {
    next.num_overlap = num_overlap.value
  }
  if (hasField('chunk_size') && typeof chunk_size.value === 'number' && Number.isFinite(chunk_size.value)) {
    next.chunk_size = chunk_size.value
  }
  if (hasField('window_size') && typeof window_size.value === 'number' && Number.isFinite(window_size.value)) {
    next.window_size = window_size.value
  }
  if (hasField('aggression') && typeof aggression.value === 'number' && Number.isFinite(aggression.value)) {
    next.aggression = aggression.value
  }
  if (hasField('enable_post_process')) next.enable_post_process = enable_post_process.value
  if (hasField('post_process_threshold') && typeof post_process_threshold.value === 'number' && Number.isFinite(post_process_threshold.value)) {
    next.post_process_threshold = post_process_threshold.value
  }
  if (hasField('high_end_process')) next.high_end_process = high_end_process.value
  if (showStandardizeField.value) next.standardize = standardize.value
  if (showNormalizeField.value) next.normalize = normalize.value

  return next
}

function applyCurrentModelInferenceDefaults(info: ModelEntry) {
  const latest = model.models.find(item => item.name === info.name) || info
  task.applySelectedModelDefaults(
    model.getModelBaseInferenceDefaults(info.name) || latest.defaultInferenceParams,
    latest.modelType,
    task.getSavedModelState(info.name),
    model.getModelInferenceOverrides(info.name),
    { force: true },
  )
}

async function saveCurrentModelInferenceDefaults() {
  const info = currentModelInfo.value
  if (!info) return
  task.normalizeInferenceInputsBeforeSubmit()
  const overrides = collectCurrentModelInferenceDefaults()
  if (!Object.keys(overrides).length) return
  if (currentChunkIssue.value) {
    message.warning(t('inference.invalidChunkSize', currentChunkIssue.value))
    return
  }
  try {
    await model.setModelInferenceOverrides(info.name, overrides)
    applyCurrentModelInferenceDefaults(info)
    message.success(t('models.inferenceDefaultsSaved'))
  } catch (error) {
    studioLog.error('Failed to persist model inference defaults', error)
    message.error(t('models.inferenceDefaultsSaveFailed'))
  }
}

async function resetCurrentModelInferenceDefaults() {
  const info = currentModelInfo.value
  if (!info) return
  try {
    await model.resetModelInferenceOverrides(info.name)
    applyCurrentModelInferenceDefaults(info)
    message.success(t('models.inferenceDefaultsReset'))
  } catch (error) {
    studioLog.error('Failed to restore model inference defaults', error)
    message.error(t('models.inferenceDefaultsResetFailed'))
  }
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
const outputStemSelectionCleared = ref(false)
const selectedStemSummary = computed(() => {
  if (outputStemSelectionCleared.value) return t('separate.noStemsSelected')
  if (!selectedStems.value.length) return t('separate.allStems')
  return selectedStems.value.join(', ')
})
const checkedOutputStems = computed<string[]>({
  get() {
    if (outputStemSelectionCleared.value) return []
    if (!selectedStems.value.length) return [...availableStemNames.value]
    return selectedStems.value
  },
  set(value) {
    const allowed = new Set(availableStemNames.value)
    const next = value.filter(stem => allowed.has(stem))
    const cleared = next.length === 0
    outputStemSelectionCleared.value = cleared
    persistedOutputStemSelectionCleared.value = cleared
    selectedStems.value = next.length === availableStemNames.value.length ? [] : next
  },
})
const selectedOutputStemCount = computed(() => checkedOutputStems.value.length)
const hasOutputStemSelection = computed(() => (
  !availableStemNames.value.length || selectedOutputStemCount.value > 0
))
function selectAllOutputStems() {
  outputStemSelectionCleared.value = false
  persistedOutputStemSelectionCleared.value = false
  selectedStems.value = []
}
function clearOutputStems() {
  outputStemSelectionCleared.value = true
  persistedOutputStemSelectionCleared.value = true
  selectedStems.value = []
}
const selectedStemDetail = computed(() => {
  if (selectedStems.value.length > 6) {
    return t('separate.selectedStemCount', {
      count: selectedOutputStemCount.value,
      total: availableStemNames.value.length,
    })
  }
  return selectedStemSummary.value
})

const namingTokens = computed(() => [
  { value: '%index%', label: t('separate.namingTokenIndex') },
  { value: '%input_number%', label: t('separate.namingTokenInputNumber') },
  { value: '%filename%', label: t('separate.namingTokenFilename') },
  { value: '%stem%', label: t('separate.namingTokenStem') },
  { value: '%model%', label: t('separate.namingTokenModel') },
  { value: '%yyyyMMdd%', label: t('separate.namingTokenDate') },
  { value: '%hhmmss%', label: t('separate.namingTokenTime') },
  { value: '%ddmmss%', label: t('separate.namingTokenLegacyTime') },
])

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
const modelCategoryOptions = computed(() => [
  { label: t('common.all'), value: '' },
  ...buildModelCategoryOptionsFromModels(listedDownloadedModels.value, locale.value),
])
const filteredDownloadedModels = computed(() => {
  const query = modelSearch.value.trim().toLowerCase()
  const selectedCategory = modelCategoryFilter.value.trim().toLowerCase()
  return listedDownloadedModels.value.filter((item) => {
    // Notes are searchable here too: picking a model to run is exactly when "the one that was
    // good for vocals" is how someone remembers it.
    const matchesQuery = matchesModelQuery(item, query, modelNote(item.name))
    const matchesCategory = !selectedCategory
      || item.category.toLowerCase() === selectedCategory
      || item.primaryCategory.toLowerCase() === selectedCategory
      || item.secondaryCategory.toLowerCase() === selectedCategory
    return matchesQuery && matchesCategory
  })
})
const pagedDownloadedModels = computed(() => {
  const start = (modelPage.value - 1) * modelPageSize.value
  return filteredDownloadedModels.value.slice(start, start + modelPageSize.value)
})
const modelPageSizeOptions = [...SEPARATE_MODEL_PAGE_SIZES]

const outputDirectoryInput = computed({
  get: () => temporaryOutputDir.value || normalizedOutputDir.value,
  set: (value: string | null) => { temporaryOutputDir.value = value || '' },
})
const outputDirectoryPlatform = computed(() => {
  const reported = String(app.envInfo?.platform || '').trim().toLowerCase()
  if (reported === 'win32' || reported === 'windows') return 'windows' as const
  if (reported === 'darwin' || reported === 'macos' || reported === 'mac') return 'macos' as const
  if (reported === 'linux') return 'linux' as const
  const browserPlatform = String(typeof navigator !== 'undefined' ? navigator.platform : '').toLowerCase()
  if (browserPlatform.includes('win')) return 'windows' as const
  if (browserPlatform.includes('mac')) return 'macos' as const
  return 'linux' as const
})
const outputDirectoryError = computed(() => validateOutputDirectory(normalizedOutputDir.value, outputDirectoryPlatform.value))
const outputPreview = computed(() => {
  const windows = outputDirectoryPlatform.value === 'windows'
  const base = normalizedOutputDir.value.replace(windows ? /[\\/]$/ : /[/]$/, '')
  const separator = windows && base.includes('\\') ? '\\' : '/'
  const previewName = outputNamingPreviewFiles.value[0] || t('separate.outputFilePreview')
  if (effectiveOutputLayout.value === 'flat') {
    return `${base}${separator}${previewName}`
  }
  return `${base}${separator}${t('separate.resultFolderPreview')}${separator}${previewName}`
})
const effectiveFormat = computed(() => String(settings.defaultFormat || 'wav').trim().toLowerCase() || 'wav')
const formatLabel = computed(() => effectiveFormat.value.toUpperCase())
const outputNamingPreviewFiles = computed(() => {
  const stems = orderedOutputStems.value.length ? orderedOutputStems.value : ['vocals']
  return stems.slice(0, 8).map((stem, index) => formatOutputNamingPreviewFile(stem, index))
})
const outputNamingPreviewParts = computed(() => {
  const stems = orderedOutputStems.value.length ? orderedOutputStems.value : ['vocals']
  return stems.slice(0, 8).map((stem, index) => ({
    key: `${stem}-${index}`,
    parts: formatOutputNamingPreviewParts(stem, index),
  }))
})
const outputSummaryPath = computed(() => outputPreview.value)
const canStart = computed(() => (
  !submittingJob.value
  && !outputDirectoryError.value
  && (ensembleEnabled.value
    ? ensembleReady.value
    : (modelDownloaded.value && inputFiles.value.length > 0 && hasOutputStemSelection.value))
))
function isSeparationJob(job: SeparationJob) {
  return !job.primary.runConfig?.workbench
}
function isNoResultSeparationJob(job: SeparationJob) {
  return isSeparationJob(job) && job.tasks.every(item => ['done', 'failed', 'cancelled'].includes(item.status))
    && !job.tasks.some(item => item.status === 'done' && !item.resultHidden && (item.outputs.length || item.files.length))
}
const separationJobs = computed(() => task.allJobs.filter(isSeparationJob))
const separationSessionJobs = computed(() => task.sessionJobs.filter(isSeparationJob))

function journalAudioJob(job: SeparationJob) {
  const statusMap: Record<string, 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'> = {
    queued: 'queued',
    running: 'running',
    done: 'completed',
    failed: 'failed',
    cancelled: 'cancelled',
  }
  void journalJob({
    jobId: job.id,
    command: 'separation',
    source: 'manual',
    status: statusMap[job.status] || 'running',
    fraction: Math.max(0, Math.min(1, Number(job.progress || 0) / 100)),
    message: `${job.model} · ${job.inputCount} ${t('separate.queueFileUnit')}`,
    createdAt: job.createdAt / 1000,
    finishedAt: job.finishedAt ? job.finishedAt / 1000 : null,
    payload: { operation: 'separation', model: job.model, inputs: job.tasks.map(item => item.input) },
    result: { outputs: job.tasks.flatMap(item => item.outputs) },
    cancelTaskId: job.primary?.id,
  })
}

watch(separationSessionJobs, jobs => {
  for (const job of jobs) journalAudioJob(job)
}, { deep: true, immediate: true })
const noResultSeparationJobs = computed(() => separationJobs.value.filter(isNoResultSeparationJob)
  .sort((left, right) => right.updatedAt - left.updatedAt))
const newestRunningJob = computed(() => {
  return [...separationJobs.value]
    .filter(job => job.tasks.some(item => !['done', 'failed', 'cancelled'].includes(item.status)))
    .sort((a, b) => b.createdAt - a.createdAt)[0] || null
})
const backgroundRunningJobs = computed(() => separationSessionJobs.value
  .filter(job => !['queued', 'done', 'failed', 'cancelled'].includes(job.status))
  .sort((left, right) => right.updatedAt - left.updatedAt))
const backgroundQueuedJobs = computed(() => separationSessionJobs.value
  .filter(job => job.status === 'queued')
  .sort((left, right) => left.createdAt - right.createdAt))
const completedSessionJobs = computed(() => separationSessionJobs.value
  .filter(job => job.status === 'done')
  .sort((left, right) => right.updatedAt - left.updatedAt))
const recentFailedJobs = computed(() => separationSessionJobs.value
  .filter(job => job.status === 'failed')
  .sort((left, right) => right.updatedAt - left.updatedAt))
const cancelledSessionJobs = computed(() => separationSessionJobs.value
  .filter(job => job.status === 'cancelled')
  .sort((left, right) => right.updatedAt - left.updatedAt))
const queueJobs = computed(() => queueScope.value === 'no-results' ? noResultSeparationJobs.value : [
  ...backgroundRunningJobs.value,
  ...backgroundQueuedJobs.value,
  ...recentFailedJobs.value,
  ...completedSessionJobs.value,
  ...cancelledSessionJobs.value,
])
const finishedSessionJobCount = computed(() => recentFailedJobs.value.length + completedSessionJobs.value.length + cancelledSessionJobs.value.length)
const hasQueueEntries = computed(() => queueJobs.value.length > 0 || noResultSeparationJobs.value.length > 0)
const focusedJob = computed(() => separationJobs.value.find(job => job.id === focusedSeparationJobId.value) || null)
const currentJob = computed(() => composingNewJob.value ? null : (focusedJob.value || newestRunningJob.value))
const focusedBatchTasks = computed(() => currentJob.value?.tasks || [])
const activeFocusedBatchTask = computed(() => {
  return [...focusedBatchTasks.value]
    .filter(item => !['done', 'failed', 'cancelled'].includes(item.status))
    .sort((a, b) => {
      const aQueued = a.status === 'queued' ? 1 : 0
      const bQueued = b.status === 'queued' ? 1 : 0
      if (aQueued !== bQueued) return aQueued - bQueued
      return a.createdAt - b.createdAt
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
  return ['done', 'failed', 'cancelled'].includes(status)
    ? status as 'done' | 'failed' | 'cancelled'
    : 'running'
})
const isConfigCompact = computed(() => taskPanelState.value !== 'ready')
const isTerminalState = computed(() => ['done', 'failed', 'cancelled'].includes(taskPanelState.value))
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
    if (item.status === 'done' || item.status === 'failed' || item.status === 'cancelled') return sum + 100
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


function getOutputPlayback(path: string) {
  return outputPlayback.value[path] || { currentTime: 0, duration: 0 }
}

function setOutputPlayback(path: string, patch: Partial<{ currentTime: number; duration: number }>) {
  const previous = getOutputPlayback(path)
  outputPlayback.value = {
    ...outputPlayback.value,
    [path]: { ...previous, ...patch },
  }
}

function touchPreviewAudio(path: string) {
  const index = audioAccessOrder.indexOf(path)
  if (index >= 0) audioAccessOrder.splice(index, 1)
  audioAccessOrder.push(path)
}

function releasePreviewAudio(path: string) {
  const audio = audioElements.get(path)
  if (audio) {
    audioElements.delete(path)
    audio.pause()
    audio.removeAttribute('src')
    audio.load()
  }
  const accessIndex = audioAccessOrder.indexOf(path)
  if (accessIndex >= 0) audioAccessOrder.splice(accessIndex, 1)
  if (playingOutputPath.value === path) playingOutputPath.value = ''
  if (outputPlayback.value[path]) {
    const { [path]: _, ...rest } = outputPlayback.value
    outputPlayback.value = rest
  }
}

function trimPreviewAudioCache(protectedPath = '') {
  while (audioElements.size > PREVIEW_AUDIO_CACHE_LIMIT) {
    const candidate = audioAccessOrder.find(path => path !== protectedPath && path !== playingOutputPath.value)
      || audioAccessOrder[0]
    if (!candidate) return
    releasePreviewAudio(candidate)
  }
}

function getAudio(path: string) {
  const cached = audioElements.get(path)
  if (cached) {
    touchPreviewAudio(path)
    return cached
  }

  const audio = new Audio()
  audio.preload = 'metadata'
  audio.src = convertFileSrc(path)
  audio.addEventListener('loadedmetadata', () => {
    if (audioElements.get(path) !== audio) return
    setOutputPlayback(path, { duration: audio.duration || 0 })
  })
  audio.addEventListener('timeupdate', () => {
    if (audioElements.get(path) !== audio) return
    setOutputPlayback(path, { currentTime: audio.currentTime || 0, duration: audio.duration || 0 })
  })
  audio.addEventListener('ended', () => {
    if (audioElements.get(path) !== audio) return
    if (playingOutputPath.value === path) playingOutputPath.value = ''
    setOutputPlayback(path, { currentTime: 0, duration: audio.duration || 0 })
  })
  audio.addEventListener('error', () => {
    if (audioElements.get(path) !== audio) return
    if (playingOutputPath.value === path) playingOutputPath.value = ''
  })
  audioElements.set(path, audio)
  touchPreviewAudio(path)
  trimPreviewAudioCache(path)
  audio.load()
  return audio
}

function syncOutputPreviewAudio(outputs: StemOutput[]) {
  const activePaths = new Set(outputs.map(output => output.path).filter(Boolean))
  for (const path of [...audioElements.keys()]) {
    if (!activePaths.has(path)) releasePreviewAudio(path)
  }
  outputs.slice(0, PREVIEW_AUDIO_CACHE_LIMIT).forEach((output) => {
    if (output.path) getAudio(output.path)
  })
}

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

function getFileName(path: string) {
  return path.split(/[/\\]/).filter(Boolean).pop() || path
}

function validateOutputDirectory(value: string, platform: 'windows' | 'macos' | 'linux') {
  const path = String(value || '').trim()
  if (!path) return ''
  if (/[\u0000-\u001f]/u.test(path)) {
    return t('separate.outputDirectoryInvalidCharacters')
  }

  if (platform !== 'windows') {
    if (path.includes('\0')) return t('separate.outputDirectoryInvalidCharacters')
    return path.startsWith('/') ? '' : t('separate.outputDirectoryAbsoluteRequired')
  }

  if (/[<>"|?*]/u.test(path)) return t('separate.outputDirectoryInvalidCharacters')
  const normalized = path.replace(/\//g, '\\')
  if (/^[A-Za-z]:[^\\]/u.test(normalized)) return t('separate.outputDirectoryIncomplete')
  const withoutRoot = normalized
    .replace(/^[A-Za-z]:\\/u, '')
    .replace(/^\\\\[^\\]+\\[^\\]+\\?/u, '')
    .replace(/^\\+/u, '')
  const segments = withoutRoot.split(/\\+/u).filter(Boolean)
  if (segments.some(segment => segment.includes(':'))) return t('separate.outputDirectoryInvalidCharacters')
  if (segments.some(segment => /[ .]$/u.test(segment))) return t('separate.outputDirectoryTrailingCharacter')
  if (segments.some(segment => WINDOWS_RESERVED_FILENAMES.has(segment.split('.', 1)[0].toUpperCase()))) {
    return t('separate.outputDirectoryReservedName')
  }
  if (/^[A-Za-z]:$/u.test(path) || /^\\\\[^\\]+$/u.test(path)) return t('separate.outputDirectoryIncomplete')
  const isAbsolute = /^[A-Za-z]:\\/u.test(normalized) || /^\\\\[^\\]+\\[^\\]+(?:\\|$)/u.test(normalized)
  return isAbsolute ? '' : t('separate.outputDirectoryAbsoluteRequired')
}

function stripFileExtension(path: string) {
  return getFileName(path).replace(/\.[^/.\\]+$/, '')
}


function safeFilenamePart(value: string) {
  let text = normalizeFilenameFragment(value).replace(/^[ ._]+|[ ._]+$/g, '')
  text = truncateUtf8(text, MAX_FILENAME_PART_BYTES) || 'output'
  const reservedKey = text.split('.', 1)[0].toUpperCase()
  if (WINDOWS_RESERVED_FILENAMES.has(reservedKey) || text === '.' || text === '..') text = `${text}_`
  return text
}

function formatOutputNamingPreviewFile(stem: string, stemIndex: number) {
  return formatOutputNamingPreviewParts(stem, stemIndex).map(part => part.text).join('')
}

function formatOutputNamingPreviewParts(stem: string, stemIndex: number) {
  const inputPath = inputFiles.value[0] || t('separate.resultFolderPreview')
  const modelName = ensembleEnabled.value ? 'Ensemble' : selectedModelName.value || t('separate.model')
  const now = new Date()
  const values: Record<string, string> = {
    '%index%': padNumber(stemIndex + 1),
    '%input_number%': '01',
    '%filename%': stripFileExtension(inputPath),
    '%stem%': stem,
    '%model%': modelName,
    '%yyyyMMdd%': formatDateToken(now),
    '%hhmmss%': formatTimeToken(now),
    '%ddmmss%': formatLegacyTimeToken(now),
  }
  const template = outputNamingConfig.value.enabled ? outputNamingConfig.value.template : '%filename%_%stem%'
  const parts: Array<{ text: string; token: string }> = []
  const tokenPattern = /%(?:index|input_number|filename|stem|model|yyyyMMdd|hhmmss|ddmmss)%/g
  let cursor = 0
  for (const match of template.matchAll(tokenPattern)) {
    const token = match[0]
    const index = match.index ?? 0
    if (index > cursor) parts.push({ text: template.slice(cursor, index), token: 'literal' })
    parts.push({ text: values[token] || token, token })
    cursor = index + token.length
  }
  if (cursor < template.length) parts.push({ text: template.slice(cursor), token: 'literal' })
  const namedParts = parts.length
    ? parts.map(part => ({ ...part, text: normalizeFilenameFragment(part.text) }))
    : [{ text: 'output', token: 'literal' }]
  const firstPart = namedParts[0]
  const lastPart = namedParts[namedParts.length - 1]
  firstPart.text = firstPart.text.replace(/^[ ._]+/g, '')
  lastPart.text = lastPart.text.replace(/[ ._]+$/g, '')
  const fullName = namedParts.map(part => part.text).join('')
  if (!fullName) namedParts.splice(0, namedParts.length, { text: 'output', token: 'literal' })
  else {
    const safeName = safeFilenamePart(fullName)
    if (safeName.startsWith(fullName)) lastPart.text += safeName.slice(fullName.length)
  }
  return [
    ...namedParts.filter(part => part.text),
    { text: `.${effectiveFormat.value || 'wav'}`, token: 'extension' },
  ]
}

function moveItem<T>(items: T[], fromIndex: number, toIndex: number) {
  if (fromIndex === toIndex || fromIndex < 0 || toIndex < 0 || fromIndex >= items.length || toIndex >= items.length) return items
  const next = [...items]
  const [item] = next.splice(fromIndex, 1)
  next.splice(toIndex, 0, item)
  return next
}

function moveStem(fromIndex: number, toIndex: number) {
  customStemOrder.value = moveItem(orderedOutputStems.value, fromIndex, toIndex)
}

function onInputPointerDown(event: PointerEvent, index: number) {
  if (isRunModeLocked.value) return
  const target = event.target as HTMLElement | null
  if (target?.closest('.file-chip__remove')) return
  draggedInputIndex.value = index
  inputDragPointerId.value = event.pointerId
  ;(event.currentTarget as HTMLElement | null)?.setPointerCapture?.(event.pointerId)
}

function onInputPointerMove(event: PointerEvent) {
  if (inputDragPointerId.value !== event.pointerId || draggedInputIndex.value === null) return
  const list = (event.currentTarget as HTMLElement).parentElement
  const rows = Array.from(list?.querySelectorAll<HTMLElement>('.file-chip') || [])
  if (!rows.length) return
  const targetIndex = rows.findIndex(row => event.clientY < row.getBoundingClientRect().top + row.getBoundingClientRect().height / 2)
  const nextIndex = targetIndex === -1 ? rows.length - 1 : targetIndex
  if (nextIndex === draggedInputIndex.value) return
  task.moveInputFile(draggedInputIndex.value, nextIndex)
  draggedInputIndex.value = nextIndex
}

function finishInputPointerDrag(event?: PointerEvent) {
  if (event && inputDragPointerId.value === event.pointerId) {
    ;(event.currentTarget as HTMLElement | null)?.releasePointerCapture?.(event.pointerId)
  }
  inputDragPointerId.value = null
  draggedInputIndex.value = null
}

function onStemPointerDown(event: PointerEvent, index: number) {
  const target = event.target as HTMLElement | null
  if (target?.closest('.stem-order-row__actions')) return
  draggedStemIndex.value = index
  stemDragPointerId.value = event.pointerId
  ;(event.currentTarget as HTMLElement | null)?.setPointerCapture?.(event.pointerId)
}

function onStemPointerMove(event: PointerEvent) {
  if (stemDragPointerId.value !== event.pointerId || draggedStemIndex.value === null) return
  const list = (event.currentTarget as HTMLElement).parentElement
  const listTop = list?.getBoundingClientRect().top ?? (event.currentTarget as HTMLElement).getBoundingClientRect().top
  const targetIndex = Math.max(0, Math.min(
    orderedOutputStems.value.length - 1,
    Math.trunc((event.clientY - listTop) / STEM_ORDER_ROW_HEIGHT),
  ))
  if (targetIndex === draggedStemIndex.value) return
  moveStem(draggedStemIndex.value, targetIndex)
  draggedStemIndex.value = targetIndex
}

function finishStemPointerDrag(event?: PointerEvent) {
  if (event && stemDragPointerId.value === event.pointerId) {
    ;(event.currentTarget as HTMLElement | null)?.releasePointerCapture?.(event.pointerId)
  }
  stemDragPointerId.value = null
  draggedStemIndex.value = null
}

async function insertNamingToken(token: string) {
  const input = namingTemplateInputRef.value?.inputElRef as HTMLInputElement | undefined
  const current = outputNamingTemplate.value
  const withSeparators = (start: number, end: number) => {
    const before = current.slice(0, start)
    const after = current.slice(end)
    const prefix = before && !before.endsWith('_') ? '_' : ''
    const suffix = after && !after.startsWith('_') ? '_' : ''
    return {
      text: `${before}${prefix}${token}${suffix}${after}`,
      cursor: before.length + prefix.length + token.length,
    }
  }
  if (!input) {
    const next = withSeparators(current.length, current.length)
    outputNamingTemplate.value = next.text
    return
  }
  const start = input.selectionStart ?? current.length
  const end = input.selectionEnd ?? start
  const next = withSeparators(start, end)
  outputNamingTemplate.value = next.text
  await nextTick()
  input.focus()
  input.setSelectionRange(next.cursor, next.cursor)
}

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

function shortenMiddle(text: string, maxLength = 48) {
  if (text.length <= maxLength) return text
  const keep = Math.max(8, Math.floor((maxLength - 3) / 2))
  return `${text.slice(0, keep)}...${text.slice(-keep)}`
}

function statusLabel(status: string) {
  const labels: Record<string, string> = {
    queued: t('tasks.statusQueued'),
    preparing: t('tasks.statusPreparing'),
    validating_input: t('tasks.statusValidatingInput'),
    downloading_model: t('tasks.statusCheckingModel'),
    ensuring_model: t('tasks.statusCheckingModel'),
    loading_model: t('tasks.statusLoadingModel'),
    separating: t('tasks.statusSeparating'),
    writing_output: t('tasks.statusWritingOutput'),
    done: t('tasks.statusDone'),
    failed: t('tasks.statusFailed'),
    cancelled: t('tasks.statusCancelled'),
  }
  return labels[status] || status
}

function statusType(status: string) {
  switch (status) {
    case 'done': return 'success' as const
    case 'failed': return 'error' as const
    case 'cancelled': return 'warning' as const
    default: return 'info' as const
  }
}

function normalizeProgressMessage(value?: string) {
  const message = (value || '').trim()
  const key = message.toLowerCase()
  const mapped: Record<string, string> = {
    'task started': t('tasks.progressPreparingTask'),
    'validating input': t('tasks.progressValidatingInput'),
    'checking model files': t('tasks.progressCheckingModel'),
    'loading model': t('tasks.progressLoadingModel'),
    'separating audio': t('tasks.progressSeparatingHint'),
    'processing audio chunks': t('tasks.progressProcessingChunks'),
    'processing vr batches': t('tasks.progressProcessingVrBatches'),
    'collecting outputs': t('tasks.progressCollectingOutputs'),
  }
  return mapped[key] || message
}

function progressStatus(status: string) {
  switch (status) {
    case 'done': return 'success' as const
    case 'failed': return 'error' as const
    case 'cancelled': return 'warning' as const
    default: return 'info' as const
  }
}

function progressTitle(item: SeparationTask) {
  if (item.status === 'separating') return t('tasks.progressTitleSeparating')
  return statusLabel(item.status)
}


function progressDetail(item: SeparationTask) {
  if (
    item.status === 'separating'
    && typeof item.progressCurrent === 'number'
    && typeof item.progressTotal === 'number'
    && item.progressTotal > 0
  ) {
    return `${formatProgressTime(item.progressCurrent)} / ${formatProgressTime(item.progressTotal)}`
  }
  return ''
}

function taskSubMessage(item: SeparationTask) {
  if (item.error) return item.error
  return normalizeProgressMessage(item.progressDetail || item.message)
}

function taskDuration(item: SeparationTask, now: number) {
  const terminal = ['done', 'failed', 'cancelled'].includes(item.status)
  const startedAt = item.startedAt ?? item.createdAt
  const durationMs = terminal
    ? (typeof item.durationMs === 'number' && Number.isFinite(item.durationMs) && item.durationMs >= 0
        ? item.durationMs : (item.finishedAt ?? item.updatedAt) - startedAt)
    : item.status === 'queued' ? 0 : now - startedAt
  const seconds = Math.max(0, Math.round(durationMs / 1000))
  if (seconds < 60) return `${seconds}s`
  const minutes = Math.floor(seconds / 60)
  const rest = seconds % 60
  return `${minutes}m ${rest}s`
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

async function start() {
  if (submittingJob.value) return
  if (outputDirectoryError.value) {
    message.warning(outputDirectoryError.value)
    return
  }
  if (ensembleEnabled.value && !ensembleReady.value) {
    message.warning(t('separate.ensembleNotReady'))
    return
  }
  if (!inputFiles.value.length) {
    message.warning(t('separate.startHintNoInput'))
    return
  }
  if (!ensembleEnabled.value && !modelDownloaded.value) {
    message.warning(t('separate.startHintModelMissing'))
    return
  }
  if (!ensembleEnabled.value && currentChunkIssue.value) {
    message.warning(t('inference.invalidChunkSize', currentChunkIssue.value))
    return
  }
  if (!ensembleEnabled.value && !hasOutputStemSelection.value) {
    message.warning(t('separate.startHintNoOutputStems'))
    return
  }
  submittingJob.value = true
  try {
    const result = ensembleEnabled.value
      ? await task.startWorkflowInference(buildEnsembleWorkflow(), { outputDir: normalizedOutputDir.value, outputLayout: effectiveOutputLayout.value, outputNaming: outputNamingConfig.value })
      : await task.startSeparation({ outputDir: normalizedOutputDir.value, outputLayout: effectiveOutputLayout.value, outputNaming: outputNamingConfig.value })
    focusedSeparationJobId.value = result?.jobId || newestRunningJob.value?.id || focusedSeparationJobId.value
    composingNewJob.value = false
    if (ensembleEnabled.value && result) {
      ensembleModels.value.forEach((name) => model.recordModelUse(name))
    }
    if (settings.clearInputAfterSubmit) task.clearInputFiles()
    if (result && result.failed > 0) {
      message.warning(t('separate.batchPartial', { succeeded: result.succeeded, failed: result.failed }))
    } else {
      message.success(t('separate.batchStarted', { count: result?.succeeded ?? 1 }))
    }
  } catch (err) {
    message.error(err instanceof Error ? err.message : t('toast.taskFailed'))
  } finally {
    submittingJob.value = false
  }
}

function beginNextSeparation() {
  stopAllPreviewAudio()
  showLogModal.value = false
  focusedSeparationJobId.value = null
  composingNewJob.value = true
}

function focusQueueJob(target: SeparationJob) {
  if (!isSeparationJob(target)) return false
  composingNewJob.value = false
  focusedSeparationJobId.value = target.id
  showQueueModal.value = false
  return true
}

function viewQueueJobLogs(target: SeparationJob) {
  if (!focusQueueJob(target)) return
  void nextTick(() => { showLogModal.value = true })
}

function dismissQueueJob(target: SeparationJob) {
  if (!isSeparationJob(target)) return
  task.removeSessionJob(target.id)
  if (focusedSeparationJobId.value === target.id) focusedSeparationJobId.value = null
}

function clearFinishedQueueJobs() {
  task.clearFinishedSessionJobs(separationSessionJobs.value.map(job => job.id))
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

async function cancelQueueJob(job: SeparationJob) {
  if (!isSeparationJob(job) || cancellingQueueJobId.value) return false
  const targets = job.tasks.filter(item => !['done', 'failed', 'cancelled'].includes(item.status))
  if (!targets.length) return false
  cancellingQueueJobId.value = job.id
  try {
    let cancelled = 0
    for (const item of targets) {
      // Keep cancellation serialized because the Rust worker manager accepts
      // one cancellation transition at a time.
      // eslint-disable-next-line no-await-in-loop
      if (await task.cancelTask(item.id)) cancelled += 1
    }
    // A batch shares one Worker process. Cancelling its primary task can stop
    // the whole Worker, so later child cancellations may legitimately return
    // false even though the batch cancellation succeeded.
    return cancelled > 0 || targets.every(item => ['done', 'failed', 'cancelled'].includes(item.status))
  } finally {
    cancellingQueueJobId.value = null
  }
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
  const targets = currentBatchTasks.value.filter(item => !['done', 'failed', 'cancelled'].includes(item.status))
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

            <div class="picker-buttons">
              <button type="button" class="picker-btn" :disabled="isRunModeLocked" @click="handlePickFiles">
                <n-icon :component="MusicalNotesOutline" />
                <span>{{ t('separate.chooseFiles') }}</span>
              </button>
              <button type="button" class="picker-btn" :disabled="isRunModeLocked" @click="handlePickFolder">
                <n-icon :component="FolderOutline" />
                <span>{{ t('separate.chooseFolder') }}</span>
              </button>
            </div>

            <div
              class="dropzone"
              :class="{ 'dropzone--dragging': isDragging, 'dropzone--filled': inputFiles.length, 'dropzone--clickable': !inputFiles.length && !isRunModeLocked }"
              @click="(!inputFiles.length && !isRunModeLocked) ? handlePickFiles() : undefined"
            >
              <div v-if="inputFiles.length" class="file-list">
                <div
                  v-for="(path, index) in inputFiles"
                  :key="path"
                  class="file-chip"
                  :class="{ 'file-chip--dragging': draggedInputIndex === index }"
                  @pointerdown="onInputPointerDown($event, index)"
                  @pointermove="onInputPointerMove"
                  @pointerup="finishInputPointerDrag"
                  @pointercancel="finishInputPointerDrag"
                >
                  <span class="file-chip__handle" :title="t('separate.dragToReorder')"><n-icon :component="ReorderFourOutline" /></span>
                  <span class="file-chip__glyph"><n-icon :component="MusicalNotesOutline" /></span>
                  <div class="file-chip__main">
                    <strong :title="getFileName(path)">{{ getFileName(path) }}</strong>
                    <div class="file-chip__sub">
                      <span class="file-chip__kind">{{ getFileKindLabel(path) }}</span>
                      <code :title="path">{{ shortenMiddle(path, 60) }}</code>
                    </div>
                  </div>
                  <n-button quaternary circle size="tiny" class="file-chip__remove" :title="t('separate.remove')" :disabled="isRunModeLocked" @click.stop="task.removeInputFile(path)">
                    <template #icon><n-icon :component="CloseOutline" /></template>
                  </n-button>
                </div>
              </div>
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

            <label class="ofield" data-guide="output-location">
              <span class="ofield__label">{{ t('separate.temporaryOutputDir') }}</span>
              <div class="dir-input" data-drop-zone="outputDir">
                <n-input v-model:value="outputDirectoryInput" size="small" :status="outputDirectoryError ? 'error' : undefined" :placeholder="t('separate.outputDefault')" :disabled="isRunModeLocked" clearable />
                <n-button secondary size="small" class="dir-input__browse" :title="t('separate.chooseOutput')" :disabled="isRunModeLocked" @click="pickTemporaryOutputDir">
                  <template #icon><n-icon :component="FolderOutline" /></template>
                </n-button>
              </div>
              <small v-if="outputDirectoryError" class="output-directory-error">{{ outputDirectoryError }}</small>
            </label>

            <div class="ofield-row" data-guide="output-format">
              <label class="ofield">
                <span class="ofield__label">{{ t('settings.defaultFormat') }}</span>
                <n-select v-model:value="settings.defaultFormat" size="small" :options="formatOptions" :disabled="isRunModeLocked" />
              </label>
              <div class="ofield">
                <span class="ofield__label">{{ t('separate.saveMode') }}</span>
                <div class="seg" :class="{ 'seg--locked': isRunModeLocked }">
                  <button
                    type="button"
                    class="seg__btn"
                    :class="{ 'seg__btn--active': !saveAsFolder }"
                    :disabled="isRunModeLocked"
                    @click="saveAsFolder = false"
                  >{{ t('separate.saveModeFlatName') }}</button>
                  <button
                    type="button"
                    class="seg__btn"
                    :class="{ 'seg__btn--active': saveAsFolder }"
                    :disabled="isRunModeLocked"
                    @click="saveAsFolder = true"
                  >{{ t('separate.saveModeFolderName') }}</button>
                </div>
              </div>
            </div>

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
          <section v-if="taskPanelState === 'running' && currentTask" key="running" class="stage-view stage-view--running">
            <div class="stage-hero">
              <div class="stage-hero__top">
                <span class="stage-badge stage-badge--running">
                  <span class="stage-badge__dot"></span>
                  {{ statusLabel(currentTask.status) }}
                </span>
                <div class="stage-hero__top-actions">
                  <n-button text size="small" data-guide="task-queue" @click="showQueueModal = true">
                    {{ t('separate.batchQueue') }} ({{ queueJobs.length }})
                  </n-button>
                  <n-button text size="small" :disabled="!currentTask.logs.length" @click="openCurrentLogs">
                    <template #icon><n-icon :component="TerminalOutline" /></template>
                    {{ t('tasks.logs') }}
                  </n-button>
                </div>
              </div>
              <div class="stage-hero__title">
                <h2>{{ currentBatchTitle }}</h2>
                <p>{{ currentBatchLine }}</p>
              </div>
              <div class="progress-ring-block">
                <div class="progress-ring" :style="{ '--pct': currentBatchProgress }">
                  <div class="progress-ring__center">
                    <strong>{{ currentBatchProgress }}</strong>
                    <span>%</span>
                  </div>
                </div>
                <div class="progress-ring__meta">
                  <div class="progress-meta-line">
                    <span class="progress-meta-line__label">
                      {{ currentBatchIsMulti ? t('separate.batchOverallProgress') : progressTitle(currentTask) }}
                    </span>
                    <span v-if="!currentBatchIsMulti && progressDetail(currentTask)" class="progress-meta-line__detail">{{ progressDetail(currentTask) }}</span>
                  </div>
                  <p v-if="taskSubMessage(currentTask)" class="progress-submessage">{{ taskSubMessage(currentTask) }}</p>
                  <div class="progress-facts">
                    <span><n-icon :component="TimeOutline" /> {{ currentTaskDuration }}</span>
                    <span :title="currentTaskOutputPath">{{ t('separate.outputTo') }} {{ currentTaskOutputSummary }}</span>
                  </div>
                </div>
              </div>
            </div>
            <div class="stage-actions">
              <n-button
                secondary
                strong
                size="large"
                @click="beginNextSeparation"
              >
                {{ t('separate.addNextBatch') }}
              </n-button>
              <n-button
                strong
                size="large"
                type="error"
                secondary
                class="stage-actions__primary"
                :loading="cancellingTaskId === currentTask.id || cancellingTaskId === 'batch'"
                :disabled="cancellingTaskId === currentTask.id || cancellingTaskId === 'batch'"
                @click="handleCancelCurrentTask"
              >
                {{ t('tasks.cancelAction') }}
              </n-button>
            </div>
          </section>

          <section v-else-if="isTerminalState && currentTask" key="terminal" :class="['stage-view', 'stage-view--terminal', `stage-view--${taskPanelState}`]">
            <div class="stage-hero stage-hero--result">
              <div class="stage-hero__top">
                <span class="stage-badge" :class="`stage-badge--${taskPanelState}`">
                  <n-icon :component="taskPanelState === 'done' ? CheckmarkCircle : (taskPanelState === 'failed' ? CloseOutline : PauseOutline)" />
                  {{ statusLabel(currentTask.status) }}
                </span>
                <div class="stage-hero__top-actions">
                  <n-button v-if="hasQueueEntries" text size="small" data-guide="task-queue" @click="showQueueModal = true">
                    {{ t('separate.batchQueue') }} ({{ queueJobs.length }})
                  </n-button>
                  <n-button v-if="taskPanelState !== 'done' && currentTask.logs.length" text size="small" @click="openCurrentLogs">
                    <template #icon><n-icon :component="TerminalOutline" /></template>
                    {{ t('tasks.logs') }}
                  </n-button>
                </div>
              </div>
              <div class="stage-hero__title">
                <h2>{{ currentBatchIsMulti ? currentBatchTitle : statusLabel(currentTask.status) }}</h2>
                <p>
                  {{ currentBatchLine }}
                  <template v-if="taskPanelState === 'done' && !currentBatchIsMulti"> · {{ currentTask.outputs.length }} {{ t('separate.previewStemUnit') }} · {{ currentTaskDuration }}</template>
                </p>
              </div>
              <div v-if="taskPanelState === 'done'" class="result-path" :title="currentTaskOutputPath">
                <n-icon :component="FolderOutline" />
                <code>{{ currentBatchOutputSummary }}</code>
                <n-button
                  size="small"
                  quaternary
                  :title="t('separate.openOutput')"
                  @click.stop="task.revealPath(currentTask.outputs[0]?.path || currentTask.output)"
                >
                  <template #icon><n-icon :component="OpenOutline" /></template>
                  {{ t('common.open') }}
                </n-button>
              </div>
              <div v-else-if="taskSubMessage(currentTask)" class="result-note">{{ taskSubMessage(currentTask) }}</div>
              <section v-if="taskPanelState === 'done' && playableOutputs.length" class="result-preview-panel">
                <div class="result-preview-panel__head">
                  <strong>{{ t('separate.previewTitle') }}</strong>
                  <span>{{ playableOutputs.length }} {{ t('separate.previewStemUnit') }}</span>
                </div>
                <div class="preview-output-groups">
                  <div v-for="group in playableOutputGroups" :key="group.taskId" class="preview-output-group">
                    <div v-if="currentBatchIsMulti" class="preview-output-group__head">
                      <strong :title="group.input">{{ getFileName(group.input) }}</strong>
                      <span>{{ group.outputs.length }} {{ t('separate.previewStemUnit') }}</span>
                    </div>
                    <div class="preview-track-list">
                      <div v-for="output in group.outputs" :key="`${group.taskId}:${output.path}`" class="preview-track">
                        <div class="preview-track__title">
                          <strong :title="output.path">{{ getFileName(output.path) }}</strong>
                          <small :title="output.path">{{ output.stem }}</small>
                        </div>
                        <n-button circle secondary size="small" @click="toggleOutputPlayback(output)">
                          <template #icon>
                            <n-icon :component="playingOutputPath === output.path ? PauseOutline : PlayOutline" />
                          </template>
                        </n-button>
                        <n-slider
                          class="preview-track__slider"
                          :value="getOutputPlayback(output.path).currentTime"
                          :min="0"
                          :max="Math.max(getOutputPlayback(output.path).duration, 1)"
                          :step="0.1"
                          :tooltip="false"
                          @update:value="(value: number) => seekOutput(output.path, value)"
                        />
                        <span class="preview-track__time">
                          {{ formatPlaybackTime(getOutputPlayback(output.path).currentTime) }} / {{ formatPlaybackTime(getOutputPlayback(output.path).duration) }}
                        </span>
                      </div>
                    </div>
                  </div>
                </div>
              </section>
            </div>
            <div class="stage-actions">
              <template v-if="taskPanelState === 'done'">
                <n-dropdown :options="completedActionOptions" placement="top-end" @select="handleCompletedAction">
                  <n-button secondary size="large">
                    {{ t('separate.moreActions') }}
                    <n-icon :component="ChevronDownOutline" />
                  </n-button>
                </n-dropdown>
                <n-button secondary size="large" @click="goToResults">
                  {{ t('separate.viewResults') }}
                </n-button>
                <n-button type="primary" size="large" class="stage-actions__primary" @click="beginNextSeparation">
                  {{ t('separate.newSeparation') }}
                </n-button>
              </template>
              <template v-else-if="taskPanelState === 'failed'">
                <n-button secondary size="large" @click="beginNextSeparation">
                  {{ t('separate.newSeparation') }}
                </n-button>
                <n-button type="primary" size="large" class="stage-actions__primary" @click="retryCurrentTask">
                  <template #icon><n-icon :component="PlayOutline" /></template>
                  {{ t('common.retry') }}
                </n-button>
              </template>
              <template v-else>
                <n-button secondary size="large" @click="retryCurrentTask">
                  <template #icon><n-icon :component="PlayOutline" /></template>
                  {{ t('separate.runAgain') }}
                </n-button>
                <n-button type="primary" size="large" class="stage-actions__primary" @click="beginNextSeparation">
                  {{ t('separate.newSeparation') }}
                </n-button>
              </template>
            </div>
          </section>

          <section v-else key="ready" class="stage-view stage-view--ready">
            <div class="stage-head">
              <div class="stage-head__label">
                <n-icon :component="CubeOutline" />
                <div>
                  <h2>{{ t('separate.runTarget') }}</h2>
                  <p>{{ t('separate.modelPanelHint') }}</p>
                </div>
              </div>
              <div class="stage-head__controls">
                <div class="stage-head__extra" data-guide="model-mode">
                  <n-select
                    :value="ensembleEnabled ? 'ensemble' : 'single'"
                    size="small"
                    :options="[
                      { label: t('separate.singleModelMode'), value: 'single' },
                      { label: t('separate.ensembleMode'), value: 'ensemble' },
                    ]"
                    class="model-mode-select"
                    :aria-label="t('separate.modelModeLabel')"
                    @update:value="(value: string | number) => { ensembleEnabled = value === 'ensemble' }"
                  />
                </div>
                <n-button class="stage-head__queue" size="small" secondary data-guide="task-queue" @click="showQueueModal = true">
                  <template #icon><n-icon :component="ListOutline" /></template>
                  {{ t('separate.batchQueue') }}
                  <span class="stage-head__queue-count" :class="{ 'stage-head__queue-count--active': hasQueueEntries }">{{ queueJobs.length }}</span>
                </n-button>
              </div>
            </div>

            <transition name="stage-swap" mode="out-in">
              <div key="model" class="target-pane" :class="{ 'target-pane--ensemble': ensembleEnabled }">
                <template v-if="modelPanelHasModels">
                  <div class="target-toolbar" data-guide="separation-models">
                    <n-input
                      v-model:value="modelSearch"
                      clearable
                      :placeholder="t('separate.modelSearchPlaceholder')"
                    >
                      <template #prefix><n-icon :component="SearchOutline" /></template>
                    </n-input>
                    <n-select
                      class="target-toolbar__filter"
                      v-model:value="modelCategoryFilter"
                      :menu-props="{ class: 'model-picker__category-menu' }"
                      :options="modelCategoryOptions"
                    />
                    <n-select
                      v-model:value="modelListSortMode"
                      class="target-toolbar__sort"
                      :options="modelSortOptions"
                      :aria-label="t('separate.modelSortLabel')"
                    />
                    <div class="view-toggle target-toolbar__view" role="group" :aria-label="t('models.viewMode')">
                      <button
                        type="button"
                        class="view-toggle__button"
                        :class="{ 'view-toggle__button--active': modelListViewMode === 'list' }"
                        :aria-pressed="modelListViewMode === 'list'"
                        :title="t('models.viewList')"
                        @click="modelListViewMode = 'list'"
                      >
                        <n-icon :component="ListOutline" />
                      </button>
                      <button
                        type="button"
                        class="view-toggle__button"
                        :class="{ 'view-toggle__button--active': modelListViewMode === 'card' }"
                        :aria-pressed="modelListViewMode === 'card'"
                        :title="t('models.viewCard')"
                        @click="modelListViewMode = 'card'"
                      >
                        <n-icon :component="GridOutline" />
                      </button>
                    </div>
                  </div>
                  <div v-if="ensembleEnabled" class="ensemble-summary-bar">
                    <div class="ensemble-summary-bar__info">
                      <strong>{{ t('separate.ensembleMode') }}</strong>
                      <span>{{ ensembleModels.length ? t('separate.ensembleSelectedCount', { count: ensembleModels.length }) : t('separate.ensembleNeedModels') }}</span>
                    </div>
                    <n-button size="small" secondary :disabled="!ensembleModels.length" @click="showEnsembleModal = true">
                      <template #icon><n-icon :component="SettingsOutline" /></template>
                      {{ t('separate.ensembleConfigure') }}
                    </n-button>
                  </div>
                  <div
                    v-if="pagedDownloadedModels.length"
                    class="target-list target-list--models"
                    :class="`target-list--${modelListViewMode}`"
                    role="listbox"
                    :aria-label="t('separate.model')"
                  >
                    <button
                      v-for="item in pagedDownloadedModels"
                      :key="item.name"
                      type="button"
                      role="option"
                      :aria-selected="ensembleEnabled ? ensembleModels.includes(item.name) : selectedModelName === item.name"
                      class="target-row"
                      :class="{ 'target-row--active': ensembleEnabled ? ensembleModels.includes(item.name) : selectedModelName === item.name }"
                      @click="handleSelectModel(item)"
                      @contextmenu.stop.prevent="openSeparateContextMenu($event, 'model', item)"
                    >
                      <span class="target-row__radio"></span>
                      <span class="target-row__body">
                        <span class="target-row__name" :title="item.name">{{ item.name }}</span>
                        <span class="target-row__meta">
                          <span class="target-row__tag" :title="categoryLabel(item)">{{ categoryLabel(item) }}</span>
                          <span class="target-row__desc" :title="modelMetaLine(item)">{{ modelMetaLine(item) }}</span>
                          <span v-if="modelListViewMode === 'list'" class="target-row__usage">{{ t('models.useCountValue', { count: modelUseCount(item.name) }) }}</span>
                        </span>
                        <!-- Shown because it is searchable: matching on text the user cannot see
                             would make the result list look wrong. -->
                        <span
                          v-if="modelNote(item.name)"
                          class="target-row__note"
                          :title="modelNote(item.name)"
                        >{{ modelNote(item.name) }}</span>
                      </span>
                    </button>
                  </div>
                  <div v-else class="stage-empty">
                    <div class="stage-empty__glyph"><n-icon :component="SearchOutline" /></div>
                    <strong>{{ t('separate.modelSearchEmpty') }}</strong>
                  </div>
                  <div v-if="filteredDownloadedModels.length" class="model-pagination">
                    <span>{{ t('separate.modelPageSummary', { total: filteredDownloadedModels.length }) }}</span>
                    <n-pagination
                      v-model:page="modelPage"
                      v-model:page-size="modelPageSize"
                      :item-count="filteredDownloadedModels.length"
                      :page-sizes="modelPageSizeOptions"
                      show-size-picker
                      size="small"
                    />
                  </div>
                  <div v-if="selectedModelName && !modelDownloaded" class="stage-alert">
                    {{ t('separate.startHintModelMissing') }}
                  </div>
                </template>
                <div v-else class="stage-empty" :class="{ 'stage-empty--loading': modelPanelLoading }">
                  <div class="stage-empty__glyph">
                    <n-spin v-if="modelPanelLoading" size="medium" />
                    <n-icon v-else :component="CubeOutline" />
                  </div>
                  <strong>{{ modelPanelLoading ? t('separate.modelPanelLoadingTitle') : t('separate.modelPanelEmptyTitle') }}</strong>
                  <p>{{ modelPanelLoading ? t('separate.modelPanelLoadingDesc') : t('separate.modelPanelEmptyDesc') }}</p>
                  <n-button v-if="!modelPanelLoading" secondary @click="router.push('/models')">
                    {{ t('separate.modelPanelPrimaryAction') }}
                  </n-button>
                </div>
              </div>

            </transition>

            <footer class="launch-bar" :class="`launch-bar--${canStart ? 'ready' : 'idle'}`">
              <div class="launch-bar__status">
                <span class="launch-bar__glyph"><n-icon :component="CheckmarkCircle" /></span>
                <div class="launch-bar__text">
                  <strong>{{ startStatusText }}</strong>
                  <span :title="outputSummaryPath">{{ outputSummaryPath }}</span>
                </div>
              </div>
              <div class="launch-bar__actions">
                <n-button quaternary class="launch-bar__reveal" :title="t('separate.openOutput')" @click="task.revealPath(currentTask?.outputs[0]?.path || normalizedOutputDir)">
                  <template #icon><n-icon :component="OpenOutline" /></template>
                  {{ t('separate.openOutput') }}
                </n-button>
                <n-button type="primary" size="large" class="launch-bar__go" data-guide="start-separation" :loading="submittingJob" :disabled="!canStart" @click="start">
                  <template #icon><n-icon :component="PlayOutline" /></template>
                  {{ t('separate.startTask') }}
                </n-button>
              </div>
            </footer>
          </section>
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

    <n-modal
      :show="showModelNoteEditor"
      @update:show="handleModelNoteEditorUpdate"
    >
      <n-card
        class="separate-note-modal"
        :title="t('models.editNote')"
        :bordered="false"
        closable
        role="dialog"
        aria-modal="true"
        @close="closeModelNoteEditor"
      >
        <div v-if="noteEditorModel" class="separate-note-modal__body">
          <strong class="separate-note-modal__name">{{ noteEditorModel.name }}</strong>
          <n-input
            v-model:value="noteDraft"
            type="textarea"
            :autosize="{ minRows: 4, maxRows: 8 }"
            clearable
            :maxlength="240"
            show-count
            :placeholder="t('models.notePlaceholder')"
          />
        </div>
        <template #footer>
          <div class="separate-note-modal__footer">
            <n-button secondary @click="closeModelNoteEditor">{{ t('common.cancel') }}</n-button>
            <n-button type="primary" :disabled="!noteEditorModel" @click="saveModelNote">{{ t('common.save') }}</n-button>
          </div>
        </template>
      </n-card>
    </n-modal>

    <n-modal v-model:show="showEnsembleModal" :mask-closable="true">
      <n-card
        class="ensemble-modal"
        :title="t('separate.ensembleConfigureTitle')"
        :bordered="false"
        closable
        role="dialog"
        aria-modal="true"
        @close="showEnsembleModal = false"
      >
        <div class="ensemble-modal__intro">
          <span>{{ t('separate.ensembleHint') }}</span>
          <strong>{{ t('separate.ensembleSelectedCount', { count: ensembleModels.length }) }}</strong>
        </div>
        <div class="ensemble-config__fields">
          <label>
            <span>{{ t('separate.ensembleStem') }}</span>
            <n-input v-model:value="ensembleStem" :placeholder="t('separate.ensembleStemPlaceholder')" />
          </label>
          <label>
            <span>{{ t('separate.ensembleType') }}</span>
            <n-select v-model:value="ensembleType" :options="ensembleTypeOptions" />
          </label>
        </div>
        <n-divider title-placement="left">{{ t('separate.ensembleWeights') }}</n-divider>
        <div class="ensemble-modal__weights">
          <div v-for="name in ensembleModels" :key="name" class="ensemble-modal__weight">
            <span class="ensemble-modal__model-name" :title="name">{{ name }}</span>
            <n-select
              v-model:value="ensembleModelStems[name]"
              size="small"
              :options="ensembleStemOptionsByModel[name] || []"
              :placeholder="t('separate.ensembleModelStemPlaceholder')"
              :disabled="!(ensembleStemOptionsByModel[name] || []).length"
            />
            <n-slider v-model:value="ensembleWeights[name]" :min="0" :max="1" :step="0.05" />
            <n-input-number v-model:value="ensembleWeights[name]" class="ensemble-modal__weight-input" size="small" :min="0" :max="1" :step="0.05" />
          </div>
        </div>
        <template #footer>
          <n-button type="primary" @click="showEnsembleModal = false">{{ t('common.close') }}</n-button>
        </template>
      </n-card>
    </n-modal>

    <n-modal v-model:show="showSettingsDrawer" @after-enter="locateGuidedParameter">
      <InferenceParameterLesson
        v-if="parameterHelpPage !== null" :topic="helpTopics[parameterHelpPage]!" :topics="helpTopics" :progress="`${parameterHelpPage + 1} / ${helpTopics.length}`"
        :close-label="t('parameterHelp.backToParameters')" :next-label="parameterHelpPage === helpTopics.length - 1 ? t('parameterHelp.done') : undefined"
        @select="topic => parameterHelpPage = helpTopics.indexOf(topic)"
        @close="parameterHelpPage = null" @previous="parameterHelpPage > 0 ? parameterHelpPage-- : parameterHelpPage = null"
        @next="parameterHelpPage < helpTopics.length - 1 ? parameterHelpPage++ : parameterHelpPage = null"
      />
      <n-card
        v-else
        ref="settingsCardRef"
        class="settings-modal"
        :title="t('separate.settingsDrawerTitle')"
        :bordered="false"
        closable
        role="dialog"
        aria-modal="true"
        @close="showSettingsDrawer = false"
      >
        <div class="settings-drawer__content">
          <div class="settings-group settings-group--run">
            <div class="settings-group__head">
              <strong>{{ t('separate.runOptionsTitle') }}</strong>
              <span>{{ t('separate.runOptionsHint') }}</span>
            </div>
            <div class="check-list">
              <n-checkbox v-model:checked="useTta" data-parameter-guide="tta" :class="{ 'parameter-guide-target--active': guidedParameter?.id === 'tta' }">{{ t('separate.tta') }}</n-checkbox>
            </div>
          </div>

          <div class="settings-group">
            <div class="settings-group__head">
              <strong>{{ t('separate.audioQualityTitle') }} · {{ formatLabel }}</strong>
              <span>{{ t('separate.audioQualityEditable') }}</span>
            </div>
            <n-grid :cols="2" :x-gap="12" :y-gap="12" responsive="screen">
              <n-grid-item v-if="effectiveFormat === 'wav'">
                <div class="field-block" data-parameter-guide="wavBitDepth" :class="{ 'parameter-guide-target--active': guidedParameter?.id === 'wavBitDepth' }">
                  <label>{{ t('audio.wavBitDepth') }}</label>
                  <n-select v-model:value="settings.wavBitDepth" :options="wavBitDepthOptions" />
                </div>
              </n-grid-item>
              <n-grid-item v-if="effectiveFormat === 'flac'">
                <div class="field-block" data-parameter-guide="flacBitDepth" :class="{ 'parameter-guide-target--active': guidedParameter?.id === 'flacBitDepth' }">
                  <label>{{ t('audio.flacBitDepth') }}</label>
                  <n-select v-model:value="settings.flacBitDepth" :options="flacBitDepthOptions" />
                </div>
              </n-grid-item>
              <n-grid-item v-if="effectiveFormat === 'mp3'">
                <div class="field-block" data-parameter-guide="mp3BitRate" :class="{ 'parameter-guide-target--active': guidedParameter?.id === 'mp3BitRate' }">
                  <label>{{ t('audio.mp3BitRate') }}</label>
                  <n-select v-model:value="settings.mp3BitRate" :options="mp3BitRateOptions" />
                </div>
              </n-grid-item>
              <n-grid-item v-if="effectiveFormat === 'm4a'">
                <div class="field-block" data-parameter-guide="m4aBitRate" :class="{ 'parameter-guide-target--active': guidedParameter?.id === 'm4aBitRate' }">
                  <label>{{ t('audio.m4aBitRate') }}</label>
                  <n-select v-model:value="settings.m4aBitRate" :options="bitRateOptions" />
                </div>
              </n-grid-item>
              <n-grid-item v-if="effectiveFormat === 'm4a'">
                <div class="field-block" data-parameter-guide="m4aCodec" :class="{ 'parameter-guide-target--active': guidedParameter?.id === 'm4aCodec' }">
                  <label>{{ t('audio.m4aCodec') }}</label>
                  <n-select v-model:value="settings.m4aCodec" :options="m4aCodecOptions" />
                </div>
              </n-grid-item>
              <n-grid-item v-if="showNormalizeField">
                <div class="field-block field-block--inline-check" data-parameter-guide="normalize" :class="{ 'parameter-guide-target--active': guidedParameter?.id === 'normalize' }">
                  <n-checkbox v-model:checked="normalize">{{ t('inference.normalize') }}</n-checkbox>
                </div>
              </n-grid-item>
            </n-grid>
          </div>

          <div class="settings-group">
            <n-collapse v-model:expanded-names="expandedInferenceSections">
              <n-collapse-item :title="t('inference.advancedParams')" name="inference">
                <template #header-extra><n-button v-if="!beginnerGuide.parameterStep" text size="small" :aria-label="t('parameterHelp.label')" @click.stop="openParameterHelp"><n-icon :component="InformationCircleOutline" size="18" /></n-button></template>
                <p class="advanced-hint">{{ t('separate.advancedPanelHint') }}</p>
                <div v-if="advancedParamsLoading" class="advanced-loading">
                  <n-spin size="small" />
                  <span>{{ t('separate.advancedPanelLoading') }}</span>
                </div>
                <!-- 有槽位接口时按渲染计划画（分组/顺序来自布局档）；没有接口的模型走下面的旧控件。 -->
                <ModelSlotFields
                  v-if="slotPanelPlan"
                  :plan="slotPanelPlan"
                  :model-value="slotPanelValues"
                  :active-slot="guidedParameter?.id"
                  @update:model-value="updateSlotPanel"
                />
                <template v-else>
                <n-grid :cols="2" :x-gap="12" :y-gap="12" responsive="screen">
                  <n-grid-item v-if="hasInferenceField('batch_size')">
                    <div class="field-block" data-parameter-guide="batch_size" :class="{ 'parameter-guide-target--active': guidedParameter?.id === 'batch_size' }">
                      <label>{{ t('inference.batchSize') }}</label>
                      <n-input-number v-model:value="batch_size" :min="0" :max="32" style="width:100%" @blur="task.restoreInferenceNumberFallback('batch_size')" />
                    </div>
                  </n-grid-item>
                  <n-grid-item v-if="hasInferenceField('overlap_size')">
                    <div class="field-block" data-parameter-guide="overlap_size" :class="{ 'parameter-guide-target--active': guidedParameter?.id === 'overlap_size' }">
                      <label>{{ t('inference.overlapSize') }}</label>
                      <AlignedInferenceInputNumber :value="overlap_size" :step="overlapSizeStep" :alignment-step="currentInferenceSampleStep" @update:value="updateOverlapSize" @blur="task.restoreInferenceNumberFallback('overlap_size')" />
                    </div>
                  </n-grid-item>
                  <n-grid-item v-if="hasInferenceField('num_overlap')">
                    <div class="field-block" data-parameter-guide="num_overlap" :class="{ 'parameter-guide-target--active': guidedParameter?.id === 'num_overlap' }">
                      <label>{{ t('inference.numOverlap') }}</label>
                      <n-input-number v-model:value="num_overlap" :min="0" :max="128" style="width:100%" @blur="task.restoreInferenceNumberFallback('num_overlap')" />
                    </div>
                  </n-grid-item>
                  <n-grid-item v-if="hasInferenceField('chunk_size')">
                    <div class="field-block" data-parameter-guide="chunk_size" :class="{ 'parameter-guide-target--active': guidedParameter?.id === 'chunk_size' }">
                      <label>{{ t('inference.chunkSize') }}</label>
                      <AlignedInferenceInputNumber :value="chunk_size" :step="chunkSizeStep" :alignment-step="currentChunkConstraint?.step || currentInferenceSampleStep" :alignment-offset="currentChunkConstraint?.offset" :alignment-min="currentChunkConstraint?.min" @update:value="updateChunkSize" @blur="task.restoreInferenceNumberFallback('chunk_size')" />
                      <n-text v-if="currentChunkIssue" type="warning">{{ t('inference.invalidChunkSize', currentChunkIssue) }}</n-text>
                    </div>
                  </n-grid-item>
                  <n-grid-item v-if="hasInferenceField('window_size')">
                    <div class="field-block" data-parameter-guide="window_size" :class="{ 'parameter-guide-target--active': guidedParameter?.id === 'window_size' }">
                      <label>{{ t('inference.vrWindowSize') }}</label>
                      <n-input-number v-model:value="window_size" :min="0" :max="4096" style="width:100%" @blur="task.restoreInferenceNumberFallback('window_size')" />
                    </div>
                  </n-grid-item>
                  <n-grid-item v-if="hasInferenceField('aggression')">
                    <div class="field-block" data-parameter-guide="aggression" :class="{ 'parameter-guide-target--active': guidedParameter?.id === 'aggression' }">
                      <label>{{ t('inference.vrAggression') }}</label>
                      <n-input-number v-model:value="aggression" :min="0" :max="100" style="width:100%" />
                    </div>
                  </n-grid-item>
                  <n-grid-item v-if="hasInferenceField('post_process_threshold')">
                    <div class="field-block" data-parameter-guide="post_process_threshold" :class="{ 'parameter-guide-target--active': guidedParameter?.id === 'post_process_threshold' }">
                      <label>{{ t('inference.vrPostProcessThreshold') }}</label>
                      <n-input-number v-model:value="post_process_threshold" :min="0" :max="1" :step="0.05" style="width:100%" />
                    </div>
                  </n-grid-item>
                </n-grid>
                <div class="check-list check-list--spaced">
                  <n-checkbox v-if="showStandardizeField" v-model:checked="standardize" data-parameter-guide="standardize" :class="{ 'parameter-guide-target--active': guidedParameter?.id === 'standardize' }">{{ t('inference.standardize') }}</n-checkbox>
                  <n-checkbox v-if="hasInferenceField('enable_post_process')" v-model:checked="enable_post_process" data-parameter-guide="enable_post_process" :class="{ 'parameter-guide-target--active': guidedParameter?.id === 'enable_post_process' }">{{ t('inference.vrEnablePostProcess') }}</n-checkbox>
                  <n-checkbox v-if="hasInferenceField('high_end_process')" v-model:checked="high_end_process" data-parameter-guide="high_end_process" :class="{ 'parameter-guide-target--active': guidedParameter?.id === 'high_end_process' }">{{ t('inference.vrHighEndProcess') }}</n-checkbox>
                </div>
                </template>
                <p v-if="!advancedParamsLoading && !hasVisibleAdvancedFields" class="advanced-empty">
                  {{ t('separate.advancedPanelEmpty') }}
                </p>
                <div
                  v-if="currentModelInfo && currentModelDefaultsResolved && !advancedParamsLoading"
                  class="advanced-actions"
                  data-parameter-guide="modelDefaults"
                  :class="{ 'parameter-guide-target--active': guidedParameter?.id === 'modelDefaults' }"
                >
                  <n-button size="small" secondary @click="resetCurrentModelInferenceDefaults">
                    {{ t('separate.resetInferenceDefaults') }}
                  </n-button>
                  <n-button size="small" type="primary" @click="saveCurrentModelInferenceDefaults">
                    {{ t('separate.saveInferenceDefaults') }}
                  </n-button>
                </div>
              </n-collapse-item>
            </n-collapse>
          </div>
        </div>

        <ParameterFieldGuide v-if="guidedParameter" ref="fieldGuideRef" :parameter="guidedParameter" :target="guideTarget" />

        <template #footer>
          <div class="drawer-footer">
            <n-button type="primary" @click="showSettingsDrawer = false">{{ t('common.close') }}</n-button>
          </div>
        </template>
      </n-card>
    </n-modal>
    <n-modal v-model:show="showNamingModal" style="width:var(--modal-width-sm)">
      <n-card
        class="naming-modal"
        :title="t('separate.namingModalTitle')"
        :bordered="false"
        closable
        role="dialog"
        aria-modal="true"
        @close="showNamingModal = false"
      >
        <div class="naming-modal__content">
          <div class="naming-section naming-section--main">
            <div class="naming-section__head">
              <strong>{{ t('separate.namingTemplate') }}</strong>
              <span>{{ t('separate.namingTemplateHint') }}</span>
            </div>
            <n-input
              ref="namingTemplateInputRef"
              v-model:value="outputNamingTemplate"
              placeholder="%index%_%filename%_%stem%"
            />
            <div class="naming-preview-inline">
              <span>{{ t('separate.namingPreview') }}</span>
              <div v-for="row in outputNamingPreviewParts" :key="row.key" class="naming-preview-file">
                <span
                  v-for="(part, partIndex) in row.parts"
                  :key="`${row.key}-${partIndex}`"
                  class="naming-preview-part"
                  :class="`naming-preview-part--${part.token.replace(/[%_]/g, '')}`"
                >{{ part.text }}</span>
              </div>
            </div>
          </div>

          <div class="naming-section">
            <div class="naming-section__head">
              <strong>{{ t('separate.namingVariables') }}</strong>
              <span>{{ t('separate.namingVariablesHint') }}</span>
            </div>
            <div class="token-grid">
              <button
                v-for="token in namingTokens"
                :key="token.value"
                type="button"
                class="token-chip"
                :title="token.value"
                @click="insertNamingToken(token.value)"
              >
                <span>{{ token.label }}</span>
                <code>{{ token.value }}</code>
              </button>
            </div>
          </div>

          <div v-if="usesIndexToken" class="naming-section">
            <div class="naming-section__head">
              <strong>{{ t('separate.namingNumberOrder') }}</strong>
              <span>{{ t('separate.namingNumberOrderHint') }}</span>
            </div>
            <div v-if="orderedOutputStems.length" class="stem-order-list">
              <div
                v-for="(stem, index) in orderedOutputStems"
                :key="stem"
                class="stem-order-row"
                :class="{ 'stem-order-row--dragging': draggedStemIndex === index }"
                @pointerdown="onStemPointerDown($event, index)"
                @pointermove="onStemPointerMove"
                @pointerup="finishStemPointerDrag"
                @pointercancel="finishStemPointerDrag"
              >
                <n-icon :component="ReorderFourOutline" />
                <span>{{ padNumber(index + 1) }}</span>
                <strong>{{ stem }}</strong>
                <div class="stem-order-row__actions">
                  <n-button quaternary circle size="tiny" :disabled="index === 0" :title="t('separate.moveUp')" @click="moveStem(index, index - 1)">
                    <template #icon><n-icon :component="ChevronUpOutline" /></template>
                  </n-button>
                  <n-button quaternary circle size="tiny" :disabled="index === orderedOutputStems.length - 1" :title="t('separate.moveDown')" @click="moveStem(index, index + 1)">
                    <template #icon><n-icon :component="ChevronDownOutline" /></template>
                  </n-button>
                </div>
              </div>
            </div>
            <n-empty v-else size="small" :description="t('separate.namingNoStems')" />
          </div>
          <div v-else class="naming-section naming-section--muted">
            {{ t('separate.namingNumberOrderDisabled') }}
          </div>
        </div>

        <template #footer>
          <div class="drawer-footer">
            <n-button secondary @click="resetOutputNaming">{{ t('separate.namingReset') }}</n-button>
            <n-button type="primary" @click="showNamingModal = false">{{ t('common.close') }}</n-button>
          </div>
        </template>
      </n-card>
    </n-modal>
    <n-modal v-model:show="showQueueModal" style="width:var(--modal-width-md)">
      <n-card
        class="batch-queue-modal"
        :title="t('separate.batchQueue')"
        :bordered="false"
        closable
        role="dialog"
        aria-modal="true"
        @close="showQueueModal = false"
      >
        <template #header-extra>
          <n-button size="small" quaternary @click="router.push('/queue')">{{ t('agent.queue') }}</n-button>
          <n-button
            v-if="queueScope === 'session' && finishedSessionJobCount"
            size="small"
            quaternary
            @click="clearFinishedQueueJobs"
          >
            {{ t('separate.clearFinishedTasks') }}
          </n-button>
          <n-button v-else-if="queueScope === 'no-results' && noResultSeparationJobs.length" size="small" quaternary type="error"
            :loading="removingRunRecords" :disabled="removingRunRecords" @click="removeSeparationRunRecords(noResultSeparationJobs)">
            {{ t('separate.clearNoResultHistory') }}
          </n-button>
        </template>
        <n-radio-group v-model:value="queueScope" size="small" class="batch-queue-scopes">
          <n-radio-button value="session">{{ t('separate.queueCurrent') }}</n-radio-button>
          <n-radio-button value="no-results">{{ t('separate.queueNoResultHistory') }} · {{ noResultSeparationJobs.length }}</n-radio-button>
        </n-radio-group>
        <div v-if="queueJobs.length" class="batch-queue-list">
          <article
            v-for="job in queueJobs"
            :key="job.id"
            class="batch-queue-row"
            :class="`batch-queue-row--${job.status}`"
          >
            <button type="button" class="batch-queue-row__main" @click="focusQueueJob(job)">
              <span class="batch-queue-row__status">
                <i></i>
                {{ statusLabel(job.status) }}
              </span>
              <strong>{{ job.model }}</strong>
              <span class="batch-queue-row__input" :title="job.primary.input">{{ queueJobInputSummary(job) }}</span>
              <n-progress
                type="line"
                :percentage="job.progress"
                :show-indicator="false"
                :height="5"
                :border-radius="3"
              />
            </button>
            <div class="batch-queue-row__meta">
              <span>{{ job.inputCount }} {{ t('separate.queueFileUnit') }}</span>
              <div class="batch-queue-row__actions">
                <template v-if="['done', 'failed', 'cancelled'].includes(job.status)">
                  <n-button size="small" secondary @click="job.status === 'failed' ? viewQueueJobLogs(job) : focusQueueJob(job)">
                    {{ job.status === 'failed' ? t('tasks.logs') : t('separate.viewTaskDetails') }}
                  </n-button>
                  <n-button v-if="isNoResultSeparationJob(job)" size="small" quaternary type="error"
                    :loading="removingRunRecords" :disabled="removingRunRecords" @click="removeSeparationRunRecords([job])">
                    {{ t('workbench.removeRun') }}
                  </n-button>
                  <n-button v-else size="small" quaternary @click="dismissQueueJob(job)">
                    {{ t('common.remove') }}
                  </n-button>
                </template>
                <n-button
                  v-else
                  size="small"
                  tertiary
                  type="error"
                  :loading="cancellingQueueJobId === job.id"
                  :disabled="Boolean(cancellingQueueJobId)"
                  @click="requestCancelQueueJob(job)"
                >
                  {{ t('tasks.cancelAction') }}
                </n-button>
              </div>
            </div>
          </article>
        </div>
        <n-empty v-else :description="t(queueScope === 'no-results' ? 'separate.queueNoResultEmpty' : 'separate.queueEmpty')" />
      </n-card>
    </n-modal>
    <n-modal v-model:show="showLogModal" style="width:var(--modal-width-lg)">
      <n-card
        :title="currentTask ? `${currentTaskFileName} - ${t('tasks.logs')}` : t('tasks.logs')"
        :bordered="false"
        size="small"
        role="dialog"
        aria-modal="true"
      >
        <TaskLogConsole :lines="currentTask?.logs || []" />
      </n-card>
    </n-modal>
  </div>
</template>


<style scoped src="./SeparateView.css"></style>
