import i18n from '@/i18n'
import type { PersistedSeparateModelState } from '@/stores/taskTypes'

export function normalizePersistedModelState(value: unknown): PersistedSeparateModelState {
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

export function resolveTaskErrorMessage(code: unknown, message: unknown, input?: string) {
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
