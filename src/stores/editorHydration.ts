import type { Ref } from 'vue'
import { getAudioMetadata, generateWaveformPeaks } from '@/api/editor'
import {
  WAVEFORM_FULL_RESOLUTION, cachedPeaksMatchSource, hasPeaks,
  hasRenderablePeaks, loadPeaksFromCache, runLimited, type AudioMetadata,
} from '@/stores/editorSupport'
import type { EditorSession, EditorSource } from '@/types/editor'

type Deps = {
  session: Ref<EditorSession | null>
  scheduleSave: () => void
  saveProject: () => Promise<unknown>
}

/** Waveform/metadata hydration for editor sources. */
export function makeEditorHydration({ session, scheduleSave, saveProject }: Deps) {
  const pendingPeaks = new Map<string, Promise<EditorSource | null>>()

  async function getMetadata(path: string) {
    return getAudioMetadata({ path })
  }

  function hasUsableAudioMetadata(metadata: AudioMetadata | EditorSource | null | undefined) {
    return Boolean(metadata && [metadata.duration, metadata.sampleRate, metadata.channels]
      .every((value) => Number.isFinite(value) && value > 0))
  }

  async function ensurePeaks(sourceId: string) {
    if (!session.value) return null
    const source = session.value.sources.find((item) => item.id === sourceId)
    if (!source || source.missing || hasRenderablePeaks(source)) return source || null

    const cachedPeaks = await loadPeaksFromCache(source.peaksPath, source.path)
    if (cachedPeaks?.peaks.length && cachedPeaksMatchSource(source, cachedPeaks)) {
      source.peaks = cachedPeaks.peaks
      source.channelPeaks = cachedPeaks.channelPeaks
      return source
    }

    const pending = pendingPeaks.get(sourceId)
    if (pending) return pending
    const projectId = session.value.id
    const task = (async () => {
      try {
        const result = await generateWaveformPeaks({
          projectId,
          path: source.path,
          resolution: WAVEFORM_FULL_RESOLUTION,
        })
        if (!session.value || session.value.id !== projectId) return source
        const current = session.value.sources.find((item) => item.id === sourceId)
        if (!current) return source
        current.peaks = result.peaks?.length ? result.peaks : [-1]
        current.channelPeaks = result.channelPeaks || []
        current.peaksPath = result.peaksPath
        current.duration = Number(result.duration || current.duration)
        current.sampleRate = Number(result.sampleRate || current.sampleRate)
        current.channels = Number(result.channels || current.channels)
        return current
      } catch {
        return source
      } finally {
        pendingPeaks.delete(sourceId)
      }
    })()
    pendingPeaks.set(sourceId, task)
    return task
  }

  function ensurePeaksInBackground(sourceId: string) {
    void ensurePeaks(sourceId)
      .then(() => {
        if (!session.value) return
        scheduleSave()
      })
      .catch(() => {})
  }

  function hydrateSessionSourcesInBackground(projectId: string) {
    void hydrateSessionSources(projectId)
      .then((changed) => {
        if (!changed) return
        if (session.value?.id !== projectId) return
        void saveProject()
      })
      .catch(() => {})
  }

  async function hydrateSourceMetadataState(projectId = session.value?.id || '') {
    if (!session.value || !projectId || session.value.id !== projectId) return false

    let changed = false
    const sources = [...session.value.sources]

    await runLimited(sources, 4, async (source) => {
      if (!session.value || session.value.id !== projectId) return
      const before = {
        name: source.name,
        duration: source.duration,
        sampleRate: source.sampleRate,
        channels: source.channels,
        missing: Boolean(source.missing),
      }
      const metadata = await getMetadata(source.path).catch(() => null)
      if (!session.value || session.value.id !== projectId) return
      source.missing = !metadata
      if (metadata) {
        source.name = metadata.name || source.name
        source.duration = Number(metadata.duration || source.duration)
        source.sampleRate = Number(metadata.sampleRate || source.sampleRate)
        const peakChannels = source.channelPeaks?.filter(channel => channel.length).length || 0
        source.channels = Math.max(Number(metadata.channels || 0), peakChannels, Number(source.channels || 0))
      }
      if (
        source.name !== before.name
        || source.duration !== before.duration
        || source.sampleRate !== before.sampleRate
        || source.channels !== before.channels
        || Boolean(source.missing) !== before.missing
      ) {
        changed = true
      }
    })

    return changed
  }

  async function hydrateSessionSources(projectId = session.value?.id || '') {
    if (!session.value || !projectId || session.value.id !== projectId) return false

    let changed = false
    const sources = [...session.value.sources]

    await runLimited(sources, 2, async (source) => {
      if (!session.value || session.value.id !== projectId) return

      const before = {
        name: source.name,
        duration: source.duration,
        sampleRate: source.sampleRate,
        channels: source.channels,
        peaksPath: source.peaksPath || null,
        peaksCount: source.peaks?.length || 0,
        missing: Boolean(source.missing),
      }

      const metadata = await getMetadata(source.path).catch(() => null)
      if (!session.value || session.value.id !== projectId) return

      source.missing = !metadata
      if (source.missing) {
        source.duration = Number(source.duration || 0)
        source.sampleRate = Number(source.sampleRate || 0)
        source.channels = Number(source.channels || 0)
        source.peaks = Array.isArray(source.peaks) ? source.peaks : []
      } else if (!hasPeaks(source.peaks)) {
        await ensurePeaks(source.id)
      }

      if (metadata) {
        source.name = metadata.name || source.name
        source.duration = Number(metadata.duration || source.duration)
        source.sampleRate = Number(metadata.sampleRate || source.sampleRate)
        const peakChannels = source.channelPeaks?.filter(channel => channel.length).length || 0
        source.channels = Math.max(Number(metadata.channels || 0), peakChannels, Number(source.channels || 0))
      }

      if (
        source.name !== before.name
        || source.duration !== before.duration
        || source.sampleRate !== before.sampleRate
        || source.channels !== before.channels
        || (source.peaksPath || null) !== before.peaksPath
        || (source.peaks?.length || 0) !== before.peaksCount
        || Boolean(source.missing) !== before.missing
      ) {
        changed = true
      }
    })

    return changed
  }

  async function hydratePeaksFromCache(projectId = session.value?.id || '') {
    if (!session.value || !projectId || session.value.id !== projectId) return
    const sources = [...session.value.sources]
    await Promise.allSettled(sources.map(async (source) => {
      if (hasRenderablePeaks(source)) return
      const cachedPeaks = await loadPeaksFromCache(source.peaksPath, source.path)
      if (cachedPeaks?.peaks.length && cachedPeaksMatchSource(source, cachedPeaks) && session.value?.id === projectId) {
        source.peaks = cachedPeaks.peaks
        source.channelPeaks = cachedPeaks.channelPeaks
      }
    }))
  }

  return {
    getMetadata, hasUsableAudioMetadata, ensurePeaks, ensurePeaksInBackground,
    hydrateSessionSourcesInBackground, hydrateSourceMetadataState, hydrateSessionSources,
    hydratePeaksFromCache,
  }
}
