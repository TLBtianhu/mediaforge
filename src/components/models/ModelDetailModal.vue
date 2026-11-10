<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { formatBytes } from '@/utils/format'
import ModelProgressBlock from '@/components/ModelProgressBlock.vue'
import type { ModelEntry } from '@/stores/model'
import {
  CheckmarkCircleOutline,
  CloudDownloadOutline,
  Star,
  StarOutline,
  TrashOutline,
} from '@vicons/ionicons5'

type DownloadTask = { status: string; progress: number; message?: string; completedFiles: number; totalFiles: number }
type DetailContext = {
  isFavorite: (model: ModelEntry) => boolean
  statusType: (model: ModelEntry) => string
  statusLabel: (model: ModelEntry) => string
  targetStemLabel: (model: ModelEntry) => string
  categoryLabel: (model: ModelEntry) => string
  isPartiallyAvailable: (model: ModelEntry) => boolean
  isDeletingModel: (name: string) => boolean
  deleteMessage: (name: string) => string
  deleteTask: (name: string) => DownloadTask | undefined
  downloadMessage: (name: string) => string
  downloadTask: (name: string) => DownloadTask | undefined
  downloadColor: (status: string) => string
  isModelBusy: (model: ModelEntry) => boolean
  toggleFavorite: (model: ModelEntry, event: MouseEvent) => void
  downloadModel: (model: ModelEntry, event?: MouseEvent) => void
  cancelDownload: (model: ModelEntry, event?: MouseEvent) => void
  confirmDelete: (model: ModelEntry) => void
}

const show = defineModel<boolean>('show', { required: true })
defineProps<{ model: ModelEntry | null; detailLoading: boolean; context: DetailContext }>()
defineEmits<{ afterLeave: [] }>()

const { t } = useI18n()
</script>

<template>
  <n-modal :show="show" @update:show="show = $event" @after-leave="$emit('afterLeave')">
    <n-card
      class="model-detail-modal"
      :title="t('models.detail')"
      :bordered="false"
      closable
      role="dialog"
      aria-modal="true"
      @close="show = false"
    >
      <template v-if="model">
        <div class="model-detail-modal__body">
          <div class="detail-content">
            <div class="detail-title-row">
              <strong class="detail-name">{{ model.name }}</strong>
              <n-button
                secondary
                size="small"
                :type="context.isFavorite(model) ? 'warning' : 'default'"
                @click="context.toggleFavorite(model, $event)"
              >
                <template #icon>
                  <n-icon :component="context.isFavorite(model) ? Star : StarOutline" />
                </template>
                {{ context.isFavorite(model) ? t('models.favorited') : t('models.favorite') }}
              </n-button>
            </div>

            <div class="detail-badges">
              <n-tag v-if="detailLoading" :bordered="false" size="small" type="info" round>
                {{ t('common.refreshing') }}
              </n-tag>
              <n-tag v-if="model.architecture" :bordered="false" size="small" type="info" round>
                {{ model.architecture }}
              </n-tag>
              <n-tag :bordered="false" size="small" :type="context.statusType(model)" round>
                {{ context.statusLabel(model) }}
              </n-tag>
            </div>

            <n-divider style="margin:16px 0" />

            <div class="detail-grid">
              <div class="detail-row">
                <span class="detail-label">{{ t('models.type') }}</span>
                <span class="detail-val">{{ model.modelType || '—' }}</span>
              </div>
              <div class="detail-row">
                <span class="detail-label">{{ t('models.targetStem') }}</span>
                <span class="detail-val">{{ context.targetStemLabel(model) || '—' }}</span>
              </div>
              <div class="detail-row">
                <span class="detail-label">{{ t('models.size') }}</span>
                <span class="detail-val">{{ formatBytes(model.sizeBytes) }}</span>
              </div>
              <div class="detail-row">
                <span class="detail-label">{{ t('models.category') }}</span>
                <span class="detail-val">{{ context.categoryLabel(model) || '—' }}</span>
              </div>
              <div v-if="model.aliases?.length" class="detail-row">
                <span class="detail-label">{{ t('models.aliases') }}</span>
                <span class="detail-val">{{ model.aliases.join(', ') }}</span>
              </div>
            </div>

            <n-divider style="margin:16px 0" />

            <n-collapse class="detail-path-collapse" :default-expanded-names="[]">
              <n-collapse-item :title="t('models.path')" name="model-path">
                <div class="detail-path-block">
                  <code class="detail-path">{{ model.modelPath }}</code>
                </div>
                <div v-if="context.isPartiallyAvailable(model) && model.missingPaths?.length" class="detail-partial-block">
                  <div class="detail-label" style="margin-bottom:6px">{{ t('models.partialHintTitle') }}</div>
                  <div class="text-muted">{{ t('models.partialHint') }}</div>
                  <ul class="detail-missing-list">
                    <li v-for="path in model.missingPaths" :key="path">
                      <code class="detail-path">{{ path }}</code>
                    </li>
                  </ul>
                </div>
              </n-collapse-item>
            </n-collapse>
          </div>
        </div>
      </template>

      <template #footer>
        <div v-if="model" class="drawer-footer">
          <ModelProgressBlock
            v-if="context.isDeletingModel(model.name)"
            status="downloading"
            :message="context.deleteMessage(model.name) || t('models.deleting')"
            :progress="context.deleteTask(model.name)?.progress || 0"
            :files-text="t('models.deleteProgress', { completed: context.deleteTask(model.name)?.completedFiles || 0, total: context.deleteTask(model.name)?.totalFiles || 0 })"
          />

          <ModelProgressBlock
            v-else-if="['preparing', 'downloading'].includes(context.downloadTask(model.name)?.status || '')"
            :status="context.downloadTask(model.name)!.status"
            :message="context.downloadMessage(model.name)"
            :progress="context.downloadTask(model.name)!.progress"
            :files-text="context.downloadTask(model.name)!.totalFiles > 1 ? t('models.fileProgress', { completed: context.downloadTask(model.name)!.completedFiles, total: context.downloadTask(model.name)!.totalFiles }) : ''"
            :color="context.downloadColor(context.downloadTask(model.name)!.status)"
          >
            <template #actions>
              <n-button block secondary @click="context.cancelDownload(model, $event)">
                {{ t('common.cancel') }}
              </n-button>
            </template>
          </ModelProgressBlock>

          <template v-else-if="!model.downloaded && context.downloadTask(model.name) && ['paused','cancelled','error','interrupted'].includes(context.downloadTask(model.name)!.status)">
            <ModelProgressBlock
              :status="context.downloadTask(model.name)!.status"
              :message="context.downloadMessage(model.name)"
              :progress="context.downloadTask(model.name)!.progress"
              :files-text="context.downloadTask(model.name)!.totalFiles > 1 ? t('models.fileProgress', { completed: context.downloadTask(model.name)!.completedFiles, total: context.downloadTask(model.name)!.totalFiles }) : ''"
              :color="context.downloadColor(context.downloadTask(model.name)!.status)"
            />
            <div class="drawer-footer-actions">
              <n-button
                block
                type="primary"
                :disabled="context.isModelBusy(model)"
                @click="context.downloadModel(model, $event)"
              >
                {{ context.downloadTask(model.name)?.status === 'interrupted' ? t('models.continueDownload') : t('common.resume') }}
              </n-button>
              <n-button block type="error" secondary @click="context.confirmDelete(model)">
                <template #icon><n-icon :component="TrashOutline" /></template>
                {{ t('models.delete') }}
              </n-button>
            </div>
          </template>

          <template v-else-if="model.downloaded">
            <n-button block secondary disabled>
              <template #icon><n-icon :component="CheckmarkCircleOutline" /></template>
              {{ t('models.downloaded') }}
            </n-button>
            <n-button block type="error" secondary @click="context.confirmDelete(model)">
              <template #icon><n-icon :component="TrashOutline" /></template>
              {{ t('models.delete') }}
            </n-button>
          </template>

          <n-button
            v-else-if="model.supported && !model.downloaded"
            block
            type="primary"
            @click="context.downloadModel(model, $event)"
          >
            <template #icon><n-icon :component="CloudDownloadOutline" /></template>
            {{ t('common.download') }}
          </n-button>
        </div>
      </template>
    </n-card>
  </n-modal>
