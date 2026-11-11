<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import type { DropdownOption } from 'naive-ui'
import type { SeparationJob, SeparationTask, StemOutput } from '@/stores/task'
import {
  CheckmarkCircle,
  ChevronDownOutline,
  CloseOutline,
  FolderOutline,
  OpenOutline,
  PauseOutline,
  PlayOutline,
  TerminalOutline,
  TimeOutline,
} from '@vicons/ionicons5'

type PlayableGroup = { taskId: string; input: string; outputs: StemOutput[] }

const props = defineProps<{
  currentTask: SeparationTask
  taskPanelState: string
  statusLabel: (status: string) => string
  queueJobs: SeparationJob[]
  openCurrentLogs: () => void
  currentBatchTitle: string
  currentBatchLine: string
  currentBatchProgress: number
  currentBatchIsMulti: boolean
  progressTitle: (task: SeparationTask) => string
  progressDetail: (task: SeparationTask) => string
  taskSubMessage: (task: SeparationTask) => string
  currentTaskDuration: string
  currentTaskOutputPath: string
  currentTaskOutputSummary: string
  cancellingTaskId: string | null
  handleCancelCurrentTask: () => void
  beginNextSeparation: () => void
  hasQueueEntries: boolean
  currentBatchOutputSummary: string
  playableOutputs: StemOutput[]
  playableOutputGroups: PlayableGroup[]
  getFileName: (path: string) => string
  toggleOutputPlayback: (output: StemOutput) => void
  playingOutputPath: string | null
  getOutputPlayback: (path: string) => { currentTime: number; duration: number }
  seekOutput: (path: string, value: number) => void
  formatPlaybackTime: (seconds: number) => string
  completedActionOptions: DropdownOption[]
  handleCompletedAction: (key: string) => void
  goToResults: () => void
  retryCurrentTask: () => void
  revealOutput: () => void
}>()
const emit = defineEmits<{ openQueue: [] }>()
const { t } = useI18n()
const running = () => props.taskPanelState === 'running'
</script>

