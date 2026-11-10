<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { formatBytes } from '@/utils/format'
import { isTutorialFrame } from '@/features/onboarding/demoContext'
import type { StorageListItem } from '@/views/models/storage'
import {
  CubeOutline,
  FolderOpenOutline,
  RefreshOutline,
  SearchOutline,
} from '@vicons/ionicons5'

type BatchDeleteState = { active: boolean; totalModels: number; completedModels: number; currentModel: string }
type ResidualCleanupState = { active: boolean; completedFiles: number; totalFiles: number; progress: number }
type StorageSummary = { totalBytes: number; downloadedCount: number; residualBytes: number; modelDir: string; residualFiles: unknown[] }

const show = defineModel<boolean>('show', { required: true })
const search = defineModel<string>('search', { required: true })
const sort = defineModel<string>('sort', { required: true })
const downloadedOnly = defineModel<boolean>('downloadedOnly', { required: true })

defineProps<{
  summary: StorageSummary | null
  modelDir: string
  settingsModelDir: string
  loading: boolean
  models: StorageListItem[]
  selected: string[]
  selectedBytes: number
  hasDeletingSelected: boolean
  batchDeleteState: BatchDeleteState
  residualCleanupState: ResidualCleanupState
  deleteTasks: Record<string, { completedFiles?: number; totalFiles?: number } | undefined>
  batchDeleteProgress: number
  storageModelTypeLabel: (item: StorageListItem) => string
  isDeletingModel: (modelName: string) => boolean
  setStorageSelected: (key: string, checked: boolean) => void
}>()

const emit = defineEmits<{
  refresh: []
  openModelDir: []
  batchDelete: []
  cleanupResidual: []
}>()

const { t } = useI18n()
</script>

