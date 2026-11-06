import { inputStemSegment } from './taskOutputPaths.ts'
import { normalizeOutputLayout } from './taskNormalize.ts'
import type { OutputLayout, SeparationTask } from './task.ts'

export function resolveRetryOutput(
  existing: SeparationTask,
  windows: boolean,
  errorMessage: string,
): { jobOutput: string; outputLayout: OutputLayout } {
  const outputLayout = normalizeOutputLayout(existing.runConfig?.outputLayout)
  const isAbsolute = (path: string) => windows ? /^(?:[A-Za-z]:[/\\]|[/\\]{2})/.test(path) : path.startsWith('/')
  let jobOutput = existing.jobOutput
  if (!jobOutput || !isAbsolute(jobOutput)) {
    const output = existing.output
    if (!isAbsolute(output)) throw new Error(errorMessage)
    if (outputLayout === 'folders') {
      const folderName = output.split(windows ? /[/\\]/ : /[/]/).filter(Boolean).pop()
      const inputName = inputStemSegment(existing.input, existing.resultId || existing.id, windows)
      const matchesInput = windows ? folderName?.toLowerCase() === inputName.toLowerCase() : folderName === inputName
      if (!matchesInput) throw new Error(errorMessage)
      jobOutput = output.replace(windows ? /[^/\\]+[/\\]*$/ : /[^/]+[/]*$/, '')
      if (jobOutput.length > 1 && !(windows && /^[A-Za-z]:[/\\]$/.test(jobOutput))) {
        jobOutput = jobOutput.replace(windows ? /[/\\]$/ : /[/]$/, '')
      }
    } else {
      jobOutput = output
    }
  }
  return { jobOutput, outputLayout }
}
