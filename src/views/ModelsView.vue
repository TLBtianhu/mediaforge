<script setup lang="ts">
import ModelSlotFields from '../components/ModelSlotFields.vue'
import { makeModelStorage, type StorageListItem } from './models/storage'
import { makeModelSorting } from './models/sorting'
import { makeModelNotes } from './models/notes'
import { makeInferenceEditor } from './models/inferenceEditor'
import { fromWireParams, paramsFromInterface, toWireParams } from '../litegraph/inferenceParams.ts'
import { layoutFor } from '../litegraph/layouts.ts'
import { buildRenderPlan, type ModelInterface } from '../litegraph/renderPlan.ts'
import { createLogger } from '@/utils/logger'
import { isTutorialFrame } from '@/features/onboarding/demoContext'

import { computed, onMounted, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useI18n } from 'vue-i18n'
import { useMessage, useDialog, type DropdownOption } from 'naive-ui'
import { invoke } from '@tauri-apps/api/core'
import {
  SearchOutline,
  DownloadOutline,
  CheckmarkCircleOutline,
  CloudDownloadOutline,
  RefreshOutline,
  CubeOutline,
  TrashOutline,
  FolderOpenOutline,
  ServerOutline,
  Star,
  StarOutline,
  AddCircleOutline,
  EllipsisHorizontalOutline,
  LinkOutline,
  GridOutline,
  ListOutline,
} from '@vicons/ionicons5'
import {
  useModelStore,
  type ModelDefaultInferenceParams,
  type ModelEntry,
  type ModelStorageDeleteTarget,
  type ToolModelStorageItem,
} from '@/stores/model'
import { useSettingsStore } from '@/stores/settings'
import { useTaskStore } from '@/stores/task'
import { useAppStore } from '@/stores/app'
import { formatBytes, formatSpeedMBps } from '@/utils/format'
import { buildModelCategoryOptionsFromPairs, getModelCategoryLabel } from '@/utils/modelCategory'
import { MODEL_LIBRARY_PAGE_SIZES } from '@/utils/pagination'
import { inferenceChunkSizeIssue, resolveInferenceChunkStep, resolveInferenceSampleStep } from '@/features/inference/sampleStep'
import ModelProgressBlock from '@/components/ModelProgressBlock.vue'
import DownloadDetailModal from '@/components/DownloadDetailModal.vue'
import CustomModelImportDialog from '@/components/CustomModelImportDialog.vue'
import AlignedInferenceInputNumber from '@/components/AlignedInferenceInputNumber.vue'

const studioLog = createLogger('ui.views.ModelsView')

const { t, locale } = useI18n()
const message = useMessage()
const dialog = useDialog()
const modelStore = useModelStore()
const settings = useSettingsStore()
const taskStore = useTaskStore()
const app = useAppStore()
const {
  filteredModels,
  selectedInfo,
  selectedModel,
  search,
  category,
  categories,
  categoriesCn,
  isLoading,
  detailLoading,
  modelDir,
  debugStatus,
  modelSource,
  modelViewMode,
  modelPageSize: pageSize,
  customModelCount,
  debugModelCount,
  downloadTasks,
  deleteTasks,
  modelStorageSummary,
  storageLoading,
  modelPreferences,
  batchDeleteState,
  residualCleanupState,
} = storeToRefs(modelStore)

const showDetail = ref(false)
const showCustomImport = ref(false)
const showDownloadDetail = ref(false)
const downloadDetailModel = ref<string>('')
const downloadedOnly = ref(false)
const page = ref(1)
const pageSizeOptions = [...MODEL_LIBRARY_PAGE_SIZES]
type ModelSort = 'default' | 'favorite' | 'name-asc' | 'name-desc' | 'size-desc' | 'size-asc' | 'category' | 'type' | 'downloaded'
const modelSort = ref<ModelSort>('default')
const showStorage = ref(false)
const storageSearch = ref('')
const storageSort = ref<'size-desc' | 'size-asc' | 'name-asc' | 'name-desc'>('size-desc')
const storageDownloadedOnly = ref(true)
const selectedStorageModels = ref<string[]>([])
const inferenceDraft = ref<Required<Pick<ModelDefaultInferenceParams, 'batch_size' | 'overlap_size' | 'chunk_size'>>>({
  batch_size: 1,
  overlap_size: 0,
  chunk_size: 0,
})
const contextMenuX = ref(0)
const contextMenuY = ref(0)
const contextMenuVisible = ref(false)
const contextModel = ref<ModelEntry | null>(null)
const showNoteEditor = ref(false)
const noteEditorModel = ref<ModelEntry | null>(null)
const noteDraft = ref('')
const showInferenceEditor = ref(false)
const inferenceEditorModel = ref<ModelEntry | null>(null)
const inferenceEditorParams = ref<Record<string, unknown>>({})
const {
  inferenceEditorMeta, inferenceEditorInterface, inferenceEditorPlan,
  inferenceEditorSampleStep, inferenceEditorOverlapStep, inferenceEditorChunkStep,
  inferenceEditorChunkConstraint, inferenceEditorChunkIssue,
  updateInferenceDraftSize, inferenceValue, resolveInferenceDraft, syncInferenceDraft,
  hasInferenceOverride, slotParamsChanged, hasInferenceDraftChanges, canResetInferenceDefaults,
  saveInferenceDefaults, resetInferenceDefaults,
  openInferenceEditor, closeInferenceEditor, handleInferenceEditorUpdate,
} = makeInferenceEditor({
  computed, watch, modelStore, taskStore, selectedModel, message, t, studioLog,
  inferenceDraft, showInferenceEditor, inferenceEditorModel, inferenceEditorParams,
})

const categoryOptions = computed(() => {
  return buildModelCategoryOptionsFromPairs(categories.value, categoriesCn.value, locale.value, t('common.all'))
})

// The imported count is shown on the option itself: it is the quickest answer to "did my import
// land?", and it keeps the option from looking broken when there is nothing to show yet.
const modelSourceOptions = computed(() => [
  { label: t('models.sourceAll'), value: 'all' },
  { label: t('models.sourceCatalog'), value: 'catalog' },
  { label: t('models.sourceDebug', { count: debugModelCount.value }), value: 'debug' },
  { label: t('models.sourceUser', { count: customModelCount.value }), value: 'user' },
])

const debugStatusText = computed(() => {
  const status = debugStatus.value
  if (!status?.active) return ''
  if (status.addedCount || status.changedCount || status.removedCount) return t('models.debugCatalogActive')
  return ''
})

const modelSortOptions = computed(() => [
  { label: t('models.sortDefault'), value: 'default' },
  { label: t('models.sortFavorite'), value: 'favorite' },
  { label: t('models.sortNameAsc'), value: 'name-asc' },
  { label: t('models.sortNameDesc'), value: 'name-desc' },
  { label: t('models.sortSizeDesc'), value: 'size-desc' },
  { label: t('models.sortSizeAsc'), value: 'size-asc' },
  { label: t('models.sortCategory'), value: 'category' },
  { label: t('models.sortType'), value: 'type' },
  { label: t('models.sortDownloaded'), value: 'downloaded' },
])

