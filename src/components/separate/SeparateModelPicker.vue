<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import ModelPanelState from '@/components/ModelPanelState.vue'
import type { ModelEntry } from '@/stores/model'
import {
  GridOutline,
  ListOutline,
  SearchOutline,
  SettingsOutline,
} from '@vicons/ionicons5'

const search = defineModel<string>('search', { required: true })
const category = defineModel<string>('category', { required: true })
const sort = defineModel<string>('sort', { required: true })
const viewMode = defineModel<'list' | 'card'>('viewMode', { required: true })
const page = defineModel<number>('page', { required: true })
const pageSize = defineModel<number>('pageSize', { required: true })
const props = defineProps<{
  ensembleEnabled: boolean
  hasModels: boolean
  models: ModelEntry[]
  categoryOptions: Array<{ label: string; value: string }>
  sortOptions: Array<{ label: string; value: string }>
  ensembleModels: string[]
  selectedModelName: string
  filteredCount: number
  pageSizeOptions: number[]
  modelDownloaded: boolean
  panelLoading: boolean
  categoryLabel: (model: ModelEntry) => string
  modelMetaLine: (model: ModelEntry) => string
  modelUseCount: (name: string) => number
  modelNote: (name: string) => string
}>()
const emit = defineEmits<{
  select: [model: ModelEntry]
  contextmenu: [event: MouseEvent, model: ModelEntry]
  openEnsemble: []
  openModels: []
}>()
const { t } = useI18n()
</script>

<template>
  <div key="model" class="target-pane" :class="{ 'target-pane--ensemble': props.ensembleEnabled }">
    <template v-if="props.hasModels">
      <div class="target-toolbar" data-guide="separation-models">
        <n-input v-model:value="search" clearable :placeholder="t('separate.modelSearchPlaceholder')">
          <template #prefix><n-icon :component="SearchOutline" /></template>
        </n-input>
        <n-select
          class="target-toolbar__filter"
          v-model:value="category"
          :menu-props="{ class: 'model-picker__category-menu' }"
          :options="props.categoryOptions"
        />
        <n-select
          v-model:value="sort"
          class="target-toolbar__sort"
          :options="props.sortOptions"
          :aria-label="t('separate.modelSortLabel')"
        />
        <div class="view-toggle target-toolbar__view" role="group" :aria-label="t('models.viewMode')">
          <button
            type="button"
            class="view-toggle__button"
            :class="{ 'view-toggle__button--active': viewMode === 'list' }"
            :aria-pressed="viewMode === 'list'"
            :title="t('models.viewList')"
            @click="viewMode = 'list'"
          >
            <n-icon :component="ListOutline" />
          </button>
          <button
            type="button"
            class="view-toggle__button"
            :class="{ 'view-toggle__button--active': viewMode === 'card' }"
            :aria-pressed="viewMode === 'card'"
            :title="t('models.viewCard')"
            @click="viewMode = 'card'"
          >
            <n-icon :component="GridOutline" />
          </button>
        </div>
      </div>
      <div v-if="props.ensembleEnabled" class="ensemble-summary-bar">
        <div class="ensemble-summary-bar__info">
          <strong>{{ t('separate.ensembleMode') }}</strong>
          <span>{{ props.ensembleModels.length ? t('separate.ensembleSelectedCount', { count: props.ensembleModels.length }) : t('separate.ensembleNeedModels') }}</span>
        </div>
        <n-button size="small" secondary :disabled="!props.ensembleModels.length" @click="emit('openEnsemble')">
          <template #icon><n-icon :component="SettingsOutline" /></template>
          {{ t('separate.ensembleConfigure') }}
        </n-button>
      </div>
      <div
        v-if="props.models.length"
        class="target-list target-list--models"
        :class="`target-list--${viewMode}`"
        role="listbox"
        :aria-label="t('separate.model')"
      >
        <button
          v-for="item in props.models"
          :key="item.name"
          type="button"
          role="option"
          :aria-selected="props.ensembleEnabled ? props.ensembleModels.includes(item.name) : props.selectedModelName === item.name"
          class="target-row"
          :class="{ 'target-row--active': props.ensembleEnabled ? props.ensembleModels.includes(item.name) : props.selectedModelName === item.name }"
          @click="emit('select', item)"
          @contextmenu.stop.prevent="emit('contextmenu', $event, item)"
        >
          <span class="target-row__radio"></span>
          <span class="target-row__body">
            <span class="target-row__name" :title="item.name">{{ item.name }}</span>
            <span class="target-row__meta">
              <span class="target-row__tag" :title="props.categoryLabel(item)">{{ props.categoryLabel(item) }}</span>
              <span class="target-row__desc" :title="props.modelMetaLine(item)">{{ props.modelMetaLine(item) }}</span>
              <span v-if="viewMode === 'list'" class="target-row__usage">{{ t('models.useCountValue', { count: props.modelUseCount(item.name) }) }}</span>
            </span>
            <span
              v-if="props.modelNote(item.name)"
              class="target-row__note"
              :title="props.modelNote(item.name)"
            >{{ props.modelNote(item.name) }}</span>
          </span>
        </button>
      </div>
      <div v-else class="stage-empty">
        <div class="stage-empty__glyph"><n-icon :component="SearchOutline" /></div>
        <strong>{{ t('separate.modelSearchEmpty') }}</strong>
      </div>
      <div v-if="props.filteredCount" class="model-pagination">
        <span>{{ t('separate.modelPageSummary', { total: props.filteredCount }) }}</span>
        <n-pagination
          v-model:page="page"
          v-model:page-size="pageSize"
          :item-count="props.filteredCount"
          :page-sizes="props.pageSizeOptions"
          show-size-picker
          size="small"
        />
      </div>
      <div v-if="props.selectedModelName && !props.modelDownloaded" class="stage-alert">
        {{ t('separate.startHintModelMissing') }}
      </div>
    </template>
    <ModelPanelState
      v-else
      :loading="props.panelLoading"
      :empty-title="t('separate.modelPanelEmptyTitle')"
      :empty-description="t('separate.modelPanelEmptyDesc')"
      :loading-title="t('separate.modelPanelLoadingTitle')"
      :loading-description="t('separate.modelPanelLoadingDesc')"
      :action-label="t('separate.modelPanelPrimaryAction')"
      @action="emit('openModels')"
    />
  </div>
