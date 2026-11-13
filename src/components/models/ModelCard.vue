<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import type { ModelEntry } from '@/stores/model'
import {
  CheckmarkCircleOutline,
  DownloadOutline,
  EllipsisHorizontalOutline,
  LinkOutline,
  Star,
  StarOutline,
} from '@vicons/ionicons5'

type DownloadTask = { status: string; progress: number }
type DeleteTask = { progress?: number }
type CardContext = {
  modelNameParts: (name: string) => string[]
  modelNote: (model: ModelEntry) => string
  modelUseCount: (model: ModelEntry) => number
  isFavorite: (model: ModelEntry) => boolean
  toggleFavorite: (model: ModelEntry, event: MouseEvent) => void
  selectModel: (model: ModelEntry) => void
  openContextMenu: (event: MouseEvent, model: ModelEntry) => void
  showModelTypeTag: (model: ModelEntry) => boolean
  targetStemLabel: (model: ModelEntry) => string
  categoryLabel: (model: ModelEntry) => string
  sizeLabel: (model: ModelEntry) => string
  isDeletingModel: (name: string) => boolean
  deleteMessage: (name: string) => string
  deleteTask: (name: string) => DeleteTask | undefined
  isCustomModel: (model: ModelEntry) => boolean
  relinkCustom: (model: ModelEntry, event?: MouseEvent) => void
  downloadTask: (name: string) => DownloadTask | undefined
  cardDownloadMessage: (name: string) => string
  downloadSpeed: (name: string) => string
  openDownloadDetail: (name: string) => void
  cancelDownload: (model: ModelEntry, event?: MouseEvent) => void
  downloadModel: (model: ModelEntry, event?: MouseEvent) => void
  isModelBusy: (model: ModelEntry) => boolean
  cardProgressPercent: (model: ModelEntry) => number | null
  cardProgressState: (model: ModelEntry) => string
}

defineProps<{ model: ModelEntry; selected: boolean; context: CardContext }>()
const { t } = useI18n()
</script>

