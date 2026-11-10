<script setup lang="ts">
import { reactive } from 'vue'
import { useI18n } from 'vue-i18n'
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

const props = defineProps<{ context: Record<string, any> }>()
const ctx = reactive(props.context) as any
const { t } = useI18n()
const running = () => ctx.taskPanelState === 'running'
</script>

<template>
  <section
    :class="running() ? 'stage-view stage-view--running' : ['stage-view', 'stage-view--terminal', `stage-view--${ctx.taskPanelState}`]"
  >
    <template v-if="running()">
      <div class="stage-hero">
        <div class="stage-hero__top">
          <span class="stage-badge stage-badge--running">
            <span class="stage-badge__dot"></span>
            {{ ctx.statusLabel(ctx.currentTask.status) }}
          </span>
          <div class="stage-hero__top-actions">
            <n-button text size="small" data-guide="task-queue" @click="ctx.showQueueModal = true">
              {{ t('separate.batchQueue') }} ({{ ctx.queueJobs.length }})
            </n-button>
            <n-button text size="small" :disabled="!ctx.currentTask.logs.length" @click="ctx.openCurrentLogs">
              <template #icon><n-icon :component="TerminalOutline" /></template>
              {{ t('tasks.logs') }}
            </n-button>
          </div>
        </div>
        <div class="stage-hero__title">
          <h2>{{ ctx.currentBatchTitle }}</h2>
          <p>{{ ctx.currentBatchLine }}</p>
        </div>
        <div class="progress-ring-block">
          <div class="progress-ring" :style="{ '--pct': ctx.currentBatchProgress }">
            <div class="progress-ring__center">
              <strong>{{ ctx.currentBatchProgress }}</strong>
              <span>%</span>
            </div>
          </div>
          <div class="progress-ring__meta">
            <div class="progress-meta-line">
              <span class="progress-meta-line__label">
                {{ ctx.currentBatchIsMulti ? t('separate.batchOverallProgress') : ctx.progressTitle(ctx.currentTask) }}
              </span>
              <span v-if="!ctx.currentBatchIsMulti && ctx.progressDetail(ctx.currentTask)" class="progress-meta-line__detail">{{ ctx.progressDetail(ctx.currentTask) }}</span>
            </div>
            <p v-if="ctx.taskSubMessage(ctx.currentTask)" class="progress-submessage">{{ ctx.taskSubMessage(ctx.currentTask) }}</p>
            <div class="progress-facts">
              <span><n-icon :component="TimeOutline" /> {{ ctx.currentTaskDuration }}</span>
              <span :title="ctx.currentTaskOutputPath">{{ t('separate.outputTo') }} {{ ctx.currentTaskOutputSummary }}</span>
            </div>
          </div>
        </div>
      </div>
      <div class="stage-actions">
        <n-button secondary strong size="large" @click="ctx.beginNextSeparation">
          {{ t('separate.addNextBatch') }}
        </n-button>
        <n-button
          strong
          size="large"
          type="error"
          secondary
          class="stage-actions__primary"
          :loading="ctx.cancellingTaskId === ctx.currentTask.id || ctx.cancellingTaskId === 'batch'"
          :disabled="ctx.cancellingTaskId === ctx.currentTask.id || ctx.cancellingTaskId === 'batch'"
          @click="ctx.handleCancelCurrentTask"
        >
          {{ t('tasks.cancelAction') }}
        </n-button>
      </div>
    </template>

    <template v-else>
      <div class="stage-hero stage-hero--result">
        <div class="stage-hero__top">
          <span class="stage-badge" :class="`stage-badge--${ctx.taskPanelState}`">
            <n-icon :component="ctx.taskPanelState === 'done' ? CheckmarkCircle : (ctx.taskPanelState === 'failed' ? CloseOutline : PauseOutline)" />
            {{ ctx.statusLabel(ctx.currentTask.status) }}
          </span>
          <div class="stage-hero__top-actions">
            <n-button v-if="ctx.hasQueueEntries" text size="small" data-guide="task-queue" @click="ctx.showQueueModal = true">
              {{ t('separate.batchQueue') }} ({{ ctx.queueJobs.length }})
            </n-button>
            <n-button v-if="ctx.taskPanelState !== 'done' && ctx.currentTask.logs.length" text size="small" @click="ctx.openCurrentLogs">
              <template #icon><n-icon :component="TerminalOutline" /></template>
              {{ t('tasks.logs') }}
            </n-button>
          </div>
        </div>
        <div class="stage-hero__title">
          <h2>{{ ctx.currentBatchIsMulti ? ctx.currentBatchTitle : ctx.statusLabel(ctx.currentTask.status) }}</h2>
          <p>
            {{ ctx.currentBatchLine }}
            <template v-if="ctx.taskPanelState === 'done' && !ctx.currentBatchIsMulti"> · {{ ctx.currentTask.outputs.length }} {{ t('separate.previewStemUnit') }} · {{ ctx.currentTaskDuration }}</template>
          </p>
        </div>
        <div v-if="ctx.taskPanelState === 'done'" class="result-path" :title="ctx.currentTaskOutputPath">
          <n-icon :component="FolderOutline" />
          <code>{{ ctx.currentBatchOutputSummary }}</code>
          <n-button
            size="small"
            quaternary
            :title="t('separate.openOutput')"
            @click.stop="ctx.revealOutput"
          >
            <template #icon><n-icon :component="OpenOutline" /></template>
            {{ t('common.open') }}
          </n-button>
        </div>
        <div v-else-if="ctx.taskSubMessage(ctx.currentTask)" class="result-note">{{ ctx.taskSubMessage(ctx.currentTask) }}</div>
        <section v-if="ctx.taskPanelState === 'done' && ctx.playableOutputs.length" class="result-preview-panel">
          <div class="result-preview-panel__head">
            <strong>{{ t('separate.previewTitle') }}</strong>
            <span>{{ ctx.playableOutputs.length }} {{ t('separate.previewStemUnit') }}</span>
          </div>
          <div class="preview-output-groups">
            <div v-for="group in ctx.playableOutputGroups" :key="group.taskId" class="preview-output-group">
              <div v-if="ctx.currentBatchIsMulti" class="preview-output-group__head">
                <strong :title="group.input">{{ ctx.getFileName(group.input) }}</strong>
                <span>{{ group.outputs.length }} {{ t('separate.previewStemUnit') }}</span>
              </div>
              <div class="preview-track-list">
                <div v-for="output in group.outputs" :key="`${group.taskId}:${output.path}`" class="preview-track">
                  <div class="preview-track__title">
                    <strong :title="output.path">{{ ctx.getFileName(output.path) }}</strong>
                    <small :title="output.path">{{ output.stem }}</small>
                  </div>
                  <n-button circle secondary size="small" @click="ctx.toggleOutputPlayback(output)">
                    <template #icon>
                      <n-icon :component="ctx.playingOutputPath === output.path ? PauseOutline : PlayOutline" />
                    </template>
                  </n-button>
                  <n-slider
                    class="preview-track__slider"
                    :value="ctx.getOutputPlayback(output.path).currentTime"
                    :min="0"
                    :max="Math.max(ctx.getOutputPlayback(output.path).duration, 1)"
                    :step="0.1"
                    :tooltip="false"
                    @update:value="(value: number) => ctx.seekOutput(output.path, value)"
                  />
                  <span class="preview-track__time">
                    {{ ctx.formatPlaybackTime(ctx.getOutputPlayback(output.path).currentTime) }} / {{ ctx.formatPlaybackTime(ctx.getOutputPlayback(output.path).duration) }}
                  </span>
                </div>
              </div>
            </div>
          </div>
        </section>
      </div>
      <div class="stage-actions">
        <template v-if="ctx.taskPanelState === 'done'">
          <n-dropdown :options="ctx.completedActionOptions" placement="top-end" @select="ctx.handleCompletedAction">
            <n-button secondary size="large">
              {{ t('separate.moreActions') }}
              <n-icon :component="ChevronDownOutline" />
            </n-button>
          </n-dropdown>
          <n-button secondary size="large" @click="ctx.goToResults">
            {{ t('separate.viewResults') }}
          </n-button>
          <n-button type="primary" size="large" class="stage-actions__primary" @click="ctx.beginNextSeparation">
            {{ t('separate.newSeparation') }}
          </n-button>
        </template>
        <template v-else-if="ctx.taskPanelState === 'failed'">
          <n-button secondary size="large" @click="ctx.beginNextSeparation">
            {{ t('separate.newSeparation') }}
          </n-button>
          <n-button type="primary" size="large" class="stage-actions__primary" @click="ctx.retryCurrentTask">
            <template #icon><n-icon :component="PlayOutline" /></template>
            {{ t('common.retry') }}
          </n-button>
        </template>
        <template v-else>
          <n-button secondary size="large" @click="ctx.retryCurrentTask">
            <template #icon><n-icon :component="PlayOutline" /></template>
            {{ t('separate.runAgain') }}
          </n-button>
          <n-button type="primary" size="large" class="stage-actions__primary" @click="ctx.beginNextSeparation">
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
