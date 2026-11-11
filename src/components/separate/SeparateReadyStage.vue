<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import type { ModelEntry } from '@/stores/model'
import { CubeOutline, ListOutline } from '@vicons/ionicons5'
import SeparateLaunchBar from './SeparateLaunchBar.vue'
import SeparateModelPicker from './SeparateModelPicker.vue'

const ensembleEnabled = defineModel<boolean>('ensembleEnabled', { required: true })
const modelSearch = defineModel<string>('modelSearch', { required: true })
const modelCategoryFilter = defineModel<string>('modelCategoryFilter', { required: true })
const modelListSortMode = defineModel<string>('modelListSortMode', { required: true })
const modelListViewMode = defineModel<'list' | 'card'>('modelListViewMode', { required: true })
const modelPage = defineModel<number>('modelPage', { required: true })
const modelPageSize = defineModel<number>('modelPageSize', { required: true })
const props = defineProps<{
  hasQueueEntries: boolean
  queueJobs: import('@/stores/task').SeparationJob[]
  modelPanelHasModels: boolean
  modelCategoryOptions: Array<{ label: string; value: string }>
  modelSortOptions: Array<{ label: string; value: string }>
  ensembleModels: string[]
  pagedDownloadedModels: ModelEntry[]
  selectedModelName: string
  handleSelectModel: (model: ModelEntry) => void
  openSeparateContextMenu: (event: MouseEvent, kind: 'model', item: ModelEntry) => void
  categoryLabel: (model: ModelEntry) => string
  modelMetaLine: (model: ModelEntry) => string
  modelUseCount: (name: string) => number
  modelNote: (name: string) => string
  filteredDownloadedModels: ModelEntry[]
  modelPageSizeOptions: number[]
  modelDownloaded: boolean
  modelPanelLoading: boolean
  canStart: boolean
  startStatusText: string
  outputSummaryPath: string
  revealOutput: () => void
  submittingJob: boolean
  start: () => void | Promise<void>
}>()
const emit = defineEmits<{ openQueue: []; openEnsemble: []; openModels: [] }>()
const { t } = useI18n()
</script>

<template>
  <section class="stage-view stage-view--ready">
    <div class="stage-head">
      <div class="stage-head__label">
        <n-icon :component="CubeOutline" />
        <div>
          <h2>{{ t('separate.runTarget') }}</h2>
          <p>{{ t('separate.modelPanelHint') }}</p>
        </div>
      </div>
      <div class="stage-head__controls">
        <div class="stage-head__extra" data-guide="model-mode">
          <n-select
            :value="ensembleEnabled ? 'ensemble' : 'single'"
            size="small"
            :options="[
              { label: t('separate.singleModelMode'), value: 'single' },
              { label: t('separate.ensembleMode'), value: 'ensemble' },
            ]"
            class="model-mode-select"
            :aria-label="t('separate.modelModeLabel')"
            @update:value="(value: string | number) => { ensembleEnabled = value === 'ensemble' }"
          />
        </div>
        <n-button class="stage-head__queue" size="small" secondary data-guide="task-queue" @click="emit('openQueue')">
          <template #icon><n-icon :component="ListOutline" /></template>
          {{ t('separate.batchQueue') }}
          <span class="stage-head__queue-count" :class="{ 'stage-head__queue-count--active': props.hasQueueEntries }">{{ props.queueJobs.length }}</span>
        </n-button>
      </div>
    </div>

    <transition name="stage-swap" mode="out-in">
      <SeparateModelPicker
        key="model"
        v-model:search="modelSearch"
        v-model:category="modelCategoryFilter"
        v-model:sort="modelListSortMode"
        v-model:view-mode="modelListViewMode"
        v-model:page="modelPage"
        v-model:page-size="modelPageSize"
        :ensemble-enabled="ensembleEnabled"
        :has-models="props.modelPanelHasModels"
        :models="props.pagedDownloadedModels"
        :category-options="props.modelCategoryOptions"
        :sort-options="props.modelSortOptions"
        :ensemble-models="props.ensembleModels"
        :selected-model-name="props.selectedModelName"
        :filtered-count="props.filteredDownloadedModels.length"
        :page-size-options="props.modelPageSizeOptions"
        :model-downloaded="props.modelDownloaded"
        :panel-loading="props.modelPanelLoading"
        :category-label="props.categoryLabel"
        :model-meta-line="props.modelMetaLine"
        :model-use-count="props.modelUseCount"
        :model-note="props.modelNote"
        @select="props.handleSelectModel"
        @contextmenu="(event, model) => props.openSeparateContextMenu(event, 'model', model)"
        @open-ensemble="emit('openEnsemble')"
        @open-models="emit('openModels')"
      />
    </transition>

    <SeparateLaunchBar
      :can-start="props.canStart"
      :start-status-text="props.startStatusText"
      :output-summary-path="props.outputSummaryPath"
      :submitting-job="props.submittingJob"
      @reveal="props.revealOutput"
      @start="props.start"
    />
  </section>
</template>

<style scoped>/* stage: ready target selection */
.stage-head
{
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.stage-head__label
{
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}
.stage-head__label > .n-icon
{
  flex: 0 0 auto;
  width: 38px;
  height: 38px;
  display: grid;
  place-items: center;
  border-radius: 12px;
  font-size: 19px;
  color: var(--primary-ink);
  background: color-mix(in srgb, var(--primary-soft) 40%, var(--surface-2));
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--primary-border) 34%, transparent);
}
.stage-head__label h2
{
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  letter-spacing: -0.02em;
}
.stage-head__label p
{
  margin: 2px 0 0;
  font-size: 12px;
  color: var(--on-surface-muted);
  line-height: 1.4;
}
.stage-head__extra
{ flex: 0 0 auto; }
.model-mode-select
{ width: 156px; }
.model-mode-select :deep(.n-base-selection-label)
{ white-space: nowrap; }
.stage-head__queue
{
  flex: 0 0 auto;
}
.stage-head__controls
{
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  margin-left: auto;
}
.stage-head__queue-count
{
  min-width: 20px;
  height: 20px;
  display: inline-grid;
  place-items: center;
  margin-left: 3px;
  padding: 0 6px;
  border-radius: 999px;
  color: var(--on-surface-muted);
  background: color-mix(in srgb, var(--outline) 42%, transparent);
  font-size: 10px;
  font-variant-numeric: tabular-nums;
}
.stage-head__queue-count--active
{
  color: var(--primary-ink);
  background: var(--primary-soft);
}</style>
