<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import { useAppStore, type RuntimeBackend } from '@/stores/app'
import { runtimeAcceleratorReady } from '@/utils/runtime'
import RuntimePreparationProgress from '@/components/RuntimePreparationProgress.vue'
import type { RuntimeBackendCard } from '@/views/settings/runtimeState'
import {
  AlertCircleOutline,
  CheckmarkCircleOutline,
  DocumentTextOutline,
  DownloadOutline,
  HardwareChipOutline,
  PlayOutline,
  TerminalOutline,
  TrashOutline,
} from '@vicons/ionicons5'

type RuntimeSummaryItem = { label: string; value: string; meta: string }
type BackendCatalogItem = { backend: string; label: string }

defineProps<{
  runtimeDetecting: boolean
  runtimeBusy: boolean
  runtimeCurrentError: string
  runtimeCurrentLabel: string
  runtimeSummaryItems: RuntimeSummaryItem[]
  runtimeBackendCatalog: BackendCatalogItem[]
  runtimeBackendCards: RuntimeBackendCard[]
  runtimeDiskMeasuring: boolean
  runtimeDiskTotalLabel: string
  runtimeInstalling: boolean
  runtimeActivating: string | null
  runtimeDeleting: string | null
  runtimeCoreVersionLabel: (card: RuntimeBackendCard) => string
  detectRuntime: () => void
  installBackend: (backend: RuntimeBackend) => void
  activateInstalledRuntime: (card: RuntimeBackendCard) => void
  deleteRuntime: (card: RuntimeBackendCard) => void
  cancelRuntimeInstall: () => void
  retryRuntimeInstall: () => void
  revealPath: (path: string) => void
}>()

const runtimeMirror = defineModel<string>('runtimeMirror', { required: true })
const emit = defineEmits<{ showLogs: [] }>()

const app = useAppStore()
const route = useRoute()
const { t } = useI18n()
</script>

