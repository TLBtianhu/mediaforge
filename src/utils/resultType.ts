import type { SeparationTask } from '../stores/task.ts'
import { detectWorkflowFormat } from '../workflows/formats.ts'
import { workflowSourceDefinition } from '../workflows/workbench.ts'

export type ResultType = 'model' | 'ensemble' | 'simple' | 'advanced' | 'unknown'
export const RESULT_TYPE_KEYS: Record<ResultType, string> = {
  model: 'results.typeModel', ensemble: 'results.typeEnsemble', simple: 'results.typeSimple', advanced: 'results.typeAdvanced',
  unknown: 'results.typeUnknown',
}

/** Use the saved submission source, never the models/nodes currently selected in an editor. */
export function resultType(task: Pick<SeparationTask, 'runConfig'>): ResultType {
  const config = task.runConfig
  if (!config) return 'model'
  if (config.workbench) {
    const format = detectWorkflowFormat(workflowSourceDefinition(config))
    return format === 'graph' || config.workbench.nativeGraph ? 'advanced' : format === 'simple' ? 'simple' : 'unknown'
  }
  const definition = config.workflowDefinition
  const extra = definition?.extra as Record<string, unknown> | undefined
  if (config.workflowId === 'temporary-ensemble' && extra?.studioEnsemble) return 'ensemble'
  if (config.runMode === 'workflow') {
    const format = detectWorkflowFormat(definition)
    return format === 'simple' ? 'simple' : format === 'graph' ? 'advanced' : 'unknown'
  }
  return 'model'
}
