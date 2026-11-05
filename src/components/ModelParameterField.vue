<script setup lang="ts">
import { useI18n } from 'vue-i18n'

withDefaults(defineProps<{
  label: string
  value: unknown
  type?: string
  options?: Array<string | number>
  min?: number
  max?: number
  step?: number
  disabled?: boolean
}>(), {
  type: 'string',
  options: undefined,
  min: undefined,
  max: undefined,
  step: undefined,
  disabled: false,
})

const emit = defineEmits<{
  'update:value': [value: unknown]
  blur: []
}>()
const { t } = useI18n()
</script>

<template>
  <div class="model-parameter-field">
    <label>{{ label }}</label>
    <n-select
      v-if="options"
      :value="value"
      :options="options.map(item => ({ label: String(item), value: item }))"
      :disabled="disabled"
      @update:value="emit('update:value', $event)"
    />
    <n-input-number
      v-else-if="type === 'integer' || type === 'number'"
      :value="Number(value ?? 0)"
      :min="min"
      :max="max"
      :step="step"
      :disabled="disabled"
      @update:value="emit('update:value', $event)"
      @blur="emit('blur')"
    />
    <n-input
      v-else
      :value="String(value ?? '')"
      :disabled="disabled"
      @update:value="emit('update:value', $event)"
      @blur="emit('blur')"
    />
  </div>
</template>

<style scoped>
.model-parameter-field { display: grid; gap: var(--space-xs); }
.model-parameter-field > label { color: var(--on-surface-muted); font-size: 9px; font-weight: 700; letter-spacing: .06em; text-transform: uppercase; }
.model-parameter-field > :deep(.n-select),
.model-parameter-field > :deep(.n-input-number),
.model-parameter-field > :deep(.n-input) { width: 100%; }
</style>
