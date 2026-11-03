import { formatProgressTime } from './format.ts'
import { isTerminalTaskStatus, type TaskStatus } from '../../features/tasks/lifecycle.ts'

export type TaskPresentationItem = {
  status: TaskStatus
  error?: string | null
  progressCurrent?: number | null
  progressTotal?: number | null
  progressDetail?: string | null
  message?: string | null
  createdAt: number
  startedAt?: number | null
  updatedAt: number
  finishedAt?: number | null
  durationMs?: number | null
}

export function makeTaskPresentation(t: (key: string) => string) {
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

  function normalizeProgressMessage(value?: string | null) {
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

  function progressTitle(item: TaskPresentationItem) {
    if (item.status === 'separating') return t('tasks.progressTitleSeparating')
    return statusLabel(item.status)
  }

  function progressDetail(item: TaskPresentationItem) {
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

  function taskSubMessage(item: TaskPresentationItem) {
    if (item.error) return item.error
    return normalizeProgressMessage(item.progressDetail || item.message)
  }

  function taskDuration(item: TaskPresentationItem, now: number) {
    const terminal = isTerminalTaskStatus(item.status)
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

  return {
    shortenMiddle,
    statusLabel,
    statusType,
    progressStatus: statusType,
    normalizeProgressMessage,
    progressTitle,
    progressDetail,
    taskSubMessage,
    taskDuration,
  }
}
