/**
 * 输出路径与文件命名：跨 Windows/POSIX 的路径拼接、规范化与词干推断。
 *
 * 全是纯函数，不碰 store 状态；stemFromOutputPath 复用 utils/stemOutputs 的唯一实现。
 */

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
