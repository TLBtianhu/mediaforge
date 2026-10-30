<!--
  单个槽位的控件。控件种类只由 `field.kind` 决定：
  bool → 开关 / enum → 下拉 / multi → 多选 / 其余 → 滑块。

  这里不出现任何具体槽位名——所以新增槽位不需要改这个文件。
-->
<script setup lang="ts">
import { computed } from 'vue'
import { NCheckbox, NCheckboxGroup, NFormItem, NSelect, NSlider, NSwitch } from 'naive-ui'
import { useI18n } from 'vue-i18n'

import type { RenderField } from '../litegraph/renderPlan.ts'

const props = defineProps<{
  field: RenderField
  value: unknown
  disabled?: boolean
  /** onboarding 引导当前聚焦的槽位 */
  active?: boolean
}>()

const emit = defineEmits<{
  (event: 'update:value', value: unknown): void
}>()

const { t } = useI18n()

const label = computed(() => {
  const key = props.field.spec.helpKey
  return key ? t(key) : props.field.slot
})

const numberValue = computed(() => {
  const raw = props.value
  if (typeof raw === 'number' && Number.isFinite(raw)) return raw
  const fallback = props.field.binding.default
  return typeof fallback === 'number' ? fallback : (props.field.binding.min ?? 0)
})

const options = computed(() =>
  (props.field.binding.choices || []).map(choice => ({ label: String(choice), value: choice })),
)

const multipleValue = computed(() => (Array.isArray(props.value) ? (props.value as Array<string | number>) : []))
</script>

<template>
  <NFormItem
    :label="label"
    :data-parameter-guide="field.slot"
    :class="{ 'parameter-guide-target--active': active }"
  >
    <NSwitch
      v-if="field.kind === 'bool'"
      :value="Boolean(value)"
      :disabled="disabled"
      @update:value="(next: boolean) => emit('update:value', next)"
    />
    <NSelect
      v-else-if="field.kind === 'enum'"
      :value="value as string | number | null"
      :options="options"
      :disabled="disabled"
      @update:value="(next: unknown) => emit('update:value', next)"
    />
    <NCheckboxGroup
      v-else-if="field.kind === 'multi'"
      :value="multipleValue"
      :disabled="disabled"
      @update:value="(next: Array<string | number>) => emit('update:value', next)"
    >
      <NCheckbox
        v-for="option in options"
        :key="String(option.value)"
        :value="option.value"
        :label="option.label"
      />
    </NCheckboxGroup>
    <div v-else class="slot-field">
      <NSlider
        :value="numberValue"
        :min="field.binding.min ?? 0"
        :max="field.binding.max ?? 100"
        :step="field.binding.step ?? 1"
        :disabled="disabled"
        @update:value="(next: number) => emit('update:value', next)"
      />
      <span v-if="field.binding.derive || field.spec.derive" class="slot-field__hint">
        {{ t('parameterHelp.details') }}
      </span>
    </div>
  </NFormItem>
</template>

<style scoped>
.slot-field {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
}

.slot-field__hint {
  color: var(--text-secondary, #888);
  font-size: 12px;
  white-space: nowrap;
}
</style>
