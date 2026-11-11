import { onScopeDispose, watch, type Ref } from 'vue'
import { invoke } from '@tauri-apps/api/core'
import i18n from '@/i18n'
import { toPersistedSession } from '@/stores/editorNormalize'
import { AUTO_SAVE_DELAY, type PersistedSession } from '@/stores/editorSupport'
import type { EditorPluginSlot, EditorSession } from '@/types/editor'
import {
  currentPluginSession, discardUnavailablePluginSession, observePluginSession,
  reloadPluginSession, snapshotPluginStates, unloadPluginSession, PluginHostDialogPendingError,
  type PluginSessionState,
} from '@/utils/pluginHost'
import { hasEditorPlugins, projectPluginSlots } from '@/utils/editorPlugins'
import { registerWindowCloseGuard, WindowClosePersistenceError } from '@/utils/windowCloseGuards'

type Deps = {
  session: Ref<EditorSession | null>
  saving: Ref<boolean>
  lastError: Ref<string | null>
  pluginStateDirty: Ref<boolean>
  pluginStateError: Ref<string | null>
  pluginStateUnavailable: Ref<boolean>
  setPluginChain: (trackId: string | null, chain: EditorPluginSlot[]) => void
}

/** Autosave queue, plugin-state capture and window-close persistence. */
export function makeEditorPersistence({
  session, saving, lastError, pluginStateDirty, pluginStateError, pluginStateUnavailable, setPluginChain,
}: Deps) {
  let pluginSaveTimer: ReturnType<typeof setTimeout> | null = null
  let saveTimer: ReturnType<typeof setTimeout> | null = null
  let saveDrainTask: Promise<EditorSession | null> | null = null
  const pendingProjectSaves = new Map<string, PersistedSession>()
  const suspendedProjectSaves = new Set<string>()
  let discardingWindowChanges = false
  let pendingDialogMessage: string | null = null

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

  async function saveProject() {
    if (!queueSessionSave() && !pendingProjectSaves.size) return null
    if (saveTimer) { clearTimeout(saveTimer); saveTimer = null }
    if (pluginSaveTimer) { clearTimeout(pluginSaveTimer); pluginSaveTimer = null }
    const host = currentPluginSession()
    if (host && host.project !== session.value && (host.dirty || host.error)) await releasePluginSession(host.project)
    if (session.value && hasEditorPlugins(session.value)) await capturePluginState(session.value, true)
    return drainPendingSaves()
  }

  function suspendProjectSave(projectId: string) {
    suspendedProjectSaves.add(projectId)
    pendingProjectSaves.delete(projectId)
  }

  function resumeProjectSave(projectId: string) {
    suspendedProjectSaves.delete(projectId)
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

  return {
    recoverPluginChain, capturePluginState, retryPluginStateSave, restoreSavedPluginSession,
    releasePluginSession, drainPendingSaves, scheduleSave, flushSave, saveProject,
    suspendProjectSave, resumeProjectSave,
  }
}
