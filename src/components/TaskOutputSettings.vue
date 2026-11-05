<script setup lang="ts">
import { FolderOpenOutline, RefreshOutline } from '@vicons/ionicons5'
import { useI18n } from 'vue-i18n'
import type { OutputLayout } from '@/stores/task'

withDefaults(defineProps<{
  modelValue: string
  layout: OutputLayout
  error?: string
  preview?: string
  readonly?: boolean
  disabled?: boolean
  showReset?: boolean
}>(), {
  error: '',
  preview: '',
  readonly: false,
  disabled: false,
  showReset: false,
})

const emit = defineEmits<{
  'update:modelValue': [value: string | null]
  'update:layout': [value: OutputLayout]
  browse: []
  reset: []
}>()
const { t } = useI18n()
</script>

<template>
  <div class="task-output-settings">
    <label class="task-output-settings__label">{{ t('separate.outputDirectorySummary') }}</label>
    <div class="task-output-settings__row">
      <n-input
        :value="modelValue"
        size="small"
        :readonly="readonly"
        :status="error ? 'error' : undefined"
        :placeholder="t('separate.outputDefault')"
        :disabled="disabled"
        clearable
        @update:value="emit('update:modelValue', $event)"
      />
      <n-button size="small" secondary :disabled="disabled" @click="emit('browse')">
        <template #icon><n-icon :component="FolderOpenOutline" /></template>
      </n-button>
      <n-button v-if="showReset" size="small" secondary :disabled="disabled" @click="emit('reset')">
        <template #icon><n-icon :component="RefreshOutline" /></template>
      </n-button>
    </div>
    <small v-if="error" class="task-output-settings__error">{{ error }}</small>
    <small v-else-if="preview" class="task-output-settings__preview">{{ preview }}</small>
    <label class="task-output-settings__layout">
      <span>{{ t('separate.saveMode') }}</span>
      <n-select
        size="small"
        :value="layout"
        :disabled="disabled"
        :options="[
          { label: t('separate.saveModeFlatName'), value: 'flat' },
          { label: t('separate.saveModeFolderName'), value: 'folders' },
        ]"
        @update:value="emit('update:layout', $event)"
      />
    </label>
  </div>
</template>

<style scoped>
.task-output-settings { display: grid; gap: var(--space-sm); }
.task-output-settings__label, .task-output-settings__layout > span { color: var(--on-surface-muted); font-size: 12px; }
.task-output-settings__row { display: flex; align-items: center; gap: var(--space-sm); }
.task-output-settings__row > :first-child { flex: 1; min-width: 0; }
.task-output-settings__error { color: var(--danger); }
.task-output-settings__preview { overflow: hidden; color: var(--on-surface-muted); text-overflow: ellipsis; white-space: nowrap; }
.task-output-settings__layout { display: flex; align-items: center; justify-content: space-between; gap: var(--space-sm); }
.task-output-settings__layout :deep(.n-select) { min-width: 150px; }
</style>
