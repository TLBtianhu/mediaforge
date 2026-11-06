import type { ModelDefaultInferenceParams } from './model.ts'
import {
  isApolloModelType,
  isVrModelType,
  preferCurrentOverride,
  resolveBooleanOverride,
  resolveNumberOverride,
} from './taskInference.ts'

export type InferenceUiValues = {
  batch_size: number | null | undefined
  overlap_size: number | null | undefined
  num_overlap: number | null | undefined
  chunk_size: number | null | undefined
  window_size: number | null | undefined
  aggression: number | null | undefined
  enable_post_process: boolean
  post_process_threshold: number | null | undefined
  high_end_process: boolean
  standardize: boolean
  normalize: boolean
}

export function buildInferenceParams(input: {
  modelType?: string | null
  defaults: ModelDefaultInferenceParams
  modelOverrides: Record<string, unknown>
  dirty: boolean
  values: InferenceUiValues
}): Record<string, unknown> {
  const { defaults, modelOverrides, values } = input
  const useModelOverrideFallback = !input.dirty
  const inferenceParams: Record<string, unknown> = {}
  const batchSizeOverride = preferCurrentOverride(resolveNumberOverride(defaults, 'batch_size', values.batch_size, { zeroMeansUnset: true }), modelOverrides, 'batch_size', useModelOverrideFallback)
  if (batchSizeOverride !== null) inferenceParams.batch_size = batchSizeOverride
  if (isVrModelType(input.modelType)) {
    const windowSizeOverride = preferCurrentOverride(resolveNumberOverride(defaults, 'window_size', values.window_size, { zeroMeansUnset: true }), modelOverrides, 'window_size', useModelOverrideFallback)
    const aggressionOverride = preferCurrentOverride(resolveNumberOverride(defaults, 'aggression', values.aggression), modelOverrides, 'aggression', useModelOverrideFallback)
    const enablePostProcessOverride = preferCurrentOverride(resolveBooleanOverride(defaults, 'enable_post_process', values.enable_post_process), modelOverrides, 'enable_post_process', useModelOverrideFallback)
    const postProcessThresholdOverride = preferCurrentOverride(resolveNumberOverride(defaults, 'post_process_threshold', values.post_process_threshold), modelOverrides, 'post_process_threshold', useModelOverrideFallback)
    const highEndProcessOverride = preferCurrentOverride(resolveBooleanOverride(defaults, 'high_end_process', values.high_end_process), modelOverrides, 'high_end_process', useModelOverrideFallback)
    const normalizeOverride = preferCurrentOverride(resolveBooleanOverride(defaults, 'normalize', values.normalize), modelOverrides, 'normalize', useModelOverrideFallback)
    if (windowSizeOverride !== null) inferenceParams.window_size = windowSizeOverride
    if (aggressionOverride !== null) inferenceParams.aggression = aggressionOverride
    if (enablePostProcessOverride !== null) inferenceParams.enable_post_process = enablePostProcessOverride
    if (postProcessThresholdOverride !== null) inferenceParams.post_process_threshold = postProcessThresholdOverride
    if (highEndProcessOverride !== null) inferenceParams.high_end_process = highEndProcessOverride
    if (normalizeOverride !== null) inferenceParams.normalize = normalizeOverride
    return inferenceParams
  }
  const overlapSizeOverride = preferCurrentOverride(resolveNumberOverride(defaults, 'overlap_size', values.overlap_size, { zeroMeansUnset: true }), modelOverrides, 'overlap_size', useModelOverrideFallback)
  const numOverlapOverride = isApolloModelType(input.modelType) ? null : preferCurrentOverride(resolveNumberOverride(defaults, 'num_overlap', values.num_overlap, { zeroMeansUnset: true }), modelOverrides, 'num_overlap', useModelOverrideFallback)
  const chunkSizeOverride = preferCurrentOverride(resolveNumberOverride(defaults, 'chunk_size', values.chunk_size, { zeroMeansUnset: true }), modelOverrides, 'chunk_size', useModelOverrideFallback)
  const standardizeOverride = preferCurrentOverride(resolveBooleanOverride(defaults, 'standardize', values.standardize), modelOverrides, 'standardize', useModelOverrideFallback)
  const normalizeOverride = preferCurrentOverride(resolveBooleanOverride(defaults, 'normalize', values.normalize), modelOverrides, 'normalize', useModelOverrideFallback)
  if (overlapSizeOverride !== null) inferenceParams.overlap_size = overlapSizeOverride
  if (numOverlapOverride !== null) inferenceParams.num_overlap = numOverlapOverride
  if (chunkSizeOverride !== null) inferenceParams.chunk_size = chunkSizeOverride
  if (standardizeOverride !== null) inferenceParams.standardize = standardizeOverride
  if (normalizeOverride !== null) inferenceParams.normalize = normalizeOverride
  return inferenceParams
}

export function modelInferenceParamsFromState(input: {
  modelType?: string | null
  defaults: ModelDefaultInferenceParams
  overrides: Record<string, unknown>
  saved: Record<string, unknown>
}): Record<string, unknown> {
  const source = { ...input.saved, ...input.overrides }
  const params: Record<string, unknown> = {}
  const numberParam = (key: keyof ModelDefaultInferenceParams, options?: { zeroMeansUnset?: boolean }) => {
    const value = source[key]
    if (typeof value !== 'number' || !Number.isFinite(value)) return
    if (options?.zeroMeansUnset && value <= 0) return
    if (input.defaults[key] === value) return
    params[key] = value
  }
  const booleanParam = (key: keyof ModelDefaultInferenceParams) => {
    const value = source[key]
    if (typeof value !== 'boolean') return
    if (input.defaults[key] === value) return
    if (!value && input.defaults[key] === undefined) return
    params[key] = value
  }
  numberParam('batch_size', { zeroMeansUnset: true })
  if (isVrModelType(input.modelType)) {
    numberParam('window_size', { zeroMeansUnset: true })
    numberParam('aggression')
    numberParam('post_process_threshold')
    booleanParam('enable_post_process')
    booleanParam('high_end_process')
    booleanParam('normalize')
    return params
  }
  numberParam('overlap_size', { zeroMeansUnset: true })
  if (!isApolloModelType(input.modelType)) numberParam('num_overlap', { zeroMeansUnset: true })
  numberParam('chunk_size', { zeroMeansUnset: true })
  booleanParam('standardize')
  booleanParam('normalize')
  return params
}
