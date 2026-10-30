<!--
  按"渲染计划"画参数表单：分组与顺序来自布局档，控件来自槽位词汇表。

  这个组件**不认识模型，也不认识具体槽位名**——只认识 `RenderPlan`。
  因此：换模型不用改这里，调布局也不用改这里。
-->
<script setup lang="ts">
import type { RenderField, RenderPlan } from '../litegraph/renderPlan.ts'
import ModelSlotField from './ModelSlotField.vue'
import { NCollapse, NCollapseItem } from 'naive-ui'
import { useI18n } from 'vue-i18n'

const props = defineProps<{
  plan: RenderPlan
  modelValue: Record<string, unknown>
  disabled?: boolean
  /** 引导聚焦的槽位 id，用于高亮 */
  activeSlot?: string
}>()

const emit = defineEmits<{
  (event: 'update:modelValue', value: Record<string, unknown>): void
}>()

const { t } = useI18n()

function valueOf(field: RenderField): unknown {
  if (Object.prototype.hasOwnProperty.call(props.modelValue, field.slot)) return props.modelValue[field.slot]
  return field.binding.default
}

function patch(slot: string, value: unknown) {
  emit('update:modelValue', { ...props.modelValue, [slot]: value })
}
</script>

<template>
  <div class="model-slot-fields">
    <template v-for="section in plan.sections" :key="section.id">
      <NCollapse v-if="section.collapsed" :default-expanded-names="[]">
        <NCollapseItem :title="t(section.titleKey)" :name="section.id">
          <ModelSlotField
            v-for="field in section.fields"
            :key="field.slot"
            :field="field"
            :value="valueOf(field)"
            :disabled="disabled"
            :active="activeSlot === field.slot"
            @update:value="(value: unknown) => patch(field.slot, value)"
          />
        </NCollapseItem>
      </NCollapse>
      <template v-else>
        <ModelSlotField
          v-for="field in section.fields"
          :key="field.slot"
          :field="field"
          :value="valueOf(field)"
          :disabled="disabled"
          :active="activeSlot === field.slot"
          @update:value="(value: unknown) => patch(field.slot, value)"
        />
      </template>
    </template>
  </div>
</template>
