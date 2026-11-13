<script setup lang="ts">
import { reactive } from 'vue'
import { useI18n } from 'vue-i18n'
import { logMessagePreview } from '@/utils/logRecord'
import type { useDebugLogs } from '@/views/debug/useDebugLogs'
import { TerminalOutline } from '@vicons/ionicons5'

type LogsApi = ReturnType<typeof useDebugLogs>

const props = defineProps<{
  logs: LogsApi
  executionLogTaskId: string
  canReturnToTask: boolean
  logsDir: string
  showExecutionLogs: (taskId: string) => void
  revealPath: (path: string) => void
  copyText: (text: string, success: string) => void
}>()

// The composable returns refs; a reactive proxy unwraps them for the template
// while keeping the parent's refs as the single state source.
const state = reactive(props.logs) as any
const { t } = useI18n()

function shortTaskId(value: unknown) {
  const text = String(value || '').trim()
  if (!text) return '-'
  return text.length > 28 ? `${text.slice(0, 14)}…${text.slice(-10)}` : text
}
</script>

<template>
  <n-card class="debug-card debug-card--logs" :bordered="true" size="small">
    <template #header>
      <div class="debug-section-title">
        <n-icon :component="TerminalOutline" />
        <span>{{ t('debug.sessionLog') }}</span>
      </div>
    </template>
    <n-alert v-if="state.debugLogError" type="error" class="debug-log-read-error">{{ state.debugLogError }}</n-alert>
    <div class="debug-log-console-head">
      <div class="debug-log-toolbar">
        <n-button size="small" secondary :loading="state.debugLogLoading" @click="state.loadDebugLog">
          {{ t('common.refresh') }}
        </n-button>
        <n-button size="small" secondary :disabled="!state.debugLogInfo?.logsDir" @click="revealPath(state.debugLogInfo?.logsDir || logsDir)">
          {{ t('debug.openLogsDir') }}
        </n-button>
        <n-button size="small" secondary :disabled="!state.filteredDebugLogLines.length" @click="state.copyDebugLog">
          {{ t('debug.copyLog') }}
        </n-button>
        <span class="debug-log-toolbar__spacer" />
        <n-popconfirm @positive-click="state.clearDebugLog">
          <template #trigger><n-button size="small" secondary type="warning" :disabled="state.debugLogReportLoading" :loading="state.debugLogClearing">{{ t('debug.clearLog') }}</n-button></template>
          {{ t('debug.confirmClearLog') }}
        </n-popconfirm>
      </div>
      <details class="debug-log-meta-panel">
        <summary>{{ t('debug.logFileDetails') }}</summary>
        <div class="kv-list debug-log-meta">
          <div v-for="row in state.debugLogRows" :key="row.label" class="kv-row">
            <span>{{ row.label }}</span>
            <code :title="row.value || '-'">{{ row.value || '-' }}</code>
          </div>
        </div>
      </details>
    </div>
    <n-alert v-if="state.debugLogContent?.truncated" type="warning" :show-icon="true">
      {{ t('debug.logTailHint') }}
    </n-alert>
    <div v-if="state.debugLogTask !== 'all'" class="debug-task-context">
      <span>{{ t('debug.logScope', { id: state.debugLogTask }) }}</span>
      <n-button v-if="state.debugLogTask === executionLogTaskId && canReturnToTask" size="tiny" tertiary @click="showExecutionLogs(executionLogTaskId)">{{ t('debug.returnToTask') }}</n-button>
    </div>
    <section v-if="state.parsedDebugLogLines.length" class="debug-log-insights">
      <button class="debug-log-insight debug-log-insight--error" type="button" @click="state.debugLogLevel = 'error'; state.debugLogCategory = 'all'">
        <span>{{ t('debug.logErrors') }}</span>
        <strong>{{ state.debugLogIssueCounts.errors }}</strong>
      </button>
      <button class="debug-log-insight debug-log-insight--warn" type="button" @click="state.debugLogLevel = 'warn'; state.debugLogCategory = 'all'">
        <span>{{ t('debug.logWarnings') }}</span>
        <strong>{{ state.debugLogIssueCounts.warnings }}</strong>
      </button>
      <button class="debug-log-insight debug-log-insight--traceback" type="button" @click="state.debugLogCategory = 'traceback'; state.debugLogLevel = 'all'">
        <span>{{ t('debug.logTracebacks') }}</span>
        <strong>{{ state.debugLogIssueCounts.tracebacks }}</strong>
      </button>
    </section>
    <section v-if="state.parsedDebugLogLines.length" class="debug-log-viewer">
      <div class="debug-log-filters">
        <n-input v-model:value="state.debugLogQuery" size="small" clearable :placeholder="t('debug.logSearchPlaceholder')" />
        <n-select v-model:value="state.debugLogLevel" size="small" :options="state.debugLogLevelOptions" />
        <n-select v-model:value="state.debugLogCategory" size="small" :options="state.debugLogCategoryOptions" />
        <n-select v-model:value="state.debugLogTask" size="small" filterable :options="state.debugLogTaskOptions" />
        <n-select v-model:value="state.debugLogSource" size="small" :options="state.debugLogSourceOptions" />
        <n-button size="small" secondary @click="state.clearDebugLogFilters">
          {{ t('debug.logClearFilters') }}
        </n-button>
      </div>
      <div class="debug-log-viewer__head">
        <span>{{ t('debug.logFilteredCount', { shown: state.filteredDebugLogLines.length, total: state.parsedDebugLogLines.length }) }}</span>
        <span>{{ t('debug.logLatestHint') }}</span>
      </div>
      <div v-if="state.filteredDebugLogLines.length" :ref="(element) => (state.debugLogScroller = element as HTMLElement | null)" class="debug-log-stream" role="log" aria-live="polite">
        <div class="debug-log-table__head" aria-hidden="true">
          <span>{{ t('debug.logColumnTime') }}</span>
          <span>{{ t('debug.logColumnLevel') }}</span>
          <span>{{ t('debug.logColumnSource') }}</span>
          <span>{{ t('debug.logColumnModule') }}</span>
          <span>{{ t('debug.logColumnScope') }}</span>
          <span>{{ t('debug.logColumnMessage') }}</span>
        </div>
        <article
          v-for="line in state.filteredDebugLogLines"
          :key="line.id"
          class="debug-log-row"
          :class="[`debug-log-row--${line.severity}`, `log-source--${line.sourceKind}`, { 'debug-log-row--expandable': state.canExpandDebugLogLine(line) }]"
          :title="logMessagePreview(line.record)"
          tabindex="0"
          :aria-expanded="Boolean(state.expandedDebugLogLines[line.id])"
          @keydown.enter.prevent="state.toggleDebugLogLine(line)"
          @keydown.space.prevent="state.toggleDebugLogLine(line)"
          @click="state.toggleDebugLogLine(line)"
        >
          <code class="debug-log-row__time">{{ line.timestamp || '-' }}</code>
          <span class="debug-log-row__level">{{ line.level || line.severity }}</span>
          <code class="debug-log-row__source" :title="line.source || '-'">{{ state.logSourceLabel(line.sourceKind) }}</code>
          <code class="debug-log-row__module" :title="line.source">{{ line.source || '—' }}</code>
          <code class="debug-log-row__location" :title="[line.taskId, line.record.nodeId].filter(Boolean).join(' / ')">{{ line.record.nodeId || shortTaskId(line.taskId) || '—' }}</code>
          <span class="debug-log-row__message"><span class="debug-log-row__message-text">{{ line.message || line.record.event || line.raw }}</span><span v-if="line.messageContext" class="debug-log-row__message-context"> · {{ line.messageContext }}</span></span>
          <div v-if="state.expandedDebugLogLines[line.id]" class="debug-log-row__detail" @click.stop @keydown.stop>
            <div v-if="line.message" class="debug-log-row__full-message">
              <span>{{ t('debug.logColumnMessage') }}</span>
              <pre>{{ line.message }}</pre>
            </div>
            <div v-if="line.location" class="debug-log-row__location-detail">
              <span>{{ t('debug.logLocation') }}</span>
              <code>{{ line.location }}</code>
            </div>
            <pre v-if="line.traceback">{{ line.traceback }}</pre>
            <pre v-if="line.details">{{ line.details }}</pre>
            <div class="debug-log-row__raw">
              <div class="debug-log-row__raw-head">
                <span>{{ t('debug.logRawLine') }}</span>
                <n-button size="tiny" quaternary @click.stop="copyText(line.raw, 'debug.logCopied')">{{ t('common.copy') }}</n-button>
              </div>
              <pre>{{ line.raw }}</pre>
            </div>
          </div>
        </article>
      </div>
      <n-empty v-else :description="t('debug.logNoFilteredResults')">
        <template #extra>
          <n-button size="small" secondary @click="state.clearDebugLogFilters">
            {{ t('debug.logClearFilters') }}
          </n-button>
        </template>
      </n-empty>
    </section>
    <n-empty v-else :description="t('debug.logEmpty')" />
  </n-card>
</template>