<template>
  <div
    :data-guide="model.supported ? 'model-library' : undefined"
    :class="['model-card', {
      'model-card--selected': selected,
      'model-card--unsupported': !model.supported,
      'model-card--busy': context.isModelBusy(model)
    }]"
    tabindex="0"
    :aria-label="t('models.viewDetailAria', { name: model.name })"
    :aria-current="selected ? 'true' : undefined"
    @click="context.selectModel(model)"
    @contextmenu.stop.prevent="context.openContextMenu($event, model)"
    @keydown.enter.self.prevent="context.selectModel(model)"
    @keydown.space.self.prevent="context.selectModel(model)"
  >
    <!-- Card Header -->
    <div class="mc-header">
      <div class="mc-title">
        <span class="mc-name" :title="model.name"><template v-for="(part, index) in context.modelNameParts(model.name)" :key="index">{{ part }}<wbr /></template></span>
        <span v-if="context.modelNote(model)" class="mc-note" :title="context.modelNote(model)">{{ context.modelNote(model) }}</span>
      </div>
      <div class="mc-controls">
        <n-button
          quaternary
          circle
          size="tiny"
          :type="context.isFavorite(model) ? 'warning' : 'default'"
          :title="context.isFavorite(model) ? t('models.unfavorite') : t('models.favorite')"
          :aria-label="context.isFavorite(model) ? t('models.unfavorite') : t('models.favorite')"
          class="mc-favorite"
          @click.stop="context.toggleFavorite(model, $event)"
        >
          <template #icon>
            <n-icon :component="context.isFavorite(model) ? Star : StarOutline" />
          </template>
        </n-button>
        <n-button
          quaternary
          circle
          size="tiny"
          class="mc-more"
          :title="t('models.moreActions')"
          :aria-label="t('models.moreActions')"
          @click.stop="context.openContextMenu($event, model)"
        >
          <template #icon><n-icon :component="EllipsisHorizontalOutline" /></template>
        </n-button>
      </div>
    </div>

    <!-- Meta Tags -->
    <div class="mc-tags">
      <n-tag v-if="model.architecture" :bordered="false" size="tiny" type="info" round :title="model.architecture">
        {{ model.architecture }}
      </n-tag>
      <n-tag v-if="context.showModelTypeTag(model)" :bordered="false" size="tiny" round :title="model.modelType || undefined">
        {{ model.modelType }}
      </n-tag>
    </div>

    <!-- Stable field positions keep models comparable across each card row. -->
    <div class="mc-meta">
      <div class="mc-meta-item mc-meta-item--target">
        <span class="mc-label">{{ t('models.targetStem') }}</span>
        <span class="mc-value" :title="context.targetStemLabel(model)">{{ context.targetStemLabel(model) || '—' }}</span>
      </div>
      <div class="mc-meta-item mc-meta-item--category">
        <span class="mc-label">{{ t('models.category') }}</span>
        <span class="mc-value" :title="context.categoryLabel(model) || undefined">{{ context.categoryLabel(model) || '—' }}</span>
      </div>
      <div class="mc-meta-item mc-meta-item--usage">
        <span class="mc-label">{{ t('models.useCountLabel') }}</span>
        <span class="mc-value">{{ t('models.useCountValue', { count: context.modelUseCount(model) }) }}</span>
      </div>
    </div>

    <!-- Bottom bar: size on the left, whatever the model needs on the right. -->
    <div class="mc-bottom">
      <span class="mc-size">{{ context.sizeLabel(model) }}</span>

      <template v-if="context.isDeletingModel(model.name)">
        <span class="mc-state mc-state--downloading">
          <span class="mc-state__dot" />
          {{ context.deleteMessage(model.name) || t('models.deleting') }}
        </span>
        <span class="mc-pct">{{ context.deleteTask(model.name)?.progress || 0 }}%</span>
      </template>

      <!-- Imported models are local-only: a missing file means "relink", not "download". -->
      <template v-else-if="context.isCustomModel(model)">
        <span :class="['mc-state', model.downloaded ? 'mc-state--ok' : 'mc-state--warn']">
          <n-icon :component="model.downloaded ? CheckmarkCircleOutline : LinkOutline" />
          {{ model.downloaded ? t('models.customReady') : t('models.customFilesMissing') }}
        </span>
        <div class="mc-actions">
          <n-button size="tiny" quaternary @click.stop="context.relinkCustom(model, $event)">
            <template #icon><n-icon :component="LinkOutline" /></template>
          </n-button>
        </div>
      </template>

      <template v-else-if="!model.downloaded && context.downloadTask(model.name) && context.downloadTask(model.name)!.status !== 'done'">
        <span :class="['mc-state', `mc-state--${context.downloadTask(model.name)!.status}`]">
          <span class="mc-state__dot" />
          {{ context.cardDownloadMessage(model.name) }}
        </span>
        <span v-if="context.downloadSpeed(model.name)" class="mc-speed">{{ context.downloadSpeed(model.name) }}</span>
        <span class="mc-pct">{{ context.downloadTask(model.name)!.progress }}%</span>
        <div class="mc-actions">
          <n-button size="tiny" quaternary @click.stop="context.openDownloadDetail(model.name)">
            {{ t('models.downloadDetail') }}
          </n-button>
          <n-button
            v-if="['preparing', 'downloading'].includes(context.downloadTask(model.name)!.status)"
            size="tiny"
            quaternary
            @click.stop="context.cancelDownload(model, $event)"
          >
            {{ t('common.cancel') }}
          </n-button>
          <n-button
            v-else-if="context.downloadTask(model.name)!.status !== 'preparing'"
            size="tiny"
            quaternary
            type="primary"
            @click.stop="context.downloadModel(model, $event)"
          >
            {{ context.downloadTask(model.name)?.status === 'interrupted' ? t('models.continueDownload') : t('common.resume') }}
          </n-button>
        </div>
      </template>

      <template v-else-if="model.downloaded">
        <span class="mc-state mc-state--ok">
          <n-icon :component="CheckmarkCircleOutline" />
          {{ t('models.downloaded') }}
        </span>
      </template>

      <n-button
        v-else-if="model.supported"
        class="mc-download-button"
        secondary
        size="tiny"
        :disabled="context.isModelBusy(model)"
        @click.stop="context.downloadModel(model, $event)"
      >
        <template #icon><n-icon :component="DownloadOutline" /></template>
        {{ t('common.download') }}
      </n-button>
    </div>

    <!-- Progress as the card's bottom edge: findable at a glance across the grid. -->
    <div
      v-if="context.cardProgressPercent(model) !== null"
      class="mc-progressbar"
      :style="{ '--mc-progress': context.cardProgressPercent(model) + '%' }"
      :data-state="context.cardProgressState(model)"
    />
  </div>
