<script setup lang="ts">
import { reactive } from 'vue'
import { useI18n } from 'vue-i18n'
import ModelPanelState from '@/components/ModelPanelState.vue'
import {
  CheckmarkCircle,
  CubeOutline,
  GridOutline,
  ListOutline,
  OpenOutline,
  PlayOutline,
  SearchOutline,
  SettingsOutline,
} from '@vicons/ionicons5'

const props = defineProps<{ context: Record<string, any> }>()
const ctx = reactive(props.context) as any
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
            :value="ctx.ensembleEnabled ? 'ensemble' : 'single'"
            size="small"
            :options="[
              { label: t('separate.singleModelMode'), value: 'single' },
              { label: t('separate.ensembleMode'), value: 'ensemble' },
            ]"
            class="model-mode-select"
            :aria-label="t('separate.modelModeLabel')"
            @update:value="(value: string | number) => { ctx.ensembleEnabled = value === 'ensemble' }"
          />
        </div>
        <n-button class="stage-head__queue" size="small" secondary data-guide="task-queue" @click="ctx.showQueueModal = true">
          <template #icon><n-icon :component="ListOutline" /></template>
          {{ t('separate.batchQueue') }}
          <span class="stage-head__queue-count" :class="{ 'stage-head__queue-count--active': ctx.hasQueueEntries }">{{ ctx.queueJobs.length }}</span>
        </n-button>
      </div>
    </div>

    <transition name="stage-swap" mode="out-in">
      <div key="model" class="target-pane" :class="{ 'target-pane--ensemble': ctx.ensembleEnabled }">
        <template v-if="ctx.modelPanelHasModels">
          <div class="target-toolbar" data-guide="separation-models">
            <n-input
              v-model:value="ctx.modelSearch"
              clearable
              :placeholder="t('separate.modelSearchPlaceholder')"
            >
              <template #prefix><n-icon :component="SearchOutline" /></template>
            </n-input>
            <n-select
              class="target-toolbar__filter"
              v-model:value="ctx.modelCategoryFilter"
              :menu-props="{ class: 'model-picker__category-menu' }"
              :options="ctx.modelCategoryOptions"
            />
            <n-select
              v-model:value="ctx.modelListSortMode"
              class="target-toolbar__sort"
              :options="ctx.modelSortOptions"
              :aria-label="t('separate.modelSortLabel')"
            />
            <div class="view-toggle target-toolbar__view" role="group" :aria-label="t('models.viewMode')">
              <button
                type="button"
                class="view-toggle__button"
                :class="{ 'view-toggle__button--active': ctx.modelListViewMode === 'list' }"
                :aria-pressed="ctx.modelListViewMode === 'list'"
                :title="t('models.viewList')"
                @click="ctx.modelListViewMode = 'list'"
              >
                <n-icon :component="ListOutline" />
              </button>
              <button
                type="button"
                class="view-toggle__button"
                :class="{ 'view-toggle__button--active': ctx.modelListViewMode === 'card' }"
                :aria-pressed="ctx.modelListViewMode === 'card'"
                :title="t('models.viewCard')"
                @click="ctx.modelListViewMode = 'card'"
              >
                <n-icon :component="GridOutline" />
              </button>
            </div>
          </div>
          <div v-if="ctx.ensembleEnabled" class="ensemble-summary-bar">
            <div class="ensemble-summary-bar__info">
              <strong>{{ t('separate.ensembleMode') }}</strong>
              <span>{{ ctx.ensembleModels.length ? t('separate.ensembleSelectedCount', { count: ctx.ensembleModels.length }) : t('separate.ensembleNeedModels') }}</span>
            </div>
            <n-button size="small" secondary :disabled="!ctx.ensembleModels.length" @click="ctx.showEnsembleModal = true">
              <template #icon><n-icon :component="SettingsOutline" /></template>
              {{ t('separate.ensembleConfigure') }}
            </n-button>
          </div>
          <div
            v-if="ctx.pagedDownloadedModels.length"
            class="target-list target-list--models"
            :class="`target-list--${ctx.modelListViewMode}`"
            role="listbox"
            :aria-label="t('separate.model')"
          >
            <button
              v-for="item in ctx.pagedDownloadedModels"
              :key="item.name"
              type="button"
              role="option"
              :aria-selected="ctx.ensembleEnabled ? ctx.ensembleModels.includes(item.name) : ctx.selectedModelName === item.name"
              class="target-row"
              :class="{ 'target-row--active': ctx.ensembleEnabled ? ctx.ensembleModels.includes(item.name) : ctx.selectedModelName === item.name }"
              @click="ctx.handleSelectModel(item)"
              @contextmenu.stop.prevent="ctx.openSeparateContextMenu($event, 'model', item)"
            >
              <span class="target-row__radio"></span>
              <span class="target-row__body">
                <span class="target-row__name" :title="item.name">{{ item.name }}</span>
                <span class="target-row__meta">
                  <span class="target-row__tag" :title="ctx.categoryLabel(item)">{{ ctx.categoryLabel(item) }}</span>
                  <span class="target-row__desc" :title="ctx.modelMetaLine(item)">{{ ctx.modelMetaLine(item) }}</span>
                  <span v-if="ctx.modelListViewMode === 'list'" class="target-row__usage">{{ t('models.useCountValue', { count: ctx.modelUseCount(item.name) }) }}</span>
                </span>
                <span
                  v-if="ctx.modelNote(item.name)"
                  class="target-row__note"
                  :title="ctx.modelNote(item.name)"
                >{{ ctx.modelNote(item.name) }}</span>
              </span>
            </button>
          </div>
          <div v-else class="stage-empty">
            <div class="stage-empty__glyph"><n-icon :component="SearchOutline" /></div>
            <strong>{{ t('separate.modelSearchEmpty') }}</strong>
          </div>
          <div v-if="ctx.filteredDownloadedModels.length" class="model-pagination">
            <span>{{ t('separate.modelPageSummary', { total: ctx.filteredDownloadedModels.length }) }}</span>
            <n-pagination
              v-model:page="ctx.modelPage"
              v-model:page-size="ctx.modelPageSize"
              :item-count="ctx.filteredDownloadedModels.length"
              :page-sizes="ctx.modelPageSizeOptions"
              show-size-picker
              size="small"
            />
          </div>
          <div v-if="ctx.selectedModelName && !ctx.modelDownloaded" class="stage-alert">
            {{ t('separate.startHintModelMissing') }}
          </div>
        </template>
        <ModelPanelState
          v-else
          :loading="ctx.modelPanelLoading"
          :empty-title="t('separate.modelPanelEmptyTitle')"
          :empty-description="t('separate.modelPanelEmptyDesc')"
          :loading-title="t('separate.modelPanelLoadingTitle')"
          :loading-description="t('separate.modelPanelLoadingDesc')"
          :action-label="t('separate.modelPanelPrimaryAction')"
          @action="ctx.openModelsPage"
        />
      </div>
    </transition>

    <footer class="launch-bar" :class="`launch-bar--${ctx.canStart ? 'ready' : 'idle'}`">
      <div class="launch-bar__status">
        <span class="launch-bar__glyph"><n-icon :component="CheckmarkCircle" /></span>
        <div class="launch-bar__text">
          <strong>{{ ctx.startStatusText }}</strong>
          <span :title="ctx.outputSummaryPath">{{ ctx.outputSummaryPath }}</span>
        </div>
      </div>
      <div class="launch-bar__actions">
        <n-button quaternary class="launch-bar__reveal" :title="t('separate.openOutput')" @click="ctx.revealOutput">
          <template #icon><n-icon :component="OpenOutline" /></template>
          {{ t('separate.openOutput') }}
        </n-button>
        <n-button type="primary" size="large" class="launch-bar__go" data-guide="start-separation" :loading="ctx.submittingJob" :disabled="!ctx.canStart" @click="ctx.start">
          <template #icon><n-icon :component="PlayOutline" /></template>
          {{ t('separate.startTask') }}
        </n-button>
      </div>
    </footer>
  </section>
