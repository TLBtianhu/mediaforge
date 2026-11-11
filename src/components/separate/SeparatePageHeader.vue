<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { MusicalNotesOutline } from '@vicons/ionicons5'
import MediaPageHeader from '@/components/media/MediaPageHeader.vue'

const emit = defineEmits<{ openWorkflows: []; openModels: [] }>()
const { t } = useI18n()
</script>

<template>
  <MediaPageHeader
    :title="t('separate.title')"
    :subtitle="t('separate.subtitle')"
    :icon="MusicalNotesOutline"
    eyebrow="AUDIO WORKSPACE"
    tone="blue"
  >
    <div class="console-topbar__controls" data-guide="separation-modes">
      <n-tag size="small" :bordered="false">{{ t('separate.runModeModel') }}</n-tag>
      <n-button text class="console-topbar__manage" @click="emit('openWorkflows')">
        {{ t('separate.manageWorkflowsInline') }}
      </n-button>
      <n-button text class="console-topbar__manage" @click="emit('openModels')">
        {{ t('separate.manageModelsInline') }}
      </n-button>
    </div>
  </MediaPageHeader>
</template>

<style scoped>
.console-topbar__brand :deep(.app-brand-mark)
{
  transform-origin: center center;
}
.console-topbar__controls
{
  display: flex;
  align-items: center;
  gap: 14px;
  min-width: 0;
}
.console-topbar__manage
{
  color: color-mix(in srgb, var(--on-surface-muted) 84%, var(--on-surface));
  white-space: nowrap;
  font-size: 12px;
}
</style>
