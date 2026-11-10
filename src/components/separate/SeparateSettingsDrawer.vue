<script setup lang="ts">
import { reactive } from 'vue'
import { useI18n } from 'vue-i18n'
import InferenceParameterLesson from '@/components/InferenceParameterLesson.vue'
import ParameterFieldGuide from '@/components/ParameterFieldGuide.vue'
import SeparateAdvancedPanel from '@/components/separate/SeparateAdvancedPanel.vue'
import { InformationCircleOutline } from '@vicons/ionicons5'

const show = defineModel<boolean>('show', { required: true })
const props = defineProps<{ context: Record<string, any> }>()
const ctx = reactive(props.context) as any
const { t } = useI18n()
</script>

<template>
  <n-modal v-model:show="show" @after-enter="ctx.locateGuidedParameter">
    <InferenceParameterLesson
      v-if="ctx.parameterHelpPage !== null"
      :topic="ctx.helpTopics[ctx.parameterHelpPage]!"
      :topics="ctx.helpTopics"
      :progress="`${ctx.parameterHelpPage + 1} / ${ctx.helpTopics.length}`"
      :close-label="t('parameterHelp.backToParameters')"
      :next-label="ctx.parameterHelpPage === ctx.helpTopics.length - 1 ? t('parameterHelp.done') : undefined"
      @select="topic => ctx.parameterHelpPage = ctx.helpTopics.indexOf(topic)"
      @close="ctx.parameterHelpPage = null"
      @previous="ctx.parameterHelpPage > 0 ? ctx.parameterHelpPage-- : ctx.parameterHelpPage = null"
      @next="ctx.parameterHelpPage < ctx.helpTopics.length - 1 ? ctx.parameterHelpPage++ : ctx.parameterHelpPage = null"
    />
    <n-card
      v-else
      :ref="(element: any) => (ctx.settingsCardRef = element)"
      class="settings-modal"
      :title="t('separate.settingsDrawerTitle')"
      :bordered="false"
      closable
      role="dialog"
      aria-modal="true"
      @close="show = false"
    >
      <div class="settings-drawer__content">
        <div class="settings-group settings-group--run">
          <div class="settings-group__head">
            <strong>{{ t('separate.runOptionsTitle') }}</strong>
            <span>{{ t('separate.runOptionsHint') }}</span>
          </div>
          <div class="check-list">
            <n-checkbox v-model:checked="ctx.useTta" data-parameter-guide="tta" :class="{ 'parameter-guide-target--active': ctx.guidedParameter?.id === 'tta' }">{{ t('separate.tta') }}</n-checkbox>
          </div>
        </div>

        <div class="settings-group">
          <div class="settings-group__head">
            <strong>{{ t('separate.audioQualityTitle') }} · {{ ctx.formatLabel }}</strong>
            <span>{{ t('separate.audioQualityEditable') }}</span>
          </div>
          <n-grid :cols="2" :x-gap="12" :y-gap="12" responsive="screen">
            <n-grid-item v-if="ctx.effectiveFormat === 'wav'">
              <div class="field-block" data-parameter-guide="wavBitDepth" :class="{ 'parameter-guide-target--active': ctx.guidedParameter?.id === 'wavBitDepth' }">
                <label>{{ t('audio.wavBitDepth') }}</label>
                <n-select v-model:value="ctx.settings.wavBitDepth" :options="ctx.wavBitDepthOptions" />
              </div>
            </n-grid-item>
            <n-grid-item v-if="ctx.effectiveFormat === 'flac'">
              <div class="field-block" data-parameter-guide="flacBitDepth" :class="{ 'parameter-guide-target--active': ctx.guidedParameter?.id === 'flacBitDepth' }">
                <label>{{ t('audio.flacBitDepth') }}</label>
                <n-select v-model:value="ctx.settings.flacBitDepth" :options="ctx.flacBitDepthOptions" />
              </div>
            </n-grid-item>
            <n-grid-item v-if="ctx.effectiveFormat === 'mp3'">
              <div class="field-block" data-parameter-guide="mp3BitRate" :class="{ 'parameter-guide-target--active': ctx.guidedParameter?.id === 'mp3BitRate' }">
                <label>{{ t('audio.mp3BitRate') }}</label>
                <n-select v-model:value="ctx.settings.mp3BitRate" :options="ctx.mp3BitRateOptions" />
              </div>
            </n-grid-item>
            <n-grid-item v-if="ctx.effectiveFormat === 'm4a'">
              <div class="field-block" data-parameter-guide="m4aBitRate" :class="{ 'parameter-guide-target--active': ctx.guidedParameter?.id === 'm4aBitRate' }">
                <label>{{ t('audio.m4aBitRate') }}</label>
                <n-select v-model:value="ctx.settings.m4aBitRate" :options="ctx.bitRateOptions" />
              </div>
            </n-grid-item>
            <n-grid-item v-if="ctx.effectiveFormat === 'm4a'">
              <div class="field-block" data-parameter-guide="m4aCodec" :class="{ 'parameter-guide-target--active': ctx.guidedParameter?.id === 'm4aCodec' }">
                <label>{{ t('audio.m4aCodec') }}</label>
                <n-select v-model:value="ctx.settings.m4aCodec" :options="ctx.m4aCodecOptions" />
              </div>
            </n-grid-item>
            <n-grid-item v-if="ctx.showNormalizeField">
              <div class="field-block field-block--inline-check" data-parameter-guide="normalize" :class="{ 'parameter-guide-target--active': ctx.guidedParameter?.id === 'normalize' }">
                <n-checkbox v-model:checked="ctx.normalize">{{ t('inference.normalize') }}</n-checkbox>
              </div>
            </n-grid-item>
          </n-grid>
        </div>

        <div class="settings-group">
          <n-collapse v-model:expanded-names="ctx.expandedInferenceSections">
            <n-collapse-item :title="t('inference.advancedParams')" name="inference">
              <template #header-extra><n-button v-if="!ctx.beginnerGuide.parameterStep" text size="small" :aria-label="t('parameterHelp.label')" @click.stop="ctx.openParameterHelp"><n-icon :component="InformationCircleOutline" size="18" /></n-button></template>
              <SeparateAdvancedPanel :context="ctx.advancedPanelContext" />
            </n-collapse-item>
          </n-collapse>
        </div>
      </div>

      <ParameterFieldGuide v-if="ctx.guidedParameter" :ref="(element: any) => (ctx.fieldGuideRef = element)" :parameter="ctx.guidedParameter" :target="ctx.guideTarget" />

      <template #footer>
        <div class="drawer-footer">
          <n-button type="primary" @click="show = false">{{ t('common.close') }}</n-button>
        </div>
      </template>
    </n-card>
  </n-modal>
