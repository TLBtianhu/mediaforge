/**
 * 布局档：**界面侧**的数据，按"任务族"写，不按模型写。
 *
 * 一个档只做两件事：分组、排顺序。它不描述控件长什么样（那是 `slots.ts` 的 `kind`），
 * 也不描述模型有哪些参数（那是模型接口）。所以：
 *
 *   - 换模型不用改档
 *   - 调布局不用改模型
 *   - 档里写了模型没有的槽位不会报错，只会进 `RenderPlan.missing`（提示档过期）
 */
import type { LayoutProfile } from './renderPlan.ts'

const quality = (slots: string[]) => ({
  id: 'quality',
  titleKey: 'section.quality',
  slots,
})

const speed = (slots: string[]) => ({
  id: 'speed',
  titleKey: 'section.speed',
  slots,
})

const advanced = (slots: string[]) => ({
  id: 'advanced',
  titleKey: 'section.advanced',
  collapsed: true,
  slots,
})

/** 任务族 → 布局档。没登记的任务走 `DEFAULT_PROFILE`（全部落到自动分组）。 */
export const LAYOUTS: Record<string, LayoutProfile> = {
  separation: {
    id: 'layout.separation',
    sections: [
      quality(['stems']),
      speed(['chunk_size', 'overlap', 'overlap_count', 'batch_size', 'window_size', 'aggression']),
      advanced(['post_process', 'high_end_process', 'post_process_threshold', 'standardize']),
    ],
  },
  audio_restoration: {
    id: 'layout.audio_restoration',
    sections: [quality(['strength'])],
  },
  upscale: {
    id: 'layout.upscale',
    sections: [quality(['scale', 'denoise_strength']), speed(['tile'])],
  },
  restoration: {
    id: 'layout.restoration',
    sections: [quality(['strength']), speed(['tile'])],
  },
  face_restoration: {
    id: 'layout.face_restoration',
    sections: [quality(['fidelity', 'upscale'])],
  },
  inpainting: {
    id: 'layout.inpainting',
    sections: [quality(['expand']), advanced(['steps'])],
  },
  matting: {
    id: 'layout.matting',
    sections: [quality(['threshold'])],
  },
  segmentation: {
    id: 'layout.segmentation',
    sections: [quality(['threshold'])],
  },
  detection: {
    id: 'layout.detection',
    sections: [quality(['conf']), advanced(['iou'])],
  },
  frame_interpolation: {
    id: 'layout.frame_interpolation',
    sections: [quality(['multiplier']), advanced(['scene_cut'])],
  },
  video_super_resolution: {
    id: 'layout.video_super_resolution',
    sections: [quality(['scale']), speed(['tile'])],
  },
  video_inpainting: {
    id: 'layout.video_inpainting',
    sections: [quality(['neighbor_frames']), speed(['fp16'])],
  },
}

/** 没有专属档的任务：所有槽位按词汇表的兜底分组落进自动分组。 */
export const DEFAULT_PROFILE: LayoutProfile = {
  id: 'layout.default',
  sections: [],
}

export function layoutFor(task: string): LayoutProfile {
  return LAYOUTS[task] || DEFAULT_PROFILE
}

export function layoutIds(): string[] {
  return Object.keys(LAYOUTS).sort()
}
