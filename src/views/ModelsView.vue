<script setup lang="ts">
import { pickModelConfigFile, pickModelWeightsFile } from '@/api/native'
import ModelSlotFields from '../components/ModelSlotFields.vue'
import { makeModelStorage, type StorageListItem } from './models/storage'
import { makeModelCardStatus } from './models/cardStatus'
import { makeModelSorting } from './models/sorting'
import { makeModelNotes } from './models/notes'
import { makeInferenceEditor } from './models/inferenceEditor'
import { createLogger } from '@/utils/logger'
import { isActiveDownloadStatus } from '@/features/downloads/lifecycle'

import { computed, onMounted, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useI18n } from 'vue-i18n'
import { useMessage, useDialog, type DropdownOption } from 'naive-ui'
import {
  SearchOutline,
  RefreshOutline,
  CubeOutline,
  ServerOutline,
  AddCircleOutline,
  GridOutline,
  ListOutline,
} from '@vicons/ionicons5'
import {
  useModelStore,
  type ModelDefaultInferenceParams,
  type ModelEntry,
} from '@/stores/model'
import { useSettingsStore } from '@/stores/settings'
import { useTaskStore } from '@/stores/task'
import { useAppStore } from '@/stores/app'
import { formatBytes } from '@/utils/format'
import { buildModelCategoryOptionsFromPairs } from '@/utils/modelCategory'
import { MODEL_LIBRARY_PAGE_SIZES } from '@/utils/pagination'
import ModelStorageDrawer from '@/components/models/ModelStorageDrawer.vue'
import ModelDetailModal from '@/components/models/ModelDetailModal.vue'
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
  inferenceEditorPlan,
  inferenceEditorSampleStep, inferenceEditorOverlapStep, inferenceEditorChunkStep,
  inferenceEditorChunkConstraint, inferenceEditorChunkIssue,
  updateInferenceDraftSize, canResetInferenceDefaults,
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
  categoryLabel, targetStemLabel, showModelTypeTag,
  isPartiallyAvailable, modelStatusLabel, modelStatusType,
  downloadStatusMessage, cardDownloadMessage, downloadProgressColor,
  deleteStatusMessage, isDeletingModel, downloadSpeed, sizeLabel,
  cardProgressPercent, cardProgressState, isModelBusy,
} = makeModelCardStatus({
  t, locale, downloadTasks, deleteTasks, isActiveDownloadStatus,
})

const {
  collator, isFavoriteModel, sortedModels, pagedModels,
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
  storageModels, allStorageModelsByKey, selectedStorageBytes,
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

const modelCardContext = {
  modelNameParts,
  modelNote,
  modelUseCount,
  isFavorite: (model: ModelEntry) => isFavoriteModel(model),
  toggleFavorite: (model: ModelEntry, event: MouseEvent) => toggleFavorite(model, event),
  selectModel: (model: ModelEntry) => selectModel(model),
  openContextMenu: (event: MouseEvent, model: ModelEntry) => openModelContextMenu(event, model),
  showModelTypeTag: (model: ModelEntry) => showModelTypeTag(model),
  targetStemLabel: (model: ModelEntry) => targetStemLabel(model),
  categoryLabel: (model: ModelEntry) => categoryLabel(model),
  sizeLabel: (model: ModelEntry) => sizeLabel(model),
  isDeletingModel: (name: string) => isDeletingModel(name),
  deleteMessage: (name: string) => deleteStatusMessage(name),
  deleteTask: (name: string) => deleteTasks.value[name],
  isCustomModel: (model: ModelEntry) => isCustomModel(model),
  relinkCustom: (model: ModelEntry, event?: MouseEvent) => relinkCustomModel(model, event),
  downloadTask: (name: string) => downloadTasks.value[name],
  cardDownloadMessage: (name: string) => cardDownloadMessage(name),
  downloadSpeed: (name: string) => downloadSpeed(name),
  openDownloadDetail: (name: string) => openDownloadDetail(name),
  cancelDownload: (model: ModelEntry, event?: MouseEvent) => cancelDownloadModel(model, event),
  downloadModel: (model: ModelEntry, event?: MouseEvent) => downloadModel(model, event),
  isModelBusy: (model: ModelEntry) => isModelBusy(model),
  cardProgressPercent: (model: ModelEntry) => cardProgressPercent(model),
  cardProgressState: (model: ModelEntry) => cardProgressState(model),
}

const modelDetailContext = {
  isFavorite: (model: ModelEntry) => isFavoriteModel(model),
  statusType: (model: ModelEntry) => modelStatusType(model),
  statusLabel: (model: ModelEntry) => modelStatusLabel(model),
  targetStemLabel: (model: ModelEntry) => targetStemLabel(model),
  categoryLabel: (model: ModelEntry) => categoryLabel(model),
  isPartiallyAvailable: (model: ModelEntry) => isPartiallyAvailable(model),
  isDeletingModel: (name: string) => isDeletingModel(name),
  deleteMessage: (name: string) => deleteStatusMessage(name),
  deleteTask: (name: string) => deleteTasks.value[name],
  downloadMessage: (name: string) => downloadStatusMessage(name),
  downloadTask: (name: string) => downloadTasks.value[name],
  downloadColor: (status: string) => downloadProgressColor(status),
  isModelBusy: (model: ModelEntry) => isModelBusy(model),
  toggleFavorite: (model: ModelEntry, event: MouseEvent) => toggleFavorite(model, event),
  downloadModel: (model: ModelEntry, event?: MouseEvent) => downloadModel(model, event),
  cancelDownload: (model: ModelEntry, event?: MouseEvent) => cancelDownloadModel(model, event),
  confirmDelete: (model: ModelEntry) => confirmDeleteModel(model),
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
    const picked = await pickModelWeightsFile(t('models.customPickWeightsTitle', { name: model.name }))
    if (!picked) return
    let configPicked: string | null = null
    if (model.configPath) {
      configPicked = await pickModelConfigFile(t('models.customPickConfigTitle', { name: model.name }))
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

          <ModelCard
            v-for="model in pagedModels"
            :key="model.name"
            :model="model"
            :selected="selectedModel === model.name"
            :context="modelCardContext"
          />
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

    <ModelDetailModal
      :show="showDetail"
      :model="selectedInfo"
      :detail-loading="detailLoading"
      :context="modelDetailContext"
      @update:show="handleDetailDrawerUpdate"
      @after-leave="handleDetailModalAfterLeave"
    />

    <ModelStorageDrawer
      v-model:show="showStorage"
      v-model:search="storageSearch"
      v-model:sort="storageSort"
      v-model:downloaded-only="storageDownloadedOnly"
      :summary="modelStorageSummary"
      :model-dir="modelDir"
      :settings-model-dir="settings.modelDir || ''"
      :loading="storageLoading"
      :models="storageModels"
      :selected="selectedStorageModels"
      :selected-bytes="selectedStorageBytes"
      :has-deleting-selected="hasDeletingSelectedStorageModels"
      :batch-delete-state="batchDeleteState"
      :residual-cleanup-state="residualCleanupState"
      :delete-tasks="deleteTasks"
      :batch-delete-progress="batchDeleteProgress"
      :storage-model-type-label="storageModelTypeLabel"
      :is-deleting-model="isDeletingModel"
      :set-storage-selected="setStorageSelected"
      @refresh="modelStore.loadModelStorageSummary({ force: true })"
      @open-model-dir="openModelDir"
      @batch-delete="confirmBatchDelete"
      @cleanup-residual="confirmCleanupResidual"
    />

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
