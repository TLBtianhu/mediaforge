import { specFor } from '../litegraph/paramCodec.ts'

export type WorkflowFormat = 'simple' | 'graph' | 'unknown'

export const WORKFLOW_FORMAT_VERSION = 1
// Persisted pymss graphs use Comfy's tuple-link schema, independently of our store envelope.
export const COMFY_WORKFLOW_VERSION = 0.4

export const SIMPLE_ENSEMBLE_ALGORITHMS = [
  'avg_wave', 'median_wave', 'min_wave', 'max_wave',
  'avg_fft', 'median_fft', 'min_fft', 'max_fft',
] as const
export const SIMPLE_AUDIO_OPERATIONS = ['sum', 'subtract', 'invert'] as const
export const SIMPLE_INFERENCE_NUMBER_FIELDS: ReadonlySet<string> = new Set([
  'batch_size', 'overlap_size', 'chunk_size', 'window_size', 'aggression', 'post_process_threshold',
])

export type SimpleEnsembleAlgorithm = typeof SIMPLE_ENSEMBLE_ALGORITHMS[number]
export type SimpleAudioOperation = typeof SIMPLE_AUDIO_OPERATIONS[number]
export type SimpleProcessingAlgorithm = SimpleEnsembleAlgorithm | SimpleAudioOperation

export function isSimpleAudioOperation(value: unknown): value is SimpleAudioOperation {
  return typeof value === 'string' && SIMPLE_AUDIO_OPERATIONS.includes(value as SimpleAudioOperation)
}

export function isSimpleProcessingAlgorithm(value: unknown): value is SimpleProcessingAlgorithm {
  return isSimpleAudioOperation(value)
    || (typeof value === 'string' && SIMPLE_ENSEMBLE_ALGORITHMS.includes(value as SimpleEnsembleAlgorithm))
}

