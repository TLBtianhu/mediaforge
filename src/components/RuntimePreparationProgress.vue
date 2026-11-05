<script setup lang="ts">
import { DocumentTextOutline } from '@vicons/ionicons5'
import { useI18n } from 'vue-i18n'

withDefaults(defineProps<{
  message?: string
  percent?: number | null
  downloadedMb?: number | null
  totalMb?: number | null
  showLogs?: boolean
  showCancel?: boolean
}>(), {
  message: '',
  percent: null,
  downloadedMb: null,
  totalMb: null,
  showLogs: false,
  showCancel: false,
})

const emit = defineEmits<{ logs: []; cancel: [] }>()
const { t } = useI18n()
</script>

<template>
  <div class="runtime-preparation-progress" role="status">
    <n-spin size="small" />
    <div class="runtime-preparation-progress__body">
      <span class="runtime-preparation-progress__message" :title="message">{{ message }}</span>
      <template v-if="percent !== null">
        <div class="runtime-preparation-progress__bar">
          <div class="runtime-preparation-progress__fill" :style="{ width: `${percent}%` }" />
        </div>
        <span class="runtime-preparation-progress__percent">{{ percent }}% · {{ downloadedMb }} / {{ totalMb }} MB</span>
      </template>
    </div>
    <div v-if="showLogs || showCancel" class="runtime-preparation-progress__actions">
      <n-button v-if="showLogs" size="tiny" tertiary @click="emit('logs')">
        <template #icon><n-icon :component="DocumentTextOutline" /></template>
        {{ t('settings.runtimeShowInstallLog') }}
      </n-button>
      <n-button v-if="showCancel" size="tiny" tertiary @click="emit('cancel')">
        {{ t('common.cancel') }}
      </n-button>
    </div>
  </div>
</template>

<style scoped>
.runtime-preparation-progress { display: flex; align-items: center; gap: var(--space-sm); }
.runtime-preparation-progress__body { display: grid; flex: 1; gap: var(--space-xs); min-width: 0; }
.runtime-preparation-progress__message { overflow: hidden; color: var(--on-surface-muted); text-overflow: ellipsis; white-space: nowrap; }
.runtime-preparation-progress__bar { height: 6px; overflow: hidden; border-radius: var(--radius-sm); background: var(--primary-soft); }
.runtime-preparation-progress__fill { height: 100%; background: var(--primary); transition: width .3s ease; }
.runtime-preparation-progress__percent { color: var(--on-surface-muted); font-size: 12px; }
.runtime-preparation-progress__actions { display: flex; gap: var(--space-sm); align-items: center; }
</style>