/** Whether anything is narrowing the list — decides "no matches" vs "nothing loaded yet". */
const hasActiveFilter = computed(() =>
  Boolean(search.value || category.value || downloadedOnly.value || modelSource.value !== 'all'))

const {
  collator, compareModelName, isFavoriteModel, sortedModels, pagedModels,
} = makeModelSorting({
  computed, locale, filteredModels, downloadedOnly, modelSort, page, pageSize,
  modelPreferences, categoryLabel,
})

const {
  modelNote, modelUseCount, modelNameParts, toggleFavorite,
  openModelNoteEditor, closeModelNoteEditor, handleNoteEditorUpdate, saveModelNote,
} = makeModelNotes({
  modelPreferences, modelStore, noteEditorModel, noteDraft, showNoteEditor, message, t,
})

const {
  allStorageModels, storageModels, allStorageModelsByKey, selectedStorageBytes,
  hasDeletingSelectedStorageModels, batchDeleteProgress, setStorageSelected,
} = makeModelStorage({
  computed, modelStorageSummary, storageSearch, storageSort, storageDownloadedOnly,
  selectedStorageModels, collator, isDeletingModel, batchDeleteState, deleteTasks,
})

watch([search, category, modelSource, downloadedOnly, pageSize, modelSort], () => {
  page.value = 1
})

watch(sortedModels, (list) => {
  const maxPage = Math.max(1, Math.ceil(list.length / pageSize.value))
  if (page.value > maxPage) page.value = maxPage
})

function categoryLabel(model: ModelEntry) {
  return getModelCategoryLabel(model, locale.value, '—')
}

function targetStemLabel(model: ModelEntry | null | undefined) {
  const direct = String(model?.targetStem || '').trim()
  if (direct) return direct
  const configTarget = String(model?.configTargetInstrument || '').trim()
  if (configTarget) return configTarget
  return String(model?.configInstruments || '')
    .split('|')
    .map((item) => item.trim())
    .filter(Boolean)
    .join(' / ')
}

function normalizedTagValue(value: string | null | undefined) {
  return String(value || '').trim().toLowerCase()
}

function showModelTypeTag(model: ModelEntry) {
  if (!model.modelType) return false
  return normalizedTagValue(model.modelType) !== normalizedTagValue(model.architecture)
}

function hasRequiredCompanionFiles(model: ModelEntry) {
  return Boolean(model.configPath || (model.auxiliaryPaths && model.auxiliaryPaths.length > 0))
}

function requiredModelFileCount(model: ModelEntry) {
  return 1 + (model.configPath ? 1 : 0) + (model.auxiliaryPaths?.length || 0)
}

function isPartiallyAvailable(model: ModelEntry) {
  if (model.downloaded || !hasRequiredCompanionFiles(model) || !Array.isArray(model.missingPaths)) return false
  const missingCount = model.missingPaths.length
  const totalCount = requiredModelFileCount(model)
  return missingCount > 0 && missingCount < totalCount
}

function modelStatusLabel(model: ModelEntry) {
  if (!model.supported) return t('models.unsupported')
  if (model.downloaded) return t('models.downloaded')
  if (isPartiallyAvailable(model)) return t('models.partial')
  return t('models.notDownloaded')
}

function modelStatusType(model: ModelEntry) {
  if (!model.supported) return 'warning'
  if (model.downloaded) return 'success'
  if (isPartiallyAvailable(model)) return 'info'
  return 'default'
}

function downloadStatusMessage(modelName: string) {
  const task = downloadTasks.value[modelName]
  if (!task) return ''
  if (task.status === 'preparing') return t('models.downloadPreparing')
  if (task.status === 'interrupted') return t('models.downloadInterrupted')
  return task.message || task.status
}

function cardDownloadMessage(modelName: string) {
  const task = downloadTasks.value[modelName]
  if (!task) return ''
  switch (task.status) {
    case 'preparing':
      return t('models.cardDownloadPreparing')
    case 'downloading':
      return task.totalFiles > 1
        ? t('models.cardDownloadingFiles', { completed: task.completedFiles, total: task.totalFiles })
        : t('models.cardDownloading')
    case 'error':
      return t('models.cardDownloadError')
    case 'paused':
      return t('models.cardDownloadPaused')
    case 'cancelled':
      return t('models.cardDownloadCancelled')
    case 'interrupted':
      return t('models.cardDownloadInterrupted')
    case 'done':
      return t('models.downloaded')
    default:
      return task.status
  }
}

function downloadProgressColor(status: string) {
  if (status === 'error') return 'var(--danger)'
  if (status === 'interrupted') return 'var(--warning)'
  return 'var(--primary)'
}

function deleteStatusMessage(modelName: string) {
  const task = deleteTasks.value[modelName]
  if (!task) return ''
  return task.message || t('models.deleting')
}

function isDeletingModel(modelName: string) {
  return deleteTasks.value[modelName]?.status === 'deleting'
}

/**
 * Whether the card is showing a progress block rather than an action button.
 *
 * The footer then needs the full width of the card: a progress bar squeezed into the narrow
 * action column is unreadable, and its status line has nowhere to go.
 */
/**
 * The percentage to draw along the card's bottom edge, or null when nothing is running.
 *
 * Rendering progress as the card border rather than as a block inside it costs no height, which
 * is what stops a downloading card from towering over its neighbours and leaving the grid ragged.
 */
/** Current rate, or '' when the download is not moving (paused, queued, finished). */
function downloadSpeed(modelName: string) {
  const task = downloadTasks.value[modelName]
  if (!task || task.status !== 'downloading') return ''
  return formatSpeedMBps(task.speedBytesPerSecond)
}

/**
 * The size cell. While downloading it becomes "done / total", which is the same number the
 * percentage refers to and answers "how much is left" without a second row.
 */
function sizeLabel(model: ModelEntry) {
  const task = downloadTasks.value[model.name]
  if (task && task.status === 'downloading' && task.downloadedBytes && task.totalBytes) {
    return `${formatBytes(task.downloadedBytes)} / ${formatBytes(task.totalBytes)}`
  }
  return formatBytes(model.sizeBytes)
}

function cardProgressPercent(model: ModelEntry): number | null {
  if (isDeletingModel(model.name)) return deleteTasks.value[model.name]?.progress || 0
  const task = downloadTasks.value[model.name]
  if (task && task.status !== 'done') return task.progress || 0
  return null
}

function cardProgressState(model: ModelEntry) {
  if (isDeletingModel(model.name)) return 'downloading'
  return downloadTasks.value[model.name]?.status || 'downloading'
}

function isModelBusy(model: ModelEntry) {
  if (isDeletingModel(model.name)) return true
  const task = downloadTasks.value[model.name]
  return Boolean(task && task.status !== 'done')
}

