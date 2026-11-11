<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import TaskInputFiles from '@/components/TaskInputFiles.vue'
import TaskOutputSettings from '@/components/TaskOutputSettings.vue'
import WorkbenchCard from '@/components/media/WorkbenchCard.vue'
import { CloudUploadOutline, SettingsOutline } from '@vicons/ionicons5'
import type { OutputLayout } from '@/stores/task'

const outputDirectoryInput = defineModel<string>('outputDirectoryInput', { required: true })
const outputLayout = defineModel<OutputLayout>('layout', { required: true })
const checkedOutputStems = defineModel<string[]>('checkedOutputStems', { required: true })
const props = defineProps<{
  inputFiles: string[]
  isDragging: boolean
  draggedInputIndex: number | null
  isRunModeLocked: boolean
  outputDirectoryError: string
  outputPreview: string
  formatOptions: Array<{ label: string; value: string }>
  outputNamingSummary: string
  availableStemNames: string[]
  ensembleEnabled: boolean
  ensembleStem: string
  selectedOutputStemCount: number
  settings: { clearInputAfterSubmit: boolean; defaultFormat: string }
  getFileKindLabel: (path: string) => string
  shortenMiddle: (value: string, max: number) => string
}>()
const emit = defineEmits<{
  pickFiles: []
  pickFolder: []
  removeInput: [path: string]
  clearInput: []
  inputPointerDown: [event: PointerEvent, index: number]
  inputPointerMove: [event: PointerEvent]
  inputPointerEnd: []
  openSettings: []
  browseOutput: []
  openNaming: []
  selectAllStems: []
  clearStems: []
}>()
const { t } = useI18n()
</script>

<template>
  <aside class="console__rail">
    <WorkbenchCard class="rail-card--input" data-guide="input" index="01" :title="t('separate.input')" :subtitle="t('separate.candidateCount', { count: props.inputFiles.length })">
      <template #actions>
        <div class="rail-card__actions rail-card__actions--input">
          <label class="input-retention-toggle input-retention-toggle--head">
            <span>{{ t('separate.clearInputAfterSubmit') }}</span>
            <n-switch v-model:value="props.settings.clearInputAfterSubmit" size="small" :disabled="props.isRunModeLocked" />
          </label>
          <n-button v-if="props.inputFiles.length" text size="tiny" class="rail-card__clear" :disabled="props.isRunModeLocked" @click="emit('clearInput')">{{ t('separate.clearAll') }}</n-button>
        </div>
      </template>

      <div
        class="dropzone"
        :class="{ 'dropzone--dragging': props.isDragging, 'dropzone--filled': props.inputFiles.length, 'dropzone--clickable': !props.inputFiles.length && !props.isRunModeLocked }"
        @click="(!props.inputFiles.length && !props.isRunModeLocked) ? emit('pickFiles') : undefined"
      >
        <TaskInputFiles
          v-if="props.inputFiles.length"
          :files="props.inputFiles"
          :busy="props.isRunModeLocked"
          reorderable
          :dragged-index="props.draggedInputIndex"
          :drag-handlers="{
            down: (event: PointerEvent, index: number) => emit('inputPointerDown', event, index),
            move: (event: PointerEvent) => emit('inputPointerMove', event),
            up: () => emit('inputPointerEnd'),
            cancel: () => emit('inputPointerEnd'),
          }"
          :kind-for="props.getFileKindLabel"
          :shorten-for="value => props.shortenMiddle(value, 60)"
          @add="emit('pickFiles')"
          @folder="emit('pickFolder')"
          @remove="path => emit('removeInput', path)"
          @clear="emit('clearInput')"
        />
        <div v-else class="dropzone__empty">
          <div class="dropzone__glyph"><n-icon :component="CloudUploadOutline" /></div>
          <strong>{{ props.isDragging ? t('separate.dropHere') : t('separate.candidateEmpty') }}</strong>
        </div>
      </div>
    </WorkbenchCard>

    <WorkbenchCard class="rail-card--output" data-guide="output" index="02" :title="t('separate.output')" :subtitle="t('separate.outputSummaryHint')">
      <template #actions>
        <n-button text size="tiny" class="rail-card__more" :disabled="props.isRunModeLocked" @click="emit('openSettings')">
          <template #icon><n-icon :component="SettingsOutline" /></template>
          {{ t('separate.configParams') }}
        </n-button>
      </template>

      <TaskOutputSettings
        v-model:model-value="outputDirectoryInput"
        v-model:layout="outputLayout"
        :error="props.outputDirectoryError"
        :preview="props.outputPreview"
        :disabled="props.isRunModeLocked"
        @browse="emit('browseOutput')"
      />

      <label class="ofield" data-guide="output-format">
        <span class="ofield__label">{{ t('settings.defaultFormat') }}</span>
        <n-select v-model:value="props.settings.defaultFormat" size="small" :options="props.formatOptions" :disabled="props.isRunModeLocked" />
      </label>

      <div class="ofield naming-field" data-guide="output-naming">
        <span class="ofield__label">{{ t('separate.namingRule') }}</span>
        <button type="button" class="naming-summary" :disabled="props.isRunModeLocked" @click="emit('openNaming')">
          <span>{{ props.outputNamingSummary }}</span>
          <small>{{ t('separate.namingRuleAction') }}</small>
        </button>
      </div>

      <div class="ofield ofield--stems" data-guide="output-stems">
        <div class="ofield__heading">
          <span class="ofield__label">{{ t('separate.outputStems') }}</span>
          <div v-if="!props.ensembleEnabled && props.availableStemNames.length > 3" class="stem-selection-actions">
            <button
              type="button"
              :disabled="props.isRunModeLocked || props.selectedOutputStemCount === props.availableStemNames.length"
              @click="emit('selectAllStems')"
            >{{ t('separate.selectAllStems') }}</button>
            <span aria-hidden="true">·</span>
            <button
              type="button"
              :disabled="props.isRunModeLocked || props.selectedOutputStemCount === 0"
              @click="emit('clearStems')"
            >{{ t('separate.clearStemSelection') }}</button>
          </div>
        </div>
        <div v-if="props.ensembleEnabled" class="ofield__static">{{ props.ensembleStem || '—' }}</div>
        <div v-else-if="props.availableStemNames.length" class="stem-chips">
          <n-checkbox-group v-model:value="checkedOutputStems" :disabled="props.isRunModeLocked">
            <n-checkbox
              v-for="stem in props.availableStemNames"
              :key="stem"
              :value="stem"
              :label="stem"
            />
          </n-checkbox-group>
        </div>
        <div v-else class="ofield__static ofield__static--muted">{{ t('separate.allStems') }}</div>
      </div>
    </WorkbenchCard>
  </aside>
