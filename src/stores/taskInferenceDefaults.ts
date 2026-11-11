import { nextTick, type Ref } from 'vue'
import {
  ZERO_AS_UNSET_FIELDS, applyModelDefaultsToUi,
  normalizeEditableNumberValue, type InferenceNumberField,
} from '@/stores/taskInference'
import type { ModelDefaultInferenceParams } from '@/stores/model'
import type { PersistedSeparateModelState } from '@/stores/taskTypes'

type Deps = {
  selectedModelDefaults: Ref<ModelDefaultInferenceParams>
  selectedModelOverrides: Ref<ModelDefaultInferenceParams>
  selectedModelType: Ref<string | null>
  selectedModelUiDefaults: Ref<Record<string, number | boolean | null | undefined>>
  inferenceParamsDirty: Ref<boolean>
  batch_size: Ref<number | null>
  overlap_size: Ref<number | null>
  num_overlap: Ref<number | null>
  chunk_size: Ref<number | null>
  standardize: Ref<boolean>
  normalize: Ref<boolean>
  window_size: Ref<number | null>
  aggression: Ref<number | null>
  enable_post_process: Ref<boolean>
  post_process_threshold: Ref<number | null>
  high_end_process: Ref<boolean>
  selectedStems: Ref<string[]>
  selectedStemsCleared: Ref<boolean>
}

