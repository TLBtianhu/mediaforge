<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import ModelSlotFields from '@/components/ModelSlotFields.vue'
import ModelParameterField from '@/components/ModelParameterField.vue'
import AlignedInferenceInputNumber from '@/components/AlignedInferenceInputNumber.vue'
import type { RenderPlan } from '@/litegraph/renderPlan'
import type { ModelEntry } from '@/stores/model'
import type { ChunkConstraint } from './panelTypes'

const batchSize = defineModel<number | null>('batchSize', { required: true })
const overlapSize = defineModel<number | null>('overlapSize', { required: true })
const numOverlap = defineModel<number | null>('numOverlap', { required: true })
const chunkSize = defineModel<number | null>('chunkSize', { required: true })
const windowSize = defineModel<number | null>('windowSize', { required: true })
const aggression = defineModel<number | null>('aggression', { required: true })
const postProcessThreshold = defineModel<number | null>('postProcessThreshold', { required: true })
const standardize = defineModel<boolean>('standardize', { required: true })
const enablePostProcess = defineModel<boolean>('enablePostProcess', { required: true })
const highEndProcess = defineModel<boolean>('highEndProcess', { required: true })
const props = defineProps<{
  slotPanelPlan: RenderPlan | null
  slotPanelValues: Record<string, unknown>
  updateSlotPanel: (next: Record<string, unknown>) => void
  advancedParamsLoading: boolean
  guidedParameterId?: string
  hasInferenceField: (key: string) => boolean
  hasVisibleAdvancedFields: boolean
  overlapSizeStep: number
  currentInferenceSampleStep: number | undefined
  chunkSizeStep: number
  currentChunkConstraint: ChunkConstraint | null | undefined
  currentChunkIssue: Record<string, unknown> | null | undefined
  showStandardizeField: boolean
  currentModelInfo: ModelEntry | null
  currentModelDefaultsResolved: boolean
  resetCurrentModelInferenceDefaults: () => void
  saveCurrentModelInferenceDefaults: () => void
  updateOverlapSize: (value: number | null) => void
  updateChunkSize: (value: number | null) => void
  restoreInferenceNumberFallback: (key: string) => void
}>()
const { t } = useI18n()
</script>

