<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useSettingsStore } from '@/stores/settings'
import {
  DEFAULT_SCALE_FACTOR,
  SCALE_FACTOR_MAX,
  SCALE_FACTOR_MIN,
  SCALE_FACTOR_STEP,
  normalizeScaleFactor,
} from '@/utils/appZoom'

const props = withDefaults(defineProps<{ enabled?: boolean }>(), { enabled: true })
const { t } = useI18n()
const settings = useSettingsStore()
const viewportWidth = ref(0)
const viewportHeight = ref(0)
const overlayRef = ref<HTMLElement | null>(null)
const MIN_SAFE_WIDTH = 760
const MIN_SAFE_HEIGHT = 560
let scaleSyncTimers: number[] = []
let previousFocus: HTMLElement | null = null
let inertedElements: Array<{ element: HTMLElement; previous: boolean }> = []

const visible = computed(() => props.enabled && (
  viewportWidth.value < MIN_SAFE_WIDTH
  || viewportHeight.value < MIN_SAFE_HEIGHT
))
const scalePercent = computed(() => Math.round(normalizeScaleFactor(settings.scaleFactor) * 100))
const canScaleDown = computed(() => settings.scaleFactor > SCALE_FACTOR_MIN + 0.001)
const canScaleUp = computed(() => settings.scaleFactor < SCALE_FACTOR_MAX - 0.001)
const viewportLabel = computed(() => `${viewportWidth.value} × ${viewportHeight.value}`)
const requiredLabel = `${MIN_SAFE_WIDTH} × ${MIN_SAFE_HEIGHT}`

function syncViewport() {
  viewportWidth.value = Math.round(window.innerWidth)
  viewportHeight.value = Math.round(window.innerHeight)
}

function updateScale(delta: number) {
  const current = normalizeScaleFactor(settings.scaleFactor)
  const next = Math.round((current + delta) * 100) / 100
  settings.scaleFactor = normalizeScaleFactor(next)
}

function resetScale() {
  settings.scaleFactor = DEFAULT_SCALE_FACTOR
}

function queueViewportSync() {
  scaleSyncTimers.forEach(timer => window.clearTimeout(timer))
  scaleSyncTimers = [180, 360].map(delay => window.setTimeout(syncViewport, delay))
}

function restoreBackgroundInteractivity() {
  inertedElements.forEach(({ element, previous }) => {
    if (element.isConnected) element.inert = previous
  })
  inertedElements = []
}

function disableBackgroundInteractivity() {
  restoreBackgroundInteractivity()
  const shell = overlayRef.value?.closest<HTMLElement>('.app-shell')
  if (!shell) return
  inertedElements = Array.from(shell.children).flatMap((child) => {
    if (!(child instanceof HTMLElement) || child === overlayRef.value || child.classList.contains('title-bar')) return []
    const previous = child.inert
    child.inert = true
    return [{ element: child, previous }]
  })
}

watch(() => settings.scaleFactor, queueViewportSync)
watch(visible, async (show) => {
  if (show) {
    previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
    await nextTick()
    disableBackgroundInteractivity()
    const target = overlayRef.value?.querySelector<HTMLElement>('button:not(:disabled)') || overlayRef.value
    target?.focus()
    return
  }
  restoreBackgroundInteractivity()
  if (previousFocus?.isConnected) previousFocus.focus()
  previousFocus = null
}, { flush: 'post' })

onMounted(() => {
  syncViewport()
  window.addEventListener('resize', syncViewport)
  window.visualViewport?.addEventListener('resize', syncViewport)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', syncViewport)
  window.visualViewport?.removeEventListener('resize', syncViewport)
  scaleSyncTimers.forEach(timer => window.clearTimeout(timer))
  restoreBackgroundInteractivity()
})
</script>

<template>
  <transition name="viewport-recovery-fade">
    <div
      v-if="visible"
      ref="overlayRef"
      class="viewport-recovery"
      role="alertdialog"
      tabindex="-1"
      aria-live="assertive"
      aria-labelledby="viewport-recovery-title"
      aria-describedby="viewport-recovery-description"
    >
      <span class="viewport-recovery__corner viewport-recovery__corner--tl" aria-hidden="true">↖</span>
      <span class="viewport-recovery__corner viewport-recovery__corner--tr" aria-hidden="true">↗</span>
      <span class="viewport-recovery__corner viewport-recovery__corner--bl" aria-hidden="true">↙</span>
      <span class="viewport-recovery__corner viewport-recovery__corner--br" aria-hidden="true">↘</span>

      <section class="viewport-recovery__panel">
        <span class="viewport-recovery__eyebrow">{{ t('app.viewportRecoveryEyebrow') }}</span>
        <h2 id="viewport-recovery-title">{{ t('app.viewportRecoveryTitle') }}</h2>
        <p id="viewport-recovery-description">{{ t('app.viewportRecoveryHint') }}</p>

        <div class="viewport-recovery__metrics">
          <div>
            <span>{{ t('app.viewportRecoveryCurrent') }}</span>
            <strong>{{ viewportLabel }}</strong>
          </div>
          <div>
            <span>{{ t('app.viewportRecoveryRequired') }}</span>
            <strong>{{ requiredLabel }}</strong>
          </div>
        </div>

        <div class="viewport-recovery__scale">
          <div>
            <span>{{ t('settings.scaleFactor') }}</span>
            <strong>{{ scalePercent }}%</strong>
          </div>
          <div class="viewport-recovery__scale-actions">
            <button type="button" :disabled="!canScaleDown" :aria-label="t('app.viewportRecoveryScaleDown')" @click="updateScale(-SCALE_FACTOR_STEP)">−</button>
            <button type="button" class="viewport-recovery__reset" :disabled="scalePercent === 100" @click="resetScale">{{ t('settings.restoreDefaultScale') }}</button>
            <button type="button" :disabled="!canScaleUp" :aria-label="t('app.viewportRecoveryScaleUp')" @click="updateScale(SCALE_FACTOR_STEP)">＋</button>
          </div>
        </div>

        <small>{{ t('app.viewportRecoveryResizeHint') }}</small>
      </section>
    </div>
  </transition>