</template>

<style scoped>
.model-card {
  cursor: pointer;
  min-height: 0;
  min-width: 0;
  padding: 14px 16px;
  border-radius: 12px;
  border: 1px solid color-mix(in srgb, var(--outline) 58%, transparent);
  background:
    linear-gradient(180deg, color-mix(in srgb, white 1.8%, transparent), transparent 54%),
    color-mix(in srgb, var(--surface-1) 64%, transparent);
  transition: box-shadow 0.15s ease, border-color 0.15s ease, background 0.15s ease;
  position: relative;
  overflow: hidden;
}

/* ---- Card: a stack that ends in a bottom bar ---- */
.model-grid--card .model-card {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 10px;
  /* Room for the progress edge so it never sits under the action row. */
  padding-bottom: 15px;
}

.model-grid--card .mc-bottom {
  margin-top: auto;
  min-height: 43px;
  padding-top: 10px;
  border-top: 1px solid color-mix(in srgb, var(--outline) 38%, transparent);
}

@supports (grid-template-rows: subgrid) {
  .model-grid--card .model-card {
    display: grid;
    grid-template-rows: subgrid;
    grid-row: span 4;
  }
}
.model-grid--card .mc-tags { min-height: 20px; flex-wrap: nowrap; }
.model-grid--card .mc-meta {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  grid-template-areas: "target target" "category usage";
  gap: 6px 12px;
}
.model-grid--card .mc-meta-item--target { grid-area: target; }
.model-grid--card .mc-meta-item--category { grid-area: category; }
.model-grid--card .mc-meta-item--usage { grid-area: usage; }
.model-grid--card .mc-meta-item:not(.mc-meta-item--usage) {
  display: grid;
  grid-template-columns: minmax(4em, max-content) minmax(0, 1fr);
}
.mc-meta-item--usage { color: var(--on-surface-muted); font-variant-numeric: tabular-nums; white-space: nowrap; }

/* ---- List: the original two-line row — the name over its details, with tags and actions
   riding on the right. Spreading the same content across one flex line left large gaps between
   the pieces and made long lists harder to scan. ---- */
.model-grid--list .model-card {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(112px, 160px) minmax(230px, 280px) auto;
  grid-template-areas:
    "header tags bottom controls"
    "meta   tags bottom controls";
  align-items: center;
  gap: 8px 14px;
  padding: 14px 16px 17px;
}

.model-grid--list .mc-header { display: contents; }
.model-grid--list .mc-title { grid-area: header; }
.model-grid--list .mc-controls { grid-area: controls; }
.model-grid--list .mc-meta { grid-area: meta; min-width: 0; }
.model-grid--list .mc-tags { grid-area: tags; }
.model-grid--list .mc-bottom { grid-area: bottom; justify-content: flex-end; gap: 12px; }

