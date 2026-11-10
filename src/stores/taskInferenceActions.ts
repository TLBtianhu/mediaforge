import type { Ref } from 'vue'

type Deps = {
  selectedModelDefaults: Ref<Record<string, unknown>>
  selectedModelOverrides: Ref<Record<string, unknown>>
  inferenceParamsDirty: Ref<boolean>
  batch_size: Ref<number | null>
  overlap_size: Ref<number | null>
  num_overlap: Ref<number | null>
  chunk_size: Ref<number | null>
  window_size: Ref<number | null>
  aggression: Ref<number | null>
  enable_post_process: Ref<boolean>
  post_process_threshold: Ref<number | null>
  high_end_process: Ref<boolean>
  standardize: Ref<boolean>
  normalize: Ref<boolean>
  getSavedModelState: (modelName: string) => Record<string, unknown> | null
  useModelStore: () => any
  buildInferenceParamsFromValues: (input: any) => Record<string, unknown>
  modelInferenceParamsFromValues: (input: any) => Record<string, unknown>
}

export function makeTaskInferenceParams({
  selectedModelDefaults, selectedModelOverrides, inferenceParamsDirty,
  batch_size, overlap_size, num_overlap, chunk_size, window_size, aggression,
  enable_post_process, post_process_threshold, high_end_process, standardize, normalize,
  getSavedModelState, useModelStore, buildInferenceParamsFromValues, modelInferenceParamsFromValues,
}: Deps) {
  function buildInferenceParams(modelType?: string | null): Record<string, unknown> {
    return buildInferenceParamsFromValues({
      modelType,
      defaults: selectedModelDefaults.value,
      modelOverrides: selectedModelOverrides.value,
      dirty: inferenceParamsDirty.value,
      values: {
        batch_size: batch_size.value,
        overlap_size: overlap_size.value,
        num_overlap: num_overlap.value,
        chunk_size: chunk_size.value,
        window_size: window_size.value,
        aggression: aggression.value,
        enable_post_process: enable_post_process.value,
        post_process_threshold: post_process_threshold.value,
        high_end_process: high_end_process.value,
        standardize: standardize.value,
        normalize: normalize.value,
      },
    })
  }

  function modelInferenceParamsFromState(modelName: string, modelType?: string | null): Record<string, unknown> {
    const modelStore = useModelStore()
    if (modelStore.selectedModel === modelName) return buildInferenceParams(modelType)
    const entry = modelStore.models.find((item: any) => item.name === modelName) || null
    const defaults = modelStore.hasKnownModelInferenceBase(modelName)
      ? (modelStore.getModelBaseInferenceDefaults(modelName) || {})
      : (entry?.defaultInferenceParams || {})
    const overrides = modelStore.getModelInferenceOverrides(modelName) || {}
    const saved = getSavedModelState(modelName) || {}
    return modelInferenceParamsFromValues({ modelType, defaults, overrides, saved })
  }

  return { buildInferenceParams, modelInferenceParamsFromState }
}
