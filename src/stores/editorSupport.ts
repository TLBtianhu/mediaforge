import { getCurrentLocale } from '@/i18n'
import i18n from '@/i18n'
import { mediaPreviewUrl } from '@/utils/mediaPreview'
import {
  EXACT_STEM_KEYS, LEGACY_STEM_NAMES, STEM_NAME_KEYS, normalizedDefaultName,
  recordingTrackIndex, stemKeyFromOutput,
} from '@/stores/editorStems'
import type { WorkerCommandResult } from '@/types/workerContracts.generated'
import type {
  EditorMasterLimiter, EditorPluginSlot, EditorSession, EditorSource, EditorSourceRole,
  EditorTrack,
} from '@/types/editor'

export type HistorySnapshot = {
  tracks: EditorTrack[]
  masterVolume: number
  masterPan: number
  masterLimiter?: EditorMasterLimiter
  pluginChain?: EditorPluginSlot[]
  selectedTrackId: string | null
  selectedClipId: string | null
}

export type ScanAudioPathsResult = {
  files: string[]
  warnings: string[]
}

export type LinkedEditorAsset = {
  path: string
  name: string
  originKind: string
  originRoot?: string | null
  relativePath?: string | null
  missing?: boolean
}

export type ImportEditorAssetsResult = {
  files: LinkedEditorAsset[]
  warnings: string[]
}

export type EditorProjectCleanupResult = {
  deletedProjectIds: string[]
  missingProjectIds: string[]
  blockedProjectIds: string[]
  failedProjectIds: string[]
}

export type OrphanedEditorProject = {
  projectId: string
  sourceTaskId: string
  name: string
}

export type RelinkEditorSourcesResult = {
  project: PersistedSession
  relinked: number
  unresolved: string[]
}

export type PickFileResult = string | null

export type AudioMetadata = WorkerCommandResult<'audio_metadata'>
export type WaveformPeaks = WorkerCommandResult<'waveform_peaks'>

export type LoadedWaveformPeaks = {
  peaks: number[]
  channelPeaks: number[][]
}

export type ExportResult = {
  path: string
  duration: number
  sampleRate: number
  channels: number
  format: string
  peakProtectionApplied?: boolean
  peakAdjustmentDb?: number
  pluginTailLimited?: boolean
}

export type EditorExportProgress = {
  phase: 'preparing' | 'rendering' | 'finalizing' | 'writing' | 'completed'
  completed: number
  total: number
  current: string
}

export type EditorExportWorkerEvent = {
  type: string
  taskId?: string | null
  requestId?: string | null
  payload?: Record<string, unknown>
}

export type PendingEditorExport = {
  id: string
  projectId: string
  exportDir?: string
  progressOffset?: number
  resolve: (result: ExportResult) => void
  reject: (error: Error) => void
}

export class EditorExportCancelledError extends Error {
  constructor(message = 'Editor export cancelled') {
    super(message)
    this.name = 'EditorExportCancelledError'
  }
}

export type PersistedSession = EditorSession & { version: 4 }

export const HISTORY_LIMIT = 80
export const AUTO_SAVE_DELAY = 420
export const NATIVE_RENDER_PROGRESS_WEIGHT = 75
export const WAVEFORM_FULL_RESOLUTION = 2200




export function blankProjectName() {
  const locale = getCurrentLocale()
  return String(i18n.global.t('projects.blankProjectDefaultName', {}, { locale }))
}

export function isDefaultBlankProjectName(name: string) {
  const normalized = String(name || '').trim()
  if (!normalized) return true
  return normalized === '未命名工程'
    || normalized === '未命名空工程'
    || normalized === 'Untitled Project'
    || normalized === 'Untitled Blank Project'
}




