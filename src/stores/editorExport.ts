import { invoke } from '@tauri-apps/api/core'
import { listen, type UnlistenFn } from '@tauri-apps/api/event'
import type { Ref } from 'vue'
import i18n from '@/i18n'
import { startEditorMixExport } from '@/api/editor'
import { isTauriRuntime } from '@/utils/appStore'
import {
  assertPluginProjectSupported,
  pluginHostCapabilities,
} from '@/utils/pluginHost'
import { masterLimiterRenderRate, normalizeMasterLimiter } from '@/utils/masterLimiter'
import {
  neutralPluginRenderProject, pluginProcessingProject, projectPluginSlots, pluginProjectVersion,
} from '@/utils/editorPlugins'
import {
  NATIVE_RENDER_PROGRESS_WEIGHT, EditorExportCancelledError,
  sourceToExportAsset, trackToExportTrack,
  type EditorExportProgress, type EditorExportWorkerEvent,
  type ExportResult, type PendingEditorExport,
} from '@/stores/editorSupport'
import type {
  EditorExportFormat, EditorExportOptions, EditorSession,
} from '@/types/editor'

type Deps = {
  session: Ref<EditorSession | null>
  exporting: Ref<boolean>
  exportCancelling: Ref<boolean>
  exportProgress: Ref<EditorExportProgress | null>
  lastExport: Ref<ExportResult | null>
  lastError: Ref<string | null>
  exportFormat: Ref<EditorExportFormat>
  assertNoMissingSourcesInUse: (message: string) => void
  capturePluginState: (project: EditorSession, force?: boolean, recovery?: boolean) => Promise<unknown>
  studioLog: { warn: (message: string, error: unknown) => void }
}

