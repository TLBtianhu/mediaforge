/**
 * 槽位参数 ↔ 线上参数。
 *
 * UI 内部只认**槽位名**（`chunk_size`、`overlap`），线上要的是**模型原生键**
 * （`chunk_size`、`overlap_size`…）。翻译只发生在这里，由模型接口的 `bind` 决定。
 *
 * 于是：
 *   - 换模型只换接口，UI 代码不动
 *   - 原生键改名只改接口里的 `bind`，UI 与线上格式各自独立
 */
import type { ModelInterface } from './renderPlan.ts'

export type SlotParams = Record<string, unknown>

/** 接口里的默认值，作为表单初值。 */
export function paramsFromInterface(model: ModelInterface): SlotParams {
  const params: SlotParams = {}
  for (const [slot, binding] of Object.entries(model.slots || {})) {
    params[slot] = binding.default ?? (binding.choices ? binding.choices[0] : '')
  }
  return params
}

function clampNumber(value: number, min?: number, max?: number): number {
  let next = value
  if (typeof min === 'number' && next < min) next = min
  if (typeof max === 'number' && next > max) next = max
  return next
}

export interface CoerceResult {
  params: SlotParams
  /** 被丢弃的键（模型不支持的、或类型不对的），调用方可以提示或记录 */
  rejected: string[]
}

/**
 * 把外部来的参数收敛到接口允许的范围内。
 *
 * 严格收窄是有意的：多出来的键说明调用方按旧接口传参，
 * 静默丢掉比让 worker 收到未知键更安全——但也必须报告，不能装没看见。
 */
export function coerceParams(model: ModelInterface, raw: SlotParams | null | undefined): CoerceResult {
  const params: SlotParams = {}
  const rejected: string[] = []
  for (const [key, value] of Object.entries(raw || {})) {
    const binding = model.slots[key]
    if (!binding) {
      rejected.push(key)
      continue
    }
    if (typeof value === 'number') {
      params[key] = clampNumber(value, binding.min, binding.max)
      continue
    }
    if (typeof value === 'boolean' || typeof value === 'string') {
      if (binding.choices && typeof value === 'string' && value !== '' && !binding.choices.includes(value)) {
        rejected.push(key)
        continue
      }
      params[key] = value
      continue
    }
    if (Array.isArray(value)) {
      params[key] = value
      continue
    }
    rejected.push(key)
  }
  return { params, rejected }
}

/** 槽位参数 → 线上参数（按 `bind` 翻译）。 */
export function toWireParams(model: ModelInterface, params: SlotParams): SlotParams {
  const wire: SlotParams = {}
  for (const [slot, value] of Object.entries(params || {})) {
    const binding = model.slots[slot]
    if (!binding) continue
    wire[binding.bind || slot] = value
  }
  return wire
}

/** 线上参数 → 槽位参数（反向翻译，用于把已保存的设置读回表单）。 */
export function fromWireParams(model: ModelInterface, wire: SlotParams | null | undefined): SlotParams {
  const byBind = new Map<string, string>()
  for (const [slot, binding] of Object.entries(model.slots || {})) {
    byBind.set(binding.bind || slot, slot)
  }
  const params: SlotParams = {}
  for (const [key, value] of Object.entries(wire || {})) {
    const slot = byBind.get(key)
    if (slot) params[slot] = value
  }
  return params
}