</template>

<style scoped>
.settings-drawer__content{
  display: grid;
  gap: 12px;
  min-height: 0;
  padding-top: 16px;
}.settings-modal{
  width: var(--modal-width-md);
  max-height: var(--modal-max-height);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border-radius: 16px;
}.settings-modal :deep(.n-card-header){
  flex: 0 0 auto;
  padding: 14px 20px;
  border-bottom: 1px solid color-mix(in srgb, var(--outline) 86%, transparent);
  background: color-mix(in srgb, var(--surface-1) 96%, transparent);
}.settings-modal :deep(.n-card-header__main){
  font-size: 15px;
  font-weight: 600;
}.settings-modal :deep(.n-card-content),
.settings-modal :deep(.n-card__content){
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
  padding: 0 20px 16px;
}.settings-modal :deep(.n-card-footer),
.settings-modal :deep(.n-card__footer){
  flex: 0 0 auto;
  padding: 12px 20px;
  border-top: 1px solid color-mix(in srgb, var(--outline) 86%, transparent);
  background: color-mix(in srgb, var(--surface-1) 96%, transparent);
}.field-block{
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
}.settings-group{
  display: grid;
  gap: 10px;
  padding: 12px 14px;
  border: 1px solid var(--outline);
  border-radius: 12px;
  background: color-mix(in srgb, var(--surface-2) 62%, transparent);
}.settings-group--run{
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 16px;
}.settings-group__head{
  display: grid;
  gap: 3px;
}.settings-group__head strong{
  font-size: 13px;
}.settings-group__head span{
  color: var(--on-surface-muted);
  font-size: 12px;
  line-height: 1.5;
}.check-list{
  display: flex;
  flex-wrap: wrap;
  gap: 14px 18px;
}.check-list--spaced{
  margin-top: 14px;
}.drawer-footer{
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}</style>
