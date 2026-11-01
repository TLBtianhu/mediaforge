/**
 * 推理参数的覆盖与默认值：可编辑数值规范化、按默认值判空、覆盖优先级、UI 默认档。
 *
 * 纯函数，不碰 store 状态；模型类型从 stores/model 引入。
 */
import type { ModelDefaultInferenceParams } from './model.ts'

export type InferenceNumberField =
  | 'batch_size'
  | 'overlap_size'
  | 'num_overlap'
  | 'chunk_size'
  | 'window_size'
  | 'aggression'
  | 'post_process_threshold'

export const ZERO_AS_UNSET_FIELDS = new Set<InferenceNumberField>([
  'batch_size',
  'overlap_size',
  'num_overlap',
  'chunk_size',
  'window_size',
])

export function normalizeEditableNumberValue(
  current: number | null | undefined,
  options: { zeroMeansUnset: boolean },
) {
  const { zeroMeansUnset } = options
  if (typeof current !== 'number' || !Number.isFinite(current)) return null
  if (zeroMeansUnset && current <= 0) return null
  return current
}

export function resolveNumberOverride(
  defaults: ModelDefaultInferenceParams | undefined,
  key: keyof ModelDefaultInferenceParams,
  current: number | null | undefined,
  options?: { zeroMeansUnset?: boolean },
) {
  const zeroMeansUnset = options?.zeroMeansUnset ?? false
  const normalizedCurrent = normalizeEditableNumberValue(current, { zeroMeansUnset })
  if (normalizedCurrent === null) return null
  const defaultValue = defaults?.[key]
  if (typeof defaultValue === 'number') return normalizedCurrent === defaultValue ? null : normalizedCurrent
  return normalizedCurrent
}

export function resolveBooleanOverride(defaults: ModelDefaultInferenceParams | undefined, key: keyof ModelDefaultInferenceParams, current: boolean) {
  const defaultValue = defaults?.[key]
  if (typeof defaultValue === 'boolean') return current === defaultValue ? null : current
  return current ? true : null
}

export function preferCurrentOverride<T>(
  currentOverride: T | null,
  modelOverrides: ModelDefaultInferenceParams,
  key: keyof ModelDefaultInferenceParams,
  useModelOverrideFallback: boolean,
) {
  if (currentOverride !== null) return currentOverride
  if (!useModelOverrideFallback) return null
  return Object.prototype.hasOwnProperty.call(modelOverrides, key) ? modelOverrides[key] as T : null
}

export function isVrModelType(modelType?: string | null) {
  return String(modelType || '').trim().toLowerCase() === 'vr'
}

export function isApolloModelType(modelType?: string | null) {
  return String(modelType || '').trim().toLowerCase() === 'apollo'
}

export function applyModelDefaultsToUi(
  defaults: ModelDefaultInferenceParams | undefined,
  modelType?: string | null,
) {
  const vrModel = isVrModelType(modelType)
  const apolloModel = isApolloModelType(modelType)
  return {
    batch_size: defaults?.batch_size ?? (vrModel ? 2 : 1),
    overlap_size: vrModel ? 0 : (defaults?.overlap_size ?? 0),
    num_overlap: (vrModel || apolloModel) ? 0 : (defaults?.num_overlap ?? 0),
    chunk_size: vrModel ? 0 : (defaults?.chunk_size ?? 0),
    standardize: false,
    normalize: false,
    window_size: vrModel ? (defaults?.window_size ?? 512) : 0,
    aggression: vrModel ? (defaults?.aggression ?? 5) : 0,
    enable_post_process: vrModel ? (defaults?.enable_post_process ?? false) : false,
    post_process_threshold: vrModel ? (defaults?.post_process_threshold ?? 0.2) : 0,
    high_end_process: vrModel ? (defaults?.high_end_process ?? false) : false,
  }
}
