<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { isTerminalTaskStatus } from '@/features/tasks/lifecycle'
import type { SeparationJob } from '@/stores/task'

const show = defineModel<boolean>('show', { required: true })
const scope = defineModel<'session' | 'no-results'>('scope', { required: true })
const props = defineProps<{
  jobs: SeparationJob[]
  noResultJobs: SeparationJob[]
  finishedSessionJobCount: number
  removingRunRecords: boolean
  cancellingQueueJobId: string | null
  statusLabel: (status: string) => string
  queueJobInputSummary: (job: SeparationJob) => string
  isNoResultSeparationJob: (job: SeparationJob) => boolean
}>()
const emit = defineEmits<{
  'open-queue-page': []
  'clear-finished-queue-jobs': []
  'remove-separation-run-records': [jobs: SeparationJob[]]
  'focus-queue-job': [job: SeparationJob]
  'view-queue-job-logs': [job: SeparationJob]
  'dismiss-queue-job': [job: SeparationJob]
  'request-cancel-queue-job': [job: SeparationJob]
}>()
const { t } = useI18n()
</script>

<template>
  <n-modal v-model:show="show" style="width:var(--modal-width-md)">
    <n-card
      class="batch-queue-modal"
      :title="t('separate.batchQueue')"
      :bordered="false"
      closable
      role="dialog"
      aria-modal="true"
      @close="show = false"
    >
      <template #header-extra>
        <n-button size="small" quaternary @click="emit('open-queue-page')">{{ t('agent.queue') }}</n-button>
        <n-button
          v-if="scope === 'session' && props.finishedSessionJobCount"
          size="small"
          quaternary
          @click="emit('clear-finished-queue-jobs')"
        >
          {{ t('separate.clearFinishedTasks') }}
        </n-button>
        <n-button v-else-if="scope === 'no-results' && props.noResultJobs.length" size="small" quaternary type="error"
          :loading="props.removingRunRecords" :disabled="props.removingRunRecords" @click="emit('remove-separation-run-records', props.noResultJobs)">
          {{ t('separate.clearNoResultHistory') }}
        </n-button>
      </template>
      <n-radio-group v-model:value="scope" size="small" class="batch-queue-scopes">
        <n-radio-button value="session">{{ t('separate.queueCurrent') }}</n-radio-button>
        <n-radio-button value="no-results">{{ t('separate.queueNoResultHistory') }} · {{ props.noResultJobs.length }}</n-radio-button>
      </n-radio-group>
      <div v-if="props.jobs.length" class="batch-queue-list">
        <article
          v-for="job in props.jobs"
          :key="job.id"
          class="batch-queue-row"
          :class="`batch-queue-row--${job.status}`"
        >
          <button type="button" class="batch-queue-row__main" @click="emit('focus-queue-job', job)">
            <span class="batch-queue-row__status">
              <i></i>
              {{ props.statusLabel(job.status) }}
            </span>
            <strong>{{ job.model }}</strong>
            <span class="batch-queue-row__input" :title="job.primary.input">{{ props.queueJobInputSummary(job) }}</span>
            <n-progress
              type="line"
              :percentage="job.progress"
              :show-indicator="false"
              :height="5"
              :border-radius="3"
            />
          </button>
          <div class="batch-queue-row__meta">
            <span>{{ job.inputCount }} {{ t('separate.queueFileUnit') }}</span>
            <div class="batch-queue-row__actions">
              <template v-if="isTerminalTaskStatus(job.status)">
                <n-button size="small" secondary @click="job.status === 'failed' ? emit('view-queue-job-logs', job) : emit('focus-queue-job', job)">
                  {{ job.status === 'failed' ? t('tasks.logs') : t('separate.viewTaskDetails') }}
                </n-button>
                <n-button v-if="props.isNoResultSeparationJob(job)" size="small" quaternary type="error"
                  :loading="props.removingRunRecords" :disabled="props.removingRunRecords" @click="emit('remove-separation-run-records', [job])">
                  {{ t('workbench.removeRun') }}
                </n-button>
                <n-button v-else size="small" quaternary @click="emit('dismiss-queue-job', job)">
                  {{ t('common.remove') }}
                </n-button>
              </template>
              <n-button
                v-else
                size="small"
                tertiary
                type="error"
                :loading="props.cancellingQueueJobId === job.id"
                :disabled="Boolean(props.cancellingQueueJobId)"
                @click="emit('request-cancel-queue-job', job)"
              >
                {{ t('tasks.cancelAction') }}
              </n-button>
            </div>
          </div>
        </article>
      </div>
      <n-empty v-else :description="t(scope === 'no-results' ? 'separate.queueNoResultEmpty' : 'separate.queueEmpty')" />
    </n-card>
  </n-modal>
</template>

<style scoped>
.batch-queue-scopes{ margin-bottom: 14px; }.batch-queue-list{
  display: grid;
  gap: 9px;
  max-height: min(64vh, 560px);
  overflow: auto;
  padding-right: 2px;
}.batch-queue-row{
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 14px;
  padding: 11px 12px;
  border: 1px solid color-mix(in srgb, var(--outline) 76%, transparent);
  border-radius: 13px;
  background: color-mix(in srgb, var(--surface-2) 36%, transparent);
}.batch-queue-row__main{
  min-width: 0;
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  align-items: center;
  gap: 4px 10px;
  padding: 0;
  border: 0;
  background: transparent;
  color: inherit;
  text-align: left;
  cursor: pointer;
}.batch-queue-row__main:focus-visible{
  outline: 2px solid var(--primary);
  outline-offset: 4px;
  border-radius: 6px;
}.batch-queue-row__status{
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--on-surface-muted);
  font-size: 11px;
  white-space: nowrap;
}.batch-queue-row__status i{
  width: 7px;
  height: 7px;
  border-radius: 999px;
  background: var(--primary);
}.batch-queue-row--queued .batch-queue-row__status i{ background: var(--on-surface-muted); }.batch-queue-row--failed{ border-color: color-mix(in srgb, var(--danger) 36%, var(--outline)); }.batch-queue-row--failed .batch-queue-row__status{ color: var(--danger); }.batch-queue-row--failed .batch-queue-row__status i{ background: var(--danger); }.batch-queue-row__main strong{ min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 13px; }.batch-queue-row__input{ grid-column: 1 / -1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--on-surface-muted); font-size: 11px; }.batch-queue-row__main :deep(.n-progress){ grid-column: 1 / -1; }.batch-queue-row__meta{
  display: grid;
  justify-items: end;
  gap: 7px;
  color: var(--on-surface-muted);
  font-size: 11px;
  white-space: nowrap;
}.batch-queue-row__actions{ display: flex; align-items: center; justify-content: flex-end; gap: 6px; }
</style>
