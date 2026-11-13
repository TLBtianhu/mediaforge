<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import type { StemOutput } from '@/stores/task'
import { PauseOutline, PlayOutline } from '@vicons/ionicons5'

type PlayableGroup = { taskId: string; input: string; outputs: StemOutput[] }

const props = defineProps<{
  playableOutputs: StemOutput[]
  playableOutputGroups: PlayableGroup[]
  currentBatchIsMulti: boolean
  getFileName: (path: string) => string
  playingOutputPath: string | null
  toggleOutputPlayback: (output: StemOutput) => void
  getOutputPlayback: (path: string) => { currentTime: number; duration: number }
  seekOutput: (path: string, value: number) => void
  formatPlaybackTime: (seconds: number) => string
}>()
const { t } = useI18n()
</script>

<template>
  <section v-if="props.playableOutputs.length" class="result-preview-panel">
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
</template>

<style scoped>
.result-preview-panel
{
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
}
.result-preview-panel__head
{
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.result-preview-panel__head strong
{
  font-size: 14px;
}
.result-preview-panel__head span,
.preview-track__title small,
.preview-track__time
{
  color: var(--on-surface-muted);
  font-size: 11px;
}
.preview-output-groups
{
  min-height: 0;
  display: grid;
  align-content: start;
  gap: 12px;
  overflow-y: auto;
  padding-right: 2px;
}
.preview-output-group
{
  min-width: 0;
  display: grid;
  gap: 6px;
}
.preview-output-group__head
{
  min-width: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 0 4px;
}
.preview-output-group__head strong
{
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
}
.preview-output-group__head span
{
  flex: 0 0 auto;
  color: var(--on-surface-muted);
  font-size: 11px;
}
.preview-track-list
{
  min-height: 0;
  display: grid;
  align-content: start;
  gap: 8px;
}
.preview-track
{
  display: grid;
  grid-template-columns: minmax(160px, 0.9fr) auto minmax(180px, 1fr) auto;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border: 1px solid color-mix(in srgb, var(--outline) 78%, transparent);
  border-radius: 14px;
  background: color-mix(in srgb, var(--surface-2) 44%, transparent);
}
.preview-track__title
{
  min-width: 0;
  display: grid;
  gap: 2px;
}
.preview-track__title strong,
.preview-track__title small
{
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.preview-track__title strong
{
  font-size: 13px;
}
.preview-track__title small
{
  width: fit-content;
  max-width: 100%;
  padding: 2px 6px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--primary-soft) 58%, transparent);
  color: var(--primary-ink);
  font-size: 10px;
  font-weight: 600;
}
.preview-track__slider
{
  min-width: 0;
}
.preview-track__time
{
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}
</style>