const modelMenuOptions = computed<DropdownOption[]>(() => {
  const model = contextModel.value
  if (!model) return []

  const options: DropdownOption[] = [
    { key: 'detail', label: t('models.viewDetail') },
    { key: 'note', label: t('models.editNote') },
    { key: 'inference', label: t('models.editInferenceDefaults') },
    {
      key: 'open-directory',
      label: t('models.openModelDir'),
      disabled: !model.downloaded,
    },
    {
      key: 'favorite',
      label: isFavoriteModel(model) ? t('models.unfavorite') : t('models.favorite'),
    },
  ]
  const task = downloadTasks.value[model.name]

  if (isCustomModel(model)) {
    options.push(
      { type: 'divider', key: 'model-actions-divider' },
      { key: 'relink', label: t('models.customRelink') },
      { key: 'remove-custom', label: t('models.customRemoveConfirm') },
    )
  } else if (task && task.status !== 'done') {
    options.push(
      { type: 'divider', key: 'model-actions-divider' },
      { key: 'download-detail', label: t('models.downloadDetail') },
    )
    if (['preparing', 'downloading'].includes(task.status)) {
      options.push({ key: 'cancel-download', label: t('common.cancel') })
    } else if (task.status !== 'preparing' && task.status !== 'downloading') {
      options.push({
        key: 'resume-download',
        label: task.status === 'interrupted' ? t('models.continueDownload') : t('common.resume'),
      })
      if (['paused', 'cancelled', 'error', 'interrupted'].includes(task.status)) {
        options.push({ key: 'delete', label: t('models.delete') })
      }
    }
  } else if (model.downloaded) {
    options.push(
      { type: 'divider', key: 'model-actions-divider' },
      { key: 'delete', label: t('models.delete') },
    )
  } else if (model.supported) {
    options.push(
      { type: 'divider', key: 'model-actions-divider' },
      { key: 'download', label: t('common.download') },
    )
  }

  return options
})



function storageModelTypeLabel(item: StorageListItem) {
  if (item.kind === 'catalog') return item.downloaded ? t('models.downloaded') : t('models.notDownloaded')
  if (item.role === 'vad') return t('models.storageAsrVadModel')
  if (item.role === 'punctuation') return t('models.storageAsrPunctuationModel')
  return t('models.storageAsrRecognitionModel')
}

async function loadModels() {
  try {
    await modelStore.loadModels()
  } catch {
    message.error(modelStore.error || t('models.empty'))
  }
}

function selectModel(model: ModelEntry) {
  showDetail.value = true
  void modelStore.selectModel(model).catch((err) => {
    message.error(err instanceof Error ? err.message : String(err))
  })
}

function openModelContextMenu(event: MouseEvent, model: ModelEntry) {
  contextModel.value = model
  contextMenuVisible.value = false
  contextMenuX.value = event.clientX
  contextMenuY.value = event.clientY
  window.requestAnimationFrame(() => {
    contextMenuVisible.value = true
  })
}

function closeModelContextMenu() {
  contextMenuVisible.value = false
  contextModel.value = null
}



async function revealModelDirectory(model: ModelEntry) {
  if (!model.downloaded || !model.modelPath) return
  try {
    await taskStore.revealPath(model.modelPath)
  } catch (err) {
    message.error(err instanceof Error ? err.message : String(err))
  }
}

function handleModelContextMenuSelect(key: string | number) {
  const model = contextModel.value
  closeModelContextMenu()
  if (!model) return

  if (key === 'detail') {
    selectModel(model)
    return
  }
  if (key === 'note') {
    openModelNoteEditor(model)
    return
  }
  if (key === 'inference') {
    openInferenceEditor(model)
    return
  }
  if (key === 'open-directory') {
    void revealModelDirectory(model)
    return
  }
  if (key === 'favorite') {
    toggleFavorite(model)
    return
  }
  if (key === 'download') {
    void downloadModel(model)
    return
  }
  if (key === 'download-detail') {
    openDownloadDetail(model.name)
    return
  }
  if (key === 'cancel-download') {
    void cancelDownloadModel(model)
    return
  }
  if (key === 'resume-download') {
    void downloadModel(model)
    return
  }
  if (key === 'relink') {
    void relinkCustomModel(model)
    return
  }
  if (key === 'remove-custom') {
    confirmRemoveCustomModel(model)
    return
  }
  if (key === 'delete') {
    confirmDeleteModel(model)
  }
}

function handleDetailDrawerUpdate(value: boolean) {
  showDetail.value = value
}

function handleDetailModalAfterLeave() {
  if (!showDetail.value) selectedInfo.value = null
}

async function downloadModel(model: ModelEntry, event?: MouseEvent) {
  event?.stopPropagation()
  try {
    const started = await modelStore.downloadModel(model.name)
    if (started) message.success(t('models.downloadStarted'))
  } catch (err) {
    message.error(err instanceof Error ? err.message : String(err))
  }
}

async function cancelDownloadModel(model: ModelEntry, event?: MouseEvent) {
  event?.stopPropagation()
  await modelStore.cancelDownload(model.name)
}

function openDownloadDetail(modelName: string) {
  downloadDetailModel.value = modelName
  showDownloadDetail.value = true
  modelStore.markDownloadTaskSeen(modelName)
}

function closeDownloadDetail() {
  showDownloadDetail.value = false
  if (downloadDetailModel.value) {
    modelStore.markDownloadTaskSeen(downloadDetailModel.value)
  }
}

function onDownloadDetailCancel() {
  if (!downloadDetailModel.value) return
  void modelStore.cancelDownload(downloadDetailModel.value)
}

function onDownloadDetailResume() {
  const name = downloadDetailModel.value
  if (!name) return
  const model = modelStore.models.find((m) => m.name === name)
  if (!model) return
  void downloadModel(model)
}

function onDownloadDetailDelete() {
  const name = downloadDetailModel.value
  if (!name) return
  const model = modelStore.models.find((m) => m.name === name)
  if (!model) return
  confirmDeleteModel(model)
}

const downloadDetailTask = computed(() => {
  if (!downloadDetailModel.value) return null
  return downloadTasks.value[downloadDetailModel.value] || null
})

watch(downloadTasks, (value) => {
  Object.values(value).forEach((task) => {
    if (task.status === 'error' && !task.seen) {
      if (!showDownloadDetail.value) {
        openDownloadDetail(task.model)
      }
    }
  })
}, { deep: true })

function confirmDeleteModel(model: ModelEntry) {
  if (isDeletingModel(model.name)) return
  dialog.warning({
    title: t('models.deleteConfirmTitle'),
    content: t('models.deleteConfirmContent'),
    positiveText: t('common.confirm'),
    negativeText: t('common.cancel'),
    onPositiveClick: () => {
      modelStore.deleteModel(model.name).catch((err) => {
        message.error(err instanceof Error ? err.message : String(err))
      })
    },
  })
}


/** Imported models are local-only: pymss refuses to download them, so they need their own actions. */
function isCustomModel(model: ModelEntry | null | undefined) {
  return model?.source === 'user'
}

/** Whether the active runtime's pymss exposes the user-model registry at all (2.0.15+). */
const customModelsSupported = computed(() => app.envInfo?.customModelsSupported !== false)

/**
 * Show the freshly imported model instead of leaving it buried among 300+ catalog entries.
 * Other filters are cleared for the same reason — a stale search would hide it again.
 */
function onCustomModelImported() {
  modelSource.value = 'user'
  search.value = ''
  category.value = ''
  downloadedOnly.value = false
}

