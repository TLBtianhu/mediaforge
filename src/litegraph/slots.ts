/**
 * 槽位词汇表：**应用侧**对"可调参数"的统一描述。
 *
 * 模型不认识 UI，UI 也不认识模型——两者只共同认识这份词汇表：
 *
 *   - 模型接口（`ModelInterface`）说：我支持 `chunk_size`，它映射到我的 `chunk_size` 键，
 *     范围 64–8192，默认 1024
 *   - 布局档（`LayoutProfile`）说：`chunk_size` 放在"速度"这一节里
 *   - 渲染器只认这里的 `kind`：int → 滑块、enum → 下拉、multi → 多选、bool → 开关
 *
 * 因此新增一个模型不需要新控件；新增一种参数只需要在这里加一条。
 * `kind` 决定**控件类**，不是控件实例，也不决定位置——位置在布局档里。
 */
import type { LayoutFieldKind } from './renderPlan.ts'

export type SlotKind = 'int' | 'float' | 'enum' | 'multi' | 'bool'
export type SlotUnit =
  | 'samples'
  | 'pixels'
  | 'frames'
  | 'seconds'
  | 'percent'
  | 'ratio'
  | 'count'
  | 'factor'
  | 'none'
export type SlotDiagram = 'timeline' | 'ratio' | 'spectrum' | 'grid' | 'none'

export interface SlotSpec {
  /** 稳定 id，模型接口与布局档都用它引用 */
  id: string
  kind: SlotKind
  unit: SlotUnit
  /** 帮助文案 key（必须真实存在于 i18n，否则拒绝） */
  helpKey?: string
  /** 图示范式：按"槽位语义"选，而不是按模型选 */
  diagram: SlotDiagram
  /** 合法的派生约束名（由后端计算，前端只显示吸附结果） */
  derive?: string
  /** 没写在任何布局档里时落到哪里 */
  fallbackSection: LayoutFieldKind | 'advanced'
}

export const SLOTS: Record<string, SlotSpec> = {
  // ── 音频分离 ────────────────────────────────────────────────────────────
  chunk_size: {
    id: 'chunk_size',
    kind: 'int',
    unit: 'samples',
    helpKey: 'parameterHelp.chunkTitle',
    diagram: 'timeline',
    derive: 'chunk_size_constraint',
    fallbackSection: 'advanced',
  },
  overlap: {
    id: 'overlap',
    kind: 'int',
    unit: 'samples',
    helpKey: 'parameterHelp.overlapTitle',
    diagram: 'ratio',
    fallbackSection: 'advanced',
  },
  stems: {
    id: 'stems',
    kind: 'multi',
    unit: 'none',
    diagram: 'grid',
    fallbackSection: 'quality',
  },
  // ── 画质 / 放大 ─────────────────────────────────────────────────────────
  scale: {
    id: 'scale',
    kind: 'int',
    unit: 'factor',
    diagram: 'grid',
    fallbackSection: 'quality',
  },
  denoise_strength: {
    id: 'denoise_strength',
    kind: 'float',
    unit: 'percent',
    diagram: 'none',
    fallbackSection: 'quality',
  },
  tile: {
    id: 'tile',
    kind: 'int',
    unit: 'pixels',
    diagram: 'grid',
    fallbackSection: 'speed',
  },
  // ── 修复 / 修补 ─────────────────────────────────────────────────────────
  strength: {
    id: 'strength',
    kind: 'float',
    unit: 'percent',
    diagram: 'none',
    fallbackSection: 'quality',
  },
  fidelity: {
    id: 'fidelity',
    kind: 'float',
    unit: 'percent',
    diagram: 'none',
    fallbackSection: 'quality',
  },
  expand: {
    id: 'expand',
    kind: 'int',
    unit: 'pixels',
    diagram: 'grid',
    fallbackSection: 'advanced',
  },
  steps: {
    id: 'steps',
    kind: 'int',
    unit: 'count',
    diagram: 'none',
    fallbackSection: 'advanced',
  },
  upscale: {
    id: 'upscale',
    kind: 'int',
    unit: 'factor',
    diagram: 'grid',
    fallbackSection: 'quality',
  },
  // ── 检测 / 分割 / 抠像 ──────────────────────────────────────────────────
  threshold: {
    id: 'threshold',
    kind: 'float',
    unit: 'percent',
    diagram: 'none',
    fallbackSection: 'quality',
  },
  conf: {
    id: 'conf',
    kind: 'float',
    unit: 'percent',
    diagram: 'none',
    fallbackSection: 'quality',
  },
  iou: {
    id: 'iou',
    kind: 'float',
    unit: 'percent',
    diagram: 'none',
    fallbackSection: 'advanced',
  },
  // ── 视频 ────────────────────────────────────────────────────────────────
  multiplier: {
    id: 'multiplier',
    kind: 'enum',
    unit: 'factor',
    diagram: 'timeline',
    fallbackSection: 'quality',
  },
  scene_cut: {
    id: 'scene_cut',
    kind: 'bool',
    unit: 'none',
    diagram: 'none',
    fallbackSection: 'advanced',
  },
  neighbor_frames: {
    id: 'neighbor_frames',
    kind: 'int',
    unit: 'frames',
    diagram: 'timeline',
    fallbackSection: 'quality',
  },
  fp16: {
    id: 'fp16',
    kind: 'bool',
    unit: 'none',
    diagram: 'none',
    fallbackSection: 'speed',
  },
}

export function slotSpec(id: string): SlotSpec | undefined {
  return SLOTS[id]
}

export function slotIds(): string[] {
  return Object.keys(SLOTS).sort()
}
