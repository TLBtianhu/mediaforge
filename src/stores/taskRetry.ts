import { normalizeOutputLayout } from './taskNormalize.ts'
import type { OutputLayout, SeparationTask } from './task.ts'

export function resolveRetryOutput(
  existing: SeparationTask,
  windows: boolean,
  errorMessage: string,
): { jobOutput: string; outputLayout: OutputLayout } {
  const outputLayout = normalizeOutputLayout(existing.runConfig?.outputLayout)
  const isAbsolute = (path: string) => windows ? /^(?:[A-Za-z]:[/\\]|[/\\]{2})/.test(path) : path.startsWith('/')
  const jobOutput = existing.jobOutput
  if (!jobOutput || !isAbsolute(jobOutput)) throw new Error(errorMessage)
  return { jobOutput, outputLayout }
}
