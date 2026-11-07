import type { SeparationTask } from './task.ts'
import { cleanupDirChain, parentDir, pathKey, samePath } from './taskOutputPaths.ts'
import { isTerminalTaskStatus, taskJobId } from '../features/tasks/lifecycle.ts'
import { specFor, widgetValue } from '../litegraph/paramCodec.ts'
import { workflowInputNodes } from '../workflows/workbench.ts'

/** Result removal includes the whole batch and must never race an active sibling. */
export function getRemovalTasks(sourceTasks: readonly SeparationTask[], ids: readonly string[], runningMessage: string): SeparationTask[] {
  const selected = new Set(ids)
  const jobs = new Set(sourceTasks.filter(task => selected.has(task.id)).map(taskJobId))
  const related = sourceTasks.filter(task => jobs.has(taskJobId(task)))
  if (related.some(task => !isTerminalTaskStatus(task.status))) throw new Error(runningMessage)
  return related
}

/** Plan file recycling against every surviving input/output reference. No disk writes. */
export function resultTrashTargetsForTasks(sourceTasks: readonly SeparationTask[], removing: readonly SeparationTask[], windows: boolean): { paths: string[]; retained: number } {
  const removingIds = new Set(removing.map(item => item.id))
  const protectedPaths = new Set<string>()
  const protectedDirectories = new Set<string>()
  for (const item of sourceTasks) {
    if (item.input) protectedPaths.add(pathKey(item.input, windows))
    for (const input of Object.values(item.runConfig?.workbench?.inputs || {})) {
      if (input) protectedPaths.add(pathKey(input, windows))
    }
    const definition = item.runConfig?.workflowDefinition || item.runConfig?.workbench?.sourceDefinition
    if (definition) {
      for (const node of workflowInputNodes(definition)) {
        if (node.embeddedPath) protectedPaths.add(pathKey(node.embeddedPath, windows))
      }
      if (!removingIds.has(item.id)) {
        for (const node of Array.isArray(definition.nodes) ? definition.nodes : []) {
          const batchFolder = node?.type === 'pymss_load_audio_batch'
            ? widgetValue(specFor('pymss_load_audio_batch'), node, 'folder')
            : undefined
          if (node?.type === 'pymss_load_audio_batch' && typeof batchFolder === 'string') {
            const directory = pathKey(batchFolder, windows)
            if (directory) protectedDirectories.add(directory.endsWith('/') ? directory : `${directory}/`)
          }
        }
      }
    }
    if (!removingIds.has(item.id)) {
      for (const output of item.outputs) protectedPaths.add(pathKey(output.path, windows))
    }
  }
  const seen = new Set<string>()
  let retained = 0
  const directories = [...protectedDirectories]
  const paths = removing.flatMap(resultTrashTargets).filter(path => {
    const key = pathKey(path, windows)
    if (seen.has(key)) return false
    seen.add(key)
    if (protectedPaths.has(key) || directories.some(directory => key.startsWith(directory))) {
      retained += 1
      return false
    }
    return true
  })
  return { paths, retained }
}

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
