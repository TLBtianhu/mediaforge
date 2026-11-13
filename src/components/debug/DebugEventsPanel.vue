<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { TerminalOutline } from '@vicons/ionicons5'
import type { DebugWorkerEventGroup } from '@/views/debug_types'

const query = defineModel<string>('query', { required: true })
const issuesOnly = defineModel<boolean>('issuesOnly', { required: true })
const dialogVisible = defineModel<boolean>('dialogVisible', { required: true })

defineProps<{
  groups: DebugWorkerEventGroup[]
  recentGroups: DebugWorkerEventGroup[]
  selected: DebugWorkerEventGroup | null
  executionLogTaskId: string
  canReturnToTask: boolean
  openEvent: (group: DebugWorkerEventGroup) => void
  showExecutionLogs: (taskId: string) => void
  copyText: (text: string, success: string) => void
  groupSummary: (group: DebugWorkerEventGroup | null) => string
  groupTitle: (group: DebugWorkerEventGroup | null) => string
  groupPayload: (group: DebugWorkerEventGroup | null) => string
  eventTime: (value: unknown) => string
}>()

const { t } = useI18n()

function shortTaskId(value: unknown) {
  const text = String(value || '').trim()
  if (!text) return '-'
  return text.length > 28 ? `${text.slice(0, 14)}…${text.slice(-10)}` : text
}
</script>

<template>
  <n-card class="debug-card debug-card--events" :bordered="true" size="small">
    <template #header>
      <div class="debug-section-title">
        <n-icon :component="TerminalOutline" />
        <span>{{ t('debug.workerEvents') }}</span>
      </div>
    </template>
    <div class="debug-event-toolbar">
      <n-input v-model:value="query" clearable :placeholder="t('debug.eventSearch')" :input-props="{ 'aria-label': t('debug.eventSearch') }" />
      <n-checkbox v-model:checked="issuesOnly">{{ t('debug.logCategoryIssues') }}</n-checkbox>
      <span>{{ t('debug.eventGroupCount', { shown: recentGroups.length, total: groups.length }) }}</span>
    </div>
    <div v-if="query === executionLogTaskId && canReturnToTask" class="debug-task-context">
      <span>{{ t('debug.logScope', { id: executionLogTaskId }) }}</span>
      <n-button size="tiny" tertiary @click="showExecutionLogs(executionLogTaskId)">{{ t('debug.returnToTask') }}</n-button>
    </div>
    <div class="worker-event-list">
      <button
        v-for="group in recentGroups"
        :key="group.key"
        type="button"
        class="worker-event-row"
        @click="openEvent(group)"
      >
        <code>{{ group.taskId ? t('debug.workerEventTaskGroup') : group.latestType }}</code>
        <span>{{ shortTaskId(group.taskId) }}</span>
        <span>{{ groupSummary(group) }}</span>
        <time>{{ eventTime(group.lastTimestamp) }}</time>
        <strong>{{ group.eventCount }}</strong>
      </button>
      <n-empty v-if="!recentGroups.length" :description="t('settings.developerNoWorkerEvents')" />
    </div>
    <n-modal
      v-model:show="dialogVisible"
      preset="card"
      :title="groupTitle(selected)"
      :style="{ width: 'min(920px, calc(100vw - 48px))' }"
    >
      <div class="worker-event-modal-meta">
        <span>{{ shortTaskId(selected?.taskId) }}</span>
        <span>{{ t('debug.workerEventCount', { count: selected?.eventCount || 0 }) }}</span>
        <span>{{ groupSummary(selected) }}</span>
        <time>{{ eventTime(selected?.lastTimestamp) }}</time>
        <n-button size="tiny" tertiary @click="copyText(groupPayload(selected), 'debug.eventCopied')">{{ t('common.copy') }}</n-button>
      </div>
      <pre class="worker-event-modal-payload">{{ groupPayload(selected) }}</pre>
    </n-modal>
  </n-card>
</template>