<template>
  <n-drawer v-model:show="show" :width="760" placement="right" :z-index="isTutorialFrame() ? 3500 : undefined" :trap-focus="!isTutorialFrame()">
    <n-drawer-content :title="t('models.storageTitle')" closable>
      <div class="storage-panel">
        <div class="storage-summary">
          <div class="storage-stat">
            <span>{{ t('models.storageTotal') }}</span>
            <strong>{{ formatBytes(summary?.totalBytes || 0) }}</strong>
          </div>
          <div class="storage-stat">
            <span>{{ t('models.storageDownloadedCount') }}</span>
            <strong>{{ summary?.downloadedCount || 0 }}</strong>
          </div>
          <div class="storage-stat">
            <span>{{ t('models.storageResidual') }}</span>
            <strong>{{ formatBytes(summary?.residualBytes || 0) }}</strong>
          </div>
        </div>

        <div class="storage-dir">
          <span class="text-muted">{{ t('models.modelDir') }}:</span>
          <code>{{ summary?.modelDir || modelDir || settingsModelDir || '?' }}</code>
        </div>

        <div class="storage-toolbar">
          <n-input v-model:value="search" :placeholder="t('models.storageSearch')" clearable>
            <template #prefix><n-icon :component="SearchOutline" /></template>
          </n-input>
          <n-select
            v-model:value="sort"
            :options="[
              { label: t('models.storageSortSizeDesc'), value: 'size-desc' },
              { label: t('models.storageSortSizeAsc'), value: 'size-asc' },
              { label: t('models.storageSortNameAsc'), value: 'name-asc' },
              { label: t('models.storageSortNameDesc'), value: 'name-desc' },
            ]"
            style="width:160px"
          />
          <n-switch v-model:value="downloadedOnly" size="small" />
          <span class="text-sm text-muted">{{ t('models.downloadedOnly') }}</span>
        </div>

        <div class="storage-actions">
          <n-button size="small" secondary :loading="loading" @click="emit('refresh')">
            <template #icon><n-icon :component="RefreshOutline" /></template>
            {{ t('models.storageRefresh') }}
          </n-button>
          <n-button size="small" secondary @click="emit('openModelDir')">
            <template #icon><n-icon :component="FolderOpenOutline" /></template>
            {{ t('models.openModelDir') }}
          </n-button>
          <n-button
            size="small"
            type="error"
            secondary
            :disabled="!selected.length || hasDeletingSelected || batchDeleteState.active || residualCleanupState.active"
            @click="emit('batchDelete')"
          >
            {{ t('models.storageBatchDelete') }}
            <span v-if="selected.length">（{{ selected.length }} / {{ formatBytes(selectedBytes) }}）</span>
          </n-button>
          <n-button
            size="small"
            type="warning"
            secondary
            :disabled="!(summary?.residualFiles.length) || batchDeleteState.active || residualCleanupState.active"
            @click="emit('cleanupResidual')"
          >
            {{ t('models.storageCleanup') }}
          </n-button>
        </div>

        <div v-if="batchDeleteState.active" class="storage-progress-card">
          <strong>{{ t('models.batchDeleting', { count: batchDeleteState.totalModels }) }}</strong>
          <span class="text-muted">{{ batchDeleteState.completedModels }} / {{ batchDeleteState.totalModels }}</span>
          <div class="text-sm text-muted">{{ batchDeleteState.currentModel }}</div>
          <div v-if="batchDeleteState.currentModel && deleteTasks[batchDeleteState.currentModel]" class="mc-dl-files">
            {{ t('models.deleteProgress', { completed: deleteTasks[batchDeleteState.currentModel]?.completedFiles || 0, total: deleteTasks[batchDeleteState.currentModel]?.totalFiles || 0 }) }}
          </div>
          <n-progress
            :percentage="batchDeleteProgress"
            :show-indicator="false"
            :height="8"
            :border-radius="4"
            type="line"
            :rail-color="'var(--surface-3)'"
          />
        </div>

        <div v-if="residualCleanupState.active" class="storage-progress-card">
          <strong>{{ t('models.cleanupInProgress') }}</strong>
          <span class="text-muted">{{ t('models.deleteProgress', { completed: residualCleanupState.completedFiles, total: residualCleanupState.totalFiles }) }}</span>
          <n-progress
            :percentage="residualCleanupState.progress"
            :show-indicator="false"
            :height="8"
            :border-radius="4"
            type="line"
            :rail-color="'var(--surface-3)'"
          />
        </div>

        <div class="storage-list">
          <label
            v-for="item in models"
            :key="item.selectionKey"
            class="storage-row"
          >
            <n-checkbox
              :checked="selected.includes(item.selectionKey)"
              :disabled="item.sizeBytes <= 0 || (item.kind === 'catalog' && isDeletingModel(item.id)) || batchDeleteState.active || residualCleanupState.active"
              @update:checked="(checked: boolean) => setStorageSelected(item.selectionKey, checked)"
            />
            <div class="storage-row__main">
              <strong>{{ item.name }}</strong>
              <span>{{ t('models.storageFileCount', { count: item.fileCount }) }}</span>
            </div>
            <n-tag size="small" :bordered="false" :type="item.kind === 'tool' ? 'info' : item.downloaded ? 'success' : 'default'">
              {{ storageModelTypeLabel(item) }}
            </n-tag>
            <strong class="storage-row__size">{{ formatBytes(item.sizeBytes || item.expectedSizeBytes) }}</strong>
          </label>
          <div v-if="!models.length" class="empty-state storage-empty">
            <n-icon :component="CubeOutline" size="36" color="var(--on-surface-muted)" />
            <span class="text-muted">{{ t('models.searchEmpty') }}</span>
          </div>
        </div>
      </div>
    </n-drawer-content>
  </n-drawer>
</template>

<style scoped>
.storage-panel {
  display: grid;
  gap: 14px;
}

.storage-summary {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 10px;
}

.storage-stat {
  padding: 14px;
  border: 1px solid var(--outline);
  border-radius: 12px;
  background: var(--surface-1);
}

.storage-stat span {
  display: block;
  font-size: 12px;
  color: var(--on-surface-muted);
}

.storage-stat strong {
  display: block;
  margin-top: 6px;
  font-size: 18px;
}

.storage-dir {
  display: flex;
  gap: 8px;
  align-items: center;
  min-width: 0;
  font-size: 12px;
}

.storage-dir code {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--on-surface-muted);
}

.storage-toolbar,
.storage-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.storage-toolbar .n-input {
  flex: 1;
  min-width: 220px;
}

.storage-list {
  display: grid;
  gap: 8px;
  max-height: 56vh;
  overflow: auto;
  padding-right: 4px;
}

.storage-row {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto auto;
  align-items: center;
  gap: 12px;
  padding: 12px;
  border-radius: 12px;
  border: 1px solid var(--outline);
  background: var(--surface-1);
}

.storage-row__main {
  min-width: 0;
}

.storage-row__main strong,
.storage-row__main span {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.storage-row__main span {
  margin-top: 4px;
  font-size: 12px;
  color: var(--on-surface-muted);
}

.storage-row__size {
  font-size: 13px;
  white-space: nowrap;
}

.storage-empty {
  padding: 32px 0;
}

.storage-progress-card {
  display: grid;
  gap: 8px;
  padding: 12px;
  border-radius: 12px;
  border: 1px solid var(--outline);
  background: var(--surface-1);
}
</style>