<template>
  <section class="settings-section-panel runtime-section">
    <article class="about-hero runtime-hero" data-guide="runtime-overview">
      <div class="about-hero__main">
        <div class="section-title__icon runtime-hero__icon">
          <n-icon :component="TerminalOutline" size="18" />
        </div>
        <div class="about-hero__copy">
          <span class="about-eyebrow">{{ t('settings.runtime') }}</span>
          <h2 class="about-hero__title">{{ !app.runtimeInfo && !app.envInfo && runtimeDetecting ? t('settings.runtimeChecking') : runtimeCurrentLabel }}</h2>
          <p v-if="runtimeCurrentError" class="runtime-hero__desc runtime-hero__desc--error">{{ runtimeCurrentError }}</p>
          <p v-else class="runtime-hero__desc">{{ t('settings.runtimeDesc') }}</p>
        </div>
        <n-button size="small" secondary :loading="runtimeDetecting" :disabled="runtimeBusy" @click="detectRuntime" style="margin-left: auto; align-self: center;">
          {{ t('settings.runtimeDetect') }}
        </n-button>
      </div>

      <div class="about-stats" :aria-label="t('settings.runtime')">
        <div v-for="item in runtimeSummaryItems" :key="item.label" class="about-stat">
          <span class="about-stat__label">{{ item.label }}</span>
          <n-skeleton v-if="!app.runtimeInfo && !app.envInfo && runtimeDetecting" text width="65%" :sharp="false" />
          <strong v-else :key="item.value" class="about-stat__value runtime-value">{{ item.value }}</strong>
          <small class="about-stat__meta">{{ item.meta }}</small>
        </div>
      </div>
    </article>

    <!-- Runtime Environments -->
    <article class="about-info-card runtime-envs-card">
      <div class="runtime-envs-card__content">
        <n-alert v-if="!app.runtimeManagementSupported" type="info" :bordered="false">
        </n-alert>
        <div class="section-title section-title--plain">
          <span class="section-title__icon">
            <n-icon :component="HardwareChipOutline" size="18" />
          </span>
          <span>{{ t('settings.runtimeBackendsSection') }}</span>
        </div>
        <div class="runtime-read-status" role="status" aria-live="polite">
          <span :class="{ 'runtime-read-status--error': app.runtimeInfoError }">
            <n-spin v-if="app.runtimeInfoLoading" :size="12" />
            <n-icon v-else :component="app.runtimeInfoError ? AlertCircleOutline : CheckmarkCircleOutline" size="14" />
            {{ t(app.runtimeInfoLoading ? 'settings.runtimeChecking' : app.runtimeInfoError ? 'settings.runtimeRefreshFailed' : 'settings.runtimeInfoReady') }}
          </span>
          <span v-if="app.envLoading">
            <n-spin :size="12" />{{ t('settings.runtimeDevicesChecking') }}
          </span>
          <span v-if="runtimeDiskMeasuring">
            <n-spin :size="12" />{{ t('settings.runtimeDiskMeasuring') }}
          </span>
          <span v-else-if="app.runtimeEnvSizesError" class="runtime-read-status--error">
            <n-icon :component="AlertCircleOutline" size="14" />{{ t('settings.runtimeDiskMeasureFailed') }}
          </span>
        </div>
        <p v-if="app.runtimeInfoError" class="runtime-read-error">{{ app.runtimeInfoError }}</p>
        <Transition name="runtime-content" mode="out-in">
          <div v-if="!app.runtimeInfo && app.runtimeInfoLoading" key="loading" class="runtime-envs-list" :aria-label="t('settings.runtimeChecking')" aria-busy="true">
            <div v-for="backend in runtimeBackendCatalog" :key="backend.backend" class="runtime-env-card runtime-env-card--skeleton">
              <strong class="runtime-env-card__backend">{{ backend.label }}</strong>
              <n-skeleton text width="70%" :sharp="false" />
              <n-skeleton text width="45%" :sharp="false" />
              <n-skeleton height="24px" width="96px" :sharp="false" />
            </div>
          </div>
          <div v-else key="ready" class="runtime-envs-list">
            <div
              v-for="card in runtimeBackendCards"
              :key="card.key"
              class="runtime-env-card"
              :data-runtime-backend="card.backend"
              :class="{
                'runtime-env-card--active': card.state === 'active',
                'runtime-env-card--muted': card.state === 'not_installed',
                'runtime-env-card--requested': route.query.backend === card.backend,
              }"
            >
              <div class="runtime-env-card__head">
                <strong class="runtime-env-card__backend">{{ card.label }}</strong>
                <n-tag v-if="card.state === 'active'" :bordered="false" size="small" type="success" round>
                  {{ t('settings.runtimeActive') }}
                </n-tag>
                <n-tag v-else-if="card.state === 'installed'" :bordered="false" size="small" type="info" round>
                  {{ t('settings.runtimeInstalled') }}
                </n-tag>
                <n-tag v-if="card.health === 'degraded'" :bordered="false" size="small" type="warning" round>
                  {{ t('settings.runtimeHealthDegraded') }}
                </n-tag>
                <n-tag v-else-if="card.health === 'broken'" :bordered="false" size="small" type="error" round>
                  {{ t('settings.runtimeHealthBroken') }}
                </n-tag>
                <n-tag v-if="card.recommended" :bordered="false" size="small" type="warning" round>
                  {{ t('settings.runtimeRecommended') }}
                </n-tag>
                <n-tag v-if="card.manifestOutdated" :bordered="false" size="small" type="warning" round>
                  {{ t('settings.runtimeManifestOutdated') }}
                </n-tag>
                <n-tag v-if="card.workflowGraphUnavailable" :bordered="false" size="small" type="warning" round>
                  {{ t('settings.runtimeWorkflowSupportOutdated') }}
                </n-tag>
              </div>

              <p v-if="card.description" class="runtime-env-card__desc">{{ card.description }}</p>
              <p v-if="card.offCatalog" class="runtime-env-card__desc">{{ t('settings.runtimeBackendUnsupported') }}</p>
              <p v-if="card.vendorMissing" class="runtime-env-card__desc">{{ t('settings.runtimeVendorNotDetected') }}</p>
              <p v-if="card.health === 'broken'" class="runtime-env-card__desc">{{ t('settings.runtimeHealthBrokenHint') }}</p>
              <p v-if="card.manifestOutdated" class="runtime-env-card__desc">
                {{ card.env?.coreUpdateSupported === false
                  ? t(card.manifestNewer ? 'settings.runtimeManifestNewerBundledHint' : 'settings.runtimeManifestOutdatedBundledHint')
                  : t(card.manifestNewer ? 'settings.runtimeManifestNewerHint' : 'settings.runtimeManifestOutdatedHint') }}
              </p>
              <p v-if="card.workflowGraphUnavailable" class="runtime-env-card__desc">
                {{ card.env?.coreUpdateSupported === false
                  ? t('settings.runtimeWorkflowSupportBundledHint')
                  : t('settings.runtimeWorkflowSupportHint') }}
              </p>

              <div v-if="card.env" class="runtime-env-card__meta">
                <span v-if="card.env.torchVersion">{{ t('settings.runtimeTorchVersion', { version: card.env.torchVersion }) }}</span>
                <span>{{ runtimeCoreVersionLabel(card) }}</span>
                <span v-if="runtimeAcceleratorReady(card.env, card.backend)" class="runtime-env-card__accel runtime-env-card__accel--ok">{{ t('settings.runtimeAcceleratorAvailable') }}</span>
                <span v-else-if="card.backend !== 'cpu'" class="runtime-env-card__accel">{{ t('settings.runtimeAcceleratorUnavailable') }}</span>
                <span v-if="card.diskLabel">{{ t('settings.runtimeDiskUsage', { size: card.diskLabel }) }}</span>
              </div>
              <div v-else-if="card.state === 'not_installed'" class="runtime-env-card__meta">
                <span>{{ t('settings.runtimeInstallSizeHint', { size: card.sizeHint }) }}</span>
                <span v-if="card.leftoverLabel" class="runtime-env-card__accel">
                  {{ t('settings.runtimeLeftover', { size: card.leftoverLabel }) }}
                </span>
              </div>

              <div class="runtime-env-card__actions">
                <n-button
                  v-if="app.runtimeManagementSupported && card.state === 'not_installed'"
                  size="tiny"
                  secondary
                  type="primary"
                  :loading="card.installing"
                  :disabled="runtimeBusy || !app.runtimeInfo"
                  @click="installBackend(card.backend)"
                >
                  <template #icon><n-icon :component="DownloadOutline" /></template>
                  {{ card.installing ? t('settings.runtimeInstalling') : t('settings.runtimeInstallBackend') }}
                </n-button>
                <n-button
                  v-if="app.runtimeManagementSupported && card.state === 'installed' && card.health !== 'broken'"
                  size="tiny"
                  secondary
                  type="primary"
                  :loading="runtimeActivating === card.backend"
                  :disabled="runtimeBusy"
                  @click="activateInstalledRuntime(card)"
                >
                  <template #icon><n-icon :component="PlayOutline" /></template>
                  {{ t('settings.runtimeActivateShort') }}
                </n-button>
                <n-button
                  v-if="app.runtimeManagementSupported && card.state !== 'not_installed'"
                  size="tiny"
                  tertiary
                  :loading="card.installing"
                  :disabled="runtimeBusy"
                  @click="installBackend(card.backend)"
                >
                  {{ card.installing ? t('settings.runtimeInstalling') : t('settings.runtimeRepair') }}
                </n-button>
                <n-button
                  v-if="card.env?.logPath"
                  size="tiny"
                  tertiary
                  @click="revealPath(card.env.logPath)"
                >
                  {{ t('settings.runtimeViewLog') }}
                </n-button>
                <n-button
                  v-if="app.runtimeManagementSupported && card.leftover"
                  size="tiny"
                  tertiary
                  type="error"
                  :loading="runtimeDeleting === card.backend"
                  :disabled="runtimeBusy"
                  @click="deleteRuntime(card)"
                >
                  <template #icon><n-icon :component="TrashOutline" /></template>
                  {{ t('settings.runtimeCleanLeftover') }}
                </n-button>
                <n-button
                  v-if="app.runtimeManagementSupported && card.state === 'installed' && card.env?.source !== 'bundled'"
                  size="tiny"
                  tertiary
                  type="error"
                  :loading="runtimeDeleting === card.backend"
                  :disabled="runtimeBusy"
                  @click="deleteRuntime(card)"
                >
                  <template #icon><n-icon :component="TrashOutline" /></template>
                  {{ t('settings.runtimeDelete') }}
                </n-button>
              </div>

              <RuntimePreparationProgress
                v-if="card.installing"
                :message="app.runtimeInstallMessage"
                :show-logs="true"
                :show-cancel="true"
                @logs="emit('showLogs')"
                @cancel="cancelRuntimeInstall"
              />
            </div>
          </div>
        </Transition>

        <p v-if="runtimeDiskTotalLabel" class="setting-row__hint runtime-disk-total">
          {{ t('settings.runtimeDiskTotal', { size: runtimeDiskTotalLabel }) }}
        </p>

        <div class="runtime-mirror-row">
          <label class="text-muted text-sm">{{ t('settings.runtimeMirrorLabel') }}</label>
          <n-select v-model:value="runtimeMirror" size="small" class="runtime-mirror-row__select" :options="[
            { label: t('onboarding.runtimeMirrorAuto'), value: 'auto' },
            { label: t('onboarding.runtimeMirrorUstc'), value: 'ustc' },
            { label: t('onboarding.runtimeMirrorTsinghua'), value: 'tsinghua' },
            { label: t('onboarding.runtimeMirrorAliyun'), value: 'aliyun' },
            { label: t('onboarding.runtimeMirrorTencent'), value: 'tencent' },
            { label: t('onboarding.runtimeMirrorPypi'), value: 'pypi' },
          ]" />
        </div>

        <n-alert v-if="app.runtimeInstallStatus === 'error'" type="error" :show-icon="true">
          <div class="runtime-install-error">
            <div class="runtime-install-error__message">{{ app.runtimeInstallMessage }}</div>
            <div class="runtime-install-error__actions">
              <n-button
                size="small"
                tertiary
                @click="emit('showLogs')"
              >
                <template #icon><n-icon :component="DocumentTextOutline" /></template>
                {{ t('settings.runtimeShowInstallLog') }}
              </n-button>
              <n-button v-if="app.runtimeInfo?.logPath" size="small" tertiary @click="revealPath(app.runtimeInfo.logPath)">
                {{ t('settings.runtimeOpenInstallLog') }}
              </n-button>
              <n-button size="small" secondary :disabled="runtimeBusy" @click="retryRuntimeInstall">
                {{ t('settings.runtimeRetryInstall') }}
              </n-button>
            </div>
          </div>
        </n-alert>

        <div v-if="!runtimeInstalling && app.runtimeInstallLogs.length" class="runtime-log-trigger">
          <n-button size="small" tertiary @click="emit('showLogs')">
            <template #icon><n-icon :component="DocumentTextOutline" /></template>
            {{ t('settings.runtimeShowInstallLog') }}
          </n-button>
        </div>
      </div>
    </article>
  </section>