export function localizedStemName(stemKey: string | null | undefined, fallback: string) {
  if (!stemKey) return fallback
  const exactStemKey = EXACT_STEM_KEYS.get(normalizedDefaultName(stemKey))
  const broadStemKey = stemKeyFromOutput({ stem: stemKey })
  const fallbackIsLegacyName = (LEGACY_STEM_NAMES[broadStemKey] || [])
    .some(name => normalizedDefaultName(name) === normalizedDefaultName(fallback))
  const canonicalKey = exactStemKey || (fallbackIsLegacyName ? broadStemKey : '')
  const translationKey = STEM_NAME_KEYS[canonicalKey]
  return translationKey ? String(i18n.global.t(translationKey)) : fallback
}



export function localizedAutoTrackName(
  name: string,
  role: EditorSourceRole,
  stemKey?: string | null,
) {
  if (role === 'recording') {
    const index = recordingTrackIndex(name)
    return index === null ? name : String(i18n.global.t('editor.recordingTrackName', { index }))
  }
  if (role === 'stem') return localizedStemName(stemKey, name)
  return name
}



export function normalizePathKey(value: string) {
  return value.replace(/\\/g, '/').toLowerCase()
}

export function sourceDisplayGroup(source: EditorSource) {
  if (source.role === 'recording') return String(i18n.global.t('editor.assetGroupRecordings'))
  if (source.originKind === 'task-result' || source.role === 'stem') {
    return String(i18n.global.t('editor.assetGroupSeparationResults'))
  }
  return String(i18n.global.t('editor.assetGroupExternal'))
}

export async function loadPeaksFromCache(path?: string | null, sourcePath?: string | null) {
  if (!path) return null
  try {
    const response = await fetch(mediaPreviewUrl(path))
    if (!response.ok) return null
    const data = await response.json() as { path?: unknown, peaks?: unknown, channelPeaks?: unknown }
    if (sourcePath && typeof data.path === 'string' && normalizePathKey(data.path) !== normalizePathKey(sourcePath)) {
      return null
    }
    if (!Array.isArray(data.peaks)) return null
    return {
      peaks: data.peaks.map((value) => Number(value || 0)),
      channelPeaks: Array.isArray(data.channelPeaks)
        ? data.channelPeaks
            .filter((channel) => Array.isArray(channel))
            .map((channel) => channel.map((value) => Number(value || 0)))
        : [],
    }
  } catch {
    return null
  }
}





export const hasPeaks = (p?: number[]) => p !== undefined && p.length > 0 && !(p.length === 1 && p[0] === -1)

export function hasRenderablePeaks(source: EditorSource) {
  return hasPeaks(source.peaks) && (source.channels < 2 || (source.channelPeaks?.length || 0) >= 2)
}

export function cachedPeaksMatchSource(source: EditorSource, peaks: LoadedWaveformPeaks) {
  return source.channels < 2 || peaks.channelPeaks.length >= 2
}

export function sourceToExportAsset(source: EditorSource) {
  return {
    id: source.id,
    path: source.path,
    name: source.name,
    duration: source.duration,
    sampleRate: source.sampleRate,
    channels: source.channels,
    peaksPath: source.peaksPath || null,
    peaks: [],
  }
}

export function runLimited<T>(items: T[], limit: number, runner: (item: T) => Promise<void>) {
  let index = 0
  const workers = Array.from({ length: Math.min(limit, items.length) }, async () => {
    while (index < items.length) {
      const item = items[index]
      index += 1
      await runner(item)
    }
  })
  return Promise.all(workers)
}

export function trackToExportTrack(track: EditorTrack, source?: EditorSource) {
  const clips = track.clips?.length
    ? track.clips.map((clip) => ({ ...clip }))
    : [{
        id: `clip_${track.id}`,
        assetId: track.sourceId,
        start: 0,
        offset: 0,
        duration: source?.duration || 0,
        volume: 1,
        fadeIn: track.fadeIn,
        fadeOut: track.fadeOut,
        muted: false,
        locked: false,
      }]
  return {
    id: track.id,
    sourceId: track.sourceId,
    name: track.name,
    type: track.role,
    volume: track.volume,
    pan: track.pan,
    muted: track.muted,
    solo: track.solo,
    clips,
  }
}