</template>

<style scoped>
.console__rail
{
  min-height: 0;
  display: grid;
  grid-template-rows: minmax(0, 1fr) auto;
  gap: 12px;
}
/* ============ Rail cards ============ */
.rail-card--output
{
  grid-template-rows: auto auto;
}
.rail-card__actions
{
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}
.rail-card__actions--input
{
  flex: 0 0 auto;
  align-self: center;
  margin-left: auto;
  justify-content: flex-end;
}
.rail-card__clear
{
  color: var(--danger);
  font-size: 11px;
}
.rail-card__more
{
  color: var(--on-surface-muted);
  font-size: 11px;
}
.rail-card__body
{
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.input-retention-toggle
{
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--on-surface-muted);
  font-size: 12px;
}
.input-retention-toggle--head
{
  flex: 0 0 auto;
  justify-content: flex-end;
  min-width: 0;
  gap: 6px;
  font-size: 11px;
  white-space: nowrap;
}
.input-retention-toggle--head span
{
  min-width: 0;
  line-height: 1.25;
  text-align: right;
}
/* dropzone / file list */
.dropzone
{
  flex: 1 1 auto;
  min-height: 0;
  border-radius: 13px;
  border: 1px dashed color-mix(in srgb, var(--outline) 130%, transparent);
  background: color-mix(in srgb, var(--surface) 40%, transparent);
  transition: border-color 150ms ease, background 150ms ease, box-shadow 150ms ease;
  overflow: hidden;
}
.dropzone--filled
{
  border-style: solid;
  border-color: color-mix(in srgb, var(--outline) 90%, transparent);
}
.dropzone--dragging
{
  border-color: var(--primary);
  background: color-mix(in srgb, var(--primary-soft) 18%, var(--surface-1));
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--primary-border) 60%, transparent);
}
.dropzone--clickable
{ cursor: pointer; }
.dropzone--clickable:hover
{
  border-color: color-mix(in srgb, var(--primary) 70%, var(--outline));
  background: color-mix(in srgb, var(--primary-soft) 12%, var(--surface));
}
.dropzone--clickable:hover .dropzone__glyph
{
  color: var(--primary-ink);
  background: color-mix(in srgb, var(--primary-soft) 46%, var(--surface-2));
}
.dropzone__empty
{
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 18px;
  text-align: center;
}
.dropzone__glyph
{
  width: 44px;
  height: 44px;
  display: grid;
  place-items: center;
  border-radius: 13px;
  font-size: 21px;
  color: var(--primary-ink);
  background: color-mix(in srgb, var(--primary-soft) 34%, var(--surface-2));
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--primary-border) 30%, transparent);
  margin-bottom: 2px;
}
.dropzone__empty strong
{ font-size: 13px; color: color-mix(in srgb, var(--on-surface) 72%, var(--on-surface-muted)); font-weight: 500; max-width: 240px; }
/* output fields */
.rail-card__body--output
{ gap: 14px; }
.ofield
{ display: grid; gap: 7px; min-width: 0; }
.ofield__label
{
  font-size: 11px;
  color: var(--on-surface-muted);
  font-weight: 500;
}
.ofield__heading
{
  display: flex;
  min-width: 0;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}
