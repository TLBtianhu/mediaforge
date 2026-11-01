/**
 * 运行配置规范化：输出布局、输出命名、runConfig 的版本化默认值与旧参数兼容。
 *
 * 纯函数，不碰 store 状态；类型从 task.ts 引入（import type 擦除，无运行时循环）。
 */
import type { OutputLayout, OutputNamingConfig, SeparationRunConfig } from './task.ts'

export const CURRENT_INFERENCE_PARAMS_VERSION = 3

export function normalizeOutputLayout(value: unknown): OutputLayout {
  return value === 'flat' ? 'flat' : 'folders'
}

export function normalizeOutputNaming(value: unknown): OutputNamingConfig | undefined {
  if (!value || typeof value !== 'object') return undefined
  const raw = value as Partial<OutputNamingConfig>
  const template = typeof raw.template === 'string' ? raw.template.trim() : ''
  const stemOrder = Array.isArray(raw.stemOrder)
    ? raw.stemOrder.map(item => String(item || '').trim()).filter(Boolean)
    : []
  return {
    enabled: raw.enabled === true,
    template,
    stemOrder,
  }
}

export function normalizeRunConfig(runConfig?: SeparationRunConfig): SeparationRunConfig | undefined {
  if (!runConfig) return undefined
  const next: SeparationRunConfig = {
    ...runConfig,
    downloadMethod: runConfig.downloadMethod || 'aria2c',
    outputLayout: normalizeOutputLayout(runConfig.outputLayout),
    outputNaming: normalizeOutputNaming(runConfig.outputNaming),
    selectedStems: Array.isArray(runConfig.selectedStems)
      ? runConfig.selectedStems.map(item => String(item || '').trim()).filter(Boolean)
      : [],
    audioParams: { ...(runConfig.audioParams || {}) },
    inferenceParams: { ...(runConfig.inferenceParams || {}) },
  }
  const version = next.inferenceParamsVersion || 0
  if (version >= CURRENT_INFERENCE_PARAMS_VERSION) {
    next.inferenceParamsVersion = CURRENT_INFERENCE_PARAMS_VERSION
    return next
  }

  const params = { ...(next.inferenceParams || {}) }
  const hasStandardize = Object.prototype.hasOwnProperty.call(params, 'standardize')
  const hasNormalize = Object.prototype.hasOwnProperty.call(params, 'normalize')

  if (!hasStandardize && !hasNormalize) {
    // Legacy desktop only omitted `normalize` when the old input-standardize
    // checkbox was enabled. Preserve that behavior on retry/history restore.
    params.standardize = true
    params.normalize = false
  } else if (!hasStandardize && hasNormalize) {
    // Legacy desktop used `normalize` to mean input standardization.
    params.standardize = Boolean(params.normalize)
    params.normalize = false
  } else if (hasStandardize && !hasNormalize) {
    // First compatibility pass already switched to `standardize`, but output
    // normalize had not been made explicit yet.
    params.normalize = false
  }

  next.inferenceParams = params
  next.inferenceParamsVersion = CURRENT_INFERENCE_PARAMS_VERSION
  return next
}