</template>

<style scoped>
/* ============ Launch bar (stage bottom) ============ */
.launch-bar{
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 12px 14px;
  border-radius: 15px;
  background:
    linear-gradient(180deg, color-mix(in srgb, var(--primary-soft) 12%, transparent), transparent),
    color-mix(in srgb, var(--surface-2) 34%, transparent);
  box-shadow:
    inset 0 0 0 1px color-mix(in srgb, var(--outline) 84%, transparent),
    inset 0 1px 0 rgba(255,255,255,0.04);
}.launch-bar--ready{
  box-shadow:
    inset 0 0 0 1px color-mix(in srgb, var(--success) 30%, transparent),
    inset 0 1px 0 rgba(255,255,255,0.04);
}.launch-bar__status{
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 11px;
}.launch-bar__glyph{
  flex: 0 0 auto;
  width: 34px;
  height: 34px;
  display: grid;
  place-items: center;
  border-radius: 10px;
  font-size: 19px;
  color: var(--success);
  background: color-mix(in srgb, var(--success) 14%, var(--surface-2));
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--success) 32%, transparent);
}.launch-bar--idle .launch-bar__glyph{
  color: var(--on-surface-muted);
  background: color-mix(in srgb, var(--surface-2) 60%, transparent);
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--outline) 80%, transparent);
}.launch-bar__text{
  min-width: 0;
  display: grid;
  gap: 1px;
}.launch-bar__text strong{
  font-size: 13px;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}.launch-bar--idle .launch-bar__text strong{ color: var(--on-surface-muted); }.launch-bar__text span{
  font-size: 11px;
  color: var(--on-surface-muted);
  overflow-wrap: anywhere;
  white-space: normal;
  font-variant-numeric: tabular-nums;
}.launch-bar__actions{
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 10px;
}.launch-bar__reveal{ color: var(--on-surface-muted); }.launch-bar__go{
  min-width: 156px;
  font-weight: 600;
  box-shadow:
    inset 0 1px 0 rgba(255,255,255,0.2),
    0 12px 28px color-mix(in srgb, var(--primary-glow) 42%, transparent);
}.dark-theme .launch-bar__go{ box-shadow: none; }/* stage: ready target selection */
.stage-head{
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}.stage-head__label{
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}.stage-head__label > .n-icon{
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
}.stage-head__label h2{
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  letter-spacing: -0.02em;
}.stage-head__label p{
  margin: 2px 0 0;
  font-size: 12px;
  color: var(--on-surface-muted);
  line-height: 1.4;
}.target-pane{
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
}.stage-head__extra{ flex: 0 0 auto; }.model-mode-select{ width: 156px; }.model-mode-select :deep(.n-base-selection-label){ white-space: nowrap; }.view-toggle{
  display: inline-flex;
  flex-shrink: 0;
  padding: 2px;
  border-radius: 8px;
  background: color-mix(in srgb, var(--surface-2) 60%, transparent);
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--outline) 55%, transparent);
}.view-toggle__button{
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
}.view-toggle__button:hover{ color: var(--on-surface); }.view-toggle__button:focus-visible{
  outline: 2px solid var(--primary);
  outline-offset: 2px;
}.view-toggle__button--active{
  background: var(--surface-1);
  color: var(--primary-ink);
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.12);
}.target-toolbar{
  flex: 0 0 auto;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 170px 170px auto;
  align-items: center;
  gap: 10px;
}.target-toolbar--single{ grid-template-columns: minmax(0, 1fr); }.target-toolbar__filter,
.target-toolbar__sort{ min-width: 0; }.target-toolbar__view{ justify-self: end; }.ensemble-summary-bar{
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 12px;
  padding: 7px 10px;
  border: 1px solid var(--outline);
  border-radius: 7px;
  background: var(--surface-2);
}.ensemble-summary-bar__info{ display: grid; gap: 2px; min-width: 0; }.ensemble-summary-bar__info strong{ font-size: 12px; }.ensemble-summary-bar__info span,
.ensemble-weights > span{ color: var(--on-surface-muted); font-size: 12px; }.model-pagination{
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
}.model-pagination :deep(.n-pagination){ flex: 0 0 auto; }.target-list{
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
}.target-list--list{
  grid-template-columns: minmax(0, 1fr);
  gap: 5px;
}.target-list::-webkit-scrollbar{
  width: 9px;
}.target-list::-webkit-scrollbar-track{
  border-radius: 999px;
  background: transparent;
}.target-list::-webkit-scrollbar-thumb{
  min-height: 28px;
  border: 2px solid transparent;
  border-radius: 999px;
  background: color-mix(in srgb, var(--on-surface-muted) 44%, transparent) padding-box;
}.target-list::-webkit-scrollbar-thumb:hover{
  background: color-mix(in srgb, var(--primary) 64%, transparent) padding-box;
}.target-list::-webkit-scrollbar-button,
.target-list::-webkit-scrollbar-corner{
  display: none;
  width: 0;
  height: 0;
  background: transparent;
}.target-row{
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
}.target-row:hover{
  background: color-mix(in srgb, var(--surface-2) 62%, transparent);
  box-shadow: inset 0 0 0 1px var(--outline-strong);
}.target-row--active{
  background: color-mix(in srgb, var(--surface-2) 82%, var(--surface-1));
  box-shadow: inset 0 0 0 1px var(--outline-strong);
}.target-row--unavailable,
.target-row--unavailable:hover{
  cursor: not-allowed;
  opacity: 0.72;
  background: color-mix(in srgb, var(--surface-2) 34%, transparent);
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--danger) 24%, var(--outline));
}.workflow-target--restricted .target-row__unavailable{
  color: var(--on-surface-muted);
  background: var(--surface-2);
}.workflow-target--restricted .target-row__issue{ color: var(--on-surface-muted); }.target-row__radio{
  flex: 0 0 auto;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  box-shadow: inset 0 0 0 1.5px color-mix(in srgb, var(--on-surface-muted) 60%, transparent);
  transition: box-shadow 150ms ease, background 150ms ease;
}.target-row--active .target-row__radio{
  background: var(--primary);
  box-shadow:
    inset 0 0 0 1.5px var(--primary),
    inset 0 0 0 4px var(--surface-1);
}.target-row__body{
  min-width: 0;
  flex: 1;
  display: grid;
  gap: 3px;
}.target-row__heading{
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 8px;
}.target-row__name{
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13.5px;
  font-weight: 600;
  letter-spacing: -0.01em;
}.target-row--active .target-row__name{ color: var(--on-surface); }.target-row__unavailable{
  flex: 0 0 auto;
  padding: 2px 7px;
  border-radius: 999px;
  color: var(--danger);
  background: color-mix(in srgb, var(--danger) 10%, var(--surface-2));
  font-size: 10px;
  font-weight: 650;
  line-height: 1.35;
}.target-row__meta{
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 8px;
}.target-row__tag{
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
}.target-row__desc{
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 11px;
  color: var(--on-surface-muted);
  line-height: 1.4;
}.target-row__issue{
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--danger);
  font-size: 10.5px;
  line-height: 1.4;
}.target-row__usage{
  flex: 0 0 auto;
  font-size: 11px;
  color: var(--on-surface-muted);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}/* The user's own note. Distinguished from the generated meta line by the accent bar, so a row
   with a note reads as annotated rather than as having an extra field. */
