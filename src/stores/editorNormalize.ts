/**
 * 源/片段/轨道/会话的规范化与持久化：默认值填充、旧字段兼容、克隆。
 *
 * 纯函数，不碰 store 响应式状态；类型从 editor.ts 引入（import type 擦除）。
 */
import { fileName, isLegacyAutoTrackName, trackColor } from './editorStems.ts'
import { normalizeMasterLimiter } from '../utils/masterLimiter.ts'
import { normalizePluginChain, clonePluginChain, pluginProjectVersion } from '../utils/editorPlugins.ts'
import type { EditorSource, EditorClip, EditorTrack, EditorSession, EditorSourceRole } from '../types/editor.ts'
import type { PersistedSession } from './editor.ts'

export function makeId(prefix: string) {
  return `${prefix}_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`
}

export function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value))
}

export function cloneLegacyTrackEffects(effects: unknown): EditorTrack['effects'] {
  return effects && typeof effects === 'object' && !Array.isArray(effects)
    ? JSON.parse(JSON.stringify(effects)) as Record<string, unknown>
    : undefined
}

export function cloneTracks(tracks: EditorTrack[]) {
  return tracks.map((track) => ({
    ...track,
    effects: cloneLegacyTrackEffects(track.effects),
    pluginChain: clonePluginChain(track.pluginChain),
    clips: track.clips?.map((clip) => ({ ...clip })),
  }))
}

export function normalizeSource(source: Partial<EditorSource>): EditorSource {
  const channelPeaks = Array.isArray(source.channelPeaks)
    ? source.channelPeaks
        .filter((channel) => Array.isArray(channel))
        .map((channel) => channel.map((value) => Number(value || 0)))
    : []
  const channelCount = Math.max(Number(source.channels || 0), channelPeaks.length)
  return {
    id: String(source.id || makeId('source')),
    role: source.role === 'reference' || source.role === 'recording' ? source.role : 'stem',
    stemKey: source.stemKey ? String(source.stemKey) : null,
    path: String(source.path || ''),
    name: String(source.name || fileName(String(source.path || '')) || 'Untitled'),
    duration: Number(source.duration || 0),
    sampleRate: Number(source.sampleRate || 0),
    channels: channelCount,
    peaksPath: source.peaksPath ? String(source.peaksPath) : null,
    peaks: Array.isArray(source.peaks) ? source.peaks.map((value) => Number(value || 0)) : [],
    channelPeaks,
    originKind: source.originKind ? String(source.originKind) : undefined,
    originRoot: source.originRoot ? String(source.originRoot) : null,
    relativePath: source.relativePath ? String(source.relativePath) : null,
    missing: Boolean(source.missing),
  }
}

export function normalizeClip(clip: Partial<EditorClip>, source?: EditorSource): EditorClip {
  const offset = clamp(Number(clip.offset || 0), 0, Math.max(0, Number(source?.duration || Infinity)))
  const availableDuration = Math.max(0, Number(source?.duration || 0) - offset)
  const requestedDuration = Math.max(0, Number(clip.duration ?? availableDuration))
  const duration = availableDuration > 0 ? Math.min(requestedDuration, availableDuration) : requestedDuration
  return {
    id: String(clip.id || makeId('clip')),
    assetId: String(clip.assetId || source?.id || ''),
    start: Math.max(0, Number(clip.start || 0)),
    offset,
    duration,
    volume: clamp(Number(clip.volume ?? 1), 0, 2),
    fadeIn: clamp(Number(clip.fadeIn || 0), 0, duration),
    fadeOut: clamp(Number(clip.fadeOut || 0), 0, duration),
    muted: Boolean(clip.muted),
    locked: Boolean(clip.locked),
  }
}

