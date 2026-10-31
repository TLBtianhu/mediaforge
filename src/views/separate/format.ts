// SeparateView 的纯格式化/断言辅助：不依赖组件状态，便于单测与复用。
export function padNumber(value: number) {
  return String(value).padStart(2, '0')
}

export function formatDateToken(date = new Date()) {
  return `${date.getFullYear()}${padNumber(date.getMonth() + 1)}${padNumber(date.getDate())}`
}

export function formatTimeToken(date = new Date()) {
  return `${padNumber(date.getHours())}${padNumber(date.getMinutes())}${padNumber(date.getSeconds())}`
}

export function formatLegacyTimeToken(date = new Date()) {
  return `${padNumber(date.getDate())}${padNumber(date.getMinutes())}${padNumber(date.getSeconds())}`
}

export function truncateUtf8(value: string, maxBytes: number) {
  let bytes = 0
  let result = ''
  for (const char of value) {
    const size = new TextEncoder().encode(char).length
    if (bytes + size > maxBytes) break
    bytes += size
    result += char
  }
  return result.replace(/[ ._]+$/u, '')
}

export function normalizeFilenameFragment(value: string) {
  return String(value || '')
    .replace(/[<>:"/\\|?*\x00-\x1f]+/g, '_')
    .replace(/\s+/g, ' ')
    .replace(/\s*_\s*/g, '_')
}

export function formatPlaybackTime(value: number) {
  if (!Number.isFinite(value) || value <= 0) return '00:00'
  const total = Math.floor(value)
  const minutes = Math.floor(total / 60)
  const seconds = total % 60
  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
}

export function formatProgressTime(seconds: number) {
  const total = Math.max(0, Math.round(seconds))
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const rest = total % 60
  return `${padNumber(hours)}:${padNumber(minutes)}:${padNumber(rest)}`
}

export function compareModelNames(a: string, b: string, locale: string) {
  return a.localeCompare(b, locale === 'zh-CN' ? 'zh-CN' : 'en')
}

export const WINDOWS_RESERVED_FILENAMES = new Set([
  'CON',
  'PRN',
  'AUX',
  'NUL',
  ...Array.from({ length: 9 }, (_, index) => `COM${index + 1}`),
  ...Array.from({ length: 9 }, (_, index) => `LPT${index + 1}`),
])

export const MAX_FILENAME_PART_BYTES = 200

export function getFileName(path: string) {
  return path.split(/[/\\]/).filter(Boolean).pop() || path
}

export function stripFileExtension(path: string) {
  return getFileName(path).replace(/\.[^/.\\]+$/, '')
}

export function safeFilenamePart(value: string) {
  let text = normalizeFilenameFragment(value).replace(/^[ ._]+|[ ._]+$/g, '')
  text = truncateUtf8(text, MAX_FILENAME_PART_BYTES) || 'output'
  const reservedKey = text.split('.', 1)[0].toUpperCase()
  if (WINDOWS_RESERVED_FILENAMES.has(reservedKey) || text === '.' || text === '..') text = `${text}_`
  return text
}