/* Stack metadata in narrow windows while keeping controls beside the title. */
@container model-library (max-width: 760px) {
  .model-grid--list .model-card {
    grid-template-columns: minmax(0, 1fr) auto;
    grid-template-areas:
      "header controls"
      "tags tags"
      "meta meta"
      "bottom bottom";
  }

  .model-grid--list .mc-controls { align-self: start; }

  .model-grid--list .mc-bottom {
    justify-content: space-between;
    padding-top: 10px;
    border-top: 1px solid color-mix(in srgb, var(--outline) 38%, transparent);
  }
}

@container (max-width: 350px) {
  .model-grid--card .mc-speed { display: none; }
  .model-grid--card .model-card--busy .mc-size { display: none; }
}

/* A running download is the one thing on the page that changes by itself; a tinted edge makes
   it findable without scanning every card. */
.model-card--busy {
  border-color: color-mix(in srgb, var(--primary) 45%, transparent);
  background:
    linear-gradient(180deg, color-mix(in srgb, var(--primary) 5%, transparent), transparent 46%),
    color-mix(in srgb, var(--surface-1) 72%, transparent);
}

/* ---- Action row: one line, identical height in every state ---- */
.mc-bottom {
  display: flex;
  container-type: inline-size;
  align-items: center;
  gap: 10px;
  min-width: 0;
  min-height: 30px;
}

.mc-size {
  font-size: 11px;
  color: var(--on-surface-muted);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  flex-shrink: 0;
}

/* Pushes whatever follows the size to the right edge. */
.mc-state,
.mc-download-button,
.mc-bottom > .n-button {
  margin-left: auto;
}

/* Once a state label is present it owns the middle, and the actions go right. */
.mc-state ~ .mc-pct,
.mc-state ~ .mc-actions,
.mc-state ~ .n-button {
  margin-left: 0;
}

.mc-state {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 11px;
  font-weight: 500;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--on-surface-muted);
}

.mc-state__dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex-shrink: 0;
  background: currentColor;
}

.mc-state--downloading {
  color: var(--primary);
}

.mc-state--preparing {
  color: var(--primary);
}

.mc-state--preparing .mc-state__dot,
.mc-state--downloading .mc-state__dot {
  animation: mc-pulse 1.5s infinite;
}

.mc-state--ok {
  color: var(--success);
}

.mc-state--warn,
.mc-state--paused,
.mc-state--cancelled,
.mc-state--interrupted {
  color: var(--warning);
}

.mc-state--error {
  color: var(--danger);
}

@keyframes mc-pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.35; }
}

.mc-speed {
  font-size: 11px;
  color: var(--on-surface-muted);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  flex-shrink: 0;
}

.mc-pct {
  font-size: 12px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: var(--on-surface);
  flex-shrink: 0;
}

.mc-actions {
  display: flex;
  align-items: center;
  gap: 2px;
  flex-shrink: 0;
}

/* ---- Progress drawn as the card's bottom edge ---- */
.mc-progressbar {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  height: 3px;
  border-radius: 0 0 12px 12px;
  overflow: hidden;
  background: color-mix(in srgb, var(--outline) 45%, transparent);
}

.mc-progressbar::after {
  content: '';
  position: absolute;
  inset: 0 auto 0 0;
  width: var(--mc-progress, 0%);
  background: var(--primary);
  transition: width 240ms ease;
}

.mc-progressbar[data-state='paused']::after,
.mc-progressbar[data-state='cancelled']::after,
.mc-progressbar[data-state='interrupted']::after {
  background: var(--warning);
}

.mc-progressbar[data-state='error']::after {
  background: var(--danger);
}

.model-card:hover {
  box-shadow: inset 0 1px 0 color-mix(in srgb, white 4.5%, transparent);
  border-color: color-mix(in srgb, var(--primary) 22%, var(--outline));
  background:
    linear-gradient(180deg, color-mix(in srgb, var(--primary-soft) 12%, transparent), transparent 64%),
    color-mix(in srgb, var(--surface-1) 78%, transparent);
}

