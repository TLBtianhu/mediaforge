<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import type { ModelEntry } from '@/stores/model'

const show = defineModel<boolean>('show', { required: true })
const draft = defineModel<string>('draft', { required: true })
const props = defineProps<{ model: ModelEntry | null }>()
const emit = defineEmits<{ close: []; save: [] }>()
const { t } = useI18n()
</script>

<template>
  <n-modal :show="show" @update:show="show = $event">
    <n-card
      class="separate-note-modal"
      :title="t('models.editNote')"
      :bordered="false"
      closable
      role="dialog"
      aria-modal="true"
      @close="emit('close')"
    >
      <div v-if="props.model" class="separate-note-modal__body">
        <strong class="separate-note-modal__name">{{ props.model.name }}</strong>
        <n-input
          v-model:value="draft"
          type="textarea"
          :autosize="{ minRows: 4, maxRows: 8 }"
          clearable
          :maxlength="240"
          show-count
          :placeholder="t('models.notePlaceholder')"
        />
      </div>
      <template #footer>
        <div class="separate-note-modal__footer">
          <n-button secondary @click="emit('close')">{{ t('common.cancel') }}</n-button>
          <n-button type="primary" :disabled="!props.model" @click="emit('save')">{{ t('common.save') }}</n-button>
        </div>
      </template>
    </n-card>
  </n-modal>
</template>

<style scoped>
.separate-note-modal,
.separate-workflow-meta-modal{
  width: min(520px, calc(100vw - 48px));
  border-radius: 18px;
}.separate-note-modal__body,
.separate-workflow-meta-modal__body{
  display: grid;
  gap: 12px;
  min-width: 0;
}.separate-note-modal__name{
  overflow: hidden;
  color: var(--on-surface-muted);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}.separate-note-modal__body :deep(.n-input),
.separate-workflow-meta-modal__field :deep(.n-input){
  width: 100%;
  max-width: 100%;
  box-sizing: border-box;
}.separate-note-modal__footer,
.separate-workflow-meta-modal__footer{
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}</style>