</template>

<style scoped>
.viewport-recovery {
  position: fixed;
  inset: 40px 0 0;
  z-index: 3900;
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding: 28px;
  overflow: auto;
  background: color-mix(in srgb, var(--surface) 90%, transparent);
  backdrop-filter: blur(18px) saturate(0.9);
}

:global(.app-shell--native-titlebar) .viewport-recovery { inset: 0; }

.viewport-recovery__panel {
  flex: 0 0 auto;
  width: min(520px, calc(100vw - 40px));
  display: grid;
  gap: 14px;
  padding: 26px;
  border: 1px solid color-mix(in srgb, var(--outline) 86%, transparent);
  border-radius: 20px;
  background: var(--surface-1);
  box-shadow:
    inset 0 1px 0 color-mix(in srgb, white 8%, transparent),
    0 28px 80px color-mix(in srgb, #000 34%, transparent);
  margin: auto 0;
}

.viewport-recovery__eyebrow {
  color: var(--primary-ink);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.08em;
}

.viewport-recovery h2 { margin: 0; font-size: 21px; letter-spacing: -0.025em; }
.viewport-recovery p { margin: 0; color: var(--on-surface-muted); font-size: 13px; line-height: 1.65; text-wrap: pretty; }
.viewport-recovery small { color: var(--on-surface-muted); font-size: 11px; line-height: 1.55; }

.viewport-recovery__metrics {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.viewport-recovery__metrics > div,
.viewport-recovery__scale {
  display: grid;
  gap: 5px;
  padding: 12px 13px;
  border-radius: 12px;
  background: color-mix(in srgb, var(--surface-2) 56%, transparent);
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--outline) 66%, transparent);
}

.viewport-recovery__metrics span,
.viewport-recovery__scale span { color: var(--on-surface-muted); font-size: 10px; }
.viewport-recovery__metrics strong,
.viewport-recovery__scale strong { font-size: 14px; font-variant-numeric: tabular-nums; }

.viewport-recovery__scale {
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
}

.viewport-recovery__scale > div:first-child { display: grid; gap: 5px; }

.viewport-recovery__scale-actions { display: flex; align-items: center; gap: 6px; }
.viewport-recovery__scale-actions button {
  min-width: 34px;
  height: 32px;
  padding: 0 10px;
  border: 1px solid color-mix(in srgb, var(--outline) 80%, transparent);
  border-radius: 9px;
  background: var(--surface-1);
  color: var(--on-surface);
  cursor: pointer;
  transition: background 150ms ease, border-color 150ms ease, transform 120ms ease;
}
.viewport-recovery__scale-actions button:hover:not(:disabled) { border-color: var(--primary-border); background: var(--primary-soft); }
.viewport-recovery__scale-actions button:active:not(:disabled) { transform: translateY(1px); }
.viewport-recovery__scale-actions button:focus-visible { outline: 2px solid var(--primary); outline-offset: 2px; }
.viewport-recovery__scale-actions button:disabled { opacity: 0.42; cursor: not-allowed; }
.viewport-recovery__scale-actions .viewport-recovery__reset { min-width: 128px; font-size: 11px; }

.viewport-recovery__corner {
  position: absolute;
  color: color-mix(in srgb, var(--primary-ink) 72%, var(--on-surface-muted));
  font-size: clamp(22px, 4vw, 38px);
  opacity: 0.72;
  pointer-events: none;
  animation: viewport-corner-pulse 1.8s ease-in-out infinite;
}
.viewport-recovery__corner--tl { top: 18px; left: 18px; }
.viewport-recovery__corner--tr { top: 18px; right: 18px; }
.viewport-recovery__corner--bl { bottom: 18px; left: 18px; }
.viewport-recovery__corner--br { right: 18px; bottom: 18px; }

.viewport-recovery-fade-enter-active,
.viewport-recovery-fade-leave-active { transition: opacity 180ms ease; }
.viewport-recovery-fade-enter-from,
.viewport-recovery-fade-leave-to { opacity: 0; }

@keyframes viewport-corner-pulse {
  0%, 100% { transform: scale(0.94); opacity: 0.48; }
  50% { transform: scale(1.08); opacity: 0.9; }
}

@media (max-width: 520px) {
  .viewport-recovery { padding: 16px; }
  .viewport-recovery__panel { padding: 20px; }
  .viewport-recovery__metrics { grid-template-columns: 1fr; }
  .viewport-recovery__scale { grid-template-columns: 1fr; }
  .viewport-recovery__scale-actions { justify-content: stretch; }
  .viewport-recovery__scale-actions button { flex: 1 1 auto; }
}

@media (max-height: 520px) {
  .viewport-recovery { padding: 14px 18px; }
  .viewport-recovery__panel { gap: 10px; padding: 18px 20px; }
  .viewport-recovery h2 { font-size: 18px; }
  .viewport-recovery p { line-height: 1.5; }
  .viewport-recovery__metrics > div,
  .viewport-recovery__scale { padding: 9px 11px; }
}

@media (prefers-reduced-motion: reduce) {
  .viewport-recovery__corner { animation: none; }
}
</style>