</template>

<style scoped>
.target-pane
{
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.view-toggle
{
  display: inline-flex;
  flex-shrink: 0;
  padding: 2px;
  border-radius: 8px;
  background: color-mix(in srgb, var(--surface-2) 60%, transparent);
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--outline) 55%, transparent);
}
.view-toggle__button
{
  display: grid;
  place-items: center;
  width: 28px;
  height: 24px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--on-surface-muted);
  cursor: pointer;
  font-size: 14px;
  transition: background 150ms ease, color 150ms ease;
}
.view-toggle__button:hover
{ color: var(--on-surface); }
.view-toggle__button:focus-visible
{
  outline: 2px solid var(--primary);
  outline-offset: 2px;
}
.view-toggle__button--active
{
  background: var(--surface-1);
  color: var(--primary-ink);
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.12);
}
.target-toolbar
{
  flex: 0 0 auto;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 170px 170px auto;
  align-items: center;
  gap: 10px;
}
.target-toolbar--single
{ grid-template-columns: minmax(0, 1fr); }
.target-toolbar__filter,
.target-toolbar__sort
{ min-width: 0; }
.target-toolbar__view
{ justify-self: end; }
.ensemble-summary-bar
{
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 12px;
  padding: 7px 10px;
  border: 1px solid var(--outline);
  border-radius: 7px;
  background: var(--surface-2);
}
.ensemble-summary-bar__info
{ display: grid; gap: 2px; min-width: 0; }
.ensemble-summary-bar__info strong
{ font-size: 12px; }
.ensemble-summary-bar__info span,
.ensemble-weights > span
{ color: var(--on-surface-muted); font-size: 12px; }
.model-pagination
{
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-height: 38px;
  margin-top: 4px;
  padding: 10px 4px 2px;
  border-top: 1px solid color-mix(in srgb, var(--outline) 72%, transparent);
  color: var(--on-surface-muted);
  font-size: 11px;
}
.model-pagination :deep(.n-pagination)
{ flex: 0 0 auto; }
.target-list
{
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  /* Wraps into columns once there is room. A single column of very wide rows wastes the space
     and makes each entry harder to scan, since the name and its tags drift far apart. */
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  align-content: start;
  gap: 6px;
  padding: 2px;
  padding-right: 6px;
  scrollbar-width: thin;
  scrollbar-color: color-mix(in srgb, var(--on-surface-muted) 44%, transparent) transparent;
}
.target-list--list
{
  grid-template-columns: minmax(0, 1fr);
  gap: 5px;
}
.target-list::-webkit-scrollbar
{
  width: 9px;
}
.target-list::-webkit-scrollbar-track
{
  border-radius: 999px;
  background: transparent;
}
.target-list::-webkit-scrollbar-thumb
{
  min-height: 28px;
  border: 2px solid transparent;
  border-radius: 999px;
  background: color-mix(in srgb, var(--on-surface-muted) 44%, transparent) padding-box;
}
.target-list::-webkit-scrollbar-thumb:hover
{
  background: color-mix(in srgb, var(--primary) 64%, transparent) padding-box;
}
.target-list::-webkit-scrollbar-button,
.target-list::-webkit-scrollbar-corner
{
  display: none;
  width: 0;
  height: 0;
  background: transparent;
}
.target-row
{
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  padding: 11px 14px;
  border-radius: 12px;
  border: 0;
  text-align: left;
  cursor: pointer;
  background: color-mix(in srgb, var(--surface-2) 38%, transparent);
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--outline) 78%, transparent);
  transition: background 150ms ease, box-shadow 150ms ease;
  font-family: inherit;
  color: inherit;
}
.target-row:hover
{
  background: color-mix(in srgb, var(--surface-2) 62%, transparent);
  box-shadow: inset 0 0 0 1px var(--outline-strong);
}
.target-row--active
{
  background: color-mix(in srgb, var(--surface-2) 82%, var(--surface-1));
  box-shadow: inset 0 0 0 1px var(--outline-strong);
}
.target-row--unavailable,
.target-row--unavailable:hover
{
  cursor: not-allowed;
  opacity: 0.72;
  background: color-mix(in srgb, var(--surface-2) 34%, transparent);
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--danger) 24%, var(--outline));
}
.workflow-target--restricted .target-row__unavailable
{
  color: var(--on-surface-muted);
  background: var(--surface-2);
}
.workflow-target--restricted .target-row__issue
{ color: var(--on-surface-muted); }
.target-row__radio
{
  flex: 0 0 auto;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  box-shadow: inset 0 0 0 1.5px color-mix(in srgb, var(--on-surface-muted) 60%, transparent);
  transition: box-shadow 150ms ease, background 150ms ease;
}
.target-row--active .target-row__radio
{
  background: var(--primary);
  box-shadow:
    inset 0 0 0 1.5px var(--primary),
    inset 0 0 0 4px var(--surface-1);
}
.target-row__body
{
  min-width: 0;
  flex: 1;
  display: grid;
  gap: 3px;
}
.target-row__heading
{
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 8px;
}
.target-row__name
{
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13.5px;
  font-weight: 600;
  letter-spacing: -0.01em;
}
.target-row--active .target-row__name
{ color: var(--on-surface); }
.target-row__unavailable
{
  flex: 0 0 auto;
  padding: 2px 7px;
  border-radius: 999px;
  color: var(--danger);
  background: color-mix(in srgb, var(--danger) 10%, var(--surface-2));
  font-size: 10px;
  font-weight: 650;
  line-height: 1.35;
}
.target-row__meta
{
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 8px;
}
.target-row__tag
{
  flex: 0 0 auto;
  max-width: 40%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  padding: 2px 9px;
  border-radius: 999px;
  font-size: 10px;
  color: color-mix(in srgb, var(--primary-ink) 74%, var(--on-surface-muted));
  background: color-mix(in srgb, var(--primary-soft) 26%, var(--surface-2));
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--primary-border) 26%, transparent);
}
.target-row__desc
{
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 11px;
  color: var(--on-surface-muted);
  line-height: 1.4;
}
.target-row__issue
{
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--danger);
  font-size: 10.5px;
  line-height: 1.4;
}
.target-row__usage
{
  flex: 0 0 auto;
  font-size: 11px;
  color: var(--on-surface-muted);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
/* The user's own note. Distinguished from the generated meta line by the accent bar, so a row
   with a note reads as annotated rather than as having an extra field. */
.target-row__note
{
  display: block;
  min-width: 0;
  margin-top: 3px;
  padding-left: 6px;
  border-left: 2px solid color-mix(in srgb, var(--primary) 55%, transparent);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 11px;
  line-height: 1.4;
  color: color-mix(in srgb, var(--on-surface) 72%, transparent);
  text-align: left;
}
.target-list--list .target-row
{
  min-height: 48px;
  padding: 8px 12px;
  border-radius: 8px;
}
.target-list--list .target-row__body
{
  grid-template-columns: minmax(160px, 0.9fr) minmax(180px, 1.1fr);
  align-items: center;
  gap: 4px 14px;
}
.target-list--list .target-row__name
{ font-size: 13px; }
.target-list--list .target-row__meta
{ min-width: 0; }
.target-list--list .target-row__note
{
  grid-column: 1 / -1;
  margin-top: 0;
}
.stage-empty
{
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  text-align: center;
  padding: 32px;
}
.stage-empty__glyph
{
  width: 56px;
  height: 56px;
  display: grid;
  place-items: center;
  border-radius: 16px;
  font-size: 26px;
  color: var(--primary-ink);
  background: color-mix(in srgb, var(--primary-soft) 30%, var(--surface-2));
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--primary-border) 26%, transparent);
  margin-bottom: 4px;
}
.stage-empty strong
{ font-size: 14px; }
.stage-empty p
{ margin: 0; font-size: 12px; color: var(--on-surface-muted); line-height: 1.5; max-width: 320px; }
.stage-empty .n-button
{ margin-top: 8px; }
.stage-alert
{
  flex: 0 0 auto;
  padding: 11px 13px;
  border-radius: 12px;
  font-size: 12px;
  line-height: 1.5;
  color: color-mix(in srgb, var(--warning) 78%, var(--on-surface));
  background: color-mix(in srgb, var(--warning) 12%, var(--surface-2));
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--warning) 34%, transparent);
}
</style>