/** Owns the editor export lifecycle: listener, native render and cancel handshake. */
export function makeEditorExportActions({
  session, exporting, exportCancelling, exportProgress, lastExport, lastError,
  exportFormat, assertNoMissingSourcesInUse, capturePluginState, studioLog,
}: Deps) {
  let pendingExport: PendingEditorExport | null = null
  let exportUnlisten: UnlistenFn | null = null
  let exportListenerPromise: Promise<void> | null = null
  let exportStartPromise: Promise<void> | null = null
  let exportCancelRequested = false
  let exportRenderController: AbortController | null = null
  let exportRequestSequence = 0
  let nativeRenderTaskId: string | null = null

  async function ensureEditorExportListener() {
    if (!isTauriRuntime() || exportUnlisten) return
    if (!exportListenerPromise) {
      exportListenerPromise = listen<EditorExportWorkerEvent>('pymss://worker-event', (event) => {
        const workerEvent = event.payload
        const pending = pendingExport
        const eventId = workerEvent.taskId || workerEvent.requestId
        if (workerEvent.type === 'editor_plugin_render_progress') {
          if (!nativeRenderTaskId || eventId !== nativeRenderTaskId || exportCancelRequested) return
          const payload = workerEvent.payload || {}
          const total = Number(payload.total)
          const completed = Number(payload.completed)
          if (!Number.isFinite(total) || total <= 0 || !Number.isFinite(completed) || completed < 0 || completed > total) return
          const next = completed / total * NATIVE_RENDER_PROGRESS_WEIGHT
          const previous = exportProgress.value
          if (previous && next < previous.completed / Math.max(1, previous.total) * 100) return
          const phase = payload.phase === 'preparing' ? 'preparing'
            : payload.phase === 'rendering' ? 'rendering' : 'finalizing'
          exportProgress.value = { phase, completed: next, total: 100, current: '' }
          return
        }
        if (!pending || eventId !== pending.id) return

        if (workerEvent.type === 'editor_export_progress') {
          const payload = workerEvent.payload || {}
          const total = Number(payload.total ?? 100)
          const completed = Number(payload.completed ?? 0)
          if (!Number.isFinite(total) || total <= 0 || !Number.isFinite(completed) || completed < 0 || completed > total) return
          const offset = pending.progressOffset || 0
          const nextCompleted = offset ? offset + completed / total * (100 - offset) : completed
          const nextTotal = offset ? 100 : total
          const previous = exportProgress.value
          if (previous && nextCompleted / nextTotal < previous.completed / Math.max(1, previous.total)) return
          exportProgress.value = {
            phase: String(payload.phase || 'preparing') as EditorExportProgress['phase'],
            completed: nextCompleted,
            total: nextTotal,
            current: String(payload.current || ''),
          }
          return
        }

        if (workerEvent.type === 'editor_mix_exported') {
          pendingExport = null
          pending.resolve(workerEvent.payload as ExportResult)
          return
        }

        if (workerEvent.type === 'task.cancelled') {
          pendingExport = null
          pending.reject(new EditorExportCancelledError())
          return
        }

        if (workerEvent.type === 'error') {
          if (workerEvent.payload?.recoverable === true) return
          pendingExport = null
          const message = workerEvent.payload?.code === 'EDITOR_EXPORT_PEAK_EXCEEDED'
            ? String(i18n.global.t('editor.exportPeakExceeded'))
            : String(workerEvent.payload?.message || 'Editor export failed')
          pending.reject(new Error(message))
        }
      }).then((unlisten) => {
        exportUnlisten = unlisten
      }).finally(() => {
        exportListenerPromise = null
      })
    }
    await exportListenerPromise
  }

  async function exportMix(options?: EditorExportFormat | EditorExportOptions) {
    if (!session.value) throw new Error('Editor session is not loaded')
    if (exporting.value) throw new Error('Editor export is already running')
    assertNoMissingSourcesInUse(String(i18n.global.t('editor.assetOfflineBlocked')))
    const normalized = typeof options === 'string'
      ? { format: options, audioParams: undefined }
      : (options || {})
    const fmt = normalized.format || exportFormat.value
    const taskId = `editor_export_${Date.now().toString(36)}_${++exportRequestSequence}_${Math.random().toString(36).slice(2, 8)}`
    const exportProject = session.value
    let snapshot = JSON.parse(JSON.stringify(exportProject)) as EditorSession
    const renderController = new AbortController()
    exportRenderController = renderController
    const pluginRenderRequired = projectPluginSlots(snapshot).some(slot => !slot.bypassed) || normalizeMasterLimiter(snapshot.masterLimiter).enabled
    let pluginTailLimited = false
    exporting.value = true
    exportCancelling.value = false
    exportCancelRequested = false
    exportProgress.value = { phase: 'preparing', completed: 0, total: 100, current: '' }
    lastError.value = null
    try {
      await ensureEditorExportListener()
      if (exportCancelRequested) throw new EditorExportCancelledError()
      const prepared = pluginRenderRequired
        ? await (async () => {
            const hostCapabilities = await pluginHostCapabilities()
            if (!hostCapabilities.enabled) throw new Error(String(i18n.global.t('editor.plugins.unavailable')))
            assertPluginProjectSupported(exportProject, hostCapabilities.formats || ['vst3'])
            if (projectPluginSlots(exportProject).some(slot => !slot.bypassed)) await capturePluginState(exportProject, true)
            if (exportCancelRequested) throw new EditorExportCancelledError()
            // Capture can wait while the project is edited. Freeze its complete
            // post-capture version instead of mixing fresh plugin states into an older mix.
            snapshot = JSON.parse(JSON.stringify(exportProject)) as EditorSession
            assertPluginProjectSupported(snapshot, hostCapabilities.formats || ['vst3'])
            nativeRenderTaskId = taskId
            exportProgress.value = { phase: 'preparing', completed: 0, total: 100, current: '' }
            try {
              const rendered = await invoke<{ path: string; duration: number; sampleRate: number; channels: number; tailLimited?: boolean }>('plugin_host_render', {
                project: pluginProcessingProject(snapshot), taskId,
                sampleRate: normalizeMasterLimiter(snapshot.masterLimiter).enabled
                  ? masterLimiterRenderRate(snapshot, normalized.audioParams?.sampleRate) : undefined,
              })
              exportProgress.value = { phase: 'finalizing', completed: NATIVE_RENDER_PROGRESS_WEIGHT, total: 100, current: '' }
              pluginTailLimited = rendered.tailLimited === true
              return { session: neutralPluginRenderProject(snapshot, rendered), sourceSampleRate: rendered.sampleRate }
            } finally {
              nativeRenderTaskId = null
            }
          })()
        : { session: snapshot, sourceSampleRate: undefined }
      exportRenderController = null
      if (exportCancelRequested) throw new EditorExportCancelledError()
      const project = prepared.session
      const completion = new Promise<ExportResult>((resolve, reject) => {
        pendingExport = {
          id: taskId,
          projectId: snapshot.id,
          exportDir: normalized.exportDir,
          progressOffset: prepared.session !== snapshot ? NATIVE_RENDER_PROGRESS_WEIGHT : 0,
          resolve,
          reject,
        }
      })
      exportStartPromise = startEditorMixExport({
        taskId,
        format: fmt,
        exportDir: normalized.exportDir,
        fileName: normalized.fileName,
        audioParams: normalized.audioParams || {},
        project: {
          version: pluginProjectVersion(project),
          id: project.id,
          name: project.name,
          masterVolume: project.masterVolume,
          masterPan: project.masterPan,
          sourceSampleRate: prepared.sourceSampleRate,
          assets: project.sources.map(sourceToExportAsset),
          tracks: project.tracks.map((track) => trackToExportTrack(track, project.sources.find(source => source.id === track.sourceId))),
        },
      }).then(() => undefined)
      await exportStartPromise
      exportStartPromise = null
      const result = await completion
      if (pluginTailLimited) result.pluginTailLimited = true
      lastExport.value = result
      return result
    } catch (error) {
      if (pendingExport?.id === taskId) pendingExport = null
      const failure = renderController.signal.aborted ? new EditorExportCancelledError() : error
      lastError.value = failure instanceof EditorExportCancelledError
        ? null
        : failure instanceof Error ? failure.message : String(failure)
      throw failure
    } finally {
      if (pluginRenderRequired) {
        await invoke('plugin_host_cleanup_render', { taskId }).catch(error => {
          studioLog.warn('Failed to clean up plugin render files', error)
        })
      }
      exporting.value = false
      exportCancelling.value = false
      exportProgress.value = null
      exportStartPromise = null
      exportCancelRequested = false
      exportRenderController = null
    }
  }

  async function cancelPendingExportTask(pending: PendingEditorExport | null) {
    if (!pending) return false
    return invoke<boolean>('cancel_editor_mix_export', {
      taskId: pending.id,
      projectId: pending.projectId,
      exportDir: pending.exportDir,
    })
  }

  async function cancelExport() {
    if (!exporting.value || exportCancelling.value) return false
    exportCancelling.value = true
    exportCancelRequested = true
    try {
      if (exportRenderController) {
        exportRenderController.abort()
        if (nativeRenderTaskId) await invoke('plugin_host_cancel_render', { taskId: nativeRenderTaskId })
        return true
      }
      if (exportStartPromise) await exportStartPromise
      if (!pendingExport) return true
      return cancelPendingExportTask(pendingExport)
    } catch (error) {
      exportCancelling.value = false
      throw error
    }
  }

  return { ensureEditorExportListener, exportMix, cancelExport }
}