</template>

<style scoped>
.model-detail-modal {
  width: var(--modal-width-sm);
  height: min(620px, var(--modal-max-height));
  max-height: var(--modal-max-height);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border-radius: 20px;
}

.model-detail-modal :deep(.n-card-header) {
  flex: 0 0 auto;
  padding: 18px 22px 14px;
  border-bottom: 1px solid color-mix(in srgb, var(--outline) 72%, transparent);
}

.model-detail-modal :deep(.n-card-content),
.model-detail-modal :deep(.n-card__content) {
  flex: 1 1 auto;
  min-height: 0;
  overflow: hidden;
  padding: 0;
}

.model-detail-modal__body {
  height: 100%;
  min-height: 0;
  overflow-y: auto;
  overscroll-behavior: contain;
  padding: 16px 20px;
  box-sizing: border-box;
}

.model-detail-modal :deep(.n-card-footer),
.model-detail-modal :deep(.n-card__footer) {
  flex: 0 0 auto;
  padding: 12px 20px 16px;
  border-top: 1px solid color-mix(in srgb, var(--outline) 72%, transparent);
  background: color-mix(in srgb, var(--surface-1) 96%, transparent);
}

.drawer-footer {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.model-detail-modal .drawer-footer {
  flex-direction: row;
  justify-content: flex-end;
  flex-wrap: wrap;
}

.model-detail-modal .drawer-footer :deep(.n-button) {
  width: auto;
  min-width: 112px;
}

.model-detail-modal .drawer-footer > .mc-dl {
  flex: 1 1 100%;
  width: 100%;
}

.drawer-footer-actions {
  display: flex;
  gap: 8px;
  flex: 1 1 100%;
  justify-content: flex-end;
  flex-wrap: wrap;
}

.model-detail-modal .drawer-footer-actions :deep(.n-button) {
  flex: 1 1 auto;
}

.detail-content {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.detail-title-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.detail-name {
  font-size: 16px;
  font-weight: 600;
  word-break: break-word;
  line-height: 1.4;
}

.detail-badges {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}

.detail-grid {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.detail-row {
  display: flex;
  justify-content: space-between;
  font-size: 13px;
  gap: 12px;
}

.detail-label {
  color: var(--on-surface-muted);
  flex-shrink: 0;
}

.detail-val {
  text-align: right;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.detail-path {
  font-size: 11px;
  word-break: break-all;
  color: var(--on-surface-muted);
  background: var(--surface-2);
  padding: 8px 10px;
  border-radius: 8px;
  display: block;
}

.detail-path-collapse {
  margin-top: -2px;
}

.detail-path-collapse :deep(.n-collapse-item__header) {
  padding: 0;
  color: var(--on-surface-muted);
  font-size: 13px;
  font-weight: 600;
}

.detail-path-collapse :deep(.n-collapse-item__content-inner) {
  padding-top: 10px;
}

.detail-path-block,
.detail-partial-block {
  display: grid;
  gap: 8px;
}

.detail-partial-block {
  margin-top: 12px;
}
</style>
