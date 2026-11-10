import type { Ref } from 'vue'
import type { ModelEntry } from '@/stores/model'
import { getModelCategoryLabel } from '@/utils/modelCategory'
import type { DownloadStatus } from '@/features/downloads/lifecycle'
import { formatBytes, formatSpeedMBps } from '@/utils/format'

type Translate = (key: string, params?: Record<string, unknown>) => string
type TaskMap = Ref<Record<string, Record<string, any> | undefined>>

type Deps = {
  t: Translate
  locale: Ref<string>
  downloadTasks: TaskMap
  deleteTasks: TaskMap
  isActiveDownloadStatus: (status: DownloadStatus) => boolean
}

/**
 * Presentation helpers for the model cards. Keeping them out of ModelsView makes
 * the card template depend on named status functions instead of view-local state.
 */
export function makeModelCardStatus({ t, locale, downloadTasks, deleteTasks, isActiveDownloadStatus }: Deps) {
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

  function downloadSpeed(modelName: string) {
    const task = downloadTasks.value[modelName]
    if (!task || task.status !== 'downloading') return ''
    return formatSpeedMBps(task.speedBytesPerSecond)
  }

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
    return Boolean(task && isActiveDownloadStatus(task.status))
  }

  return {
    categoryLabel, targetStemLabel, showModelTypeTag,
    isPartiallyAvailable, modelStatusLabel, modelStatusType,
    downloadStatusMessage, cardDownloadMessage, downloadProgressColor,
    deleteStatusMessage, isDeletingModel, downloadSpeed, sizeLabel,
    cardProgressPercent, cardProgressState, isModelBusy,
  }
}