<template>
  <section
    :class="running() ? 'stage-view stage-view--running' : ['stage-view', 'stage-view--terminal', `stage-view--${props.taskPanelState}`]"
  >
    <template v-if="running()">
      <div class="stage-hero">
        <div class="stage-hero__top">
          <span class="stage-badge stage-badge--running">
            <span class="stage-badge__dot"></span>
            {{ props.statusLabel(props.currentTask.status) }}
          </span>
          <div class="stage-hero__top-actions">
            <n-button text size="small" data-guide="task-queue" @click="emit('openQueue')">
              {{ t('separate.batchQueue') }} ({{ props.queueJobs.length }})
            </n-button>
            <n-button text size="small" :disabled="!props.currentTask.logs.length" @click="props.openCurrentLogs">
              <template #icon><n-icon :component="TerminalOutline" /></template>
              {{ t('tasks.logs') }}
            </n-button>
          </div>
        </div>
        <div class="stage-hero__title">
          <h2>{{ props.currentBatchTitle }}</h2>
          <p>{{ props.currentBatchLine }}</p>
        </div>
        <div class="progress-ring-block">
          <div class="progress-ring" :style="{ '--pct': props.currentBatchProgress }">
            <div class="progress-ring__center">
              <strong>{{ props.currentBatchProgress }}</strong>
              <span>%</span>
            </div>
          </div>
          <div class="progress-ring__meta">
            <div class="progress-meta-line">
              <span class="progress-meta-line__label">
                {{ props.currentBatchIsMulti ? t('separate.batchOverallProgress') : props.progressTitle(props.currentTask) }}
              </span>
              <span v-if="!props.currentBatchIsMulti && props.progressDetail(props.currentTask)" class="progress-meta-line__detail">{{ props.progressDetail(props.currentTask) }}</span>
            </div>
            <p v-if="props.taskSubMessage(props.currentTask)" class="progress-submessage">{{ props.taskSubMessage(props.currentTask) }}</p>
            <div class="progress-facts">
              <span><n-icon :component="TimeOutline" /> {{ props.currentTaskDuration }}</span>
              <span :title="props.currentTaskOutputPath">{{ t('separate.outputTo') }} {{ props.currentTaskOutputSummary }}</span>
            </div>
          </div>
        </div>
      </div>
      <div class="stage-actions">
        <n-button secondary strong size="large" @click="props.beginNextSeparation">
          {{ t('separate.addNextBatch') }}
        </n-button>
        <n-button
          strong
          size="large"
          type="error"
          secondary
          class="stage-actions__primary"
          :loading="props.cancellingTaskId === props.currentTask.id || props.cancellingTaskId === 'batch'"
          :disabled="props.cancellingTaskId === props.currentTask.id || props.cancellingTaskId === 'batch'"
          @click="props.handleCancelCurrentTask"
        >
          {{ t('tasks.cancelAction') }}
        </n-button>
      </div>
    </template>

    <template v-else>
      <div class="stage-hero stage-hero--result">
        <div class="stage-hero__top">
          <span class="stage-badge" :class="`stage-badge--${props.taskPanelState}`">
            <n-icon :component="props.taskPanelState === 'done' ? CheckmarkCircle : (props.taskPanelState === 'failed' ? CloseOutline : PauseOutline)" />
            {{ props.statusLabel(props.currentTask.status) }}
          </span>
          <div class="stage-hero__top-actions">
            <n-button v-if="props.hasQueueEntries" text size="small" data-guide="task-queue" @click="emit('openQueue')">
              {{ t('separate.batchQueue') }} ({{ props.queueJobs.length }})
            </n-button>
            <n-button v-if="props.taskPanelState !== 'done' && props.currentTask.logs.length" text size="small" @click="props.openCurrentLogs">
              <template #icon><n-icon :component="TerminalOutline" /></template>
              {{ t('tasks.logs') }}
            </n-button>
          </div>
        </div>
        <div class="stage-hero__title">
          <h2>{{ props.currentBatchIsMulti ? props.currentBatchTitle : props.statusLabel(props.currentTask.status) }}</h2>
          <p>
            {{ props.currentBatchLine }}
            <template v-if="props.taskPanelState === 'done' && !props.currentBatchIsMulti"> · {{ props.currentTask.outputs.length }} {{ t('separate.previewStemUnit') }} · {{ props.currentTaskDuration }}</template>
          </p>
        </div>
        <div v-if="props.taskPanelState === 'done'" class="result-path" :title="props.currentTaskOutputPath">
          <n-icon :component="FolderOutline" />
          <code>{{ props.currentBatchOutputSummary }}</code>
          <n-button
            size="small"
            quaternary
            :title="t('separate.openOutput')"
            @click.stop="props.revealOutput"
          >
            <template #icon><n-icon :component="OpenOutline" /></template>
            {{ t('common.open') }}
          </n-button>
        </div>
        <div v-else-if="props.taskSubMessage(props.currentTask)" class="result-note">{{ props.taskSubMessage(props.currentTask) }}</div>
        <section v-if="props.taskPanelState === 'done' && props.playableOutputs.length" class="result-preview-panel">
          <div class="result-preview-panel__head">
            <strong>{{ t('separate.previewTitle') }}</strong>
            <span>{{ props.playableOutputs.length }} {{ t('separate.previewStemUnit') }}</span>
          </div>
          <div class="preview-output-groups">
            <div v-for="group in props.playableOutputGroups" :key="group.taskId" class="preview-output-group">
              <div v-if="props.currentBatchIsMulti" class="preview-output-group__head">
                <strong :title="group.input">{{ props.getFileName(group.input) }}</strong>
                <span>{{ group.outputs.length }} {{ t('separate.previewStemUnit') }}</span>
              </div>
              <div class="preview-track-list">
                <div v-for="output in group.outputs" :key="`${group.taskId}:${output.path}`" class="preview-track">
                  <div class="preview-track__title">
                    <strong :title="output.path">{{ props.getFileName(output.path) }}</strong>
                    <small :title="output.path">{{ output.stem }}</small>
                  </div>
                  <n-button circle secondary size="small" @click="props.toggleOutputPlayback(output)">
                    <template #icon>
                      <n-icon :component="props.playingOutputPath === output.path ? PauseOutline : PlayOutline" />
                    </template>
                  </n-button>
                  <n-slider
                    class="preview-track__slider"
                    :value="props.getOutputPlayback(output.path).currentTime"
                    :min="0"
                    :max="Math.max(props.getOutputPlayback(output.path).duration, 1)"
                    :step="0.1"
                    :tooltip="false"
                    @update:value="(value: number) => props.seekOutput(output.path, value)"
                  />
                  <span class="preview-track__time">
                    {{ props.formatPlaybackTime(props.getOutputPlayback(output.path).currentTime) }} / {{ props.formatPlaybackTime(props.getOutputPlayback(output.path).duration) }}
                  </span>
                </div>
              </div>
            </div>
          </div>
        </section>
      </div>
      <div class="stage-actions">
        <template v-if="props.taskPanelState === 'done'">
          <n-dropdown :options="props.completedActionOptions" placement="top-end" @select="props.handleCompletedAction">
            <n-button secondary size="large">
              {{ t('separate.moreActions') }}
              <n-icon :component="ChevronDownOutline" />
            </n-button>
          </n-dropdown>
          <n-button secondary size="large" @click="props.goToResults">
            {{ t('separate.viewResults') }}
          </n-button>
          <n-button type="primary" size="large" class="stage-actions__primary" @click="props.beginNextSeparation">
            {{ t('separate.newSeparation') }}
          </n-button>
        </template>
        <template v-else-if="props.taskPanelState === 'failed'">
          <n-button secondary size="large" @click="props.beginNextSeparation">
            {{ t('separate.newSeparation') }}
          </n-button>
          <n-button type="primary" size="large" class="stage-actions__primary" @click="props.retryCurrentTask">
            <template #icon><n-icon :component="PlayOutline" /></template>
            {{ t('common.retry') }}
          </n-button>
        </template>
        <template v-else>
          <n-button secondary size="large" @click="props.retryCurrentTask">
            <template #icon><n-icon :component="PlayOutline" /></template>
            {{ t('separate.runAgain') }}
          </n-button>
          <n-button type="primary" size="large" class="stage-actions__primary" @click="props.beginNextSeparation">
            {{ t('separate.newSeparation') }}
          </n-button>
        </template>
      </div>
    </template>
  </section>
