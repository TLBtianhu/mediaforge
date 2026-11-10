<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import TaskLogConsole from '@/components/TaskLogConsole.vue'
import type { SeparationTask } from '@/stores/task'

defineProps<{ currentTask: SeparationTask | null; currentTaskFileName: string }>()
const show = defineModel<boolean>('show', { required: true })
const { t } = useI18n()
</script>

<template>
  <n-modal v-model:show="show" style="width:var(--modal-width-lg)">
    <n-card
      :title="currentTask ? `${currentTaskFileName} - ${t('tasks.logs')}` : t('tasks.logs')"
      :bordered="false"
      size="small"
      role="dialog"
      aria-modal="true"
    >
      <TaskLogConsole :lines="currentTask?.logs || []" />
    </n-card>
  </n-modal>
</template>