function confirmRemoveCustomModel(model: ModelEntry) {
  // State exactly what will happen rather than describing both cases: this deletes files, and a
  // vague warning is the wrong thing to show before a destructive action. Only copies the app
  // made are ever deleted — the worker enforces that too.
  const copied = model.importMode === 'copy'
  dialog.warning({
    title: t('models.customRemoveTitle'),
    content: copied
      ? t('models.customRemoveContentCopy', { name: model.name })
      : t('models.customRemoveContentReference', { name: model.name }),
    positiveText: t('models.customRemoveConfirm'),
    negativeText: t('common.cancel'),
    onPositiveClick: () => {
      modelStore.removeCustomModel(model.name, true)
        .then((result) => {
          message.success(result.deletedFiles
            ? t('models.customRemovedWithFiles', { name: model.name })
            : t('models.customRemoved', { name: model.name }))
        })
        .catch((err) => message.error(err instanceof Error ? err.message : String(err)))
    },
  })
}

async function relinkCustomModel(model: ModelEntry, event?: MouseEvent) {
  event?.stopPropagation()
  try {
    const picked = await invoke<string | null>('pick_model_weights_file', {
      title: t('models.customPickWeightsTitle', { name: model.name }),
    })
    if (!picked) return
    let configPicked: string | null = null
    if (model.configPath) {
      configPicked = await invoke<string | null>('pick_model_config_file', {
        title: t('models.customPickConfigTitle', { name: model.name }),
      })
      // Most architectures require a config, so relinking without one would fail deep inside
      // pymss with a confusing message. A cancelled picker means "abandon the relink".
      if (!configPicked) return
    }
    await modelStore.relinkCustomModel(model.name, picked, configPicked)
    message.success(t('models.customRelinked', { name: model.name }))
  } catch (err) {
    message.error(err instanceof Error ? err.message : String(err))
  }
}

async function openStorageManager() {
  showStorage.value = true
  try {
    await modelStore.loadModelStorageSummary()
  } catch (err) {
    message.error(err instanceof Error ? err.message : String(err))
  }
}

function openModelDir() {
  const path = modelStorageSummary.value?.modelDir || modelDir.value || settings.modelDir
  if (path) void taskStore.revealPath(path)
}

function confirmBatchDelete() {
  const targets = selectedStorageModels.value
    .map(key => allStorageModelsByKey.value.get(key))
    .filter((item): item is StorageListItem => Boolean(item))
    .filter(item => item.kind === 'tool' || !isDeletingModel(item.id))
    .map(({ kind, id, name, tool }) => ({ kind, id, name, tool }))
  if (!targets.length) return
  dialog.warning({
    title: t('models.storageBatchDeleteConfirmTitle'),
    content: t('models.storageBatchDeleteConfirmContent', {
      count: targets.length,
      size: formatBytes(selectedStorageBytes.value),
    }),
    positiveText: t('models.storageBatchDelete'),
    negativeText: t('common.cancel'),
    onPositiveClick: () => {
      modelStore.deleteStorageModels(targets).then(() => {
        const failed = new Set(modelStore.batchDeleteState.failedModels)
        selectedStorageModels.value = selectedStorageModels.value.filter((key) => {
          const item = allStorageModelsByKey.value.get(key)
          return Boolean(item && failed.has(item.name))
        })
        if (modelStore.batchDeleteState.failedModels.length) {
          message.warning(t('models.batchDeleteWithFailures', { count: modelStore.batchDeleteState.failedModels.length }))
        } else {
          message.success(t('models.batchDeleteSuccess'))
        }
      }).catch((err) => message.error(err instanceof Error ? err.message : String(err)))
    },
  })
}

function confirmCleanupResidual() {
  const summary = modelStorageSummary.value
  if (!summary?.residualFiles.length) return
  dialog.warning({
    title: t('models.storageCleanupConfirmTitle'),
    content: t('models.storageCleanupConfirmContent', {
      count: summary.residualFiles.length,
      size: formatBytes(summary.residualBytes),
    }),
    positiveText: t('models.storageCleanup'),
    negativeText: t('common.cancel'),
    onPositiveClick: () => {
      modelStore.cleanupModelResidualFiles().catch((err) => message.error(err instanceof Error ? err.message : String(err)))
    },
  })
}

watch(deleteTasks, (value) => {
  Object.values(value).forEach((task) => {
    if (task.status === 'done') {
      if (task.source !== 'batch') {
        message.success(t('models.deleteSuccess'))
        modelStore.clearDeleteTask(task.model)
      }
    } else if (task.status === 'error') {
      if (task.source !== 'batch') {
        message.error(`${t('models.deleteFailed')}: ${task.message}`)
        modelStore.clearDeleteTask(task.model)
      }
    }
  })
}, { deep: true })

watch(residualCleanupState, (state) => {
  if (state.status === 'done' && !state.active) {
    message.success(t('models.storageCleanupSuccess'))
    modelStore.resetResidualCleanupState()
  } else if (state.status === 'error' && !state.active && state.message) {
    message.error(`${t('models.cleanupFailed')}: ${state.message}`)
    modelStore.resetResidualCleanupState()
  }
}, { deep: true })

onMounted(() => {
  showDetail.value = false
  if (!modelStore.models.length) void loadModels()
})
</script>

