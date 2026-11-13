import type { Ref } from 'vue'
import { relinkEditorSources } from '@/api/native'
import { normalizeSession } from '@/stores/editorNormalize'
import { localizedAutoTrackName } from '@/stores/editorSupport'
import type { EditorSession } from '@/types/editor'
import type { PickFileResult } from '@/stores/editorSupport'

type Deps = {
  session: Ref<EditorSession | null>
  pickSingleAudioFile: () => Promise<PickFileResult>
  hydratePeaksFromCache: (projectId?: string) => Promise<void>
  hydrateSessionSourcesInBackground: (projectId: string) => void
}

export function makeEditorSourceActions({
  session, pickSingleAudioFile, hydratePeaksFromCache, hydrateSessionSourcesInBackground,
}: Deps) {
  function sourcesInUse() {
    if (!session.value) return []
    const sourceIds = new Set(session.value.tracks.flatMap((track) => [
      track.sourceId,
      ...(track.clips || []).map((clip) => clip.assetId),
    ]).filter(Boolean))
    return session.value.sources.filter((source) => sourceIds.has(source.id))
  }

  function missingSources() {
    return session.value?.sources.filter((source) => source.missing) || []
  }

  function missingSourcesInUse() {
    return sourcesInUse().filter((source) => source.missing)
  }

  function assertNoMissingSourcesInUse(message: string) {
    const missing = missingSourcesInUse()
    if (!missing.length) return
    throw new Error(message)
  }

  async function pickRelinkFile() {
    return pickSingleAudioFile()
  }

  async function relinkSource(sourceId: string, pickedPath?: string | null) {
    if (!session.value) throw new Error('Editor session is not loaded')
    const source = session.value.sources.find((item) => item.id === sourceId)
    if (!source) throw new Error('Source asset not found')
    const resolvedPath = pickedPath ?? await pickRelinkFile()
    if (!resolvedPath) return null
    const result = await relinkEditorSources({
      projectId: session.value.id,
      sourceId,
      pickedPath: resolvedPath,
    })
    session.value = normalizeSession(result.project, localizedAutoTrackName)
    await hydratePeaksFromCache(session.value.id)
    hydrateSessionSourcesInBackground(session.value.id)
    return result
  }

  async function relinkMissingSources(pickedPath?: string | null) {
    const firstMissing = missingSources()[0]
    if (!firstMissing) return null
    return relinkSource(firstMissing.id, pickedPath)
  }

  return {
    sourcesInUse, missingSources, missingSourcesInUse, assertNoMissingSourcesInUse,
    pickRelinkFile, relinkSource, relinkMissingSources,
  }
}
