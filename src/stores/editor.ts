import { defineStore } from 'pinia'
import { makeEditorTrackActions } from '@/stores/editorTrackActions'
import { makeEditorSourceActions } from '@/stores/editorSourceActions'
import { makeEditorExportActions } from '@/stores/editorExport'
import { makeEditorHydration } from '@/stores/editorHydration'
import { makeEditorProjects } from '@/stores/editorProjects'
import { makeEditorPersistence } from '@/stores/editorPersistence'
import { trackColor, duplicatedTrackColor, fileName, stripExt } from '@/stores/editorStems'
import { makeId, clamp, cloneTracks, normalizeClip } from '@/stores/editorNormalize'
import { createLogger } from '@/utils/logger'
import { clonePluginChain } from '@/utils/editorPlugins'
import { computed, onScopeDispose, ref, watch } from 'vue'
import { invoke } from '@tauri-apps/api/core'
import i18n from '@/i18n'
import {
  overwriteClipsInRange,
  syncSingleClipFadeCompatibility,
  timelineDuration,
} from '@/utils/editorClips'
import { registerWindowCloseGuard } from '@/utils/windowCloseGuards'
import type {
  EditorPluginSlot,
  EditorExportFormat,
  EditorProjectSummary,
  EditorSession,
  EditorSource,
  EditorTrack,
  EditorAssetTreeNode,
} from '@/types/editor'

const studioLog = createLogger('ui.stores.editor')

export type {
  EditorExportFormat,
  EditorProjectSummary,
  EditorSession,
  EditorSource,
  EditorSourceRole,
  EditorTrack,
  EditorAssetTreeNode,
} from '@/types/editor'

import type {
  ExportResult, ImportEditorAssetsResult, ScanAudioPathsResult,
  EditorExportProgress,
  AudioMetadata,
  HistorySnapshot,
} from '@/stores/editorSupport'
import {
  HISTORY_LIMIT,
  localizedAutoTrackName, normalizePathKey,
  sourceDisplayGroup,
} from '@/stores/editorSupport'
export type {
  OrphanedEditorProject, EditorExportProgress, PersistedSession,
  HistorySnapshot, ScanAudioPathsResult, LinkedEditorAsset, ImportEditorAssetsResult,
  EditorProjectCleanupResult, RelinkEditorSourcesResult, AudioMetadata,
  WaveformPeaks, LoadedWaveformPeaks, ExportResult, EditorExportWorkerEvent,
  PendingEditorExport,
} from '@/stores/editorSupport'
export { EditorExportCancelledError, isDefaultBlankProjectName } from '@/stores/editorSupport'

