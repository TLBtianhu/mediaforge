<script setup lang="ts">
import { CubeOutline } from '@vicons/ionicons5'

withDefaults(defineProps<{
  loading?: boolean
  emptyTitle: string
  emptyDescription: string
  loadingTitle: string
  loadingDescription: string
  actionLabel: string
  error?: string
}>(), {
  loading: false,
  error: '',
})

const emit = defineEmits<{ action: [] }>()
</script>

<template>
  <div class="model-panel-state" role="status">
    <n-spin v-if="loading" size="medium" />
    <n-icon v-else :component="CubeOutline" class="model-panel-state__glyph" />
    <strong>{{ loading ? loadingTitle : emptyTitle }}</strong>
    <p>{{ loading ? loadingDescription : emptyDescription }}</p>
    <p v-if="error" class="model-panel-state__error">{{ error }}</p>
    <n-button v-if="!loading" secondary @click="emit('action')">{{ actionLabel }}</n-button>
  </div>
</template>

<style scoped>
.model-panel-state { display: flex; min-height: 260px; flex-direction: column; align-items: center; justify-content: center; gap: var(--space-sm); padding: 28px; text-align: center; }
.model-panel-state__glyph { color: var(--primary-strong); font-size: 26px; }
.model-panel-state strong { color: var(--on-surface-heading); font-size: 14px; }
.model-panel-state p { max-width: 340px; margin: 0; color: var(--on-surface-muted); font-size: 12px; line-height: 1.5; }
.model-panel-state__error { color: var(--danger); }
.model-panel-state :deep(.n-button) { margin-top: var(--space-sm); }
</style>