/** Model-scoped inference defaults and UI value restoration. */
export function makeTaskInferenceDefaults({
  selectedModelDefaults, selectedModelOverrides, selectedModelType, selectedModelUiDefaults,
  inferenceParamsDirty, batch_size, overlap_size, num_overlap, chunk_size, standardize,
  normalize, window_size, aggression, enable_post_process, post_process_threshold,
  high_end_process, selectedStems, selectedStemsCleared,
}: Deps) {
  let applyingModelDefaults = false

  function applySelectedModelDefaults(
    baseDefaults: ModelDefaultInferenceParams | undefined,
    modelType?: string | null,
    saved?: PersistedSeparateModelState,
    modelOverrides?: ModelDefaultInferenceParams,
    options: { force?: boolean } = {},
  ) {
    selectedModelDefaults.value = baseDefaults ? { ...baseDefaults } : {}
    selectedModelOverrides.value = modelOverrides ? { ...modelOverrides } : {}
    selectedModelType.value = modelType ?? null
    const next = applyModelDefaultsToUi(baseDefaults, modelType)
    const modelOverrideValue = <K extends keyof ModelDefaultInferenceParams>(key: K) => (
      modelOverrides && Object.prototype.hasOwnProperty.call(modelOverrides, key) ? modelOverrides[key] : undefined
    )
    const modelDefaultValue = <K extends keyof ModelDefaultInferenceParams>(key: K) => (
      baseDefaults && Object.prototype.hasOwnProperty.call(baseDefaults, key) ? baseDefaults[key] : undefined
    )
    const savedValue = <K extends keyof ModelDefaultInferenceParams & keyof PersistedSeparateModelState>(key: K, fallback: PersistedSeparateModelState[K]) => (
      saved && Object.prototype.hasOwnProperty.call(saved, key) ? saved[key] : fallback
    )
    const modelOrSavedValue = <K extends keyof ModelDefaultInferenceParams & keyof PersistedSeparateModelState>(key: K) => {
      const override = modelOverrideValue(key)
      if (override !== undefined) return override
      const modelDefault = modelDefaultValue(key)
      if (modelDefault !== undefined) return modelDefault
      return savedValue(key, next[key])
    }
    const valueOrDefault = <T>(value: T | undefined, fallback: T): T => value === undefined ? fallback : value
    const resolvedDefaults = {
      batch_size: valueOrDefault(modelOrSavedValue('batch_size'), 1),
      overlap_size: valueOrDefault(modelOrSavedValue('overlap_size'), 0),
      num_overlap: valueOrDefault(modelOrSavedValue('num_overlap'), 0),
      chunk_size: valueOrDefault(modelOrSavedValue('chunk_size'), 0),
      standardize: valueOrDefault(modelOrSavedValue('standardize'), false),
      normalize: valueOrDefault(modelOrSavedValue('normalize'), false),
      window_size: valueOrDefault(modelOrSavedValue('window_size'), 0),
      aggression: valueOrDefault(modelOrSavedValue('aggression'), 0),
      enable_post_process: valueOrDefault(modelOrSavedValue('enable_post_process'), false),
      post_process_threshold: valueOrDefault(modelOrSavedValue('post_process_threshold'), 0),
      high_end_process: valueOrDefault(modelOrSavedValue('high_end_process'), false),
    }
    selectedModelUiDefaults.value = { ...resolvedDefaults }
    if (inferenceParamsDirty.value && !options.force) return

    applyingModelDefaults = true
    batch_size.value = resolvedDefaults.batch_size
    overlap_size.value = resolvedDefaults.overlap_size
    num_overlap.value = resolvedDefaults.num_overlap
    chunk_size.value = resolvedDefaults.chunk_size
    standardize.value = resolvedDefaults.standardize
    normalize.value = resolvedDefaults.normalize
    window_size.value = resolvedDefaults.window_size
    aggression.value = resolvedDefaults.aggression
    enable_post_process.value = resolvedDefaults.enable_post_process
    post_process_threshold.value = resolvedDefaults.post_process_threshold
    high_end_process.value = resolvedDefaults.high_end_process
    selectedStems.value = Array.isArray(saved?.selectedStems) ? [...saved.selectedStems] : []
    selectedStemsCleared.value = saved?.selectedStemsCleared === true
    inferenceParamsDirty.value = false
    void nextTick(() => {
      applyingModelDefaults = false
    })
  }

  function getCurrentUiInferenceDefaults() {
    const defaults = Object.fromEntries(
      Object.entries(selectedModelUiDefaults.value).map(([key, value]) => [key, value === null ? undefined : value]),
    ) as ModelDefaultInferenceParams
    return applyModelDefaultsToUi(defaults, selectedModelType.value)
  }

  function restoreInferenceNumberFallback(field: InferenceNumberField) {
    if (!ZERO_AS_UNSET_FIELDS.has(field)) return
    const defaults = getCurrentUiInferenceDefaults()
    const nextValue = defaults[field]
    if (typeof nextValue !== 'number' || !Number.isFinite(nextValue)) return
    switch (field) {
      case 'batch_size':
        if (normalizeEditableNumberValue(batch_size.value, { zeroMeansUnset: true }) === null) batch_size.value = nextValue
        break
      case 'overlap_size':
        if (normalizeEditableNumberValue(overlap_size.value, { zeroMeansUnset: true }) === null) overlap_size.value = nextValue
        break
      case 'num_overlap':
        if (normalizeEditableNumberValue(num_overlap.value, { zeroMeansUnset: true }) === null) num_overlap.value = nextValue
        break
      case 'chunk_size':
        if (normalizeEditableNumberValue(chunk_size.value, { zeroMeansUnset: true }) === null) chunk_size.value = nextValue
        break
      case 'window_size':
        if (normalizeEditableNumberValue(window_size.value, { zeroMeansUnset: true }) === null) window_size.value = nextValue
        break
      case 'aggression':
      case 'post_process_threshold':
        break
    }
  }

  function normalizeInferenceInputsBeforeSubmit() {
    restoreInferenceNumberFallback('batch_size')
    restoreInferenceNumberFallback('overlap_size')
    restoreInferenceNumberFallback('num_overlap')
    restoreInferenceNumberFallback('chunk_size')
    restoreInferenceNumberFallback('window_size')
  }

  return {
    applySelectedModelDefaults, getCurrentUiInferenceDefaults,
    restoreInferenceNumberFallback, normalizeInferenceInputsBeforeSubmit,
    isApplyingModelDefaults: () => applyingModelDefaults,
  }
}