.stem-selection-actions
{
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 5px;
  color: color-mix(in srgb, var(--on-surface-muted) 70%, transparent);
  font-size: 11px;
}
.stem-selection-actions button
{
  padding: 0;
  border: 0;
  color: var(--primary-ink);
  background: transparent;
  font: inherit;
  cursor: pointer;
}
.stem-selection-actions button:hover:not(:disabled)
{
  color: var(--primary-ink);
  text-decoration: underline;
  text-underline-offset: 2px;
}
.stem-selection-actions button:disabled
{
  color: color-mix(in srgb, var(--on-surface-muted) 52%, transparent);
  cursor: default;
}
.ofield__static
{
  min-height: 30px;
  display: flex;
  align-items: center;
  padding: 0 11px;
  border-radius: 9px;
  font-size: 12.5px;
  font-weight: 600;
  background: color-mix(in srgb, var(--surface-2) 56%, transparent);
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--outline) 80%, transparent);
}
.ofield__static--muted
{ font-weight: 400; color: var(--on-surface-muted); }
.ofield--stems .stem-chips
{
  max-height: 108px;
  overflow-y: auto;
  padding: 2px 4px 2px 0;
  overscroll-behavior: contain;
  scrollbar-gutter: stable;
  scrollbar-width: thin;
  scrollbar-color: color-mix(in srgb, var(--on-surface-muted) 44%, transparent) transparent;
}
.ofield--stems .stem-chips::-webkit-scrollbar
{
  width: 9px;
}
.ofield--stems .stem-chips::-webkit-scrollbar-track
{
  border-radius: 999px;
  background: transparent;
}
.ofield--stems .stem-chips::-webkit-scrollbar-thumb
{
  min-height: 28px;
  border: 2px solid transparent;
  border-radius: 999px;
  background: color-mix(in srgb, var(--on-surface-muted) 44%, transparent) padding-box;
}
.ofield--stems .stem-chips::-webkit-scrollbar-thumb:hover
{
  background: color-mix(in srgb, var(--primary) 64%, transparent) padding-box;
}
.ofield--stems .stem-chips::-webkit-scrollbar-button,
.ofield--stems .stem-chips::-webkit-scrollbar-corner
{
  display: none;
  width: 0;
  height: 0;
  background: transparent;
}
.stem-chips :deep(.n-checkbox-group)
{
  display: flex;
  flex-wrap: wrap;
  gap: 7px;
}
.stem-chips :deep(.n-checkbox)
{
  min-height: 28px;
  align-items: center;
  padding: 4px 10px 4px 8px;
  border-radius: 9px;
  background: color-mix(in srgb, var(--surface-2) 52%, transparent);
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--outline) 78%, transparent);
  font-size: 12px;
  transition: background 140ms ease, box-shadow 140ms ease;
}
.stem-chips :deep(.n-checkbox:hover)
{
  background: color-mix(in srgb, var(--primary-soft) 22%, var(--surface-2));
}
.stem-chips :deep(.n-checkbox--checked)
{
  background: color-mix(in srgb, var(--primary-soft) 34%, var(--surface-2));
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--primary-border) 60%, transparent);
}
</style>
