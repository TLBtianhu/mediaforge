/**
 * 词干/轨道的命名与颜色：词干归类、旧名映射、录音轨序号、轨道配色。
 *
 * 纯函数与常量，不依赖 i18n（localizedStemName 仍留在 editor.ts）。
 */
import type { StemOutput } from '../utils/stemOutputs.ts'
import type { EditorSource, EditorSourceRole } from './editor.ts'

export function stemKeyFromOutput(output: StemOutput | { stem?: string; path?: string }) {
  const raw = String(output.stem || stripExt(fileName(output.path || ''))).trim().toLowerCase()
  if (!raw) return 'other'
  if (raw.includes('vocal') || raw.includes('voice')) return 'vocals'
  if (raw.includes('instrument') || raw.includes('accompaniment') || raw.includes('karaoke')) return 'accompaniment'
  if (raw.includes('drum')) return 'drums'
  if (raw.includes('bass')) return 'bass'
  if (raw.includes('other')) return 'other'
  return raw
}

export function fileName(path: string) {
  return path.split(/[/\\]/).pop() || path
}

export function stripExt(name: string) {
  return name.replace(/\.[^/.]+$/, '')
}

export const STEM_NAME_KEYS: Record<string, string> = {
  vocals: 'editor.stemVocals',
  accompaniment: 'editor.stemAccompaniment',
  drums: 'editor.stemDrums',
  bass: 'editor.stemBass',
  other: 'editor.stemOther',
}

export const LEGACY_STEM_NAMES: Record<string, string[]> = {
  vocals: ['vocals', 'vocal', 'voice', '人声'],
  accompaniment: ['instrumental', 'instrument', 'accompaniment', 'karaoke', '伴奏'],
  drums: ['drums', 'drum', '鼓组'],
  bass: ['bass', '贝斯'],
  other: ['other', '其他'],
}

export const EXACT_STEM_KEYS = new Map(
  Object.entries(LEGACY_STEM_NAMES).flatMap(([stemKey, names]) => (
    names
      .filter(name => /^[a-z]+$/i.test(name))
      .map(name => [normalizedDefaultName(name), stemKey] as const)
  )),
)

export function normalizedDefaultName(value: string | null | undefined) {
  return String(value || '').trim().toLowerCase().replace(/[\s_-]+/g, ' ')
}

export function recordingTrackIndex(name: string) {
  const match = name.trim().match(/^(?:recording|录音)\s*(\d+)$/i)
  return match ? Math.max(1, Number(match[1])) : null
}

export function isLegacyAutoTrackName(name: string, role: EditorSourceRole, stemKey?: string | null) {
  if (role === 'recording') return recordingTrackIndex(name) !== null
  if (role !== 'stem' || !stemKey) return false
  const canonicalKey = stemKeyFromOutput({ stem: stemKey || '' })
  const candidates = [stemKey || '', ...(LEGACY_STEM_NAMES[canonicalKey] || [])]
  const normalizedName = normalizedDefaultName(name)
  return candidates.some((candidate) => normalizedDefaultName(candidate) === normalizedName)
}

export function trackColor(role: EditorSourceRole, stemKey?: string | null) {
  if (role === 'recording') return '#e76f91'
  if (role === 'reference') return '#93a1b6'
  return COLOR_BY_STEM[String(stemKey || '').toLowerCase()] || '#7aa2ff'
}

export function duplicatedTrackColor(source: EditorSource) {
  if (source.role === 'stem') return trackColor('stem', source.stemKey)
  return trackColor(source.role, source.stemKey)
}

export const COLOR_BY_STEM: Record<string, string> = {
  vocals: '#ff6b7c',
  vocal: '#ff6b7c',
  voice: '#ff6b7c',
  accompaniment: '#5d8dff',
  instrumental: '#5d8dff',
  instruments: '#5d8dff',
  drums: '#f2b45a',
  drum: '#f2b45a',
  bass: '#4fc58f',
  other: '#b08cff',
}