export function normalizeTrack(
  track: Partial<EditorTrack>,
  sources: Map<string, EditorSource>,
  clipFadesCanonical: boolean,
  localizedName: (name: string, role: EditorSourceRole, stemKey?: string | null) => string,
): EditorTrack {
  const source = sources.get(String(track.sourceId || ''))
  const role = track.role === 'reference' || track.role === 'recording'
    ? track.role
    : (source?.role || 'stem')
  const stemKey = source?.stemKey || null
  const storedName = String(track.name || source?.name || 'Track')
  const autoName = typeof track.autoName === 'boolean'
    ? track.autoName
    : isLegacyAutoTrackName(storedName, role, stemKey)
  const clips = Array.isArray(track.clips)
    ? track.clips
        .map((clip) => normalizeClip(clip, sources.get(String(clip.assetId || track.sourceId || ''))))
        .filter((clip) => clip.assetId && clip.duration > 0)
    : []
  if (!clips.length && source && source.duration > 0) {
    clips.push(normalizeClip({
      id: `clip_${String(track.id || source.id)}`,
      assetId: source.id,
      duration: source.duration,
      fadeIn: Number(track.fadeIn || 0),
      fadeOut: Number(track.fadeOut || 0),
    }, source))
  }
  let fadeIn = Math.max(0, Number(track.fadeIn || 0))
  let fadeOut = Math.max(0, Number(track.fadeOut || 0))
  if (clips.length === 1) {
    const clip = clips[0]
    if (clipFadesCanonical) {
      fadeIn = clip.fadeIn
      fadeOut = clip.fadeOut
    } else {
      clip.fadeIn = clamp(fadeIn, 0, clip.duration)
      clip.fadeOut = clamp(fadeOut, 0, clip.duration)
      fadeIn = clip.fadeIn
      fadeOut = clip.fadeOut
    }
  }
  return {
    id: String(track.id || makeId('track')),
    sourceId: String(track.sourceId || source?.id || ''),
    role,
    name: autoName ? localizedName(storedName, role, stemKey) : storedName,
    autoName,
    color: String(track.color || trackColor(role, stemKey)),
    volume: clamp(Number(track.volume ?? 1), 0, 2),
    pan: clamp(Number(track.pan ?? 0), -1, 1),
    muted: Boolean(track.muted),
    solo: Boolean(track.solo),
    fadeIn,
    fadeOut,
    effects: cloneLegacyTrackEffects(track.effects),
    pluginChain: normalizePluginChain(track.pluginChain),
    type: track.type === 'recording' || role === 'recording'
      ? 'recording'
      : (track.type === 'audio' ? 'audio' : role),
    clips,
  }
}

export function normalizeSession(session: PersistedSession, localizedName: (name: string, role: EditorSourceRole, stemKey?: string | null) => string): EditorSession {
  const sources = Array.isArray(session.sources)
    ? session.sources.map(normalizeSource)
    : []
  const sourceMap = new Map(sources.map((source) => [source.id, source]))
  const clipFadesCanonical = Number(session.version || 0) >= 3
  const tracks = Array.isArray(session.tracks)
    ? session.tracks.map((track) => normalizeTrack(track, sourceMap, clipFadesCanonical, localizedName))
    : []

  return {
    id: String(session.id || makeId('session')),
    name: String(session.name || 'Untitled Session'),
    sourceTaskId: session.sourceTaskId ? String(session.sourceTaskId) : undefined,
    sourceResultDir: session.sourceResultDir ? String(session.sourceResultDir) : undefined,
    masterVolume: clamp(Number(session.masterVolume ?? 1), 0, 2),
    masterPan: clamp(Number(session.masterPan ?? 0), -1, 1),
    masterLimiter: session.masterLimiter ? normalizeMasterLimiter(session.masterLimiter) : undefined,
    pluginChain: normalizePluginChain(session.pluginChain),
    sources,
    tracks,
    createdAt: Number(session.createdAt || Date.now()),
    updatedAt: Number(session.updatedAt || Date.now()),
  }
}

export function toPersistedSession(session: EditorSession) {
  return {
    ...session,
    version: pluginProjectVersion(session),
    pluginChain: clonePluginChain(session.pluginChain),
    tracks: cloneTracks(session.tracks),
    sources: session.sources.map((source) => ({
      ...source,
      peaks: [],
      channelPeaks: [],
    })),
  }
}
