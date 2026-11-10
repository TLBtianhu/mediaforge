<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { FolderOpenOutline, SwapHorizontalOutline } from '@vicons/ionicons5'
import { dirName } from '@/views/settings/appearance'

type DataDirEntry = { key: string; value: string; fallback: string }

defineProps<{
  dataRoot: string
  modelDir: string
  outputDir: string
  dataDirEntries: DataDirEntry[]
  hasActiveModelDirUsage: boolean
  isModelDirMigrating: boolean
  isCheckingModelDir: boolean
}>()

const emit = defineEmits<{
  revealPath: [path: string]
  changeModelDir: []
  changeOutputDir: []
}>()

const { t } = useI18n()
</script>

<template>
  <section class="settings-section-panel">
    <n-card class="settings-card settings-card--feature settings-card--paths" :bordered="true" size="small">
      <template #header>
        <div class="section-title">
          <span class="section-title__icon">
            <n-icon :component="FolderOpenOutline" size="18" />
          </span>
          <span>{{ t('settings.dataDir') }}</span>
        </div>
      </template>

      <div class="path-panel">
        <div class="path-panel__intro">{{ t('settings.pathsHint') }}</div>

        <div class="path-root-block">
          <div class="path-root-row">
            <code class="path-root" :title="dataRoot || t('common.notSet')">{{ dataRoot || t('common.notSet') }}</code>
            <n-button secondary size="small" :disabled="!dataRoot" @click="emit('revealPath', dataRoot)">
              {{ t('common.open') }}
            </n-button>
          </div>
        </div>

        <div class="path-primary-grid" data-guide="settings-paths">
          <div class="path-item path-item--primary">
            <div class="path-item__head">
              <div class="path-item__head-copy">
                <strong>{{ t('settings.modelDir') }}</strong>
              </div>
              <div class="path-item__actions">
                <n-tag v-if="hasActiveModelDirUsage && !isModelDirMigrating" :bordered="false" size="small" type="warning">
                  {{ t('settings.modelDirInUse') }}
                </n-tag>
                <n-button
                  secondary
                  type="primary"
                  size="small"
                  :loading="isCheckingModelDir"
                  :disabled="hasActiveModelDirUsage || isCheckingModelDir"
                  @click="emit('changeModelDir')"
                >
                  <template #icon><n-icon :component="SwapHorizontalOutline" /></template>
                  {{ t('settings.changeModelDir') }}
                </n-button>
              </div>
            </div>
            <code class="path-item__value" :title="modelDir || t('common.notSet')">{{ modelDir || t('common.notSet') }}</code>
          </div>

          <div class="path-item path-item--primary">
            <div class="path-item__head">
              <div class="path-item__head-copy">
                <strong>{{ t('settings.outputDir') }}</strong>
              </div>
              <div class="path-item__actions">
                <n-button secondary type="primary" size="small" @click="emit('changeOutputDir')">
                  <template #icon><n-icon :component="FolderOpenOutline" /></template>
                  {{ t('settings.changeOutputDir') }}
                </n-button>
              </div>
            </div>
            <code class="path-item__value" :title="outputDir || t('common.notSet')">{{ outputDir || t('common.notSet') }}</code>
          </div>
        </div>

        <div class="path-grid">
          <div v-for="entry in dataDirEntries" :key="entry.key" class="path-subcard">
            <span>{{ t(entry.key) }}</span>
            <code class="path-field__value" :title="entry.value || entry.fallback">{{ dirName(entry.value, entry.fallback) }}</code>
          </div>
        </div>
      </div>
    </n-card>
  </section>
</template>

<style scoped src="../../views/settings/settings-shared.css"></style>
<style scoped>

.path-panel {
  position: relative;
  z-index: 1;
  display: grid;
  gap: 14px;
}

.path-panel__intro {
  margin: 0;
  color: var(--on-surface-muted);
  font-size: 13px;
  line-height: 1.6;
}

.path-root-block {
  display: grid;
  gap: 8px;
}

.path-root-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 10px;
  align-items: center;
}

.path-root {
  display: flex;
  align-items: center;
  min-height: 40px;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  padding: 0 14px;
  border: 1px solid color-mix(in srgb, var(--outline) 56%, transparent);
  border-radius: 13px;
  color: color-mix(in srgb, var(--on-surface) 92%, black 8%);
  background: color-mix(in srgb, var(--surface) 26%, transparent);
  font-family: "JetBrains Mono", "Cascadia Code", Consolas, "MiSans", "PingFang SC", "Microsoft YaHei", ui-monospace, monospace;
  font-size: 13px;
  line-height: 1.45;
  letter-spacing: 0.01em;
}

.path-grid {
  display: grid;
  gap: 10px;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  margin-top: 2px;
  padding-top: 12px;
  border-top: 1px solid color-mix(in srgb, var(--outline) 48%, transparent);
}

.path-primary-grid {
  display: grid;
  gap: 12px;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 320px), 1fr));
}

.path-item {
  display: grid;
  gap: 12px;
  padding: 14px;
  border-radius: 16px;
  border: 1px solid color-mix(in srgb, var(--outline) 56%, transparent);
  background: color-mix(in srgb, var(--surface) 24%, transparent);
}

.path-item--primary {
  position: relative;
  overflow: hidden;
  border-color: color-mix(in srgb, var(--primary-border) 34%, var(--outline));
  background:
    linear-gradient(180deg, color-mix(in srgb, var(--primary-soft) 10%, transparent), transparent 58%),
    color-mix(in srgb, var(--surface) 26%, transparent);
  box-shadow:
    inset 0 1px 0 color-mix(in srgb, white 3.5%, transparent);
}

.path-item--primary::before {
  display: none;
}

.path-item__head {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.path-item__head-copy {
  min-width: min(100%, 128px);
  flex: 1 1 128px;
  display: grid;
  gap: 4px;
}

.path-item__head-copy strong {
  font-size: 13px;
  overflow-wrap: normal;
  word-break: normal;
}

.path-item__actions {
  min-width: 0;
  flex: 0 1 auto;
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  justify-content: flex-end;
}

.path-item__actions :deep(.n-button) {
  max-width: 100%;
}

.path-item__value {
  display: block;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  padding: 11px 12px;
  border-radius: 12px;
  border: 1px solid color-mix(in srgb, var(--outline) 62%, transparent);
  background: color-mix(in srgb, var(--surface-2) 52%, var(--surface-1));
  color: color-mix(in srgb, var(--on-surface) 88%, var(--on-surface-muted));
  font-family: "JetBrains Mono", "Cascadia Code", Consolas, "MiSans", "PingFang SC", "Microsoft YaHei", ui-monospace, monospace;
  font-size: 12px;
}

.path-subcard {
  display: grid;
  gap: 4px;
  padding: 8px 10px;
  border-radius: 10px;
  border: 1px solid color-mix(in srgb, var(--outline) 34%, transparent);
  background: color-mix(in srgb, var(--surface-2) 18%, transparent);
}

.path-grid span {
  color: color-mix(in srgb, var(--on-surface-muted) 94%, var(--on-surface) 6%);
  font-size: 11px;
  font-weight: 600;
}

.path-field__value {
  display: block;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  padding: 6px 0 0;
  border: 0;
  border-radius: 0;
  color: color-mix(in srgb, var(--on-surface) 82%, var(--on-surface-muted));
  background: transparent;
  font-family: "JetBrains Mono", "Cascadia Code", Consolas, "MiSans", "PingFang SC", "Microsoft YaHei", ui-monospace, monospace;
  font-size: 12px;
  line-height: 1.45;
  letter-spacing: 0.01em;
}
</style>
