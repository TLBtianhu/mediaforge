import type { RenderPlan } from '@/litegraph/renderPlan'
import type { ModelEntry } from '@/stores/model'
import type { ParameterGuide } from '@/features/onboarding/parameters'
import type { ParameterHelpTopic } from '@/features/inference/parameterHelp'
import type { useSettingsStore } from '@/stores/settings'

type SelectOption = { label: string; value: string | number }

export type ChunkConstraint = { step?: number; offset?: number; min?: number }

export type AdvancedPanelContext = {
  slotPanelPlan: RenderPlan | null
  slotPanelValues: Record<string, unknown>
  updateSlotPanel: (next: Record<string, unknown>) => void
  advancedParamsLoading: boolean
  guidedParameter?: ParameterGuide
  hasInferenceField: (key: string) => boolean
  hasVisibleAdvancedFields: boolean
  batch_size: number | null
  overlap_size: number | null
  num_overlap: number | null
  chunk_size: number | null
  window_size: number | null
  aggression: number | null
  post_process_threshold: number | null
  standardize: boolean
  enable_post_process: boolean
  high_end_process: boolean
  overlapSizeStep: number
  currentInferenceSampleStep: number | undefined
  chunkSizeStep: number
  currentChunkConstraint: ChunkConstraint | null | undefined
  currentChunkIssue: Record<string, unknown> | null | undefined
  updateOverlapSize: (value: number | null) => void
  updateChunkSize: (value: number | null) => void
  showStandardizeField: boolean
  currentModelInfo: ModelEntry | null
  currentModelDefaultsResolved: boolean
  resetCurrentModelInferenceDefaults: () => void
  saveCurrentModelInferenceDefaults: () => void
  restoreInferenceNumberFallback: (key: string) => void
}

export type SettingsDrawerContext = {
  parameterHelpPage: number | null
  helpTopics: ParameterHelpTopic[]
  settingsCardRef: { $el: HTMLElement } | null
  fieldGuideRef: unknown
  guidedParameter?: ParameterGuide
  guideTarget: HTMLElement | null
  beginnerGuide: { parameterStep: boolean }
  openParameterHelp: () => void
  useTta: boolean
  expandedInferenceSections: string[]
  advancedPanelContext: AdvancedPanelContext
  effectiveFormat: string
  formatLabel: string
  settings: ReturnType<typeof useSettingsStore>
  wavBitDepthOptions: SelectOption[]
  flacBitDepthOptions: SelectOption[]
  mp3BitRateOptions: SelectOption[]
  bitRateOptions: SelectOption[]
  m4aCodecOptions: SelectOption[]
  showNormalizeField: boolean
  normalize: boolean
  locateGuidedParameter: () => void
}