</template>

<style scoped>
/* ============ Stage ============ */
.stage-view{
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 18px;
  border-radius: 20px;
  background:
    radial-gradient(120% 90% at 50% -10%, color-mix(in srgb, var(--primary-glow) 10%, transparent), transparent 60%),
    color-mix(in srgb, var(--surface-1) 74%, transparent);
  box-shadow:
    inset 0 0 0 1px color-mix(in srgb, var(--outline) 76%, transparent),
    inset 0 1px 0 rgba(255,255,255,0.03),
    var(--shadow-panel);
}.stage-view--running{
  box-shadow:
    inset 0 0 0 1px color-mix(in srgb, var(--primary-border) 48%, transparent),
    0 0 0 1px color-mix(in srgb, var(--primary-glow) 16%, transparent),
    var(--shadow-panel);
}.stage-view--done{ box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--success) 40%, transparent), var(--shadow-panel); }.stage-view--failed{ box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--danger) 40%, transparent), var(--shadow-panel); }.stage-view--cancelled{ box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--warning) 40%, transparent), var(--shadow-panel); }.dark-theme .rail-card,
.dark-theme .stage-view{
  background: var(--surface-1);
}.dark-theme .rail-card,
.dark-theme .stage-view--ready{
  box-shadow: inset 0 0 0 1px var(--outline);
}/* stage: hero (running + terminal) */
.stage-hero{
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 16px;
}.stage-hero__top{
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}.stage-hero__top-actions{
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  flex-wrap: wrap;
}.stage-badge{
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 5px 12px 5px 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: -0.01em;
}.stage-badge .n-icon{ font-size: 14px; }.stage-badge--running{
  color: var(--primary-ink);
  background: var(--primary-soft);
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--primary-border) 50%, transparent);
}.stage-badge--done{
  color: color-mix(in srgb, var(--success) 84%, var(--on-surface));
  background: color-mix(in srgb, var(--success) 14%, var(--surface-2));
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--success) 40%, transparent);
}.stage-badge--failed{
  color: color-mix(in srgb, var(--danger) 84%, var(--on-surface));
  background: color-mix(in srgb, var(--danger) 14%, var(--surface-2));
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--danger) 40%, transparent);
}.stage-badge--cancelled{
  color: color-mix(in srgb, var(--warning) 84%, var(--on-surface));
  background: color-mix(in srgb, var(--warning) 14%, var(--surface-2));
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--warning) 40%, transparent);
}.stage-badge__dot{
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--primary);
  box-shadow: 0 0 0 0 color-mix(in srgb, var(--primary) 60%, transparent);
  animation: pulse-dot 1.6s ease-out infinite;
}.stage-hero__title h2{
  margin: 0;
  font-size: 22px;
  font-weight: 600;
  letter-spacing: -0.03em;
  line-height: 1.15;
}.stage-hero__title p{
  margin: 5px 0 0;
  font-size: 13px;
  color: var(--on-surface-muted);
  line-height: 1.45;
}/* progress ring */
.progress-ring-block{
  flex: 1 1 auto;
  min-height: 0;
  display: flex;
  align-items: center;
  gap: 28px;
  padding: 8px 0;
}.progress-ring{
  --pct: 0;
  flex: 0 0 auto;
  width: 148px;
  height: 148px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  background:
    radial-gradient(closest-side, var(--surface-1) 74%, transparent 75% 100%),
    conic-gradient(
      var(--primary) calc(var(--pct) * 1%),
      color-mix(in srgb, var(--outline) 130%, transparent) 0
    );
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--outline) 60%, transparent);
  transition: background 500ms cubic-bezier(0.22,1,0.36,1);
}.progress-ring__center{
  display: flex;
  align-items: baseline;
  gap: 2px;
  color: var(--on-surface);
}.progress-ring__center strong{
  font-size: 40px;
  font-weight: 600;
  letter-spacing: -0.04em;
  font-variant-numeric: tabular-nums;
}.progress-ring__center span{ font-size: 16px; color: var(--on-surface-muted); }.progress-ring__meta{
  flex: 1 1 auto;
  min-width: 0;
  display: grid;
  gap: 10px;
  align-content: center;
}.progress-meta-line{
  display: grid;
  gap: 2px;
}.progress-meta-line__label{ font-size: 13px; font-weight: 600; }.progress-meta-line__detail{ font-size: 12px; color: var(--on-surface-muted); }.progress-submessage{
  margin: 0;
  font-size: 12px;
  color: var(--on-surface-muted);
  line-height: 1.5;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}.progress-facts{
  display: flex;
  flex-wrap: wrap;
  gap: 6px 18px;
  padding-top: 4px;
  border-top: 1px solid color-mix(in srgb, var(--outline) 70%, transparent);
}.progress-facts span{
  min-width: 0;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 11.5px;
  color: var(--on-surface-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}.progress-facts .n-icon{ flex: 0 0 auto; }/* result path / note */
.stage-hero--result{ justify-content: flex-start; gap: 14px; }.result-path{
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 12px 14px;
  border-radius: 13px;
  background: color-mix(in srgb, var(--surface-2) 40%, transparent);
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--outline) 80%, transparent);
}.result-path .n-icon{ flex: 0 0 auto; font-size: 16px; color: var(--primary-ink); }.result-path code{
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  color: color-mix(in srgb, var(--on-surface) 88%, var(--on-surface-muted));
}.result-note{
  padding: 12px 14px;
  border-radius: 13px;
  font-size: 12.5px;
  line-height: 1.55;
  color: var(--on-surface-muted);
  background: color-mix(in srgb, var(--surface-2) 34%, transparent);
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--outline) 76%, transparent);
}.result-preview-panel{
  flex: 0 1 auto;
  display: grid;
  gap: 8px;
  min-height: 0;
  max-height: 360px;
  padding: 12px 14px;
  overflow: hidden;
  border: 1px solid color-mix(in srgb, var(--outline) 80%, transparent);
  border-radius: 16px;
  background: color-mix(in srgb, var(--surface-2) 32%, transparent);
}.result-preview-panel__head{
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}.result-preview-panel__head strong{
  font-size: 14px;
}.result-preview-panel__head span,
.preview-track__title small,
.preview-track__time{
  color: var(--on-surface-muted);
  font-size: 11px;
}.preview-output-groups{
  min-height: 0;
  display: grid;
  align-content: start;
  gap: 12px;
  overflow-y: auto;
  padding-right: 2px;
}.preview-output-group{
  min-width: 0;
  display: grid;
  gap: 6px;
}.preview-output-group__head{
  min-width: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 0 4px;
}.preview-output-group__head strong{
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
}.preview-output-group__head span{
  flex: 0 0 auto;
  color: var(--on-surface-muted);
  font-size: 11px;
}.preview-track-list{
  min-height: 0;
  display: grid;
  align-content: start;
  gap: 8px;
}.preview-track{
  display: grid;
  grid-template-columns: minmax(160px, 0.9fr) auto minmax(180px, 1fr) auto;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border: 1px solid color-mix(in srgb, var(--outline) 78%, transparent);
  border-radius: 14px;
  background: color-mix(in srgb, var(--surface-2) 44%, transparent);
}.preview-track__title{
  min-width: 0;
  display: grid;
  gap: 2px;
}.preview-track__title strong,
.preview-track__title small{
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}.preview-track__title strong{
  font-size: 13px;
}.preview-track__title small{
  width: fit-content;
  max-width: 100%;
  padding: 2px 6px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--primary-soft) 58%, transparent);
  color: var(--primary-ink);
  font-size: 10px;
  font-weight: 600;
}.preview-track__slider{
  min-width: 0;
}.preview-track__time{
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}/* stage action bar */
.stage-actions{
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: 10px;
  padding-top: 4px;
}.stage-actions__primary{ min-width: 150px; font-weight: 600; }.stage-view--running .stage-actions{ justify-content: flex-end; }</style>
