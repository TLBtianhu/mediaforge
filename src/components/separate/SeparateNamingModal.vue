<script setup lang="ts">
import { reactive } from 'vue'
import { useI18n } from 'vue-i18n'
import { ChevronDownOutline, ChevronUpOutline, ReorderFourOutline } from '@vicons/ionicons5'

const show = defineModel<boolean>('show', { required: true })
const props = defineProps<{ context: Record<string, any> }>()
const ctx = reactive(props.context) as any
const { t } = useI18n()
</script>

<template>
  <n-modal v-model:show="show" style="width:var(--modal-width-sm)">
    <n-card
      class="naming-modal"
      :title="t('separate.namingModalTitle')"
      :bordered="false"
      closable
      role="dialog"
      aria-modal="true"
      @close="show = false"
    >
      <div class="naming-modal__content">
        <div class="naming-section naming-section--main">
          <div class="naming-section__head">
            <strong>{{ t('separate.namingTemplate') }}</strong>
            <span>{{ t('separate.namingTemplateHint') }}</span>
          </div>
          <n-input
            :ref="(element: any) => (ctx.namingTemplateInputRef = element)"
            v-model:value="ctx.outputNamingTemplate"
            placeholder="%index%_%filename%_%stem%"
          />
          <div class="naming-preview-inline">
            <span>{{ t('separate.namingPreview') }}</span>
            <div v-for="row in ctx.outputNamingPreviewParts" :key="row.key" class="naming-preview-file">
              <span
                v-for="(part, partIndex) in row.parts"
                :key="`${row.key}-${partIndex}`"
                class="naming-preview-part"
                :class="`naming-preview-part--${part.token.replace(/[%_]/g, '')}`"
              >{{ part.text }}</span>
            </div>
          </div>
        </div>

        <div class="naming-section">
          <div class="naming-section__head">
            <strong>{{ t('separate.namingVariables') }}</strong>
            <span>{{ t('separate.namingVariablesHint') }}</span>
          </div>
          <div class="token-grid">
            <button
              v-for="token in ctx.namingTokens"
              :key="token.value"
              type="button"
              class="token-chip"
              :title="token.value"
              @click="ctx.insertNamingToken(token.value)"
            >
              <span>{{ token.label }}</span>
              <code>{{ token.value }}</code>
            </button>
          </div>
        </div>

        <div v-if="ctx.usesIndexToken" class="naming-section">
          <div class="naming-section__head">
            <strong>{{ t('separate.namingNumberOrder') }}</strong>
            <span>{{ t('separate.namingNumberOrderHint') }}</span>
          </div>
          <div v-if="ctx.orderedOutputStems.length" class="stem-order-list">
            <div
              v-for="(stem, index) in ctx.orderedOutputStems"
              :key="stem"
              class="stem-order-row"
              :class="{ 'stem-order-row--dragging': ctx.draggedStemIndex === index }"
              @pointerdown="ctx.onStemPointerDown($event, index)"
              @pointermove="ctx.onStemPointerMove"
              @pointerup="ctx.finishStemPointerDrag"
              @pointercancel="ctx.finishStemPointerDrag"
            >
              <n-icon :component="ReorderFourOutline" />
              <span>{{ ctx.padNumber(Number(index) + 1) }}</span>
              <strong>{{ stem }}</strong>
              <div class="stem-order-row__actions">
                <n-button quaternary circle size="tiny" :disabled="index === 0" :title="t('separate.moveUp')" @click="ctx.moveStem(Number(index), Number(index) - 1)">
                  <template #icon><n-icon :component="ChevronUpOutline" /></template>
                </n-button>
                <n-button quaternary circle size="tiny" :disabled="index === ctx.orderedOutputStems.length - 1" :title="t('separate.moveDown')" @click="ctx.moveStem(Number(index), Number(index) + 1)">
                  <template #icon><n-icon :component="ChevronDownOutline" /></template>
                </n-button>
              </div>
            </div>
          </div>
          <n-empty v-else size="small" :description="t('separate.namingNoStems')" />
        </div>
        <div v-else class="naming-section naming-section--muted">
          {{ t('separate.namingNumberOrderDisabled') }}
        </div>
      </div>

      <template #footer>
        <div class="drawer-footer">
          <n-button secondary @click="ctx.resetOutputNaming">{{ t('separate.namingReset') }}</n-button>
          <n-button type="primary" @click="show = false">{{ t('common.close') }}</n-button>
        </div>
      </template>
    </n-card>
  </n-modal>
</template>

