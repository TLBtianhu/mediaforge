<script setup lang="ts">
import { useI18n } from 'vue-i18n'

const props = defineProps<{
  currentTaskId: string
  cancellingTaskId: string | null
  beginNextSeparation: () => void
  handleCancelCurrentTask: () => void
}>()
const { t } = useI18n()
</script>

<template>
  <div class="stage-actions">
    <n-button secondary strong size="large" @click="props.beginNextSeparation">
      {{ t('separate.addNextBatch') }}
    </n-button>
    <n-button
      strong
      size="large"
      type="error"
      secondary
      class="stage-actions__primary"
      :loading="props.cancellingTaskId === props.currentTaskId || props.cancellingTaskId === 'batch'"
      :disabled="props.cancellingTaskId === props.currentTaskId || props.cancellingTaskId === 'batch'"
      @click="props.handleCancelCurrentTask"
    >
      {{ t('tasks.cancelAction') }}
    </n-button>
  </div>
</template>

<style scoped>
.stage-actions {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: 10px;
  padding-top: 4px;
}
.stage-actions__primary { min-width: 150px; font-weight: 600; }
</style>