</template>

<style scoped src="../../views/settings/settings-shared.css"></style>
<style scoped>

.about-eyebrow {
  display: inline-flex;
  align-items: center;
  min-height: 22px;
  padding: 0 9px;
  border-radius: 999px;
  color: color-mix(in srgb, var(--primary-ink) 88%, var(--on-surface));
  background: color-mix(in srgb, var(--primary-soft) 42%, transparent);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.02em;
  white-space: nowrap;
}

.runtime-hero__icon {
  flex: 0 0 auto;
}

.runtime-hero__desc {
  margin: 6px 0 0;
  color: var(--on-surface-muted);
  font-size: 13px;
  line-height: 1.6;
}

.runtime-hero__desc--error {
  color: var(--danger);
}

.runtime-section {
  display: grid;
  gap: 16px;
}

.runtime-envs-card {
  position: relative;
}

.runtime-envs-card__content {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.runtime-envs-list {
  display: grid;
  gap: 10px;
}

.runtime-read-status {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 18px;
  min-height: 20px;
  color: var(--on-surface-muted);
  font-size: 12px;
}

.runtime-read-status > span {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.runtime-read-status--error,
.runtime-read-error {
  color: var(--danger);
}

.runtime-read-error {
  margin: 0;
  font-size: 12px;
  overflow-wrap: anywhere;
}

.runtime-env-card--skeleton {
  min-height: 140px;
  align-content: start;
  gap: 12px;
}

.runtime-content-enter-active,
.runtime-content-leave-active {
  transition: opacity 160ms ease, transform 160ms ease;
}

.runtime-content-enter-from,
.runtime-content-leave-to {
  opacity: 0;
  transform: translateY(4px);
}

.runtime-value {
  animation: runtime-value-in 180ms ease both;
}

@keyframes runtime-value-in {
  from { opacity: 0; }
  to { opacity: 1; }
}

@media (prefers-reduced-motion: reduce) {
  .runtime-content-enter-active,
  .runtime-content-leave-active {
    transition: none;
  }
  .runtime-value {
    animation: none;
  }
}

.runtime-env-card {
  display: grid;
  min-width: 0;
  gap: 8px;
  padding: 14px 16px;
  border-radius: 14px;
  border: 1px solid color-mix(in srgb, var(--outline) 42%, transparent);
  background: color-mix(in srgb, var(--surface-2) 32%, transparent);
  transition: border-color 180ms ease, background 180ms ease;
}

.runtime-env-card--active {
  border-color: color-mix(in srgb, var(--primary-border) 52%, var(--outline));
  background: color-mix(in srgb, var(--primary-soft) 12%, var(--surface-2) 28%);
}

.runtime-env-card--muted {
  background: color-mix(in srgb, var(--surface-2) 18%, transparent);
  border-style: dashed;
}

.runtime-env-card--requested {
  border-style: solid;
  border-color: var(--primary-border);
  box-shadow: inset 2px 0 0 var(--primary);
}

.runtime-env-card__desc {
  margin: 0;
  color: var(--on-surface-muted);
  font-size: 12px;
  line-height: 1.55;
}

.runtime-env-card__head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.runtime-env-card__backend {
  color: var(--on-surface);
  font-size: 14px;
  font-weight: 700;
  letter-spacing: 0.01em;
}

.runtime-env-card__meta {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  color: var(--on-surface-muted);
  font-size: 12px;
}

.runtime-env-card__accel {
  color: var(--on-surface-muted);
}

.runtime-env-card__accel--ok {
  color: var(--success);
}

.runtime-env-card__actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  padding-top: 4px;
}

.runtime-disk-total {
  margin: 0;
}

.runtime-mirror-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  padding-top: 2px;
}

.runtime-mirror-row__select {
  width: 220px;
}

.runtime-install-error {
  display: grid;
  gap: 8px;
  min-width: 0;
}

.runtime-install-error__message {
  line-height: 1.45;
  overflow-wrap: anywhere;
}

.runtime-install-error__actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.runtime-log-trigger {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
</style>