export const useEditorStore = defineStore('editor', () => {
  const session = ref<EditorSession | null>(null)
  const loading = ref(false)
  const saving = ref(false)
  const exporting = ref(false)
  const exportCancelling = ref(false)
  const exportProgress = ref<EditorExportProgress | null>(null)
  const lastExport = ref<ExportResult | null>(null)
  const lastError = ref<string | null>(null)
  const pluginStateDirty = ref(false)
  const pluginStateError = ref<string | null>(null)
  const pluginStateUnavailable = ref(false)
  const selectedTrackId = ref<string | null>(null)
  const selectedClipId = ref<string | null>(null)
  const pixelsPerSecond = ref(96)
  const exportFormat = ref<EditorExportFormat>('wav')
  const projectSummaries = ref<EditorProjectSummary[]>([])

  const undoStack = ref<HistorySnapshot[]>([])
  const redoStack = ref<HistorySnapshot[]>([])
  const interactionDepth = ref(0)
                
  const canUndo = computed(() => undoStack.value.length > 0)
  const canRedo = computed(() => redoStack.value.length > 0)
  const masterVolume = computed(() => session.value?.masterVolume ?? 1)
  const masterPan = computed(() => session.value?.masterPan ?? 0)

  const sourceMap = computed(() => {
    const map = new Map<string, EditorSource>()
    session.value?.sources.forEach((source) => map.set(source.id, source))
    return map
  })

  const selectedTrack = computed(() => {
    if (!session.value || !selectedTrackId.value) return null
    return session.value.tracks.find((track) => track.id === selectedTrackId.value) || null
  })

  const selectedClip = computed(() => {
    const track = selectedTrack.value
    if (!track || !selectedClipId.value) return null
    return track.clips?.find((clip) => clip.id === selectedClipId.value) || null
  })

  const selectedSource = computed(() => {
    const track = selectedTrack.value
    if (!track) return null
    return sourceMap.value.get(selectedClip.value?.assetId || '')
      || sourceMap.value.get(track.sourceId)
      || sourceMap.value.get(track.clips?.[track.clips.length - 1]?.assetId || '')
      || null
  })

  const stemTracks = computed(() => session.value?.tracks.filter((track) => track.role === 'stem') || [])
  const referenceTracks = computed(() => session.value?.tracks.filter((track) => track.role === 'reference') || [])
  const assetTree = computed<EditorAssetTreeNode[]>(() => {
    if (!session.value) return []
    const rootName = String(i18n.global.t('editor.assets'))
    const root: EditorAssetTreeNode = {
      key: '__root__',
      name: rootName,
      path: '',
      expanded: true,
      children: [],
      assets: [],
    }
    const folderMap = new Map<string, EditorAssetTreeNode>([[root.key, root]])

    const ensureFolder = (parts: string[]) => {
      let current = root
      let currentPath = ''
      for (const part of parts) {
        if (!part) continue
        currentPath = currentPath ? `${currentPath}/${part}` : part
        const key = `folder:${currentPath.toLowerCase()}`
        let next = folderMap.get(key)
        if (!next) {
          next = {
            key,
            name: part,
            path: currentPath,
            expanded: currentPath.split('/').length <= 1,
            children: [],
            assets: [],
          }
          folderMap.set(key, next)
          current.children.push(next)
        }
        current = next
      }
      return current
    }

    for (const source of session.value.sources) {
      const groupName = sourceDisplayGroup(source)
      const relative = String(source.relativePath || '').replace(/\\/g, '/')
      const parts = [groupName, ...relative.split('/').slice(0, -1).filter(Boolean)]
      ensureFolder(parts).assets.push(source)
    }

    const sortTree = (node: EditorAssetTreeNode) => {
      node.children.sort((a, b) => a.name.localeCompare(b.name))
      node.assets.sort((a, b) => a.name.localeCompare(b.name))
      node.children.forEach(sortTree)
    }
    sortTree(root)
    return root.children.length ? root.children : [{ ...root, name: rootName }]
  })

  const duration = computed(() => {
    return session.value ? timelineDuration(session.value.tracks, sourceMap.value) : 0
  })

  function snapshot(): HistorySnapshot | null {
    if (!session.value) return null
    return {
      tracks: cloneTracks(session.value.tracks),
      masterVolume: session.value.masterVolume,
      masterPan: session.value.masterPan,
      masterLimiter: session.value.masterLimiter ? { ...session.value.masterLimiter } : undefined,
      pluginChain: clonePluginChain(session.value.pluginChain),
      selectedTrackId: selectedTrackId.value,
      selectedClipId: selectedClipId.value,
    }
  }

  function applySnapshot(next: HistorySnapshot) {
    if (!session.value) return
    session.value.tracks = cloneTracks(next.tracks)
    session.value.masterVolume = next.masterVolume
    session.value.masterPan = next.masterPan
    session.value.masterLimiter = next.masterLimiter ? { ...next.masterLimiter } : undefined
    session.value.pluginChain = clonePluginChain(next.pluginChain)
    selectedTrackId.value = next.selectedTrackId
    selectedClipId.value = next.selectedClipId
  }

  function pushHistory() {
    if (interactionDepth.value > 0) return
    const snap = snapshot()
    if (!snap) return
    undoStack.value.push(snap)
    if (undoStack.value.length > HISTORY_LIMIT) undoStack.value.shift()
    redoStack.value = []
  }

  function undo() {
    if (!session.value || !undoStack.value.length) return
    const current = snapshot()
    const previous = undoStack.value.pop()!
    if (current) redoStack.value.push(current)
    applySnapshot(previous)
    scheduleSave()
  }

  function redo() {
    if (!session.value || !redoStack.value.length) return
    const current = snapshot()
    const next = redoStack.value.pop()!
    if (current) undoStack.value.push(current)
    applySnapshot(next)
    scheduleSave()
  }

  function beginInteraction() {
    if (interactionDepth.value === 0) pushHistory()
    interactionDepth.value += 1
  }

  function commitInteraction() {
    if (interactionDepth.value > 0) interactionDepth.value -= 1
    if (interactionDepth.value === 0) scheduleSave()
  }

  function clearHistory() {
    undoStack.value = []
    redoStack.value = []
    interactionDepth.value = 0
  }

  onScopeDispose(registerWindowCloseGuard(async () => {
    if (exporting.value) await cancelExport()
  }, 100))

  function applyPluginChain(trackId: string | null, chain: EditorPluginSlot[]) {
    setPluginChain(trackId, chain)
  }

  const {
    recoverPluginChain, capturePluginState, retryPluginStateSave, restoreSavedPluginSession,
    releasePluginSession, drainPendingSaves, scheduleSave, flushSave, saveProject,
    suspendProjectSave, resumeProjectSave,
  } = makeEditorPersistence({
    session, saving, lastError, pluginStateDirty, pluginStateError, pluginStateUnavailable, setPluginChain: applyPluginChain,
  })

  const {
    selectTrack, selectClip, renameTrack, localizeDefaultTrackNames,
    toggleTrackFlag, setTrackVolume, setTrackPan, setClipFades, setClipTiming,
    setMasterVolume, setMasterLimiter, setMasterPan, removeTrack, removeClip, removeSource, setPluginChain,
  } = makeEditorTrackActions({
    session, selectedTrackId, selectedClipId, pushHistory, scheduleSave, interactionDepth, sourceMap,
  })

  const {
    getMetadata, hasUsableAudioMetadata, ensurePeaks, ensurePeaksInBackground,
    hydrateSessionSourcesInBackground, hydrateSourceMetadataState, hydrateSessionSources,
    hydratePeaksFromCache,
  } = makeEditorHydration({ session, scheduleSave, saveProject })

  const {
    clearSession, createFromTask, openProjectWindow,
    ensureProjectForTask, listProjects, refreshProjects, createBlankProject,
    deleteProject, deleteProjectsForTasks, listOpenProjectsForTasks,
    listOrphanedProjects, deleteOrphanedProjects, loadProject,
  } = makeEditorProjects({
    session, loading, lastError, projectSummaries, selectedTrackId, selectedClipId,
    invoke, clearHistory, selectTrack, localizedAutoTrackName,
    hydratePeaksFromCache, hydrateSourceMetadataState, hydrateSessionSourcesInBackground,
    saveProject, drainPendingSaves, scheduleSave,
    suspendProjectSave, resumeProjectSave,
  })

  async function addReferenceSourceByPath(path: string) {
    if (!session.value) throw new Error('Editor session is not loaded')
    const source = await ensureSourceByPath(path, {
      originKind: 'external',
      originRoot: path.replace(/[/\\][^/\\]+$/, '') || null,
      relativePath: fileName(path),
    })
    addTrackFromSourceId(source.id)
    return source
  }

  async function ensureSourceByPath(path: string, options?: Partial<EditorSource>) {
    if (!session.value) throw new Error('Editor session is not loaded')
    const targetSession = session.value
    const normalized = normalizePathKey(path)
    const existing = session.value.sources.find((source) => normalizePathKey(source.path) === normalized)
    if (existing) return existing

    const metadata = await getMetadata(path)
    if (session.value !== targetSession) throw new Error('Editor project changed during asset import')
    if (!hasUsableAudioMetadata(metadata)) throw new Error(String(i18n.global.t('editor.assetMetadataInvalid')))
    const concurrentSource = targetSession.sources.find((source) => normalizePathKey(source.path) === normalized)
    if (concurrentSource) return concurrentSource
    const source: EditorSource = {
      id: makeId('source'),
      role: 'reference',
      stemKey: null,
      path,
      name: metadata.name || fileName(path),
      duration: Number(metadata.duration || 0),
      sampleRate: Number(metadata.sampleRate || 0),
      channels: Number(metadata.channels || 0),
      peaksPath: null,
      peaks: [],
      originKind: options?.originKind || 'external',
      originRoot: options?.originRoot ? String(options.originRoot) : null,
      relativePath: options?.relativePath ? String(options.relativePath) : fileName(path),
      missing: !metadata || Boolean(options?.missing),
    }
    session.value.sources.push(source)
    if (!source.missing) ensurePeaksInBackground(source.id)
    scheduleSave()
    return source
  }

  function addTrackFromSourceId(sourceId: string) {
    if (!session.value) throw new Error('Editor session is not loaded')
    const source = session.value.sources.find((item) => item.id === sourceId)
    if (!source) throw new Error('Source asset not found')

    pushHistory()
    const track: EditorTrack = {
      id: makeId('track'),
      sourceId: source.id,
      role: 'reference',
      name: stripExt(source.name),
      color: duplicatedTrackColor(source),
      volume: 1,
      pan: 0,
      muted: false,
      solo: false,
      fadeIn: 0,
      fadeOut: 0,
      type: 'reference',
      clips: [normalizeClip({
        id: makeId('clip'),
        assetId: source.id,
        duration: source.duration,
      }, source)],
    }
    session.value.tracks.push(track)
    selectTrack(track.id)
    scheduleSave()
    return track
  }

  function addRecordingTrack() {
    if (!session.value) throw new Error('Editor session is not loaded')
    pushHistory()
    const index = session.value.tracks.filter((track) => track.role === 'recording').length + 1
    const track: EditorTrack = {
      id: makeId('track'),
      sourceId: '',
      role: 'recording',
      type: 'recording',
      name: String(i18n.global.t('editor.recordingTrackName', { index })),
      autoName: true,
      color: trackColor('recording'),
      volume: 1,
      pan: 0,
      muted: false,
      solo: false,
      fadeIn: 0,
      fadeOut: 0,
      clips: [],
    }
    session.value.tracks.push(track)
    selectTrack(track.id)
    scheduleSave()
    return track
  }

  async function addRecordingClip(
    trackId: string,
    recording: AudioMetadata,
    start: number,
  ) {
    if (!session.value) throw new Error('Editor session is not loaded')
    pushHistory()
    let track = session.value.tracks.find((item) => item.id === trackId)
    if (!track) {
      const index = session.value.tracks.filter((item) => item.role === 'recording').length + 1
      track = {
        id: makeId('track'),
        sourceId: '',
        role: 'recording',
        type: 'recording',
        name: String(i18n.global.t('editor.recordingTrackName', { index })),
        autoName: true,
        color: trackColor('recording'),
        volume: 1,
        pan: 0,
        muted: false,
        solo: false,
        fadeIn: 0,
        fadeOut: 0,
        clips: [],
      }
      session.value.tracks.push(track)
    }
    const source: EditorSource = {
      id: makeId('source'),
      role: 'recording',
      stemKey: null,
      path: recording.path,
      name: recording.name || fileName(recording.path),
      duration: Math.max(0, Number(recording.duration || 0)),
      sampleRate: Math.max(0, Number(recording.sampleRate || 0)),
      channels: Math.max(1, Number(recording.channels || 1)),
      peaksPath: null,
      peaks: [],
      channelPeaks: [],
      originKind: 'recording',
      relativePath: `recordings/${recording.name || fileName(recording.path)}`,
      missing: false,
    }
    session.value.sources.push(source)
    track.role = 'recording'
    track.type = 'recording'
    track.sourceId = source.id
    const clip = normalizeClip({
      id: makeId('clip'),
      assetId: source.id,
      start: Math.max(0, Number(start || 0)),
      duration: source.duration,
    }, source)
    const currentClips = track.clips || []
    track.clips = [
      ...overwriteClipsInRange(currentClips, clip.start, clip.duration, () => makeId('clip')),
      clip,
    ].sort((left, right) => left.start - right.start)
    syncSingleClipFadeCompatibility(track)
    selectClip(track.id, clip.id)
    scheduleSave()
    ensurePeaksInBackground(source.id)
    return source
  }

  async function scanAssets(paths: string[], options?: { addTracks?: boolean }) {
    if (!session.value) throw new Error('Editor session is not loaded')
    const targetSession = session.value
    const imported = await invoke<ImportEditorAssetsResult>('import_editor_assets', {
      projectId: targetSession.id,
      paths,
    })
    if (session.value !== targetSession) throw new Error('Editor project changed during asset import')
    const sourcePaths = new Map<string, string>()
    const warnings = [...(imported.warnings || [])]
    for (const asset of imported.files || []) {
      if (session.value !== targetSession) throw new Error('Editor project changed during asset import')
      try {
        const source = await ensureSourceByPath(asset.path, {
          originKind: asset.originKind,
          originRoot: asset.originRoot || null,
          relativePath: asset.relativePath || fileName(asset.path),
          missing: asset.missing,
        })
        if (source.missing || !hasUsableAudioMetadata(source)) {
          throw new Error(String(i18n.global.t('editor.assetMetadataInvalid')))
        }
        if (!sourcePaths.has(source.id)) sourcePaths.set(source.id, asset.path)
      } catch (error) {
        if (session.value !== targetSession) throw error
        const detail = error instanceof Error ? error.message : String(error)
        warnings.push(String(i18n.global.t('editor.assetImportFailed', { name: fileName(asset.path), detail })))
      }
    }
    // Metadata can finish while a slider or clip is being dragged. Keep the
    // imported tracks in their own undo action after that interaction ends.
    while (options?.addTracks && sourcePaths.size && interactionDepth.value > 0 && session.value === targetSession) {
      await new Promise<void>((resolve) => {
        const stop = watch([interactionDepth, session], () => {
          if (interactionDepth.value > 0 && session.value === targetSession) return
          stop()
          resolve()
        }, { flush: 'sync' })
      })
    }
    if (session.value !== targetSession) throw new Error('Editor project changed during asset import')
    const currentSources = new Map(targetSession.sources.map((source) => [source.id, source]))
    for (const [sourceId, path] of sourcePaths) {
      const source = currentSources.get(sourceId)
      if (source && !source.missing && hasUsableAudioMetadata(source)) continue
      sourcePaths.delete(sourceId)
      const detail = String(i18n.global.t(source ? 'editor.assetMetadataInvalid' : 'editor.assetRemovedDuringImport'))
      warnings.push(String(i18n.global.t('editor.assetImportFailed', { name: fileName(path), detail })))
    }
    if (options?.addTracks && sourcePaths.size) {
      beginInteraction()
      try {
        for (const sourceId of sourcePaths.keys()) addTrackFromSourceId(sourceId)
      } finally {
        commitInteraction()
      }
    }
    await saveProject()
    return {
      files: [...sourcePaths.values()],
      warnings,
    } satisfies ScanAudioPathsResult
  }

  const {
    missingSources, missingSourcesInUse, assertNoMissingSourcesInUse,
    pickRelinkFile, relinkSource, relinkMissingSources,
  } = makeEditorSourceActions({
    session, invoke, hydratePeaksFromCache, hydrateSessionSourcesInBackground,
  })

  const { exportMix, cancelExport } = makeEditorExportActions({
    session, exporting, exportCancelling, exportProgress, lastExport, lastError, exportFormat,
    assertNoMissingSourcesInUse, capturePluginState, studioLog,
  })

  function setZoom(value: number) {
    pixelsPerSecond.value = clamp(Number(value), 4, 240)
  }

  function zoomIn() {
    setZoom(pixelsPerSecond.value + 18)
  }

  function zoomOut() {
    setZoom(pixelsPerSecond.value - 18)
  }

  return {
    session,
    setPluginChain,
    recoverPluginChain,
    pluginStateDirty,
    pluginStateError,
    pluginStateUnavailable,
    restoreSavedPluginSession,
    capturePluginState,
    retryPluginStateSave,
    releasePluginSession,
    loading,
    saving,
    exporting,
    exportCancelling,
    exportProgress,
    lastExport,
    lastError,
    projectSummaries,
    selectedTrackId,
    selectedClipId,
    exportFormat,
    selectedTrack,
    selectedClip,
    selectedSource,
    sourceMap,
    stemTracks,
    referenceTracks,
    assetTree,
    pixelsPerSecond,
    masterVolume,
    masterPan,
    canUndo,
    canRedo,
    duration,
    selectTrack,
    selectClip,
    undo,
    redo,
    beginInteraction,
    commitInteraction,
    clearHistory,
    scheduleSave,
    flushSave,
    createFromTask,
    ensureProjectForTask,
    listProjects,
    refreshProjects,
    createBlankProject,
    deleteProject,
    deleteProjectsForTasks,
    listOpenProjectsForTasks,
    listOrphanedProjects,
    deleteOrphanedProjects,
    clearSession,
    openProjectWindow,
    loadProject,
    saveProject,
    getMetadata,
    ensurePeaks,
    ensurePeaksInBackground,
    hydratePeaksFromCache,
    hydrateSessionSources,
    hydrateSessionSourcesInBackground,
    ensureSourceByPath,
    addReferenceSourceByPath,
    addTrackFromSourceId,
    addRecordingTrack,
    addRecordingClip,
    scanAssets,
    relinkSource,
    relinkMissingSources,
    pickRelinkFile,
    missingSources,
    missingSourcesInUse,
    assertNoMissingSourcesInUse,
    renameTrack,
    localizeDefaultTrackNames,
    toggleTrackFlag,
    setTrackVolume,
    setTrackPan,
    setClipFades,
    setClipTiming,
    setMasterVolume,
    setMasterPan,
    setMasterLimiter,
    removeTrack,
    removeClip,
    removeSource,
    exportMix,
    cancelExport,
    setZoom,
    zoomIn,
    zoomOut,
  }
})
