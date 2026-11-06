import type { SeparationTask } from './task.ts'
import { cleanupDirChain, parentDir, pathKey, samePath } from './taskOutputPaths.ts'

export function resultTrashTargets(task: SeparationTask): string[] {
  const seen = new Set<string>()
  return task.outputs
    .map(output => output.path)
    .filter((path): path is string => Boolean(path))
    .filter(path => {
      if (seen.has(path)) return false
      seen.add(path)
      return true
    })
}

export function resultTrashEmptyDirs(task: SeparationTask, windows: boolean): string[] {
  if (task.runConfig?.workbench) return []
  if (task.runConfig?.outputLayout !== 'folders') return []
  const jobOutput = task.jobOutput
  const output = task.output
  if (output && !samePath(output, jobOutput, windows)) return cleanupDirChain(output, jobOutput, windows)
  const parents = task.outputs.map(item => parentDir(item.path || '', windows)).filter(Boolean)
  const dirs = parents.flatMap(parent => cleanupDirChain(parent, jobOutput, windows))
  const seen = new Set<string>()
  return dirs.filter(dir => {
    const key = pathKey(dir, windows)
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

export function primaryRevealPath(task: SeparationTask): string {
  return task.runConfig?.workbench ? task.outputs[0]?.path || task.output : task.output
}
