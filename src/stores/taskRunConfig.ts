/**
 * 单次分离与工作流运行配置：设置快照、运行时设备和工作流模型参数。
 * 所有有状态依赖由 task store 注入。
 */
import type { WorkflowEntry } from './workflow.ts'
import type { ModelDefaultInferenceParams, ModelEntry } from './model.ts'
import type { OutputLayout, OutputNamingConfig, SeparationRunConfig } from './task.ts'

type Deps = Record<string, any>

export function makeTaskRunConfig(deps: Deps) {
  const {
    useSettingsStore, useAppStore, useModelStore, selectedStems, useTta,
    normalizeOutputNaming, CURRENT_INFERENCE_PARAMS_VERSION,
    detectWorkflowFormat, resolveWorkflowRuntimeDefaults, prepareWorkflowDefinitionForRun,
  } = deps

  function buildRunConfig(inferenceParams: Record<string, unknown>, modelType?: string | null, outputLayout: OutputLayout = 'folders', outputNaming?: OutputNamingConfig): SeparationRunConfig {
    const settings = useSettingsStore()
    const app = useAppStore()
    const runtimeDevice = settings.getRuntimeDeviceConfig(app.envInfo)
    return {
      runMode: 'model',
      modelDir: settings.modelDir || null,
      downloadSource: settings.downloadSource,
      downloadMethod: settings.downloadMethod,
      modelType: modelType ?? null,
      outputLayout,
      outputNaming: normalizeOutputNaming(outputNaming),
      device: runtimeDevice.device,
      deviceIds: runtimeDevice.deviceIds,
      outputFormat: settings.defaultFormat,
      selectedStems: [...selectedStems.value],
      useTta: useTta.value,
      debug: settings.developerMode,
      audioParams: settings.getAudioParams(),
      inferenceParamsVersion: CURRENT_INFERENCE_PARAMS_VERSION,
      inferenceParams,
    }
  }

  function workflowModelInferenceParams(definition: Record<string, unknown>) {
    if (detectWorkflowFormat(definition) !== 'simple' || !Array.isArray(definition.steps)) {
      return {} as Record<string, Record<string, unknown>>
    }
    const modelStore = useModelStore()
    const result: Record<string, Record<string, unknown>> = {}
    for (const value of definition.steps) {
      if (!value || typeof value !== 'object' || Array.isArray(value)) continue
      const step = value as Record<string, unknown>
      const modelName = String(step.model || '').trim()
      if (!modelName || result[modelName]) continue
      const entry = modelStore.models.find((item: ModelEntry) => (
        item.name.localeCompare(modelName, undefined, { sensitivity: 'accent' }) === 0
        || item.aliases?.some((alias: string) => alias.localeCompare(modelName, undefined, { sensitivity: 'accent' }) === 0)
      ))
      const overrides = modelStore.getModelInferenceOverrides(entry?.name || modelName)
      if (!overrides) continue
      const stepModelType = typeof step.model_type === 'string' ? step.model_type.trim() : ''
      const modelType = String(stepModelType || entry?.modelType || '').trim().toLowerCase()
      const allowed = new Set<keyof ModelDefaultInferenceParams>(modelType === 'vr'
        ? ['batch_size', 'window_size', 'aggression', 'enable_post_process', 'post_process_threshold', 'high_end_process', 'normalize']
        : ['batch_size', 'overlap_size', 'chunk_size', 'standardize', 'normalize'])
      const compatible = Object.fromEntries(
        Object.entries(overrides).filter(([key, value]) => {
          if (!allowed.has(key as keyof ModelDefaultInferenceParams)) return false
          if (['batch_size', 'overlap_size', 'chunk_size', 'window_size'].includes(key)) {
            return typeof value === 'number' && Number.isFinite(value) && value > 0
          }
          return true
        }),
      )
      if (Object.keys(compatible).length) result[modelName] = compatible
    }
    return result
  }

  function buildWorkflowRunConfig(workflow: WorkflowEntry, outputLayout: OutputLayout = 'folders', outputNaming?: OutputNamingConfig): SeparationRunConfig {
    const settings = useSettingsStore()
    const app = useAppStore()
    const runtimeDevice = settings.getRuntimeDeviceConfig(app.envInfo)
    const defaults = resolveWorkflowRuntimeDefaults(workflow.definition, {
      device: runtimeDevice.device,
      outputFormat: settings.defaultFormat || 'wav',
      modelDir: settings.modelDir || null,
    })
    return {
      runMode: 'workflow',
      modelDir: defaults.modelDir,
      downloadSource: settings.downloadSource,
      downloadMethod: settings.downloadMethod,
      workflowId: workflow.id,
      workflowName: workflow.name,
      workflowDefinition: prepareWorkflowDefinitionForRun(
        workflow.definition,
        {
          device: defaults.device,
          outputFormat: defaults.outputFormat,
          modelInferenceParams: workflowModelInferenceParams(workflow.definition),
        },
      ),
      outputLayout,
      outputNaming: normalizeOutputNaming(outputNaming),
      device: defaults.device,
      deviceIds: defaults.device === runtimeDevice.device || defaults.device === 'auto' ? runtimeDevice.deviceIds : [0],
      outputFormat: defaults.outputFormat,
      selectedStems: [],
      useTta: useTta.value,
      debug: settings.developerMode,
      audioParams: settings.getAudioParams(),
      inferenceParamsVersion: CURRENT_INFERENCE_PARAMS_VERSION,
      inferenceParams: {},
    }
  }

  return { buildRunConfig, workflowModelInferenceParams, buildWorkflowRunConfig }
}
