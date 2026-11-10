<script setup lang="ts">
import { reactive } from 'vue'
import { useI18n } from 'vue-i18n'
import ModelSlotFields from '@/components/ModelSlotFields.vue'
import ModelParameterField from '@/components/ModelParameterField.vue'
import AlignedInferenceInputNumber from '@/components/AlignedInferenceInputNumber.vue'
import { InformationCircleOutline } from '@vicons/ionicons5'

const props = defineProps<{ context: Record<string, any> }>()
const ctx = reactive(props.context) as any
const { t } = useI18n()
</script>

<template>
  <p class="advanced-hint">{{ t('separate.advancedPanelHint') }}</p>
  <div v-if="ctx.advancedParamsLoading" class="advanced-loading">
    <n-spin size="small" />
    <span>{{ t('separate.advancedPanelLoading') }}</span>
  </div>
  <!-- 有槽位接口时按渲染计划画（分组/顺序来自布局档）；没有接口的模型走下面的旧控件。 -->
  <ModelSlotFields
    v-if="ctx.slotPanelPlan"
    :plan="ctx.slotPanelPlan"
    :model-value="ctx.slotPanelValues"
    :active-slot="ctx.guidedParameter?.id"
    @update:model-value="ctx.updateSlotPanel"
  />
  <template v-else>
    <n-grid :cols="2" :x-gap="12" :y-gap="12" responsive="screen">
      <n-grid-item v-if="ctx.hasInferenceField('batch_size')">
        <div class="field-block" data-parameter-guide="batch_size" :class="{ 'parameter-guide-target--active': ctx.guidedParameter?.id === 'batch_size' }">
          <label>{{ t('inference.batchSize') }}</label>
          <ModelParameterField :label="t('inference.batchSize')" :value="ctx.batch_size" type="integer" :min="0" :max="32" @update:value="value => ctx.batch_size = Number(value)" @blur="ctx.restoreInferenceNumberFallback('batch_size')" />
        </div>
      </n-grid-item>
      <n-grid-item v-if="ctx.hasInferenceField('overlap_size')">
        <div class="field-block" data-parameter-guide="overlap_size" :class="{ 'parameter-guide-target--active': ctx.guidedParameter?.id === 'overlap_size' }">
          <label>{{ t('inference.overlapSize') }}</label>
          <AlignedInferenceInputNumber :value="ctx.overlap_size" :step="ctx.overlapSizeStep" :alignment-step="ctx.currentInferenceSampleStep" @update:value="ctx.updateOverlapSize" @blur="ctx.restoreInferenceNumberFallback('overlap_size')" />
        </div>
      </n-grid-item>
      <n-grid-item v-if="ctx.hasInferenceField('num_overlap')">
        <div class="field-block" data-parameter-guide="num_overlap" :class="{ 'parameter-guide-target--active': ctx.guidedParameter?.id === 'num_overlap' }">
          <label>{{ t('inference.numOverlap') }}</label>
          <ModelParameterField :label="t('inference.numOverlap')" :value="ctx.num_overlap" type="integer" :min="0" :max="128" @update:value="value => ctx.num_overlap = Number(value)" @blur="ctx.restoreInferenceNumberFallback('num_overlap')" />
        </div>
      </n-grid-item>
      <n-grid-item v-if="ctx.hasInferenceField('chunk_size')">
        <div class="field-block" data-parameter-guide="chunk_size" :class="{ 'parameter-guide-target--active': ctx.guidedParameter?.id === 'chunk_size' }">
          <label>{{ t('inference.chunkSize') }}</label>
          <AlignedInferenceInputNumber :value="ctx.chunk_size" :step="ctx.chunkSizeStep" :alignment-step="ctx.currentChunkConstraint?.step || ctx.currentInferenceSampleStep" :alignment-offset="ctx.currentChunkConstraint?.offset" :alignment-min="ctx.currentChunkConstraint?.min" @update:value="ctx.updateChunkSize" @blur="ctx.restoreInferenceNumberFallback('chunk_size')" />
          <n-text v-if="ctx.currentChunkIssue" type="warning">{{ t('inference.invalidChunkSize', ctx.currentChunkIssue) }}</n-text>
        </div>
      </n-grid-item>
      <n-grid-item v-if="ctx.hasInferenceField('window_size')">
        <div class="field-block" data-parameter-guide="window_size" :class="{ 'parameter-guide-target--active': ctx.guidedParameter?.id === 'window_size' }">
          <label>{{ t('inference.vrWindowSize') }}</label>
          <ModelParameterField :label="t('inference.vrWindowSize')" :value="ctx.window_size" type="integer" :min="0" :max="4096" @update:value="value => ctx.window_size = Number(value)" @blur="ctx.restoreInferenceNumberFallback('window_size')" />
        </div>
      </n-grid-item>
      <n-grid-item v-if="ctx.hasInferenceField('aggression')">
        <div class="field-block" data-parameter-guide="aggression" :class="{ 'parameter-guide-target--active': ctx.guidedParameter?.id === 'aggression' }">
          <label>{{ t('inference.vrAggression') }}</label>
          <ModelParameterField :label="t('inference.vrAggression')" :value="ctx.aggression" type="integer" :min="0" :max="100" @update:value="value => ctx.aggression = Number(value)" />
        </div>
      </n-grid-item>
      <n-grid-item v-if="ctx.hasInferenceField('post_process_threshold')">
        <div class="field-block" data-parameter-guide="post_process_threshold" :class="{ 'parameter-guide-target--active': ctx.guidedParameter?.id === 'post_process_threshold' }">
          <label>{{ t('inference.vrPostProcessThreshold') }}</label>
          <ModelParameterField :label="t('inference.vrPostProcessThreshold')" :value="ctx.post_process_threshold" type="number" :min="0" :max="1" :step="0.05" @update:value="value => ctx.post_process_threshold = Number(value)" />
        </div>
      </n-grid-item>
    </n-grid>
    <div class="check-list check-list--spaced">
      <n-checkbox v-if="ctx.showStandardizeField" v-model:checked="ctx.standardize" data-parameter-guide="standardize" :class="{ 'parameter-guide-target--active': ctx.guidedParameter?.id === 'standardize' }">{{ t('inference.standardize') }}</n-checkbox>
      <n-checkbox v-if="ctx.hasInferenceField('enable_post_process')" v-model:checked="ctx.enable_post_process" data-parameter-guide="enable_post_process" :class="{ 'parameter-guide-target--active': ctx.guidedParameter?.id === 'enable_post_process' }">{{ t('inference.vrEnablePostProcess') }}</n-checkbox>
      <n-checkbox v-if="ctx.hasInferenceField('high_end_process')" v-model:checked="ctx.high_end_process" data-parameter-guide="high_end_process" :class="{ 'parameter-guide-target--active': ctx.guidedParameter?.id === 'high_end_process' }">{{ t('inference.vrHighEndProcess') }}</n-checkbox>
    </div>
  </template>
  <p v-if="!ctx.advancedParamsLoading && !ctx.hasVisibleAdvancedFields" class="advanced-empty">
    {{ t('separate.advancedPanelEmpty') }}
  </p>
  <div
    v-if="ctx.currentModelInfo && ctx.currentModelDefaultsResolved && !ctx.advancedParamsLoading"
    class="advanced-actions"
    data-parameter-guide="modelDefaults"
    :class="{ 'parameter-guide-target--active': ctx.guidedParameter?.id === 'modelDefaults' }"
  >
    <n-button size="small" secondary @click="ctx.resetCurrentModelInferenceDefaults">
      {{ t('separate.resetInferenceDefaults') }}
    </n-button>
    <n-button size="small" type="primary" @click="ctx.saveCurrentModelInferenceDefaults">
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