.target-row__note{
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
}.target-list--list .target-row{
  min-height: 48px;
  padding: 8px 12px;
  border-radius: 8px;
}.target-list--list .target-row__body{
  grid-template-columns: minmax(160px, 0.9fr) minmax(180px, 1.1fr);
  align-items: center;
  gap: 4px 14px;
}.target-list--list .target-row__name{ font-size: 13px; }.target-list--list .target-row__meta{ min-width: 0; }.target-list--list .target-row__note{
  grid-column: 1 / -1;
  margin-top: 0;
}.stage-empty{
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  text-align: center;
  padding: 32px;
}.stage-empty__glyph{
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
}.stage-empty strong{ font-size: 14px; }.stage-empty p{ margin: 0; font-size: 12px; color: var(--on-surface-muted); line-height: 1.5; max-width: 320px; }.stage-empty .n-button{ margin-top: 8px; }.stage-alert{
  flex: 0 0 auto;
  padding: 11px 13px;
  border-radius: 12px;
  font-size: 12px;
  line-height: 1.5;
  color: color-mix(in srgb, var(--warning) 78%, var(--on-surface));
  background: color-mix(in srgb, var(--warning) 12%, var(--surface-2));
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--warning) 34%, transparent);
}.stage-head__queue{
  flex: 0 0 auto;
}.stage-head__controls{
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  margin-left: auto;
}.stage-head__queue-count{
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
}.stage-head__queue-count--active{
  color: var(--primary-ink);
  background: var(--primary-soft);
}</style>
