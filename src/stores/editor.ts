import { defineStore } from 'pinia'
import { makeEditorTrackActions } from '@/stores/editorTrackActions'
import { makeEditorSourceActions } from '@/stores/editorSourceActions'
import { makeEditorExportActions } from '@/stores/editorExport'
import { makeEditorHydration } from '@/stores/editorHydration'
import { trackColor, duplicatedTrackColor, fileName, stripExt } from '@/stores/editorStems'
import { makeId, clamp, cloneTracks, normalizeClip, normalizeSession, toPersistedSession } from '@/stores/editorNormalize'
import { createLogger } from '@/utils/logger'
import { clonePluginChain, hasEditorPlugins, projectPluginSlots } from '@/utils/editorPlugins'
import { currentPluginSession, discardUnavailablePluginSession, observePluginSession, PluginHostDialogPendingError, reloadPluginSession, snapshotPluginStates, unloadPluginSession, type PluginSessionState } from '@/utils/pluginHost'
import { computed, onScopeDispose, ref, watch } from 'vue'
import { invoke } from '@tauri-apps/api/core'
import i18n, { getCurrentLocale } from '@/i18n'
import {
  overwriteClipsInRange,
  syncSingleClipFadeCompatibility,
  timelineDuration,
} from '@/utils/editorClips'
import { registerWindowCloseGuard, WindowClosePersistenceError } from '@/utils/windowCloseGuards'
import { isTauriRuntime } from '@/utils/appStore'
import type { SeparationTask, StemOutput } from '@/stores/task'
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
  EditorProjectCleanupResult, OrphanedEditorProject, PersistedSession, AudioMetadata,
  HistorySnapshot,
} from '@/stores/editorSupport'
import {
  AUTO_SAVE_DELAY, HISTORY_LIMIT,
  blankProjectName, localizedAutoTrackName, normalizePathKey,
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

  let pluginSaveTimer: ReturnType<typeof setTimeout> | null = null
  let saveTimer: ReturnType<typeof setTimeout> | null = null
  let saveDrainTask: Promise<EditorSession | null> | null = null
  const pendingProjectSaves = new Map<string, PersistedSession>()
  const suspendedProjectSaves = new Set<string>()
  let discardingWindowChanges = false

  function queueSessionSave() {
    if (!session.value || discardingWindowChanges) return null
    const project = toPersistedSession(session.value)
    if (suspendedProjectSaves.has(project.id)) return null
    pendingProjectSaves.set(project.id, project)
    return project.id
  }

  async function capturePluginState(project: EditorSession, force = false, recovery = false) {
    try {
      return await snapshotPluginStates(project, { force, recovery })
    } catch (cause) {
      const detail = cause instanceof Error ? cause.message : String(cause)
      const error = cause instanceof PluginHostDialogPendingError ? detail : String(i18n.global.t('editor.plugins.stateCaptureFailed', { detail }))
      if (session.value?.id === project.id || currentPluginSession()?.project === project) {
        pluginStateDirty.value = true
        pluginStateError.value = error
        if (!(cause instanceof PluginHostDialogPendingError)) lastError.value = error
      }
      if (cause instanceof PluginHostDialogPendingError) throw cause
      throw new Error(error)
    }
  }

  function schedulePluginStateSave(project: EditorSession, attempt = 0) {
    if (discardingWindowChanges) return
    if (pluginSaveTimer) clearTimeout(pluginSaveTimer)
    pluginSaveTimer = setTimeout(async () => {
      pluginSaveTimer = null
      if (session.value !== project) return
      try {
        await capturePluginState(project)
        if (discardingWindowChanges || session.value !== project) return
        queueSessionSave()
        await drainPendingSaves()
      } catch {
        // One automatic retry handles transient capture/storage failures; further retries are explicit.
        if (attempt === 0 && session.value === project) schedulePluginStateSave(project, 1)
      }
    }, attempt === 0 ? 900 : 2000)
  }

  let pendingDialogMessage: string | null = null
  const stopObservingPlugins = observePluginSession(host => {
    if (!host || host.project !== session.value) return
    pluginStateUnavailable.value = host.unavailable
    if (host.error) {
      pluginStateDirty.value = true
      pluginStateError.value = host.interactionRequired ? host.error : String(i18n.global.t('editor.plugins.stateCaptureFailed', { detail: host.error }))
      pendingDialogMessage = host.interactionRequired ? pluginStateError.value : null
      if (!host.interactionRequired) lastError.value = pluginStateError.value
      return
    }
    if (pendingDialogMessage) {
      if (pluginStateError.value === pendingDialogMessage) pluginStateError.value = null
      if (lastError.value === pendingDialogMessage) lastError.value = null
      pendingDialogMessage = null
    }
    if (host.dirty) {
      pluginStateDirty.value = true
      schedulePluginStateSave(host.project)
    }
  })
  watch(() => session.value?.id, () => {
    pendingDialogMessage = null
    if (pluginSaveTimer) clearTimeout(pluginSaveTimer)
    pluginSaveTimer = null
    pluginStateDirty.value = false
    pluginStateError.value = null
    pluginStateUnavailable.value = false
  })
  onScopeDispose(() => {
    stopObservingPlugins()
    if (pluginSaveTimer) clearTimeout(pluginSaveTimer)
  })

  async function retryPluginStateSave() { return saveProject() }

  async function restoreSavedPluginSession() {
    const project = session.value
    if (!project) return
    const persisted = await invoke<PersistedSession>('load_editor_project', { projectId: project.id })
    if (session.value !== project) throw new Error(String(i18n.global.t('editor.plugins.stateCaptureSuperseded')))
    const savedSlots = projectPluginSlots(persisted)
    for (const slot of projectPluginSlots(project)) {
      const saved = savedSlots.find(item => item.id === slot.id && item.path === slot.path && item.pluginId === slot.pluginId)
      if (saved?.state !== undefined) slot.state = saved.state
      else delete slot.state
    }
    await reloadPluginSession(project)
    await saveProject()
  }


  async function releasePluginSession(project: EditorSession) {
    if (currentPluginSession()?.project.id !== project.id) return
    if (pluginSaveTimer) clearTimeout(pluginSaveTimer)
    pluginSaveTimer = null
    await capturePluginState(project, true)
    pendingProjectSaves.set(project.id, toPersistedSession(project))
    await drainPendingSaves()
    await unloadPluginSession(project.id)
  }

  async function drainPendingSaves() {
    if (discardingWindowChanges) return null
    if (saveDrainTask) return saveDrainTask
    const task = (async () => {
      saving.value = true
      try {
        while (pendingProjectSaves.size) {
          if (discardingWindowChanges) return null
          const next = pendingProjectSaves.entries().next().value as [string, PersistedSession] | undefined
          if (!next) break
          const [projectId, project] = next
          pendingProjectSaves.delete(projectId)
          try {
            const target = session.value?.id === projectId ? session.value : project
            if (hasEditorPlugins(target)) await capturePluginState(target)
            if (discardingWindowChanges) return null
            const captured = toPersistedSession(target)
            const result = await invoke<PersistedSession>('save_editor_project', { project: captured })
            if (session.value?.id === projectId) {
              if (result.updatedAt) session.value.updatedAt = Number(result.updatedAt)
              const host = currentPluginSession()
              pluginStateDirty.value = Boolean(host?.project === session.value && host.dirty)
              pluginStateError.value = null
              lastError.value = null
              if (!pluginStateDirty.value && pluginSaveTimer) { clearTimeout(pluginSaveTimer); pluginSaveTimer = null }
            }
          } catch (error) {
            // Keep a newer snapshot if edits arrived while this save was in flight.
            if (!discardingWindowChanges && !pendingProjectSaves.has(projectId)) pendingProjectSaves.set(projectId, toPersistedSession(session.value?.id === projectId ? session.value : project))
            const detail = error instanceof Error ? error.message : String(error)
            if (!(error instanceof PluginHostDialogPendingError)) lastError.value = detail
            if (session.value?.id === projectId && hasEditorPlugins(project)) {
              pluginStateDirty.value = true
              pluginStateError.value = detail
            }
            throw error
          }
        }
        return session.value
      } finally {
        saving.value = false
      }
    })()
    saveDrainTask = task
    try {
      return await task
    } finally {
      if (saveDrainTask === task) saveDrainTask = null
    }
  }

  function scheduleSave() {
    if (!queueSessionSave()) return
    if (saveTimer) clearTimeout(saveTimer)
    saveTimer = setTimeout(() => {
      saveTimer = null
      void drainPendingSaves().catch(() => undefined)
    }, AUTO_SAVE_DELAY)
  }

  async function flushSave() {
    queueSessionSave()
    if (saveTimer) { clearTimeout(saveTimer); saveTimer = null }
    if (pluginSaveTimer) { clearTimeout(pluginSaveTimer); pluginSaveTimer = null }
    const host = currentPluginSession()
    if (host && host.project !== session.value && (host.dirty || host.error)) await releasePluginSession(host.project)
    if (session.value && hasEditorPlugins(session.value)) await capturePluginState(session.value, true)
    return drainPendingSaves()
  }

  // The store outlives the editor route. Keeping persistence here ensures a
  // delayed autosave is still flushed when the user closes from another page.
  onScopeDispose(registerWindowCloseGuard(async () => {
    const closingProject = session.value
    try {
      await flushSave()
      const host = currentPluginSession()
      if (host?.project === closingProject) await unloadPluginSession(host.project.id)
      else if (host) await releasePluginSession(host.project)
    } catch (cause) {
      throw new WindowClosePersistenceError(cause, async () => {
        if (session.value !== closingProject) throw new Error(String(i18n.global.t('editor.plugins.stateCaptureSuperseded')))
        discardingWindowChanges = true
        try {
          if (saveTimer) { clearTimeout(saveTimer); saveTimer = null }
          if (pluginSaveTimer) { clearTimeout(pluginSaveTimer); pluginSaveTimer = null }
          await saveDrainTask?.catch(() => undefined)
          if (session.value !== closingProject) throw new Error(String(i18n.global.t('editor.plugins.stateCaptureSuperseded')))
          const host = currentPluginSession()
          if (host && host.project !== closingProject) throw new Error(String(i18n.global.t('editor.plugins.stateCaptureSuperseded')))
          if (host) await unloadPluginSession(host.project.id, { discardUnsaved: true })
          pendingProjectSaves.clear()
        } finally {
          discardingWindowChanges = false
        }
      })
    }
  }, -100))

  onScopeDispose(registerWindowCloseGuard(async () => {
    if (exporting.value) await cancelExport()
  }, 100))

  const {
    selectTrack, selectClip, renameTrack, localizeDefaultTrackNames,
    toggleTrackFlag, setTrackVolume, setTrackPan, setClipFades, setClipTiming,
    setMasterVolume, setMasterLimiter, setMasterPan, removeTrack, removeClip, removeSource, setPluginChain,
  } = makeEditorTrackActions({
    session, selectedTrackId, selectedClipId, pushHistory, scheduleSave, interactionDepth, sourceMap,
  })

  async function requestProjectFromTask(task: SeparationTask) {
    return invoke<PersistedSession>('create_editor_project_from_task', {
      payload: {
        taskId: task.id,
        input: task.input,
        outputDir: task.output,
        outputs: task.outputs as StemOutput[],
      },
    })
  }

  async function createFromTask(task: SeparationTask) {
    loading.value = true
    lastError.value = null
    try {
      const result = await requestProjectFromTask(task)
      session.value = normalizeSession(result, localizedAutoTrackName)
      selectTrack(session.value.tracks[0]?.id || null)
      await hydratePeaksFromCache(session.value.id)
      const metadataChanged = await hydrateSourceMetadataState(session.value.id)
      clearHistory()
      const currentProjectId = session.value.id
      if (metadataChanged) await saveProject()
      hydrateSessionSourcesInBackground(currentProjectId)
      return session.value
    } catch (error) {
      lastError.value = error instanceof Error ? error.message : String(error)
      throw error
    } finally {
      loading.value = false
    }
  }

  async function openProjectWindow(projectId: string) {
    return invoke('open_editor_window', { payload: { projectId } })
  }

  async function ensureProjectForTask(task: SeparationTask, options?: { loadIntoSession?: boolean }) {
    if (options?.loadIntoSession !== false) return createFromTask(task)
    const result = await requestProjectFromTask(task)
    const normalized = normalizeSession(result, localizedAutoTrackName)
    return { id: normalized.id }
  }

  async function listProjects() {
    const result = await invoke<EditorProjectSummary[]>('list_editor_projects')
    projectSummaries.value = [...(result || [])].sort((a, b) => b.updatedAt - a.updatedAt)
    return projectSummaries.value
  }

  async function refreshProjects() {
    return listProjects()
  }

  // Keep this interface for the future import-first editor flow. The visible
  // blank-project entry is hidden until external audio import has clear UI.
  async function createBlankProject(name?: string) {
    const locale = getCurrentLocale()
    const result = await invoke<PersistedSession>('create_blank_editor_project', {
      payload: {
        name: name?.trim() || blankProjectName(),
        locale,
      },
    })
    const normalized = normalizeSession(result, localizedAutoTrackName)
    const summary: EditorProjectSummary = {
      id: normalized.id,
      name: normalized.name,
      sourceTaskId: normalized.sourceTaskId,
      sourceResultDir: normalized.sourceResultDir,
      createdAt: normalized.createdAt,
      updatedAt: normalized.updatedAt,
      type: normalized.sourceTaskId ? 'task' : 'blank',
    }
    projectSummaries.value = [summary, ...projectSummaries.value.filter((item) => item.id !== summary.id)]
      .sort((a, b) => b.updatedAt - a.updatedAt)
    return normalized
  }

  async function deleteProject(projectId: string) {
    // Serialize deletion after queued writes so a delayed autosave cannot
    // recreate the project after the backend removes it.
    await drainPendingSaves()
    suspendedProjectSaves.add(projectId)
    pendingProjectSaves.delete(projectId)
    let result: boolean
    try {
      result = await invoke<boolean>('delete_editor_project', { projectId })
    } catch (error) {
      suspendedProjectSaves.delete(projectId)
      if (session.value?.id === projectId) scheduleSave()
      throw error
    }
    projectSummaries.value = projectSummaries.value.filter((item) => item.id !== projectId)
    if (session.value?.id === projectId) {
      session.value = null
      selectedTrackId.value = null
      selectedClipId.value = null
      clearHistory()
    }
    suspendedProjectSaves.delete(projectId)
    return result
  }

  async function deleteProjectsForTasks(taskIds: string[]) {
    const uniqueTaskIds = [...new Set(taskIds.map(id => id.trim()).filter(Boolean))]
    const emptyResult = (): EditorProjectCleanupResult => ({
      deletedProjectIds: [],
      missingProjectIds: [],
      blockedProjectIds: [],
      failedProjectIds: [],
    })
    if (!uniqueTaskIds.length || !isTauriRuntime()) return emptyResult()
    const projectIds = await invoke<string[]>('editor_project_ids_for_tasks', { taskIds: uniqueTaskIds })

    await drainPendingSaves()
    projectIds.forEach((projectId) => {
      suspendedProjectSaves.add(projectId)
      pendingProjectSaves.delete(projectId)
    })
    try {
      const result = await invoke<EditorProjectCleanupResult>('delete_editor_projects_for_tasks', {
        taskIds: uniqueTaskIds,
      })
      const removedProjectIds = new Set([
        ...(result.deletedProjectIds || []),
        ...(result.missingProjectIds || []),
      ])
      projectSummaries.value = projectSummaries.value.filter(item => !removedProjectIds.has(item.id))
      if (session.value && removedProjectIds.has(session.value.id)) clearSession()
      return {
        deletedProjectIds: result.deletedProjectIds || [],
        missingProjectIds: result.missingProjectIds || [],
        blockedProjectIds: result.blockedProjectIds || [],
        failedProjectIds: result.failedProjectIds || [],
      }
    } finally {
      projectIds.forEach(projectId => suspendedProjectSaves.delete(projectId))
    }
  }

  async function listOpenProjectsForTasks(taskIds: string[]) {
    const uniqueTaskIds = [...new Set(taskIds.map(id => id.trim()).filter(Boolean))]
    if (!uniqueTaskIds.length || !isTauriRuntime()) return []
    const backendOpenProjectIds = await invoke<string[]>('list_open_editor_projects_for_tasks', {
      taskIds: uniqueTaskIds,
    })
    return [...new Set(backendOpenProjectIds || [])]
  }

  async function listOrphanedProjects() {
    if (!isTauriRuntime()) return [] as OrphanedEditorProject[]
    return invoke<OrphanedEditorProject[]>('list_orphaned_editor_projects')
  }

  async function deleteOrphanedProjects() {
    const emptyResult = (): EditorProjectCleanupResult => ({
      deletedProjectIds: [],
      missingProjectIds: [],
      blockedProjectIds: [],
      failedProjectIds: [],
    })
    if (!isTauriRuntime()) return emptyResult()
    const orphanedProjects = await listOrphanedProjects()
    const projectIds = orphanedProjects.map(project => project.projectId)
    if (!projectIds.length) return emptyResult()
    await drainPendingSaves()
    projectIds.forEach((projectId) => {
      suspendedProjectSaves.add(projectId)
      pendingProjectSaves.delete(projectId)
    })
    try {
      const result = await invoke<EditorProjectCleanupResult>('delete_orphaned_editor_projects', { projectIds })
      const removedProjectIds = new Set([
        ...(result.deletedProjectIds || []),
        ...(result.missingProjectIds || []),
      ])
      projectSummaries.value = projectSummaries.value.filter(item => !removedProjectIds.has(item.id))
      if (session.value && removedProjectIds.has(session.value.id)) clearSession()
      return {
        deletedProjectIds: result.deletedProjectIds || [],
        missingProjectIds: result.missingProjectIds || [],
        blockedProjectIds: result.blockedProjectIds || [],
        failedProjectIds: result.failedProjectIds || [],
      }
    } finally {
      projectIds.forEach(projectId => suspendedProjectSaves.delete(projectId))
    }
  }

  function clearSession() {
    session.value = null
    selectedTrackId.value = null
    selectedClipId.value = null
    clearHistory()
  }

  async function loadProject(projectId: string) {
    loading.value = true
    lastError.value = null
    try {
      const result = await invoke<PersistedSession>('load_editor_project', { projectId })
      session.value = normalizeSession(result, localizedAutoTrackName)
      await hydratePeaksFromCache(session.value.id)
      const metadataChanged = await hydrateSourceMetadataState(session.value.id)
      selectTrack(session.value.tracks[0]?.id || null)
      clearHistory()
      const currentProjectId = session.value.id
      if (metadataChanged) await saveProject()
      hydrateSessionSourcesInBackground(currentProjectId)
      return session.value
    } catch (error) {
      lastError.value = error instanceof Error ? error.message : String(error)
      throw error
    } finally {
      loading.value = false
    }
  }

  async function saveProject() {
    if (!queueSessionSave() && !pendingProjectSaves.size) return null
    if (saveTimer) { clearTimeout(saveTimer); saveTimer = null }
    if (pluginSaveTimer) { clearTimeout(pluginSaveTimer); pluginSaveTimer = null }
    const host = currentPluginSession()
    if (host && host.project !== session.value && (host.dirty || host.error)) await releasePluginSession(host.project)
    if (session.value && hasEditorPlugins(session.value)) await capturePluginState(session.value, true)
    return drainPendingSaves()
  }

  const {
    getMetadata, hasUsableAudioMetadata, ensurePeaks, ensurePeaksInBackground,
    hydrateSessionSourcesInBackground, hydrateSourceMetadataState, hydrateSessionSources,
    hydratePeaksFromCache,
  } = makeEditorHydration({ session, scheduleSave, saveProject })

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

  async function recoverPluginChain(options: {
    project: EditorSession; trackId: string | null; chain: EditorPluginSlot[];
    host: PluginSessionState; sessionId: string; signature: string;
  }) {
    const { project, trackId, host, sessionId, signature } = options
    const validate = () => {
      const target = trackId === null ? project : project.tracks.find(track => track.id === trackId)
      if (session.value !== project || !target || JSON.stringify(target.pluginChain || []) !== signature) {
        throw new Error(String(i18n.global.t('editor.plugins.stateCaptureSuperseded')))
      }
    }
    const clearTimers = () => {
      if (saveTimer) { clearTimeout(saveTimer); saveTimer = null }
      if (pluginSaveTimer) { clearTimeout(pluginSaveTimer); pluginSaveTimer = null }
    }
    validate()
    const resumeProjectSave = saveTimer !== null || pendingProjectSaves.has(project.id)
    const resumePluginSave = pluginSaveTimer !== null
    clearTimers()
    try {
      if (saveDrainTask) await saveDrainTask.catch(() => undefined)
      validate()
      await discardUnavailablePluginSession(project, host, sessionId)
      validate()
      clearTimers()
      const previousError = pluginStateError.value
      setPluginChain(trackId, options.chain)
      pluginStateDirty.value = false
      pluginStateError.value = null
      pluginStateUnavailable.value = false
      if (lastError.value === previousError) lastError.value = null
      await flushSave()
    } catch (cause) {
      // A rejected recovery must not cancel edits that were already waiting for autosave.
      if (!discardingWindowChanges && session.value === project) {
        if (saveTimer === null && (resumeProjectSave || pendingProjectSaves.has(project.id))) scheduleSave()
        if (pluginSaveTimer === null && resumePluginSave) schedulePluginStateSave(project)
      }
      throw cause
    }
  }

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