.model-card:focus-visible {
  outline: 2px solid color-mix(in srgb, var(--primary) 70%, transparent);
  outline-offset: 2px;
  border-color: color-mix(in srgb, var(--primary) 40%, var(--outline));
}

.model-card--selected {
  border-color: color-mix(in srgb, var(--outline-strong) 72%, transparent) !important;
  background: color-mix(in srgb, var(--surface-2) 58%, var(--surface-1));
}

.model-card--unsupported {
  opacity: 0.55;
}

.dark-theme .model-card {
  background: var(--surface-1);
  border-color: var(--outline);
}
.dark-theme .model-card:hover {
  background: color-mix(in srgb, var(--surface-2) 50%, var(--surface-1));
  border-color: var(--outline-strong);
  box-shadow: none;
}
.dark-theme .model-card--selected {
  border-color: var(--primary-border) !important;
  background: color-mix(in srgb, var(--primary-soft) 45%, var(--surface-1));
}
.dark-theme .model-card--busy {
  border-color: var(--primary-border);
  background: color-mix(in srgb, var(--primary-softer) 45%, var(--surface-1));
}
.dark-theme .mc-name { color: var(--on-surface-heading); }

/* Card header */
.mc-header {
  display: flex;
  align-items: flex-start;
  gap: 4px;
  min-width: 0;
}

.mc-title {
  display: grid;
  gap: 3px;
  flex: 1;
  min-width: 0;
}

.mc-controls {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}

.mc-name {
  display: -webkit-box;
  font-size: 13px;
  font-weight: 600;
  min-width: 0;
  max-width: 100%;
  line-height: 1.5;
  overflow: hidden;
  overflow-wrap: anywhere;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  line-clamp: 2;
}

.mc-note {
  display: -webkit-box;
  max-width: 100%;
  color: var(--on-surface-muted);
  font-size: 11px;
  line-height: 1.35;
  overflow: hidden;
  overflow-wrap: anywhere;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 1;
  line-clamp: 1;
}

.mc-favorite {
  flex: 0 0 auto;
  opacity: 0.62;
}

.mc-more {
  flex: 0 0 auto;
  opacity: 0.62;
}

.model-card:hover .mc-favorite,
.model-card--selected .mc-favorite,
.model-card:hover .mc-more,
.model-card--selected .mc-more {
  opacity: 1;
}

/* Tags row */
.mc-tags {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  justify-content: flex-start;
  gap: 5px;
  min-width: 0;
}

.mc-tags :deep(.n-tag) {
  max-width: 100%;
  min-width: 0;
  flex-shrink: 1;
}

.mc-tags :deep(.n-tag__content) {
  overflow: hidden;
  text-overflow: ellipsis;
}

.mc-size {
  font-size: 11px;
  color: var(--on-surface-muted);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  flex-shrink: 0;
}

/* Meta info */
.mc-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  /* Wraps instead of stretching: pinning the value to the far edge left a wide gap between a
     short label and a short value, which is what made the card look hollow. */
  gap: 4px 14px;
  font-size: 11px;
  min-width: 0;
}

.mc-meta-item {
  display: flex;
  gap: 6px;
  min-width: 0;
}

.mc-label {
  color: var(--on-surface-muted);
  flex-shrink: 0;
  white-space: nowrap;
}

.mc-value {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mc-download-button {
  min-width: 72px;
  --n-height: 30px;
  --n-padding: 0 10px;
}

.mc-download-button :deep(.n-button__content) {
  font-size: 12.5px;
  font-weight: 600;
}

.mc-download-button :deep(.n-icon) {
  font-size: 17px;
}

/* Download progress */
/* 下载/删除进度块样式已迁移至 ModelProgressBlock 组件；
   此处仅保留存储抽屉批量删除卡片仍引用的文件进度文本样式 */

@media (max-width: 900px) {
  .mc-tags {
    justify-content: flex-start;
  }

  .mc-size {
    text-align: left;
  }

  .mc-meta {
    flex-wrap: wrap;
  }
}
</style>
