import { computed, type ComputedRef, type Ref } from 'vue'
import { clamp } from '@/stores/editorNormalize'
import { syncSingleClipFadeCompatibility } from '@/utils/editorClips'
import { normalizeMasterLimiter } from '@/utils/masterLimiter'
import { clonePluginChain } from '@/utils/editorPlugins'
import { localizedAutoTrackName } from '@/stores/editorSupport'
import type { EditorClip, EditorMasterLimiter, EditorPluginSlot, EditorSession, EditorSource } from '@/types/editor'

type Deps = {
  session: Ref<EditorSession | null>
  selectedTrackId: Ref<string | null>
  selectedClipId: Ref<string | null>
  pushHistory: () => void
  scheduleSave: () => void
  interactionDepth: Ref<number>
  sourceMap: ComputedRef<Map<string, EditorSource>>
}

export function makeEditorTrackActions({
  session, selectedTrackId, selectedClipId, pushHistory, scheduleSave, interactionDepth, sourceMap,
}: Deps) {
  const selectedTrack = computed(() => session.value?.tracks.find((track) => track.id === selectedTrackId.value) || null)
  function selectTrack(trackId: string | null) {
    selectedTrackId.value = trackId
    const track = session.value?.tracks.find((item) => item.id === trackId)
    if (!track?.clips?.some((clip) => clip.id === selectedClipId.value)) {
      selectedClipId.value = track?.clips?.[0]?.id || null
    }
  }

  function selectClip(trackId: string, clipId: string) {
    const track = session.value?.tracks.find((item) => item.id === trackId)
    if (!track?.clips?.some((clip) => clip.id === clipId)) return
    selectedTrackId.value = trackId
    selectedClipId.value = clipId
  }

  function renameTrack(trackId: string, name: string, commit = true) {
    const track = session.value?.tracks.find((item) => item.id === trackId)
    if (!track) return
    const currentName = track.name
    const trimmedName = name.trim()
    const resolvedName = trimmedName || currentName

    if (!commit) {
      if (resolvedName === currentName) return
      track.name = resolvedName
      return
    }

    if (resolvedName === currentName) {
      scheduleSave()
      return
    }

    pushHistory()
    track.name = resolvedName
    track.autoName = false
    scheduleSave()
  }

  function localizeDefaultTrackNames() {
    if (!session.value) return
    for (const track of session.value.tracks) {
      if (!track.autoName) continue
      const source = sourceMap.value.get(track.sourceId)
      track.name = localizedAutoTrackName(track.name, track.role, source?.stemKey)
    }
  }

  function toggleTrackFlag(trackId: string, flag: 'muted' | 'solo') {
    const track = session.value?.tracks.find((item) => item.id === trackId)
    if (!track) return
    pushHistory()
    track[flag] = !track[flag]
    scheduleSave()
  }

  function setTrackVolume(trackId: string, value: number) {
    const track = session.value?.tracks.find((item) => item.id === trackId)
    if (!track) return
    track.volume = clamp(Number(value), 0, 2)
    if (interactionDepth.value === 0) scheduleSave()
  }

  function setTrackPan(trackId: string, value: number) {
    const track = session.value?.tracks.find((item) => item.id === trackId)
    if (!track) return
    track.pan = clamp(Number(value), -1, 1)
    if (interactionDepth.value === 0) scheduleSave()
  }

  function setClipFades(
    trackId: string,
    clipId: string,
    patch: { fadeIn?: number; fadeOut?: number },
  ) {
    const track = session.value?.tracks.find((item) => item.id === trackId)
    const clip = track?.clips?.find((item) => item.id === clipId)
    if (!track || !clip) return
    const maxDuration = Math.max(0, Number(clip.duration || 0))
    pushHistory()
    if (patch.fadeIn !== undefined) clip.fadeIn = clamp(Number(patch.fadeIn || 0), 0, maxDuration)
    if (patch.fadeOut !== undefined) clip.fadeOut = clamp(Number(patch.fadeOut || 0), 0, maxDuration)
    syncSingleClipFadeCompatibility(track)
    scheduleSave()
  }

  function setClipTiming(
    trackId: string,
    clipId: string,
    patch: Partial<Pick<EditorClip, 'start' | 'offset' | 'duration'>>,
  ) {
    const track = session.value?.tracks.find((item) => item.id === trackId)
    const clip = track?.clips?.find((item) => item.id === clipId)
    if (!track || !clip) return
    const source = sourceMap.value.get(clip.assetId)
    const sourceDuration = Math.max(0, Number(source?.duration || 0))
    const maxOffset = sourceDuration > 0 ? Math.max(0, sourceDuration - 0.01) : Number.MAX_SAFE_INTEGER
    const nextOffset = clamp(Number(patch.offset ?? clip.offset), 0, maxOffset)
    const availableDuration = sourceDuration > 0
      ? Math.max(0.01, sourceDuration - nextOffset)
      : Number.MAX_SAFE_INTEGER
    clip.start = Math.max(0, Number(patch.start ?? clip.start))
    clip.offset = nextOffset
    clip.duration = clamp(Number(patch.duration ?? clip.duration), 0.01, availableDuration)
    clip.fadeIn = clamp(clip.fadeIn, 0, clip.duration)
    clip.fadeOut = clamp(clip.fadeOut, 0, clip.duration)
    syncSingleClipFadeCompatibility(track)
    if (interactionDepth.value === 0) scheduleSave()
  }

  function setMasterVolume(value: number) {
    if (!session.value) return
    session.value.masterVolume = clamp(Number(value), 0, 2)
    if (interactionDepth.value === 0) scheduleSave()
  }

  function setMasterLimiter(patch: Partial<EditorMasterLimiter>) {
    if (!session.value) return
    const previous = normalizeMasterLimiter(session.value.masterLimiter)
    const next = normalizeMasterLimiter({ ...previous, ...patch })
    if (JSON.stringify(previous) === JSON.stringify(next)) return
    pushHistory()
    session.value.masterLimiter = next
    if (interactionDepth.value === 0) scheduleSave()
  }

  function setMasterPan(value: number) {
    if (!session.value) return
    session.value.masterPan = clamp(Number(value), -1, 1)
    if (interactionDepth.value === 0) scheduleSave()
  }

  function removeTrack(trackId: string) {
    if (!session.value) return
    const track = session.value.tracks.find((item) => item.id === trackId)
    if (!track) return
    pushHistory()
    session.value.tracks = session.value.tracks.filter((item) => item.id !== trackId)
    if (selectedTrackId.value === trackId) {
      selectTrack(session.value.tracks[0]?.id || null)
    }
    scheduleSave()
  }

  function removeClip(trackId: string, clipId: string) {
    const track = session.value?.tracks.find((item) => item.id === trackId)
    const clipIndex = track?.clips?.findIndex((item) => item.id === clipId) ?? -1
    if (!track || clipIndex < 0) return false

    pushHistory()
    const removedClip = track.clips![clipIndex]
    const nextClips = track.clips!.filter((item) => item.id !== clipId)
    track.clips = nextClips
    syncSingleClipFadeCompatibility(track)

    if (track.sourceId === removedClip.assetId && !nextClips.some((item) => item.assetId === removedClip.assetId)) {
      track.sourceId = nextClips[nextClips.length - 1]?.assetId || ''
    }

    if (selectedTrackId.value === trackId && selectedClipId.value === clipId) {
      selectedClipId.value = nextClips[Math.min(clipIndex, nextClips.length - 1)]?.id || null
    }

    scheduleSave()
    return true
  }

  function removeSource(sourceId: string) {
    if (!session.value) return { removedSource: false, removedTracks: 0 }
    const source = session.value.sources.find((item) => item.id === sourceId)
    if (!source) return { removedSource: false, removedTracks: 0 }
    if (source.role === 'stem') return { removedSource: false, removedTracks: 0 }

    pushHistory()
    const removedTrackIds: string[] = []
    session.value.tracks = session.value.tracks.flatMap((track) => {
      const nextClips = (track.clips || []).filter((clip) => clip.assetId !== sourceId)
      const usedByTrack = track.sourceId === sourceId || nextClips.length !== (track.clips || []).length
      if (!usedByTrack) return [track]
      if (!nextClips.length) {
        removedTrackIds.push(track.id)
        return []
      }
      const nextTrack = { ...track, sourceId: nextClips[nextClips.length - 1].assetId, clips: nextClips }
      syncSingleClipFadeCompatibility(nextTrack)
      return [nextTrack]
    })
    session.value.sources = session.value.sources.filter((item) => item.id !== sourceId)

    if (selectedTrackId.value && removedTrackIds.includes(selectedTrackId.value)) {
      selectTrack(session.value.tracks[0]?.id || null)
    } else if (selectedClipId.value && !selectedTrack.value?.clips?.some((clip) => clip.id === selectedClipId.value)) {
      selectTrack(selectedTrackId.value)
    }

    scheduleSave()
    return { removedSource: true, removedTracks: removedTrackIds.length }
  }

  function setPluginChain(trackId: string | null, chain: EditorPluginSlot[]) {
    if (!session.value) return
    const target = trackId ? session.value.tracks.find(track => track.id === trackId) : session.value
    if (!target) return
    pushHistory()
    target.pluginChain = clonePluginChain(chain)
    scheduleSave()
  }

  return {
    selectTrack, selectClip, renameTrack, localizeDefaultTrackNames,
    toggleTrackFlag, setTrackVolume, setTrackPan, setClipFades, setClipTiming,
    setMasterVolume, setMasterLimiter, setMasterPan, removeTrack, removeClip, removeSource,
    setPluginChain,
  }
}
