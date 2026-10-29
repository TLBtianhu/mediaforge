/**
 * 渲染计划：把"模型接口"和"布局档"合成 UI 要画的东西。
 *
 * 解耦的全部秘密在这一行：
 *
 *     render = profile.sections ∩ model.slots
 *
 *   - UI 不认识模型（只认识槽位与档）
 *   - 模型不认识 UI（只声明槽位与绑定，**不允许**出现 order/group/width 之类的布局字段）
 *   - 模型新增的槽位不会消失：没被任何档列出的，按词汇表的 `fallbackSection` 落到自动分组
 *
 * `validateInterface()` / `validateLayout()` 是这条边界的守卫，测试直接调用它们。
 */
import { slotSpec, type SlotSpec } from './slots.ts'

export type LayoutFieldKind = 'quality' | 'speed' | 'advanced'

/** 模型接口里**允许**出现的键；出现别的（尤其布局键）就是越界。 */
export const ALLOWED_BINDING_KEYS = new Set([
  'bind',
  'kind',
  'min',
  'max',
  'step',
  'default',
  'choices',
  'derive',
])

/** 一旦出现在模型接口里就说明布局又漏回来了。 */
export const FORBIDDEN_INTERFACE_KEYS = ['order', 'group', 'section', 'sections', 'width', 'row', 'column', 'collapsed', 'index']

export interface ModelSlotBinding {
  /** 该槽位映射到模型的原生键（空则视为同名） */
  bind: string
  kind?: SlotSpec['kind']
  min?: number
  max?: number
  step?: number
  default?: unknown
  choices?: Array<string | number>
  /** 派生约束名（后端计算合法值） */
  derive?: string
}

export interface ModelInterface {
  modelId: string
  task: string
  slots: Record<string, ModelSlotBinding>
}

export interface LayoutSection {
  id: string
  titleKey: string
  collapsed?: boolean
  /** 槽位 id 列表——顺序即展示顺序，跟模型无关 */
  slots: string[]
}

export interface LayoutProfile {
  id: string
  sections: LayoutSection[]
}

export interface RenderField {
  slot: string
  spec: SlotSpec
  binding: ModelSlotBinding
  kind: SlotSpec['kind']
}

export interface RenderSection {
  id: string
  titleKey: string
  collapsed: boolean
  fields: RenderField[]
}

export interface RenderPlan {
  modelId: string
  profileId: string
  sections: RenderSection[]
  /** 布局档里写了、但模型没有的槽位（用于提示布局档过期，不渲染） */
  missing: string[]
}

function field(slot: string, binding: ModelSlotBinding): RenderField | null {
  const spec = slotSpec(slot)
  if (!spec) return null
  return { slot, spec, binding, kind: binding.kind || spec.kind }
}

export function buildRenderPlan(model: ModelInterface, profile: LayoutProfile): RenderPlan {
  const sections: RenderSection[] = []
  const listed = new Set<string>()
  const missing: string[] = []

  for (const section of profile.sections) {
    const fields: RenderField[] = []
    for (const slot of section.slots) {
      const binding = model.slots[slot]
      if (!binding) {
        missing.push(slot)
        continue
      }
      const item = field(slot, binding)
      if (!item) continue
      fields.push(item)
      listed.add(slot)
    }
    if (fields.length > 0) {
      sections.push({
        id: section.id,
        titleKey: section.titleKey,
        collapsed: Boolean(section.collapsed),
        fields,
      })
    }
  }

  // 没被布局档列出的槽位：按词汇表的兜底分组收进去，绝不静默丢弃
  const unlisted = Object.keys(model.slots).filter(slot => !listed.has(slot))
  for (const group of ['quality', 'speed', 'advanced'] as const) {
    const fields = unlisted
      .filter(slot => {
        const spec = slotSpec(slot)
        const fallback = spec?.fallbackSection || 'advanced'
        return fallback === group
      })
      .map(slot => field(slot, model.slots[slot]))
      .filter((item): item is RenderField => item !== null)
    if (fields.length === 0) continue
    sections.push({
      id: `auto-${group}`,
      titleKey: `section.${group}`,
      collapsed: group !== 'quality',
      fields,
    })
  }

  return { modelId: model.modelId, profileId: profile.id, sections, missing }
}

export function validateInterface(model: ModelInterface): string[] {
  const problems: string[] = []
  if (!model.modelId) problems.push('interface without modelId')
  for (const [slot, binding] of Object.entries(model.slots || {})) {
    const spec = slotSpec(slot)
    if (!spec) {
      problems.push(`${model.modelId}: slot ${slot} is not in the vocabulary`)
      continue
    }
    if (!binding || typeof binding !== 'object') {
      problems.push(`${model.modelId}: slot ${slot} binding must be an object`)
      continue
    }
    if (!binding.bind) problems.push(`${model.modelId}: slot ${slot} needs a bind target`)
    for (const key of Object.keys(binding)) {
      if (FORBIDDEN_INTERFACE_KEYS.includes(key)) {
        problems.push(`${model.modelId}: slot ${slot} carries layout key ${key}`)
      } else if (!ALLOWED_BINDING_KEYS.has(key)) {
        problems.push(`${model.modelId}: slot ${slot} has unknown key ${key}`)
      }
    }
    if (binding.kind && !spec.kind) problems.push(`${model.modelId}: slot ${slot} bad kind`)
    if (binding.kind && spec.kind && binding.kind !== spec.kind) {
      // 接口可以收窄控件类型，但不能凭空发明一个
      problems.push(`${model.modelId}: slot ${slot} overrides kind ${spec.kind} with ${binding.kind}`)
    }
  }
  return problems
}

export function validateLayout(profile: LayoutProfile): string[] {
  const problems: string[] = []
  const seen = new Set<string>()
  for (const section of profile.sections || []) {
    if (!section.id) problems.push('layout section without id')
    if (!section.titleKey) problems.push(`layout section ${section.id} without titleKey`)
    for (const slot of section.slots || []) {
      // 布局档只认槽位，认模型就是又耦合回去了
      if (/^(audio|media|hf|spandrel):/.test(slot) || slot.includes('/')) {
        problems.push(`layout ${profile.id}: section ${section.id} references a model, not a slot: ${slot}`)
      }
      if (!slotSpec(slot)) problems.push(`layout ${profile.id}: unknown slot ${slot}`)
      if (seen.has(slot)) problems.push(`layout ${profile.id}: slot ${slot} appears twice`)
      seen.add(slot)
    }
  }
  return problems
}
