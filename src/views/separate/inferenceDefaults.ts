// SeparateView 的推理默认值：收集、应用、保存、重置。
// 通过 deps 注入组件的 ref 与 store，便于在 VM 里用桩数据直接测试。
import type { ModelDefaultInferenceParams, ModelEntry } from '@/stores/model'

type Logger = { error: (message: string, error: unknown) => void }
type Deps = Record<string, any>

export function makeInferenceDefaults(deps: Deps) {
  function collectCurrentModelInferenceDefaults(): ModelDefaultInferenceParams {
    const defaults = deps.currentModelDefaults.value
    const hasField = (key: keyof ModelDefaultInferenceParams) => Object.prototype.hasOwnProperty.call(defaults, key)
    const next: ModelDefaultInferenceParams = {}

    if (hasField('batch_size') && typeof deps.batch_size.value === 'number' && Number.isFinite(deps.batch_size.value)) {
      next.batch_size = deps.batch_size.value
    }
    if (hasField('overlap_size') && typeof deps.overlap_size.value === 'number' && Number.isFinite(deps.overlap_size.value)) {
      next.overlap_size = deps.overlap_size.value
    }
    if (hasField('num_overlap') && !deps.isApolloModel.value && typeof deps.num_overlap.value === 'number' && Number.isFinite(deps.num_overlap.value)) {
      next.num_overlap = deps.num_overlap.value
    }
    if (hasField('chunk_size') && typeof deps.chunk_size.value === 'number' && Number.isFinite(deps.chunk_size.value)) {
      next.chunk_size = deps.chunk_size.value
    }
    if (hasField('window_size') && typeof deps.window_size.value === 'number' && Number.isFinite(deps.window_size.value)) {
      next.window_size = deps.window_size.value
    }
    if (hasField('aggression') && typeof deps.aggression.value === 'number' && Number.isFinite(deps.aggression.value)) {
      next.aggression = deps.aggression.value
    }
    if (hasField('enable_post_process')) next.enable_post_process = deps.enable_post_process.value
    if (hasField('post_process_threshold') && typeof deps.post_process_threshold.value === 'number' && Number.isFinite(deps.post_process_threshold.value)) {
      next.post_process_threshold = deps.post_process_threshold.value
    }
    if (hasField('high_end_process')) next.high_end_process = deps.high_end_process.value
    if (deps.showStandardizeField.value) next.standardize = deps.standardize.value
    if (deps.showNormalizeField.value) next.normalize = deps.normalize.value

    return next
  }

  function applyCurrentModelInferenceDefaults(info: ModelEntry) {
    const latest = deps.model.models.find((item: ModelEntry) => item.name === info.name) || info
    deps.task.applySelectedModelDefaults(
      deps.model.getModelBaseInferenceDefaults(info.name) || latest.defaultInferenceParams,
      latest.modelType,
      deps.task.getSavedModelState(info.name),
      deps.model.getModelInferenceOverrides(info.name),
      { force: true },
    )
  }

  async function saveCurrentModelInferenceDefaults() {
    const info = deps.currentModelInfo.value
    if (!info) return
    deps.task.normalizeInferenceInputsBeforeSubmit()
    const overrides = collectCurrentModelInferenceDefaults()
    if (!Object.keys(overrides).length) return
    if (deps.currentChunkIssue.value) {
      deps.message.warning(deps.t('inference.invalidChunkSize', deps.currentChunkIssue.value))
      return
    }
    try {
      await deps.model.setModelInferenceOverrides(info.name, overrides)
      applyCurrentModelInferenceDefaults(info)
      deps.message.success(deps.t('models.inferenceDefaultsSaved'))
    } catch (error) {
      ;(deps.studioLog as Logger).error('Failed to persist model inference defaults', error)
      deps.message.error(deps.t('models.inferenceDefaultsSaveFailed'))
    }
  }

  async function resetCurrentModelInferenceDefaults() {
    const info = deps.currentModelInfo.value
    if (!info) return
    try {
      await deps.model.resetModelInferenceOverrides(info.name)
      applyCurrentModelInferenceDefaults(info)
      deps.message.success(deps.t('models.inferenceDefaultsReset'))
    } catch (error) {
      ;(deps.studioLog as Logger).error('Failed to restore model inference defaults', error)
      deps.message.error(deps.t('models.inferenceDefaultsResetFailed'))
    }
  }

  return {
    collectCurrentModelInferenceDefaults,
    applyCurrentModelInferenceDefaults,
    saveCurrentModelInferenceDefaults,
    resetCurrentModelInferenceDefaults,
  }
}
