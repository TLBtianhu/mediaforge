/**
 * 模型库的推理参数编辑器：草稿、槽位接口/渲染计划、打开/保存/重置。
 *
 * 槽位接口来自后端（list_models 的 slots），布局来自前端布局档；
 * 这里只把两者合成渲染计划，不决定任何控件长什么样。
 */
import type { ComputedRef } from 'vue'
import { fromWireParams, paramsFromInterface, toWireParams } from '../../litegraph/inferenceParams.ts'
import { layoutFor } from '../../litegraph/layouts.ts'
import { buildRenderPlan, type ModelInterface } from '../../litegraph/renderPlan.ts'
import { inferenceChunkSizeIssue, resolveInferenceChunkStep, resolveInferenceSampleStep } from '../../features/inference/sampleStep.ts'

type Deps = Record<string, any> & {
  computed: <T>(source: () => T) => ComputedRef<T>
}

export function makeInferenceEditor(deps: Deps) {
  const { computed, modelStore, taskStore } = deps

  const inferenceEditorMeta = computed(() => modelStore.models.find((item: any) => item.name === deps.inferenceEditorModel.value?.name)?.inferenceParamMeta
    || deps.inferenceEditorModel.value?.inferenceParamMeta)

  const inferenceEditorInterface = computed<ModelInterface | null>(() => {
    const model = deps.inferenceEditorModel.value
    const slots = model?.slots
    if (!model?.name || !slots || Object.keys(slots).length === 0) return null
    return { modelId: model.name, task: model.slotTask || 'separation', slots }
  })

  const inferenceEditorPlan = computed(() => {
    const model = inferenceEditorInterface.value
    return model ? buildRenderPlan(model, layoutFor(model.task)) : null
  })

  // 打开/切换模型时把已保存的原生参数翻回槽位值（bind 负责翻译）
  deps.watch(inferenceEditorInterface, (current: ModelInterface | null) => {
    if (!current) {
      deps.inferenceEditorParams.value = {}
      return
    }
    const saved = (deps.inferenceEditorModel.value?.defaultInferenceParams || {}) as Record<string, unknown>
    deps.inferenceEditorParams.value = { ...paramsFromInterface(current), ...fromWireParams(current, saved) }
  }, { immediate: true })

  const inferenceEditorSampleStep = computed(() => inferenceEditorMeta.value?.recommendedSampleStep)
  const inferenceEditorOverlapStep = computed(() => resolveInferenceSampleStep(inferenceEditorMeta.value, 1))
  const inferenceEditorChunkStep = computed(() => resolveInferenceChunkStep(inferenceEditorMeta.value, 1024))
  const inferenceEditorChunkConstraint = computed(() => inferenceEditorMeta.value?.chunkSizeConstraint)
  const inferenceEditorChunkIssue = computed(() => inferenceChunkSizeIssue(deps.inferenceDraft.value.chunk_size, inferenceEditorMeta.value))

  function updateInferenceDraftSize(key: 'overlap_size' | 'chunk_size', value: number | null) {
    deps.inferenceDraft.value[key] = value as number
  }

  function inferenceValue(model: any, key: string, fallback: number) {
    const value = model?.defaultInferenceParams?.[key]
    return typeof value === 'number' && Number.isFinite(value) ? value : fallback
  }

  function resolveInferenceDraft(model: any) {
    return {
      batch_size: inferenceValue(model, 'batch_size', 1),
      overlap_size: inferenceValue(model, 'overlap_size', 0),
      chunk_size: inferenceValue(model, 'chunk_size', 0),
    }
  }

  function syncInferenceDraft(model: any) {
    deps.inferenceDraft.value = resolveInferenceDraft(model)
  }

  function hasInferenceOverride(model: any) {
    return Boolean(model?.name && modelStore.getModelInferenceOverrides(model.name))
  }

  function slotParamsChanged(model: any) {
    const current = inferenceEditorInterface.value
    if (!current) return false
    const base = (model?.defaultInferenceParams || {}) as Record<string, unknown>
    return Object.entries(toWireParams(current, deps.inferenceEditorParams.value))
      .some(([key, value]) => base[key] !== value)
  }

  function hasInferenceDraftChanges(model: any) {
    const defaults = resolveInferenceDraft(model)
    return deps.inferenceDraft.value.batch_size !== defaults.batch_size
      || deps.inferenceDraft.value.overlap_size !== defaults.overlap_size
      || deps.inferenceDraft.value.chunk_size !== defaults.chunk_size
      || slotParamsChanged(model)
  }

  function canResetInferenceDefaults(model: any) {
    return hasInferenceOverride(model) || hasInferenceDraftChanges(model)
  }

  function applyDefaultsToActiveTask(model: any, selected: any) {
    if (deps.selectedModel.value !== model.name) return
    taskStore.applySelectedModelDefaults(
      modelStore.getModelBaseInferenceDefaults(model.name) || selected.defaultInferenceParams,
      selected.modelType,
      taskStore.getSavedModelState(model.name),
      modelStore.getModelInferenceOverrides(model.name),
      { force: true },
    )
  }

  async function saveInferenceDefaults() {
    const model = deps.inferenceEditorModel.value
    if (!model) return
    if (inferenceEditorChunkIssue.value) {
      deps.message.warning(deps.t('inference.invalidChunkSize', inferenceEditorChunkIssue.value))
      return
    }
    try {
      const current = inferenceEditorInterface.value
      const draft = current
        ? { ...deps.inferenceDraft.value, ...toWireParams(current, deps.inferenceEditorParams.value) }
        : deps.inferenceDraft.value
      await modelStore.setModelInferenceOverrides(model.name, draft)
      const selected = modelStore.selectedInfo?.name === model.name ? modelStore.selectedInfo : model
      applyDefaultsToActiveTask(model, selected)
      closeInferenceEditor()
      deps.message.success(deps.t('models.inferenceDefaultsSaved'))
    } catch (error) {
      deps.studioLog.error('Failed to persist model inference defaults', error)
      deps.message.error(deps.t('models.inferenceDefaultsSaveFailed'))
    }
  }

  async function resetInferenceDefaults() {
    const model = deps.inferenceEditorModel.value
    if (!model) return
    try {
      if (hasInferenceOverride(model)) {
        await modelStore.resetModelInferenceOverrides(model.name)
      }
      const latest = modelStore.models.find((item: any) => item.name === model.name) || model
      syncInferenceDraft(latest)
      const selected = modelStore.selectedInfo?.name === model.name ? modelStore.selectedInfo : latest
      applyDefaultsToActiveTask(model, selected)
      deps.message.success(deps.t('models.inferenceDefaultsReset'))
    } catch (error) {
      deps.studioLog.error('Failed to restore model inference defaults', error)
      deps.message.error(deps.t('models.inferenceDefaultsResetFailed'))
    }
  }

  function openInferenceEditor(model: any) {
    deps.inferenceEditorModel.value = model
    syncInferenceDraft(model)
    deps.showInferenceEditor.value = true
  }

  function closeInferenceEditor() {
    deps.showInferenceEditor.value = false
    deps.inferenceEditorModel.value = null
  }

  function handleInferenceEditorUpdate(value: boolean) {
    deps.showInferenceEditor.value = value
    if (!value) deps.inferenceEditorModel.value = null
  }

  return {
    inferenceEditorMeta, inferenceEditorInterface, inferenceEditorPlan,
    inferenceEditorSampleStep, inferenceEditorOverlapStep, inferenceEditorChunkStep,
    inferenceEditorChunkConstraint, inferenceEditorChunkIssue,
    updateInferenceDraftSize, inferenceValue, resolveInferenceDraft, syncInferenceDraft,
    hasInferenceOverride, slotParamsChanged, hasInferenceDraftChanges, canResetInferenceDefaults,
    saveInferenceDefaults, resetInferenceDefaults,
    openInferenceEditor, closeInferenceEditor, handleInferenceEditorUpdate,
  }
}