<template>
  <div class="page page--models">
    <div class="page-header-compact">
      <div>
        <div class="models-title-row">
          <h1>{{ t('models.title') }}</h1>
          <n-tag size="small" :bordered="false" type="info" round>
            {{ t('models.count', { count: sortedModels.length }) }}
          </n-tag>
          <n-tag v-if="debugStatusText" size="small" :bordered="false" type="warning" round>
            {{ debugStatusText }}
          </n-tag>
        </div>
        <p>{{ t('models.subtitle') }}</p>
      </div>
      <div class="header-actions" data-guide="model-management">
        <n-tooltip v-if="!customModelsSupported" trigger="hover">
          <template #trigger>
            <n-button secondary disabled>
              <template #icon><n-icon :component="AddCircleOutline" /></template>
              {{ t('models.customImport') }}
            </n-button>
          </template>
          {{ t('models.customUnsupported') }}
        </n-tooltip>
        <n-button v-else secondary @click="showCustomImport = true">
          <template #icon><n-icon :component="AddCircleOutline" /></template>
          {{ t('models.customImport') }}
        </n-button>
        <n-button secondary @click="openStorageManager">
          <template #icon><n-icon :component="ServerOutline" /></template>
          {{ t('models.storageManage') }}
        </n-button>
        <n-button secondary :loading="isLoading" data-guide="model-library-fallback" @click="loadModels">
          <template #icon><n-icon :component="RefreshOutline" /></template>
          {{ t('models.load') }}
        </n-button>
      </div>
    </div>

    <!-- Filter Toolbar -->
    <div class="toolbar" data-guide="model-filters">
      <div class="toolbar-row">
        <n-input
          v-model:value="search"
          :placeholder="t('models.search')"
          clearable
          class="search-input"
        >
          <template #prefix><n-icon :component="SearchOutline" /></template>
        </n-input>
        <n-select
          v-model:value="category"
          :options="categoryOptions"
          size="small"
          class="category-select"
          :consistent-menu-width="false"
        />
        <n-select
          v-model:value="modelSource"
          :options="modelSourceOptions"
          size="small"
          class="source-select"
          :consistent-menu-width="false"
        />
        <n-select
          v-model:value="modelSort"
          :options="modelSortOptions"
          size="small"
          class="sort-select"
          :consistent-menu-width="false"
        />
        <div class="toolbar-actions">
          <n-switch v-model:value="downloadedOnly" size="small" />
          <span class="text-sm text-muted">{{ t('models.downloadedOnly') }}</span>
          <div class="view-toggle" role="group" :aria-label="t('models.viewMode')">
            <button
              type="button"
              class="view-toggle__button"
              :class="{ 'view-toggle__button--active': modelViewMode === 'list' }"
              :aria-pressed="modelViewMode === 'list'"
              :title="t('models.viewList')"
              @click="modelViewMode = 'list'"
            >
              <n-icon :component="ListOutline" />
            </button>
            <button
              type="button"
              class="view-toggle__button"
              :class="{ 'view-toggle__button--active': modelViewMode === 'card' }"
              :aria-pressed="modelViewMode === 'card'"
              :title="t('models.viewCard')"
              @click="modelViewMode = 'card'"
            >
              <n-icon :component="GridOutline" />
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- Body: Model Grid + Detail Drawer -->
    <div class="models-body">
      <!-- Loading Skeleton -->
      <div v-if="isLoading && !modelStore.models.length" :class="['model-grid', `model-grid--${modelViewMode}`]">
        <div v-for="i in 6" :key="i" class="skel-card">
          <n-skeleton text style="width:70%;height:18px;margin-bottom:12px" />
          <n-skeleton text style="width:40%;height:14px;margin-bottom:16px" />
          <n-skeleton text style="width:100%;height:12px;margin-bottom:6px" />
          <n-skeleton text style="width:80%;height:12px" />
        </div>
      </div>

      <!-- Empty State -->
      <div v-else-if="!sortedModels.length" class="empty-state">
        <n-icon :component="CubeOutline" size="48" color="var(--on-surface-muted)" />
        <!-- Filtering to imported models with none imported is not a failed search: say what is
             missing and offer the action that fixes it, rather than "no results". -->
        <template v-if="modelSource === 'user' && !customModelCount">
          <p class="text-muted">{{ t('models.customEmpty') }}</p>
          <n-button v-if="customModelsSupported" secondary @click="showCustomImport = true">
            <template #icon><n-icon :component="AddCircleOutline" /></template>
            {{ t('models.customImport') }}
          </n-button>
        </template>
        <template v-else>
          <p class="text-muted">{{ hasActiveFilter ? t('models.searchEmpty') : t('models.empty') }}</p>
          <n-button v-if="!hasActiveFilter && !modelStore.models.length" secondary @click="loadModels">
            {{ t('models.load') }}
          </n-button>
        </template>
      </div>

      <!-- Model Grid -->
      <template v-else>
        <div :class="['model-grid', `model-grid--${modelViewMode}`]">
          <div
            v-for="model in pagedModels"
            :key="model.name"
            :data-guide="model.supported ? 'model-library' : undefined"
            :class="['model-card', {
              'model-card--selected': selectedModel === model.name,
              'model-card--unsupported': !model.supported,
              'model-card--busy': isModelBusy(model)
            }]"
            tabindex="0"
            :aria-label="t('models.viewDetailAria', { name: model.name })"
            :aria-current="selectedModel === model.name ? 'true' : undefined"
            @click="selectModel(model)"
            @contextmenu.stop.prevent="openModelContextMenu($event, model)"
            @keydown.enter.self.prevent="selectModel(model)"
            @keydown.space.self.prevent="selectModel(model)"
          >
          <!-- Card Header -->
          <div class="mc-header">
            <div class="mc-title">
              <span class="mc-name" :title="model.name"><template v-for="(part, index) in modelNameParts(model.name)" :key="index">{{ part }}<wbr /></template></span>
              <span v-if="modelNote(model)" class="mc-note" :title="modelNote(model)">{{ modelNote(model) }}</span>
            </div>
            <div class="mc-controls">
              <n-button
                quaternary
                circle
                size="tiny"
                :type="isFavoriteModel(model) ? 'warning' : 'default'"
                :title="isFavoriteModel(model) ? t('models.unfavorite') : t('models.favorite')"
                :aria-label="isFavoriteModel(model) ? t('models.unfavorite') : t('models.favorite')"
                class="mc-favorite"
                @click.stop="toggleFavorite(model, $event)"
              >
                <template #icon>
                  <n-icon :component="isFavoriteModel(model) ? Star : StarOutline" />
                </template>
              </n-button>
              <n-button
                quaternary
                circle
                size="tiny"
                class="mc-more"
                :title="t('models.moreActions')"
                :aria-label="t('models.moreActions')"
                @click.stop="openModelContextMenu($event, model)"
              >
                <template #icon><n-icon :component="EllipsisHorizontalOutline" /></template>
              </n-button>
            </div>
          </div>
          <!-- Meta Tags -->
          <div class="mc-tags">
            <n-tag v-if="model.architecture" :bordered="false" size="tiny" type="info" round :title="model.architecture">
              {{ model.architecture }}
            </n-tag>
            <n-tag v-if="showModelTypeTag(model)" :bordered="false" size="tiny" round :title="model.modelType || undefined">
              {{ model.modelType }}
            </n-tag>
          </div>

          <!-- Stable field positions keep models comparable across each card row. -->
          <div class="mc-meta">
            <div class="mc-meta-item mc-meta-item--target">
              <span class="mc-label">{{ t('models.targetStem') }}</span>
              <span class="mc-value" :title="targetStemLabel(model)">{{ targetStemLabel(model) || '—' }}</span>
            </div>
            <div class="mc-meta-item mc-meta-item--category">
              <span class="mc-label">{{ t('models.category') }}</span>
              <span class="mc-value" :title="categoryLabel(model) || undefined">{{ categoryLabel(model) || '—' }}</span>
            </div>
            <div class="mc-meta-item mc-meta-item--usage">
              <span class="mc-label">{{ t('models.useCountLabel') }}</span>
              <span class="mc-value">{{ t('models.useCountValue', { count: modelUseCount(model) }) }}</span>
            </div>
          </div>

          <!-- Bottom bar: size on the left, whatever the model needs on the right. Keeping
               them in one element means a state change swaps its contents instead of
               re-choreographing the card grid. -->
          <!-- Action row: one line in every state, so a downloading card is the same height as
               its neighbours. The progress itself becomes the card's bottom edge. -->
          <div class="mc-bottom">
            <span class="mc-size">{{ sizeLabel(model) }}</span>

            <template v-if="isDeletingModel(model.name)">
              <span class="mc-state mc-state--downloading">
                <span class="mc-state__dot" />
                {{ deleteStatusMessage(model.name) || t('models.deleting') }}
              </span>
              <span class="mc-pct">{{ deleteTasks[model.name]?.progress || 0 }}%</span>
            </template>

            <!-- Imported models are local-only: a missing file means "relink", not "download". -->
            <template v-else-if="isCustomModel(model)">
              <span :class="['mc-state', model.downloaded ? 'mc-state--ok' : 'mc-state--warn']">
                <n-icon :component="model.downloaded ? CheckmarkCircleOutline : LinkOutline" />
                {{ model.downloaded ? t('models.customReady') : t('models.customFilesMissing') }}
               </span>
               <div class="mc-actions">
                 <n-button size="tiny" quaternary @click.stop="relinkCustomModel(model, $event)">
                   <template #icon><n-icon :component="LinkOutline" /></template>
                 </n-button>
               </div>
            </template>

            <template v-else-if="!model.downloaded && downloadTasks[model.name] && downloadTasks[model.name].status !== 'done'">
              <span :class="['mc-state', `mc-state--${downloadTasks[model.name].status}`]">
                <span class="mc-state__dot" />
                {{ cardDownloadMessage(model.name) }}
              </span>
              <span v-if="downloadSpeed(model.name)" class="mc-speed">{{ downloadSpeed(model.name) }}</span>
              <span class="mc-pct">{{ downloadTasks[model.name].progress }}%</span>
              <div class="mc-actions">
                <n-button size="tiny" quaternary @click.stop="openDownloadDetail(model.name)">
                  {{ t('models.downloadDetail') }}
                </n-button>
                <n-button
                  v-if="['preparing', 'downloading'].includes(downloadTasks[model.name].status)"
                  size="tiny"
                  quaternary
                  @click.stop="cancelDownloadModel(model, $event)"
                >
                  {{ t('common.cancel') }}
                </n-button>
                <n-button
                  v-else-if="downloadTasks[model.name].status !== 'preparing'"
                  size="tiny"
                  quaternary
                  type="primary"
                  @click.stop="downloadModel(model, $event)"
                >
                  {{ downloadTasks[model.name]?.status === 'interrupted' ? t('models.continueDownload') : t('common.resume') }}
                </n-button>
              </div>
            </template>

            <template v-else-if="model.downloaded">
              <span class="mc-state mc-state--ok">
                <n-icon :component="CheckmarkCircleOutline" />
                {{ t('models.downloaded') }}
              </span>
            </template>

            <n-button
              v-else-if="model.supported"
              class="mc-download-button"
              secondary
              size="tiny"
              :disabled="isModelBusy(model)"
              @click.stop="downloadModel(model, $event)"
            >
              <template #icon><n-icon :component="DownloadOutline" /></template>
              {{ t('common.download') }}
            </n-button>
          </div>

          <!-- Progress as the card's bottom edge: findable at a glance across the grid, and it
               costs no height, which is what made downloading cards tower over their neighbours. -->
          <div
            v-if="cardProgressPercent(model) !== null"
            class="mc-progressbar"
            :style="{ '--mc-progress': cardProgressPercent(model) + '%' }"
            :data-state="cardProgressState(model)"
          />
          </div>
        </div>
        <div class="pagination-row">
          <span class="text-sm text-muted">{{ t('models.pageSummary', { total: sortedModels.length }) }}</span>
          <n-pagination
            v-model:page="page"
            v-model:page-size="pageSize"
            :item-count="sortedModels.length"
            :page-sizes="pageSizeOptions"
            show-size-picker
          />
        </div>
      </template>
    </div>

    <n-dropdown
      placement="bottom-start"
      trigger="manual"
      :x="contextMenuX"
      :y="contextMenuY"
      :options="modelMenuOptions"
      :show="contextMenuVisible"
      @clickoutside="closeModelContextMenu"
      @select="handleModelContextMenuSelect"
    />

    <n-modal
      :show="showNoteEditor"
      @update:show="handleNoteEditorUpdate"
    >
      <n-card
        class="model-note-modal"
        :title="t('models.editNote')"
        :bordered="false"
        closable
        role="dialog"
        aria-modal="true"
        @close="closeModelNoteEditor"
      >
        <div v-if="noteEditorModel" class="model-note-modal__body">
          <strong class="model-note-modal__name">{{ noteEditorModel.name }}</strong>
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
          <div class="model-note-modal__footer">
            <n-button secondary @click="closeModelNoteEditor">{{ t('common.cancel') }}</n-button>
            <n-button type="primary" :disabled="!noteEditorModel" @click="saveModelNote">{{ t('common.save') }}</n-button>
          </div>
        </template>
      </n-card>
    </n-modal>

    <n-modal
      :show="showInferenceEditor"
      @update:show="handleInferenceEditorUpdate"
    >
      <n-card
        class="model-inference-modal"
        :title="t('models.editInferenceDefaults')"
        :bordered="false"
        closable
        role="dialog"
        aria-modal="true"
        @close="closeInferenceEditor"
      >
        <div v-if="inferenceEditorModel" class="model-inference-modal__body">
          <strong class="model-inference-modal__name">{{ inferenceEditorModel.name }}</strong>
          <p class="model-inference-modal__hint">{{ t('models.inferenceDefaultsHint') }}</p>
          <!-- 有槽位接口时按渲染计划画表单（分组/顺序来自布局档）；
               没有接口的模型（自定义导入、尚未登记）继续用原来的三个字段。 -->
          <ModelSlotFields
            v-if="inferenceEditorPlan"
            :plan="inferenceEditorPlan"
            :model-value="inferenceEditorParams"
            @update:model-value="inferenceEditorParams = $event"
          />
          <div v-else class="inference-editor-grid">
            <label class="inference-editor-field">
              <span>{{ t('inference.batchSize') }}</span>
              <n-input-number v-model:value="inferenceDraft.batch_size" :min="1" :max="32" />
            </label>
            <label class="inference-editor-field">
              <span>{{ t('inference.overlapSize') }}</span>
              <AlignedInferenceInputNumber :value="inferenceDraft.overlap_size" :step="inferenceEditorOverlapStep" :alignment-step="inferenceEditorSampleStep" @update:value="updateInferenceDraftSize('overlap_size', $event)" />
            </label>
            <label class="inference-editor-field">
              <span>{{ t('inference.chunkSize') }}</span>
              <AlignedInferenceInputNumber :value="inferenceDraft.chunk_size" :step="inferenceEditorChunkStep" :alignment-step="inferenceEditorChunkConstraint?.step || inferenceEditorSampleStep" :alignment-offset="inferenceEditorChunkConstraint?.offset" :alignment-min="inferenceEditorChunkConstraint?.min" @update:value="updateInferenceDraftSize('chunk_size', $event)" />
            </label>
          </div>
          <n-alert v-if="inferenceEditorChunkIssue" type="warning" :show-icon="false" style="margin-top:12px">
            {{ t('inference.invalidChunkSize', inferenceEditorChunkIssue) }}
          </n-alert>
        </div>
        <template #footer>
          <div class="model-inference-modal__footer">
            <n-button
              secondary
              :disabled="!canResetInferenceDefaults(inferenceEditorModel)"
              @click="resetInferenceDefaults"
            >
              {{ t('models.inferenceDefaultsReset') }}
            </n-button>
            <span class="model-inference-modal__footer-spacer" />
            <n-button secondary @click="closeInferenceEditor">{{ t('common.cancel') }}</n-button>
            <n-button type="primary" :disabled="!inferenceEditorModel" @click="saveInferenceDefaults">
              {{ t('common.save') }}
            </n-button>
          </div>
        </template>
      </n-card>
    </n-modal>

    <!-- Detail Modal -->
    <n-modal :show="showDetail" @update:show="handleDetailDrawerUpdate" @after-leave="handleDetailModalAfterLeave">
      <n-card
        class="model-detail-modal"
        :title="t('models.detail')"
        :bordered="false"
        closable
        role="dialog"
        aria-modal="true"
        @close="handleDetailDrawerUpdate(false)"
      >
        <template v-if="selectedInfo">
          <div class="model-detail-modal__body">
            <div class="detail-content">
            <div class="detail-title-row">
              <strong class="detail-name">{{ selectedInfo.name }}</strong>
              <n-button
                secondary
                size="small"
                :type="isFavoriteModel(selectedInfo) ? 'warning' : 'default'"
                @click="toggleFavorite(selectedInfo, $event)"
              >
                <template #icon>
                  <n-icon :component="isFavoriteModel(selectedInfo) ? Star : StarOutline" />
                </template>
                {{ isFavoriteModel(selectedInfo) ? t('models.favorited') : t('models.favorite') }}
              </n-button>
            </div>

            <div class="detail-badges">
              <n-tag v-if="detailLoading" :bordered="false" size="small" type="info" round>
                {{ t('common.refreshing') }}
              </n-tag>
              <n-tag v-if="selectedInfo.architecture" :bordered="false" size="small" type="info" round>
                {{ selectedInfo.architecture }}
              </n-tag>
              <n-tag
                :bordered="false"
                size="small"
                :type="modelStatusType(selectedInfo)"
                round
              >
                {{ modelStatusLabel(selectedInfo) }}
              </n-tag>
            </div>

            <n-divider style="margin:16px 0" />

            <div class="detail-grid">
              <div class="detail-row">
                <span class="detail-label">{{ t('models.type') }}</span>
                <span class="detail-val">{{ selectedInfo.modelType || '—' }}</span>
              </div>
              <div class="detail-row">
                <span class="detail-label">{{ t('models.targetStem') }}</span>
                <span class="detail-val">{{ targetStemLabel(selectedInfo) || '—' }}</span>
              </div>
              <div class="detail-row">
                <span class="detail-label">{{ t('models.size') }}</span>
                <span class="detail-val">{{ formatBytes(selectedInfo.sizeBytes) }}</span>
              </div>
              <div class="detail-row">
                <span class="detail-label">{{ t('models.category') }}</span>
                <span class="detail-val">{{ categoryLabel(selectedInfo) || '—' }}</span>
              </div>
              <div v-if="selectedInfo.aliases?.length" class="detail-row">
                <span class="detail-label">{{ t('models.aliases') }}</span>
                <span class="detail-val">{{ selectedInfo.aliases.join(', ') }}</span>
              </div>
            </div>

            <n-divider style="margin:16px 0" />

            <n-collapse class="detail-path-collapse" :default-expanded-names="[]">
              <n-collapse-item :title="t('models.path')" name="model-path">
                <div class="detail-path-block">
                  <code class="detail-path">{{ selectedInfo.modelPath }}</code>
                </div>
                <div v-if="isPartiallyAvailable(selectedInfo) && selectedInfo.missingPaths?.length" class="detail-partial-block">
                  <div class="detail-label" style="margin-bottom:6px">{{ t('models.partialHintTitle') }}</div>
                  <div class="text-muted">{{ t('models.partialHint') }}</div>
                  <ul class="detail-missing-list">
                    <li v-for="path in selectedInfo.missingPaths" :key="path">
                      <code class="detail-path">{{ path }}</code>
                    </li>
                  </ul>
                </div>
              </n-collapse-item>
            </n-collapse>

            </div>
          </div>
        </template>

        <template #footer>
          <div class="drawer-footer" v-if="selectedInfo">
            <!-- 删除中：进度块 -->
            <ModelProgressBlock
              v-if="isDeletingModel(selectedInfo.name)"
              status="downloading"
              :message="deleteStatusMessage(selectedInfo.name) || t('models.deleting')"
              :progress="deleteTasks[selectedInfo.name]?.progress || 0"
              :files-text="t('models.deleteProgress', { completed: deleteTasks[selectedInfo.name]?.completedFiles || 0, total: deleteTasks[selectedInfo.name]?.totalFiles || 0 })"
            />

            <!-- 下载中：进度块 + 取消 -->
            <ModelProgressBlock
              v-else-if="['preparing', 'downloading'].includes(downloadTasks[selectedInfo.name]?.status || '')"
              :status="downloadTasks[selectedInfo.name]!.status"
              :message="downloadStatusMessage(selectedInfo.name)"
              :progress="downloadTasks[selectedInfo.name]!.progress"
              :files-text="downloadTasks[selectedInfo.name]!.totalFiles > 1 ? t('models.fileProgress', { completed: downloadTasks[selectedInfo.name]!.completedFiles, total: downloadTasks[selectedInfo.name]!.totalFiles }) : ''"
              :color="downloadProgressColor(downloadTasks[selectedInfo.name]!.status)"
            >
              <template #actions>
                <n-button block secondary @click="cancelDownloadModel(selectedInfo!, $event)">
                  {{ t('common.cancel') }}
                </n-button>
              </template>
            </ModelProgressBlock>

            <!-- 暂停/取消/错误/中断：进度块 + 继续 + 删除 -->
            <template v-else-if="!selectedInfo.downloaded && downloadTasks[selectedInfo.name] && ['paused','cancelled','error','interrupted'].includes(downloadTasks[selectedInfo.name]!.status)">
              <ModelProgressBlock
                :status="downloadTasks[selectedInfo.name]!.status"
                :message="downloadStatusMessage(selectedInfo.name)"
                :progress="downloadTasks[selectedInfo.name]!.progress"
                :files-text="downloadTasks[selectedInfo.name]!.totalFiles > 1 ? t('models.fileProgress', { completed: downloadTasks[selectedInfo.name]!.completedFiles, total: downloadTasks[selectedInfo.name]!.totalFiles }) : ''"
                :color="downloadProgressColor(downloadTasks[selectedInfo.name]!.status)"
              />
              <div class="drawer-footer-actions">
                <n-button
                  block
                  type="primary"
                  :disabled="isModelBusy(selectedInfo)"
                  @click="downloadModel(selectedInfo!, $event)"
                >
                  {{ downloadTasks[selectedInfo.name]?.status === 'interrupted' ? t('models.continueDownload') : t('common.resume') }}
                </n-button>
                <n-button
                  block
                  type="error"
                  secondary
                  @click="confirmDeleteModel(selectedInfo!)"
                >
                  <template #icon><n-icon :component="TrashOutline" /></template>
                  {{ t('models.delete') }}
                </n-button>
              </div>
            </template>

            <!-- 已下载：状态 + 删除 -->
            <template v-else-if="selectedInfo.downloaded">
              <n-button block secondary disabled>
                <template #icon><n-icon :component="CheckmarkCircleOutline" /></template>
                {{ t('models.downloaded') }}
              </n-button>
              <n-button
                block
                type="error"
                secondary
                @click="confirmDeleteModel(selectedInfo!)"
              >
                <template #icon><n-icon :component="TrashOutline" /></template>
                {{ t('models.delete') }}
              </n-button>
            </template>

            <!-- 未下载且无任务：下载（含不支持则不显示） -->
            <n-button
              v-else-if="selectedInfo.supported && !selectedInfo.downloaded"
              block
              type="primary"
              @click="downloadModel(selectedInfo!, $event)"
            >
              <template #icon><n-icon :component="CloudDownloadOutline" /></template>
              {{ t('common.download') }}
            </n-button>
          </div>
        </template>
      </n-card>
    </n-modal>

    <n-drawer v-model:show="showStorage" :width="760" placement="right" :z-index="isTutorialFrame() ? 3500 : undefined" :trap-focus="!isTutorialFrame()">
      <n-drawer-content :title="t('models.storageTitle')" closable>
        <div class="storage-panel">
          <div class="storage-summary">
            <div class="storage-stat">
              <span>{{ t('models.storageTotal') }}</span>
              <strong>{{ formatBytes(modelStorageSummary?.totalBytes || 0) }}</strong>
            </div>
            <div class="storage-stat">
              <span>{{ t('models.storageDownloadedCount') }}</span>
              <strong>{{ modelStorageSummary?.downloadedCount || 0 }}</strong>
            </div>
            <div class="storage-stat">
              <span>{{ t('models.storageResidual') }}</span>
              <strong>{{ formatBytes(modelStorageSummary?.residualBytes || 0) }}</strong>
            </div>
          </div>

          <div class="storage-dir">
            <span class="text-muted">{{ t('models.modelDir') }}:</span>
            <code>{{ modelStorageSummary?.modelDir || modelDir || settings.modelDir || '?' }}</code>
          </div>

          <div class="storage-toolbar">
            <n-input v-model:value="storageSearch" :placeholder="t('models.storageSearch')" clearable>
              <template #prefix><n-icon :component="SearchOutline" /></template>
            </n-input>
            <n-select
              v-model:value="storageSort"
              :options="[
                { label: t('models.storageSortSizeDesc'), value: 'size-desc' },
                { label: t('models.storageSortSizeAsc'), value: 'size-asc' },
                { label: t('models.storageSortNameAsc'), value: 'name-asc' },
                { label: t('models.storageSortNameDesc'), value: 'name-desc' },
              ]"
              style="width:160px"
            />
            <n-switch v-model:value="storageDownloadedOnly" size="small" />
            <span class="text-sm text-muted">{{ t('models.downloadedOnly') }}</span>
          </div>

          <div class="storage-actions">
            <n-button size="small" secondary :loading="storageLoading" @click="modelStore.loadModelStorageSummary({ force: true })">
              <template #icon><n-icon :component="RefreshOutline" /></template>
              {{ t('models.storageRefresh') }}
            </n-button>
            <n-button size="small" secondary @click="openModelDir">
              <template #icon><n-icon :component="FolderOpenOutline" /></template>
              {{ t('models.openModelDir') }}
            </n-button>
            <n-button
              size="small"
              type="error"
              secondary
              :disabled="!selectedStorageModels.length || hasDeletingSelectedStorageModels || batchDeleteState.active || residualCleanupState.active"
              @click="confirmBatchDelete"
            >
              {{ t('models.storageBatchDelete') }}
              <span v-if="selectedStorageModels.length">（{{ selectedStorageModels.length }} / {{ formatBytes(selectedStorageBytes) }}）</span>
            </n-button>
            <n-button
              size="small"
              type="warning"
              secondary
              :disabled="!(modelStorageSummary?.residualFiles.length) || batchDeleteState.active || residualCleanupState.active"
              @click="confirmCleanupResidual"
            >
              {{ t('models.storageCleanup') }}
            </n-button>
          </div>

          <div v-if="batchDeleteState.active" class="storage-progress-card">
            <strong>{{ t('models.batchDeleting', { count: batchDeleteState.totalModels }) }}</strong>
            <span class="text-muted">{{ batchDeleteState.completedModels }} / {{ batchDeleteState.totalModels }}</span>
            <div class="text-sm text-muted">{{ batchDeleteState.currentModel }}</div>
            <div v-if="batchDeleteState.currentModel && deleteTasks[batchDeleteState.currentModel]" class="mc-dl-files">
              {{ t('models.deleteProgress', { completed: deleteTasks[batchDeleteState.currentModel]?.completedFiles || 0, total: deleteTasks[batchDeleteState.currentModel]?.totalFiles || 0 }) }}
            </div>
            <n-progress
              :percentage="batchDeleteProgress"
              :show-indicator="false"
              :height="8"
              :border-radius="4"
              type="line"
              :rail-color="'var(--surface-3)'"
            />
          </div>

          <div v-if="residualCleanupState.active" class="storage-progress-card">
            <strong>{{ t('models.cleanupInProgress') }}</strong>
            <span class="text-muted">{{ t('models.deleteProgress', { completed: residualCleanupState.completedFiles, total: residualCleanupState.totalFiles }) }}</span>
            <n-progress
              :percentage="residualCleanupState.progress"
              :show-indicator="false"
              :height="8"
              :border-radius="4"
              type="line"
              :rail-color="'var(--surface-3)'"
            />
          </div>

          <div class="storage-list">
            <label
              v-for="item in storageModels"
              :key="item.selectionKey"
              class="storage-row"
            >
              <n-checkbox
                :checked="selectedStorageModels.includes(item.selectionKey)"
                :disabled="item.sizeBytes <= 0 || (item.kind === 'catalog' && isDeletingModel(item.id)) || batchDeleteState.active || residualCleanupState.active"
                @update:checked="(checked: boolean) => setStorageSelected(item.selectionKey, checked)"
              />
              <div class="storage-row__main">
                <strong>{{ item.name }}</strong>
                <span>{{ t('models.storageFileCount', { count: item.fileCount }) }}</span>
              </div>
              <n-tag size="small" :bordered="false" :type="item.kind === 'tool' ? 'info' : item.downloaded ? 'success' : 'default'">
                {{ storageModelTypeLabel(item) }}
              </n-tag>
              <strong class="storage-row__size">{{ formatBytes(item.sizeBytes || item.expectedSizeBytes) }}</strong>
            </label>
            <div v-if="!storageModels.length" class="empty-state storage-empty">
              <n-icon :component="CubeOutline" size="36" color="var(--on-surface-muted)" />
              <span class="text-muted">{{ t('models.searchEmpty') }}</span>
            </div>
          </div>
        </div>
      </n-drawer-content>
    </n-drawer>

    <DownloadDetailModal
      v-model:show="showDownloadDetail"
      :task="downloadDetailTask"
      :model-name="downloadDetailModel"
      @cancel="onDownloadDetailCancel"
      @resume="onDownloadDetailResume"
      @delete="onDownloadDetailDelete"
      @close="closeDownloadDetail"
    />

    <CustomModelImportDialog v-model:show="showCustomImport" @imported="onCustomModelImported" />
  </div>
</template>

<style scoped src="./models/models-view.css"></style>