<style scoped>
.naming-field{ margin-top: -2px; }.naming-summary{
  width: 100%;
  min-width: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  min-height: 32px;
  border: 0;
  border-radius: 9px;
  padding: 6px 10px;
  background: color-mix(in srgb, var(--surface-2) 48%, transparent);
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--outline) 82%, transparent);
  color: var(--on-surface);
  font-family: inherit;
  cursor: pointer;
}.naming-summary:disabled{
  opacity: 0.55;
  cursor: not-allowed;
}.naming-summary span{
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
}.naming-summary small{
  flex: 0 0 auto;
  color: var(--primary-ink);
  font-size: 11px;
}.naming-modal{
  max-height: min(620px, var(--modal-max-height));
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border-radius: 16px;
}.naming-modal :deep(.n-card-header){
  padding: 16px 20px 10px;
}.naming-modal :deep(.n-card-footer),
.naming-modal :deep(.n-card__footer){
  padding: 10px 20px 14px;
}.naming-modal :deep(.n-card-content),
.naming-modal :deep(.n-card__content){
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
  padding: 8px 20px 12px;
}.naming-modal__content{
  display: grid;
  gap: 10px;
}.naming-section{
  display: grid;
  gap: 8px;
  padding: 11px 12px;
  border-radius: 12px;
  background: color-mix(in srgb, var(--surface-2) 56%, transparent);
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--outline) 82%, transparent);
}.naming-section__head{
  display: grid;
  gap: 3px;
}.naming-section--muted{
  color: var(--on-surface-muted);
  font-size: 12px;
  line-height: 1.45;
}.naming-section strong,
.naming-section__head strong{
  font-size: 12.5px;
}.naming-section span,
.naming-section__head span{
  color: var(--on-surface-muted);
  font-size: 11.5px;
  line-height: 1.35;
}.token-grid{
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
}.token-chip{
  flex: 0 0 auto;
  display: inline-grid;
  grid-template-columns: auto auto;
  align-items: center;
  gap: 6px;
  border: 0;
  border-radius: 8px;
  padding: 5px 5px 5px 8px;
  background: color-mix(in srgb, var(--surface) 68%, var(--surface-1));
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--outline) 78%, transparent);
  color: var(--on-surface);
  font-family: inherit;
  cursor: pointer;
  text-align: left;
}.token-chip code{
  padding: 1px 5px;
  border-radius: 5px;
  font-family: inherit;
  font-size: 10px;
  font-weight: 600;
  color: color-mix(in srgb, var(--primary-ink) 82%, var(--on-surface-muted));
  background: color-mix(in srgb, var(--primary-soft) 34%, transparent);
}.token-chip span{
  font-size: 12px;
  font-weight: 600;
  color: var(--on-surface);
}.stem-order-list,
.naming-preview-list{
  display: grid;
  gap: 6px;
}.naming-preview-inline{
  display: grid;
  gap: 6px;
}.naming-preview-inline > span{
  color: var(--on-surface-muted);
  font-size: 11px;
}.naming-preview-file{
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  padding: 8px 10px;
  border-radius: 9px;
  background: color-mix(in srgb, var(--surface) 68%, var(--surface-1));
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--outline) 78%, transparent);
  font-family: inherit;
  font-size: 11.5px;
  font-variant-numeric: tabular-nums;
  color: color-mix(in srgb, var(--on-surface) 86%, var(--on-surface-muted));
}.naming-preview-part{
  border-radius: 5px;
  padding: 0 2px;
}.naming-preview-part--literal,
.naming-preview-part--extension{
  color: color-mix(in srgb, var(--on-surface-muted) 88%, var(--on-surface));
}.naming-preview-part--index,
.naming-preview-part--inputnumber{
  color: color-mix(in srgb, #2f6fed 78%, var(--on-surface));
  background: color-mix(in srgb, #2f6fed 10%, transparent);
}.naming-preview-part--filename{
  color: color-mix(in srgb, #0f8a6b 78%, var(--on-surface));
  background: color-mix(in srgb, #0f8a6b 10%, transparent);
}.naming-preview-part--stem{
  color: color-mix(in srgb, #9a5a00 78%, var(--on-surface));
  background: color-mix(in srgb, #d08400 12%, transparent);
}.naming-preview-part--model{
  color: color-mix(in srgb, #7a4ed8 78%, var(--on-surface));
  background: color-mix(in srgb, #7a4ed8 10%, transparent);
}.naming-preview-part--yyyyMMdd,
.naming-preview-part--hhmmss,
.naming-preview-part--ddmmss{
  color: color-mix(in srgb, #b54274 78%, var(--on-surface));
  background: color-mix(in srgb, #b54274 10%, transparent);
}.stem-order-row{
  display: grid;
  grid-template-columns: auto 30px minmax(0, 1fr) auto;
  align-items: center;
  gap: 9px;
  padding: 7px 9px;
  border-radius: 9px;
  background: color-mix(in srgb, var(--surface) 68%, var(--surface-1));
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--outline) 78%, transparent);
  cursor: grab;
  user-select: none;
  touch-action: none;
}.stem-order-row--dragging{ opacity: 0.55; }.stem-order-row .n-icon{ color: var(--on-surface-muted); }.stem-order-row span{ font-variant-numeric: tabular-nums; color: var(--primary-ink); }.stem-order-row strong{ min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.stem-order-row__actions{
  display: inline-flex;
  align-items: center;
  gap: 2px;
}.naming-preview-list code,
.naming-preview-inline code{
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  padding: 8px 10px;
  border-radius: 9px;
  background: color-mix(in srgb, var(--surface) 68%, var(--surface-1));
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--outline) 78%, transparent);
  font-family: inherit;
  font-size: 11.5px;
  font-variant-numeric: tabular-nums;
  color: color-mix(in srgb, var(--on-surface) 86%, var(--on-surface-muted));
}.drawer-footer{
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}</style>