<template>
  <p class="advanced-hint">{{ t('separate.advancedPanelHint') }}</p>
  <div v-if="props.advancedParamsLoading" class="advanced-loading">
    <n-spin size="small" />
    <span>{{ t('separate.advancedPanelLoading') }}</span>
  </div>
  <!-- 有槽位接口时按渲染计划画（分组/顺序来自布局档）；没有接口的模型走下面的旧控件。 -->
  <ModelSlotFields
    v-if="props.slotPanelPlan"
    :plan="props.slotPanelPlan"
    :model-value="props.slotPanelValues"
    :active-slot="props.guidedParameterId"
    @update:model-value="(value: Record<string, unknown>) => props.updateSlotPanel(value)"
  />
  <template v-else>
    <n-grid :cols="2" :x-gap="12" :y-gap="12" responsive="screen">
      <n-grid-item v-if="props.hasInferenceField('batch_size')">
        <div class="field-block" data-parameter-guide="batch_size" :class="{ 'parameter-guide-target--active': props.guidedParameterId === 'batch_size' }">
          <label>{{ t('inference.batchSize') }}</label>
          <ModelParameterField :label="t('inference.batchSize')" :value="batchSize" type="integer" :min="0" :max="32" @update:value="value => batchSize = Number(value)" @blur="props.restoreInferenceNumberFallback('batch_size')" />
        </div>
      </n-grid-item>
      <n-grid-item v-if="props.hasInferenceField('overlap_size')">
        <div class="field-block" data-parameter-guide="overlap_size" :class="{ 'parameter-guide-target--active': props.guidedParameterId === 'overlap_size' }">
          <label>{{ t('inference.overlapSize') }}</label>
          <AlignedInferenceInputNumber :value="overlapSize" :step="props.overlapSizeStep" :alignment-step="props.currentInferenceSampleStep" @update:value="value => { overlapSize = value; props.updateOverlapSize(value) }" @blur="props.restoreInferenceNumberFallback('overlap_size')" />
        </div>
      </n-grid-item>
      <n-grid-item v-if="props.hasInferenceField('num_overlap')">
        <div class="field-block" data-parameter-guide="num_overlap" :class="{ 'parameter-guide-target--active': props.guidedParameterId === 'num_overlap' }">
          <label>{{ t('inference.numOverlap') }}</label>
          <ModelParameterField :label="t('inference.numOverlap')" :value="numOverlap" type="integer" :min="0" :max="128" @update:value="value => numOverlap = Number(value)" @blur="props.restoreInferenceNumberFallback('num_overlap')" />
        </div>
      </n-grid-item>
      <n-grid-item v-if="props.hasInferenceField('chunk_size')">
        <div class="field-block" data-parameter-guide="chunk_size" :class="{ 'parameter-guide-target--active': props.guidedParameterId === 'chunk_size' }">
          <label>{{ t('inference.chunkSize') }}</label>
          <AlignedInferenceInputNumber :value="chunkSize" :step="props.chunkSizeStep" :alignment-step="props.currentChunkConstraint?.step || props.currentInferenceSampleStep" :alignment-offset="props.currentChunkConstraint?.offset" :alignment-min="props.currentChunkConstraint?.min" @update:value="value => { chunkSize = value; props.updateChunkSize(value) }" @blur="props.restoreInferenceNumberFallback('chunk_size')" />
          <n-text v-if="props.currentChunkIssue" type="warning">{{ t('inference.invalidChunkSize', props.currentChunkIssue) }}</n-text>
        </div>
      </n-grid-item>
      <n-grid-item v-if="props.hasInferenceField('window_size')">
        <div class="field-block" data-parameter-guide="window_size" :class="{ 'parameter-guide-target--active': props.guidedParameterId === 'window_size' }">
          <label>{{ t('inference.vrWindowSize') }}</label>
          <ModelParameterField :label="t('inference.vrWindowSize')" :value="windowSize" type="integer" :min="0" :max="4096" @update:value="value => windowSize = Number(value)" @blur="props.restoreInferenceNumberFallback('window_size')" />
        </div>
      </n-grid-item>
      <n-grid-item v-if="props.hasInferenceField('aggression')">
        <div class="field-block" data-parameter-guide="aggression" :class="{ 'parameter-guide-target--active': props.guidedParameterId === 'aggression' }">
          <label>{{ t('inference.vrAggression') }}</label>
          <ModelParameterField :label="t('inference.vrAggression')" :value="aggression" type="integer" :min="0" :max="100" @update:value="value => aggression = Number(value)" />
        </div>
      </n-grid-item>
      <n-grid-item v-if="props.hasInferenceField('post_process_threshold')">
        <div class="field-block" data-parameter-guide="post_process_threshold" :class="{ 'parameter-guide-target--active': props.guidedParameterId === 'post_process_threshold' }">
          <label>{{ t('inference.vrPostProcessThreshold') }}</label>
          <ModelParameterField :label="t('inference.vrPostProcessThreshold')" :value="postProcessThreshold" type="number" :min="0" :max="1" :step="0.05" @update:value="value => postProcessThreshold = Number(value)" />
        </div>
      </n-grid-item>
    </n-grid>
    <div class="check-list check-list--spaced">
      <n-checkbox v-if="props.showStandardizeField" v-model:checked="standardize" data-parameter-guide="standardize" :class="{ 'parameter-guide-target--active': props.guidedParameterId === 'standardize' }">{{ t('inference.standardize') }}</n-checkbox>
      <n-checkbox v-if="props.hasInferenceField('enable_post_process')" v-model:checked="enablePostProcess" data-parameter-guide="enable_post_process" :class="{ 'parameter-guide-target--active': props.guidedParameterId === 'enable_post_process' }">{{ t('inference.vrEnablePostProcess') }}</n-checkbox>
      <n-checkbox v-if="props.hasInferenceField('high_end_process')" v-model:checked="highEndProcess" data-parameter-guide="high_end_process" :class="{ 'parameter-guide-target--active': props.guidedParameterId === 'high_end_process' }">{{ t('inference.vrHighEndProcess') }}</n-checkbox>
    </div>
  </template>
  <p v-if="!props.advancedParamsLoading && !props.hasVisibleAdvancedFields" class="advanced-empty">
    {{ t('separate.advancedPanelEmpty') }}
  </p>
  <div
    v-if="props.currentModelInfo && props.currentModelDefaultsResolved && !props.advancedParamsLoading"
    class="advanced-actions"
    data-parameter-guide="modelDefaults"
    :class="{ 'parameter-guide-target--active': props.guidedParameterId === 'modelDefaults' }"
  >
    <n-button size="small" secondary @click="props.resetCurrentModelInferenceDefaults">
      {{ t('separate.resetInferenceDefaults') }}
    </n-button>
    <n-button size="small" type="primary" @click="props.saveCurrentModelInferenceDefaults">
      {{ t('separate.saveInferenceDefaults') }}
    </n-button>
  </div>
</template>

<style scoped>
.field-block{
  display: grid;
  gap: 6px;
}.parameter-guide-target--active{ position: relative; }.field-block.parameter-guide-target--active > label{ color: var(--primary-ink); }.field-block label{
  font-size: 12px;
  color: var(--on-surface-muted);
}.field-block--inline-check{
  min-height: 56px;
  align-content: end;
  padding-bottom: 7px;
}.field-block__hint{
  font-size: 11px;
  line-height: 1.45;
  color: var(--on-surface-muted);
}.advanced-hint{
  margin: 0 0 12px;
  color: var(--on-surface-muted);
  font-size: 12px;
  line-height: 1.5;
}.advanced-loading,
.advanced-empty{
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0 0 12px;
  color: var(--on-surface-muted);
  font-size: 12px;
  line-height: 1.5;
}.advanced-empty{
  margin-top: 14px;
}.advanced-actions{
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 16px;
}.check-list{
  display: flex;
  flex-wrap: wrap;
  gap: 14px 18px;
}.check-list--spaced{
  margin-top: 14px;
}</style>