export function simpleProcessingInputLimits(algorithm: SimpleProcessingAlgorithm) {
  if (algorithm === 'invert') return { min: 1, max: 1 }
  if (algorithm === 'subtract') return { min: 2, max: 2 }
  return { min: 2, max: 10 }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

export function hasInvalidSimpleInferenceNumbers(params: Record<string, unknown>): boolean {
  return Object.entries(params).some(([key, value]) => {
    if (!SIMPLE_INFERENCE_NUMBER_FIELDS.has(key)) return false
    if (value === null) return false
    if ((key === 'overlap_size' || key === 'chunk_size')
      && typeof value === 'string' && value.trim().toLowerCase() === 'default') return false
    if (typeof value !== 'number' || !Number.isFinite(value)) return true
    if (key === 'post_process_threshold') return value < 0 || value > 1
    const minimum = key === 'batch_size' || key === 'window_size' ? 1 : 0
    return !Number.isInteger(value) || value < minimum
  })
}

/** Inspect the definition shape; stored metadata is validated separately. */
export function detectWorkflowFormat(definition: unknown): WorkflowFormat {
  if (!isRecord(definition)) return 'unknown'
  const hasSteps = Array.isArray(definition.steps)
  // pymss accepts graph definitions without a `links` field (or with null)
  // and treats them as graphs without connections.
  const hasNodes = Array.isArray(definition.nodes)
    && (definition.links == null || Array.isArray(definition.links))
  if (hasSteps === hasNodes) return 'unknown'
  return hasSteps ? 'simple' : 'graph'
}

export function isSimpleWorkflowDefinition(definition: unknown): definition is Record<string, unknown> {
  return detectWorkflowFormat(definition) === 'simple'
}

export function isGraphWorkflowDefinition(definition: unknown): definition is Record<string, unknown> {
  return detectWorkflowFormat(definition) === 'graph'
}

/**
 * Validate the structural parts shared by the simple editor and runtime.
 * Format detection guarantees a `steps` array, while this check keeps malformed
 * imported values from reaching hydration or the worker with a misleading
 * "no outputs" error (for example `defaults: "cuda"`).
 */
export function hasInvalidSimpleStructure(definition: Record<string, unknown>): boolean {
  if (!Array.isArray(definition.steps)) return true
  if (definition.version != null && definition.version !== WORKFLOW_FORMAT_VERSION) return true
  if (definition.ensembles != null && !Array.isArray(definition.ensembles)) return true
  if (definition.studio != null && !isRecord(definition.studio)) return true
  if (isRecord(definition.studio)) {
    if (definition.studio.editor !== 'simple') return true
    if (definition.studio.viewport != null && !isRecord(definition.studio.viewport)) return true
    if (definition.studio.nodes != null && !isRecord(definition.studio.nodes)) return true
  }
  if (definition.defaults != null && !isRecord(definition.defaults)) return true
  const defaults = isRecord(definition.defaults) ? definition.defaults : {}
  if (['device', 'output_format'].some(key => defaults[key] != null && typeof defaults[key] !== 'string')) return true
  if (defaults.inference_params != null && !isRecord(defaults.inference_params)) return true
  if (isRecord(defaults.inference_params) && hasInvalidSimpleInferenceNumbers(defaults.inference_params)) return true
  const hasInvalidStep = (definition.steps as unknown[]).some((value) => {
    if (!isRecord(value)) return true
    if (['id', 'model', 'input'].some(key => value[key] != null && typeof value[key] !== 'string')) return true
    if (value.stems != null && typeof value.stems !== 'string'
      && (!Array.isArray(value.stems) || value.stems.some(stem => typeof stem !== 'string'))) return true
    if (value.inference_params != null && !isRecord(value.inference_params)) return true
    if (isRecord(value.inference_params) && hasInvalidSimpleInferenceNumbers(value.inference_params)) return true
    if (value.save != null && !isRecord(value.save)) return true
    return false
  })
  if (hasInvalidStep) return true
  const ensembles = Array.isArray(definition.ensembles) ? definition.ensembles : []
  const hasInvalidEnsemble = ensembles.some((value) => {
    if (!isRecord(value) || !Array.isArray(value.inputs)) return true
    if (typeof value.id !== 'string' || !value.id.trim()) return true
    if (!isSimpleProcessingAlgorithm(value.algorithm)) return true
    if (typeof value.output_stem !== 'string' || !value.output_stem.trim()) return true
    if (value.save != null && value.save !== false && typeof value.save !== 'string') return true
    if (value.output_name != null && typeof value.output_name !== 'string') return true
    const limits = simpleProcessingInputLimits(value.algorithm)
    const audioOperation = isSimpleAudioOperation(value.algorithm)
    if (value.inputs.length < limits.min || value.inputs.length > limits.max) return true
    return value.inputs.some((input) => (
      !isRecord(input)
      || typeof input.source !== 'string'
      || !input.source.trim()
      || typeof input.weight !== 'number'
      || !Number.isFinite(input.weight)
      || input.weight <= 0
      || (audioOperation && input.weight !== 1)
    ))
  })
  if (hasInvalidEnsemble) return true
  const stepOutputIndexes = new Map<string, number>()
  ;(definition.steps as unknown[]).forEach((value, index) => {
    if (!isRecord(value) || typeof value.id !== 'string' || !Array.isArray(value.stems)) return
    const stepId = value.id.trim()
    value.stems
      .filter(stem => typeof stem === 'string' && stem.trim())
      .forEach(stem => stepOutputIndexes.set(`${stepId}.${String(stem).trim()}`.toLowerCase(), index))
  })
  const availableEnsembleInputs = new Set(['input', ...stepOutputIndexes.keys()])
  for (const value of ensembles) {
    if (!isRecord(value) || !Array.isArray(value.inputs)) return true
    const sources = value.inputs.map(input => isRecord(input) && typeof input.source === 'string'
      ? input.source.trim().toLowerCase()
      : '')
    if (sources.some(source => !availableEnsembleInputs.has(source)) || new Set(sources).size !== sources.length) return true
    availableEnsembleInputs.add(`${String(value.id).trim()}.${String(value.output_stem).trim()}`.toLowerCase())
  }
  const ensembleOutputs = new Map(ensembles.flatMap(value => (
    isRecord(value) && typeof value.id === 'string' && typeof value.output_stem === 'string'
      ? [[`${value.id.trim()}.${value.output_stem.trim()}`.toLowerCase(), value] as const]
      : []
  )))
  function collectEnsembleStepDependencies(
    ensemble: Record<string, unknown>,
    visiting = new Set<string>(),
  ): number[] | null {
    const ensembleId = String(ensemble.id || '').trim().toLowerCase()
    if (!ensembleId || visiting.has(ensembleId) || !Array.isArray(ensemble.inputs)) return null
    const nextVisiting = new Set(visiting).add(ensembleId)
    const dependencies: number[] = []
    for (const input of ensemble.inputs) {
      if (!isRecord(input) || typeof input.source !== 'string') return null
      const source = input.source.trim().toLowerCase()
      if (source === 'input') continue
      const stepIndex = stepOutputIndexes.get(source)
      if (stepIndex !== undefined) {
        dependencies.push(stepIndex)
        continue
      }
      const nested = ensembleOutputs.get(source)
      if (!nested) return null
      const nestedDependencies = collectEnsembleStepDependencies(nested, nextVisiting)
      if (!nestedDependencies) return null
      dependencies.push(...nestedDependencies)
    }
    return dependencies
  }
  if ((definition.steps as unknown[]).some((value, targetIndex) => {
    if (!isRecord(value)) return true
    if (value.input != null && typeof value.input !== 'string') return true
    const input = typeof value.input === 'string' ? value.input.trim().toLowerCase() : 'input'
    if (input === 'input') return false
    const sourceIndex = stepOutputIndexes.get(input)
    if (sourceIndex !== undefined) return sourceIndex >= targetIndex
    const ensemble = ensembleOutputs.get(input)
    if (!ensemble) return true
    const dependencies = collectEnsembleStepDependencies(ensemble)
    return !dependencies || dependencies.some(dependencyIndex => dependencyIndex >= targetIndex)
  })) return true

  const outputNodeKeys = new Map<string, string>()
  ;(definition.steps as unknown[]).forEach((value) => {
    if (!isRecord(value) || typeof value.id !== 'string' || !Array.isArray(value.stems)) return
    const stepId = value.id.trim()
    value.stems.forEach((stem) => {
      if (typeof stem === 'string' && stem.trim()) {
        outputNodeKeys.set(`${stepId}.${stem.trim()}`.toLowerCase(), `step:${stepId.toLowerCase()}`)
      }
    })
  })
  ensembles.forEach((value) => {
    if (!isRecord(value)) return
    const output = `${String(value.id || '').trim()}.${String(value.output_stem || '').trim()}`.toLowerCase()
    outputNodeKeys.set(output, `ensemble:${String(value.id || '').trim().toLowerCase()}`)
  })
  const edges = new Map<string, Set<string>>()
  const addEdge = (source: string, target: string) => {
    if (!source || source === 'input') return
    const sourceNode = outputNodeKeys.get(source)
    if (!sourceNode) return
    const targets = edges.get(sourceNode) || new Set<string>()
    targets.add(target)
    edges.set(sourceNode, targets)
  }
  ;(definition.steps as unknown[]).forEach((value) => {
    if (!isRecord(value) || typeof value.id !== 'string') return
    const input = typeof value.input === 'string' ? value.input.trim().toLowerCase() : 'input'
    addEdge(input, `step:${value.id.trim().toLowerCase()}`)
  })
  ensembles.forEach((value) => {
    if (!isRecord(value) || !Array.isArray(value.inputs)) return
    value.inputs.forEach((input) => {
      if (isRecord(input) && typeof input.source === 'string') {
        addEdge(input.source.trim().toLowerCase(), `ensemble:${String(value.id).trim().toLowerCase()}`)
      }
    })
  })
  const visitingNodes = new Set<string>()
  const visitedNodes = new Set<string>()
  const hasCycleFrom = (node: string): boolean => {
    if (visitingNodes.has(node)) return true
    if (visitedNodes.has(node)) return false
    visitingNodes.add(node)
    if ([...(edges.get(node) || [])].some(hasCycleFrom)) return true
    visitingNodes.delete(node)
    visitedNodes.add(node)
    return false
  }
  if ([...outputNodeKeys.values()].some(hasCycleFrom)) return true
  const ids = [
    ...(definition.steps as unknown[]).flatMap(value => isRecord(value) && typeof value.id === 'string' ? [value.id.trim()] : []),
    ...ensembles.flatMap(value => isRecord(value) && typeof value.id === 'string' ? [value.id.trim()] : []),
  ].filter(Boolean)
  if (ids.some(id => id.includes('.'))) return true
  const normalizedIds = ids.map(id => id.toLowerCase())
  if (new Set(normalizedIds).size !== normalizedIds.length) return true
  // The pymss YAML parser accepts exactly version 1. Rejecting unsupported
  // versions here prevents the simple editor from rewriting a newer schema
  // as version 1 on save and gives the run screen a deterministic error.
  return (definition.steps.length > 0 || ensembles.length > 0) && definition.version !== WORKFLOW_FORMAT_VERSION
}

export function isWorkflowSeparationNodeType(value: unknown): boolean {
  const type = String(value || '').toLowerCase()
  return type.endsWith('_separate') || type.endsWith('_separate_list')
}

const WORKFLOW_SAVE_NODE_TYPES = new Set([
  'pymss_save_audio',
  'SaveAudio',
  'SaveAudioMP3',
  'SaveAudioOpus',
  'SaveAudioAdvanced',
])

export function isWorkflowSaveNodeType(value: unknown): boolean {
  return WORKFLOW_SAVE_NODE_TYPES.has(String(value || ''))
}

/** Validate the current YAML representation without rewriting stored values. */
export function normalizeSimpleWorkflowDefinition(definition: Record<string, unknown>): Record<string, unknown> {
  if (definition.version !== WORKFLOW_FORMAT_VERSION || !Array.isArray(definition.steps)
    || Object.prototype.hasOwnProperty.call(definition, 'save_intermediate')) {
    throw new Error('Unsupported simple workflow schema; the original definition was preserved')
  }
  for (const step of definition.steps) {
    if (isRecord(step) && isRecord(step.save) && Object.values(step.save).some(value => typeof value === 'string' && /\.(?:wav|flac|mp3|m4a)$/iu.test(value.trim()))) {
      throw new Error('Unsupported simple workflow save schema; use output_names for filenames')
    }
  }
  return definition
}

/** Validate the current graph widget layout; never convert old pymss versions. */
export function normalizeGraphWorkflowDefinition(definition: Record<string, unknown>): Record<string, unknown> {
  if (!Array.isArray(definition.nodes)) return normalizeSimpleWorkflowDefinition(definition)
  if (definition.version !== COMFY_WORKFLOW_VERSION || !Array.isArray(definition.links)
    || definition.links.some(link => !Array.isArray(link) || link.length !== 6)) {
    throw new Error('Unsupported graph workflow schema; the original definition was preserved')
  }
  for (const node of definition.nodes) {
    if (!isRecord(node)) throw new Error('Invalid graph workflow node')
    const type = String(node.type || '').replace(/^pymss_/, '')
    const widgets = node.widgets_values
    const spec = specFor(String(node.type || '')) || specFor(type)
    if (spec && Array.isArray(widgets) && widgets.length > spec.widgets.length) {
      throw new Error('Unsupported graph widget schema; the original definition was preserved')
    }
    if (['custom_mss_separate', 'custom_mss_separate_list'].includes(type) && Array.isArray(widgets)
      && typeof widgets[3] === 'boolean' && typeof widgets[4] === 'string') {
      throw new Error('Unsupported graph widget schema; the original definition was preserved')
    }
    if (Array.isArray(widgets)) {
      const obsolete = (type === 'save_audio' && widgets.length > 5)
        || (type === 'SaveAudio' && widgets.length === 0)
        || (['SaveAudioMP3', 'SaveAudioOpus'].includes(type) && widgets.length === 1)
        || (type === 'SaveAudioAdvanced' && widgets.length >= 5)
        || (type === 'AudioConcat' && ['front', 'back'].includes(String(widgets[0]).toLowerCase()))
        || (['StringTrim', 'CaseConverter', 'RegexExtract'].includes(type) && widgets.length === 1)
      if (obsolete) throw new Error('Unsupported graph widget schema; the original definition was preserved')
    }
  }
  return definition
}
