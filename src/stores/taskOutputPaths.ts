/**
 * 输出路径与文件命名：跨 Windows/POSIX 的路径拼接、规范化、目录链、词干推断。
 *
 * 全是纯函数，不碰 store 状态；stemFromOutputPath 复用 utils/stemOutputs 的唯一实现。
 */
import { stemFromOutputPath, type StemOutput } from '../utils/stemOutputs.ts'

export function normalizeOutputPath(value?: string | null) {
  return value && value.trim() ? value : 'outputs'
}

export function resolveOutputDirectory(value?: string | null, fallback?: string | null, windows = false) {
  const directory = value && value.trim() ? value : fallback && fallback.trim() ? fallback : 'outputs'
  return windows || !directory.startsWith('/') ? directory.trim() : directory
}

export function joinOutputPath(base: string, child: string, windows: boolean) {
  const separator = windows && base.includes('\\') ? '\\' : '/'
  const trailingSeparator = windows ? /[\\/]$/ : /[/]$/
  return `${base.replace(trailingSeparator, '')}${separator}${child}`
}

export function createRunId(prefix: string) {
  return `${prefix}_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`
}

export function inputStemSegment(input: string, fallback: string, windows: boolean) {
  return input
    .split(windows ? /[/\\]/ : /[/]/)
    .filter(Boolean)
    .pop()
    ?.replace(windows ? /\.[^/.\\]+$/ : /\.[^/.]+$/, '') || fallback
}

export function outputsFromFiles(outputDir: string, files: string[], outputFormat = 'wav', outputPrefix?: string, inputPath?: string, windows = /^[A-Za-z]:|^\\\\/.test(outputDir)): StemOutput[] {
  if (!files?.length) return []
  return files.flatMap((file) => {
    const rawPath = String(file || '')
    const parts = rawPath.split(windows ? /[/\\]/ : /[/]/)
    if (!rawPath || parts.some(part => part === '.' || part === '..')) return []
    const leaf = parts.at(-1) || ''
    const extension = leaf.match(windows ? /\.[^/.\\]+$/ : /\.[^/.]+$/)?.[0] || ''
    const rawStemName = extension ? leaf.slice(0, -extension.length) : leaf
    const outputFileName = outputPrefix ? `${outputPrefix}_${rawStemName}` : rawStemName
    const isAbsolutePath = windows ? /^(?:[A-Za-z]:[\\/]|[\\/]{2})/.test(rawPath) : rawPath.startsWith('/')
    const relativeParent = rawPath.slice(0, rawPath.length - leaf.length)
    return [{
      stem: stemFromOutputPath(leaf, inputPath, windows),
      path: isAbsolutePath ? rawPath : joinOutputPath(outputDir, `${relativeParent}${outputFileName}${extension || `.${outputFormat}`}`, windows),
    }]
  })
}

export function cleanupPath(path: string, windows: boolean) {
  let normalized = windows ? path.replace(/\\/g, '/') : path
  if (windows) {
    normalized = normalized.replace(/^\/\/\?\/UNC\//i, '//').replace(/^\/\/\?\/([A-Za-z]:\/)/, '$1')
  }
  return normalized.replace(/\/+$/, '') || (normalized.startsWith('/') ? '/' : '')
}

export function parentDir(path: string, windows: boolean) {
  const normalized = cleanupPath(path, windows)
  const index = normalized.lastIndexOf('/')
  return index > 0 ? normalized.slice(0, index) : ''
}

export function pathKey(path: string, windows: boolean) {
  const normalized = cleanupPath(path, windows)
  const drive = windows ? normalized.match(/^[A-Za-z]:/)?.[0] || '' : ''
  let prefix = ''
  if (drive) prefix = `${drive}/`
  else if (normalized.startsWith('/')) prefix = windows && normalized.startsWith('//') ? '//' : '/'
  const segments: string[] = []
  const rootDepth = prefix === '//' ? 2 : 0
  for (const part of (drive ? normalized.slice(2) : normalized).split('/')) {
    if (!part || part === '.') continue
    if (part === '..') {
      if (segments.length > rootDepth && segments.at(-1) !== '..') segments.pop()
      else if (!prefix) segments.push(part)
    } else segments.push(part)
  }
  const key = prefix + segments.join('/')
  return windows ? key.toLowerCase() : key
}

export function samePath(a: string | undefined, b: string | undefined, windows: boolean) {
  return Boolean(a && b && pathKey(a, windows) === pathKey(b, windows))
}

export function cleanupDirChain(path: string, stopAt: string | undefined, windows: boolean) {
  const dirs: string[] = []
  const absolute = windows ? /^(?:[A-Za-z]:[\\/]|[\\/]{2}[^\\/]+[\\/][^\\/]+)/ : /^\//
  if (!stopAt || !absolute.test(path) || !absolute.test(stopAt)) return dirs
  const stop = cleanupPath(stopAt, windows)
  let current = cleanupPath(path, windows)
  if ([stop, current].some(value => value.split('/').some(part => part === '.' || part === '..'))) return dirs
  const stopKey = pathKey(stop, windows)
  const prefix = stopKey.endsWith('/') ? stopKey : `${stopKey}/`
  while (current && !samePath(current, stop, windows)) {
    if (!pathKey(current, windows).startsWith(prefix)) return []
    dirs.push(current)
    current = parentDir(current, windows)
  }
  return dirs
}
