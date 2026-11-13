<script setup lang="ts">
import { useI18n } from 'vue-i18n'

type StemOption = { label: string; value: string }

const show = defineModel<boolean>('show', { required: true })
const stem = defineModel<string>('stem', { required: true })
const type = defineModel<string>('type', { required: true })
const props = defineProps<{
  models: string[]
  modelStems: Record<string, string>
  stemOptionsByModel: Record<string, StemOption[]>
  weights: Record<string, number>
  typeOptions: StemOption[]
}>()
const emit = defineEmits<{
  'update:model-stem': [name: string, value: string]
  'update:weight': [name: string, value: number]
}>()
const { t } = useI18n()
</script>

<template>
  <n-modal v-model:show="show" :mask-closable="true">
    <n-card
      class="ensemble-modal"
      :title="t('separate.ensembleConfigureTitle')"
      :bordered="false"
      closable
      role="dialog"
      aria-modal="true"
      @close="show = false"
    >
      <div class="ensemble-modal__intro">
        <span>{{ t('separate.ensembleHint') }}</span>
        <strong>{{ t('separate.ensembleSelectedCount', { count: props.models.length }) }}</strong>
      </div>
      <div class="ensemble-config__fields">
        <label>
          <span>{{ t('separate.ensembleStem') }}</span>
          <n-input v-model:value="stem" :placeholder="t('separate.ensembleStemPlaceholder')" />
        </label>
        <label>
          <span>{{ t('separate.ensembleType') }}</span>
          <n-select v-model:value="type" :options="props.typeOptions" />
        </label>
      </div>
      <n-divider title-placement="left">{{ t('separate.ensembleWeights') }}</n-divider>
      <div class="ensemble-modal__weights">
        <div v-for="name in props.models" :key="name" class="ensemble-modal__weight">
          <span class="ensemble-modal__model-name" :title="name">{{ name }}</span>
          <n-select
            :value="props.modelStems[name]"
            size="small"
            :options="props.stemOptionsByModel[name] || []"
            :placeholder="t('separate.ensembleModelStemPlaceholder')"
            :disabled="!(props.stemOptionsByModel[name] || []).length"
            @update:value="(value: string) => emit('update:model-stem', name, value)"
          />
          <n-slider
            :value="props.weights[name]"
            :min="0"
            :max="1"
            :step="0.05"
            @update:value="(value: number) => emit('update:weight', name, value)"
          />
          <n-input-number
            :value="props.weights[name]"
            class="ensemble-modal__weight-input"
            size="small"
            :min="0"
            :max="1"
            :step="0.05"
            @update:value="(value: number | null) => value !== null && emit('update:weight', name, value)"
          />
        </div>
      </div>
      <template #footer>
        <n-button type="primary" @click="show = false">{{ t('common.close') }}</n-button>
      </template>
    </n-card>
  </n-modal>
</template>

<style scoped>
.ensemble-summary-bar{
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 12px;
  padding: 7px 10px;
  border: 1px solid var(--outline);
  border-radius: 7px;
  background: var(--surface-2);
}.ensemble-summary-bar__info{ display: grid; gap: 2px; min-width: 0; }.ensemble-summary-bar__info strong{ font-size: 12px; }.ensemble-summary-bar__info span,
.ensemble-weights > span{ color: var(--on-surface-muted); font-size: 12px; }.ensemble-config__fields{ display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 10px; }.ensemble-config__fields label{ display: grid; gap: 5px; }.ensemble-config__fields label > span{ color: var(--on-surface-muted); font-size: 12px; }.ensemble-weights{ display: grid; gap: 5px; max-height: 78px; overflow-y: auto; padding-right: 4px; }.ensemble-weight{ display: grid; grid-template-columns: minmax(0, 1fr) 120px; align-items: center; gap: 10px; }.ensemble-modal{ width: min(720px, 92vw); }.ensemble-modal__intro{ display: flex; justify-content: space-between; gap: 16px; margin-bottom: 16px; color: var(--on-surface-muted); font-size: 12px; }.ensemble-modal__intro strong{ color: var(--primary-ink); white-space: nowrap; }.ensemble-modal__weights{ display: grid; gap: 12px; max-height: 220px; overflow-y: auto; padding-right: 5px; }.ensemble-modal__weight{ display: grid; grid-template-columns: minmax(130px, 0.9fr) minmax(120px, 0.8fr) minmax(120px, 1fr) 96px; align-items: center; gap: 10px; }.ensemble-modal__weight-input{ width: 96px; }.ensemble-modal__model-name{
  min-width: 0;
  overflow: hidden;
  color: var(--on-surface);
  font-size: 12px;
  font-weight: 600;
  line-height: 1.35;
  text-overflow: ellipsis;
  white-space: nowrap;
}</style>
