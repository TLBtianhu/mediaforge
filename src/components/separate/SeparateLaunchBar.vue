<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { CheckmarkCircle, OpenOutline, PlayOutline } from '@vicons/ionicons5'

const props = defineProps<{
  canStart: boolean
  startStatusText: string
  outputSummaryPath: string
  submittingJob: boolean
}>()
const emit = defineEmits<{ reveal: []; start: [] }>()
const { t } = useI18n()
</script>

<template>
  <footer class="launch-bar" :class="`launch-bar--${props.canStart ? 'ready' : 'idle'}`">
    <div class="launch-bar__status">
      <span class="launch-bar__glyph"><n-icon :component="CheckmarkCircle" /></span>
      <div class="launch-bar__text">
        <strong>{{ props.startStatusText }}</strong>
        <span :title="props.outputSummaryPath">{{ props.outputSummaryPath }}</span>
      </div>
    </div>
    <div class="launch-bar__actions">
      <n-button quaternary class="launch-bar__reveal" :title="t('separate.openOutput')" @click="emit('reveal')">
        <template #icon><n-icon :component="OpenOutline" /></template>
        {{ t('separate.openOutput') }}
      </n-button>
      <n-button type="primary" size="large" class="launch-bar__go" data-guide="start-separation" :loading="props.submittingJob" :disabled="!props.canStart" @click="emit('start')">
        <template #icon><n-icon :component="PlayOutline" /></template>
        {{ t('separate.startTask') }}
      </n-button>
    </div>
  </footer>
</template>

<style scoped>
/* ============ Launch bar (stage bottom) ============ */
.launch-bar
{
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 12px 14px;
  border-radius: 15px;
  background:
    linear-gradient(180deg, color-mix(in srgb, var(--primary-soft) 12%, transparent), transparent),
    color-mix(in srgb, var(--surface-2) 34%, transparent);
  box-shadow:
    inset 0 0 0 1px color-mix(in srgb, var(--outline) 84%, transparent),
    inset 0 1px 0 rgba(255,255,255,0.04);
}
.launch-bar--ready
{
  box-shadow:
    inset 0 0 0 1px color-mix(in srgb, var(--success) 30%, transparent),
    inset 0 1px 0 rgba(255,255,255,0.04);
}
.launch-bar__status
{
  flex: 1 1 auto;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 11px;
}
.launch-bar__glyph
{
  flex: 0 0 auto;
  width: 34px;
  height: 34px;
  display: grid;
  place-items: center;
  border-radius: 10px;
  font-size: 19px;
  color: var(--success);
  background: color-mix(in srgb, var(--success) 14%, var(--surface-2));
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--success) 32%, transparent);
}
.launch-bar--idle .launch-bar__glyph
{
  color: var(--on-surface-muted);
  background: color-mix(in srgb, var(--surface-2) 60%, transparent);
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--outline) 80%, transparent);
}
.launch-bar__text
{
  min-width: 0;
  display: grid;
  gap: 1px;
}
.launch-bar__text strong
{
  font-size: 13px;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.launch-bar--idle .launch-bar__text strong
{ color: var(--on-surface-muted); }
.launch-bar__text span
{
  font-size: 11px;
  color: var(--on-surface-muted);
  overflow-wrap: anywhere;
  white-space: normal;
  font-variant-numeric: tabular-nums;
}
.launch-bar__actions
{
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 10px;
}
.launch-bar__reveal
{ color: var(--on-surface-muted); }
.launch-bar__go
{
  min-width: 156px;
  font-weight: 600;
  box-shadow:
    inset 0 1px 0 rgba(255,255,255,0.2),
    0 12px 28px color-mix(in srgb, var(--primary-glow) 42%, transparent);
}
.dark-theme .launch-bar__go
{ box-shadow: none; }
</style>
