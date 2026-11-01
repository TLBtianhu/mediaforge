<script setup lang="ts">
import TaskLogConsole from '@/components/TaskLogConsole.vue'
import MediaPageHeader from '@/components/media/MediaPageHeader.vue'
import InferenceParameterHelp from '@/components/InferenceParameterHelp.vue'
import { useBeginnerGuideStore } from '@/stores/beginnerGuide'
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { useDialog, useMessage } from 'naive-ui'
import { invoke } from '@tauri-apps/api/core'
import { open } from '@tauri-apps/plugin-shell'
import packageMeta from '../../package.json'
import appLogo from '@/assets/app-logo-symbol-canvas.png'
import { SYSTEM_LOCALE, setLocale, type LocaleSetting } from '@/i18n'
import { useSettingsStore } from '@/stores/settings'
import { useAppStore, type RuntimeBackend } from '@/stores/app'
import { runtimeAcceleratorReady } from '@/utils/runtime'
import { makeRuntimeState } from './settings/runtimeState'
import { makeRuntimeActions } from './settings/runtimeActions'
import { makeProxyTest } from './settings/proxyTest'
import { makeAppearance, dirName } from './settings/appearance'
import { makeModelDirMigration } from './settings/modelDirMigration'
import { useModelStore } from '@/stores/model'
import { useTaskStore } from '@/stores/task'
import { normalizeConcurrentSeparations } from '@/features/tasks/concurrency'
import { formatBytes } from '@/utils/format'
import { formatDateTime } from '@/utils/time'
import { DEFAULT_SCALE_FACTOR, normalizeScaleFactor } from '@/utils/appZoom'
import {
  applyTheme,
  getThemeAccentPreview,
  resolvedIsDark,
  runRippleViewTransition,
  THEME_ACCENTS,
  type ThemeAccent,
  type ThemeMode,
} from '@/utils/theme'
import {
  ColorPaletteOutline,
  FolderOpenOutline,
  TerminalOutline,
  SettingsOutline,
  SpeedometerOutline,
  SwapHorizontalOutline,
  LogoGithub,
  InformationCircleOutline,
  DocumentTextOutline,
  LinkOutline,
  OpenOutline,
  DownloadOutline,
  AlertCircleOutline,
  CheckmarkCircleOutline,
  TrashOutline,
  PlayOutline,
  HardwareChipOutline,
  CloudDownloadOutline,
  RefreshOutline,
  ChevronForwardOutline,
  CompassOutline,
  OptionsOutline,
} from '@vicons/ionicons5'
import VersionUpdateCard from '@/components/settings/VersionUpdateCard.vue'

const { t, locale: currentLocale } = useI18n()
const message = useMessage()
const dialog = useDialog()
const route = useRoute()
const router = useRouter()
const settings = useSettingsStore()
const beginnerGuide = useBeginnerGuideStore()
const showParameterHelp = ref(false)
const app = useAppStore()
const modelStore = useModelStore()
const task = useTaskStore()
type SettingsSection = 'about' | 'appearance' | 'runtime' | 'paths' | 'defaults'
type BuildInfo = {
  version: string
  gitCommit: string
  gitTag: string
  gitRef: string
  runId: string
  runAttempt: string
  repository: string
  repositoryOwner: string
  buildTime: string
  target: string
  variant: string
  official: boolean
}
const activeSection = ref<SettingsSection>('appearance')
watch(() => route.query.section, (section) => {
  if (typeof section === 'string' && ['about', 'appearance', 'runtime', 'paths', 'defaults'].includes(section)) {
    if (beginnerGuide.phase === 'running' && beginnerGuide.currentStep.section && beginnerGuide.currentStep.section !== section) beginnerGuide.pause()
    activeSection.value = section as SettingsSection
  }
}, { immediate: true })
function selectSettingsSection(section: SettingsSection) {
  if (activeSection.value === section) return
  beginnerGuide.pause()
  activeSection.value = section
  if (typeof route.query.section === 'string') void router.replace({ query: { ...route.query, section } })
}
const buildInfo = ref<BuildInfo | null>(null)
const appVersion = computed(() => buildInfo.value?.version || packageMeta.version || '0.0.0')
const repoUrl = 'https://github.com/TLBtianhu/mediaforge'
const officialRepository = 'pymss-project/pymss-studio'
const officialRepositoryOwner = 'pymss-project'
const coreRepoUrl = 'https://github.com/pymss-project/pymss'
const licenseUrl = 'https://www.gnu.org/licenses/agpl-3.0.html'
const coreLicenseUrl = 'https://github.com/pymss-project/pymss/blob/main/LICENSE'
const desktopLicense = 'AGPL-3.0'
const coreLicense = 'MIT'
const {
  themeMode,
  themeAccent,
  scaleFactor,
  locale,
  animationsEnabled,
  developerMode,
  dataRoot,
  modelDir,
  outputDir,
  settingsDir,
  editorProjectsDir,
  logsDir,
  defaultDevice,
  downloadSource,
  downloadMethod,
  maxConcurrentSeparations,
  modelDirMigrationState,
  isModelDirMigrating,
  proxyMode,
  proxyUrl,
  proxyBypass,
} = storeToRefs(settings)
const { downloadTasks } = storeToRefs(modelStore)
const { activeWorkerTasks } = storeToRefs(task)
const deviceOptions = computed(() => settings.deviceOptions(app.envInfo))
const themeAccentOptions = computed(() =>
  THEME_ACCENTS.map((accent) => ({
    value: accent,
    label: t(`settings.themeAccent${accent[0].toUpperCase()}${accent.slice(1)}`),
    preview: getThemeAccentPreview(accent, resolvedIsDark(themeMode.value)),
  })),
)
const languageOptions = computed(() => [
  { label: t('settings.languageSystem'), value: SYSTEM_LOCALE },
  { label: t('settings.languageSimplifiedChinese'), value: 'zh-CN' },
  { label: t('settings.languageEnglish'), value: 'en' },
])

type ProxyTestStatus = 'idle' | 'testing' | 'success' | 'error'
const proxyTestStatus = ref<ProxyTestStatus>('idle')
const proxyTestMessage = ref('')
const proxyTestSuggestion = ref('')
const proxyTestElapsed = ref(0)
const proxyTestLoading = computed(() => proxyTestStatus.value === 'testing')
const runtimeMirror = ref('auto')
const runtimeManualChecking = ref(false)
const runtimeDeleting = ref<string | null>(null)
const runtimeActivating = ref<string | null>(null)
const runtimeLogDialogVisible = ref(false)
const runtimeLogPre = ref<HTMLElement | null>(null)

watch(() => app.runtimeInstallLogs.at(-1), () => {
  if (runtimeLogDialogVisible.value && runtimeLogPre.value) {
    const target = runtimeLogPre.value
    if (target.scrollHeight - target.scrollTop - target.clientHeight > 48) return
    nextTick(() => {
      if (runtimeLogPre.value) {
        runtimeLogPre.value.scrollTop = runtimeLogPre.value.scrollHeight
      }
    })
  }
})
const {
  runtimeInstalling, runtimeDetecting, runtimeBusy, runtimeCurrentError, runtimeCurrentBackend,
  runtimeCurrentLabel, installedRuntimes, runtimePlatform, runtimeRecommendedBackend,
  runtimeBackendDescription, runtimeBackendCatalog, runtimeBackendCards, runtimeCoreVersionLabel,
  runtimeBackendLabel, runtimeDiskTotalLabel, runtimeDiskMeasuring,
} = makeRuntimeState({
  computed, t, app, runtimeManualChecking, runtimeActivating, runtimeDeleting,
})

let locatedRuntimeBackend = ''
function locateRuntimeBackend() {
  const backend = route.query.backend
  if (activeSection.value !== 'runtime' || app.runtimeInfoLoading || typeof backend !== 'string' || backend === locatedRuntimeBackend) return
  if (!runtimeBackendCatalog.value.some(item => item.backend === backend)) return
  const card = Array.from(document.querySelectorAll<HTMLElement>('[data-runtime-backend]'))
    .find(element => element.dataset.runtimeBackend === backend)
  if (card) {
    card.scrollIntoView({ block: 'nearest' })
    locatedRuntimeBackend = backend
  }
}

watch([() => route.query.backend, activeSection, () => app.runtimeInfoLoading], async () => {
  await nextTick()
  locateRuntimeBackend()
}, { immediate: true, flush: 'post' })

const {
  confirmRuntimeAction, installBackend, retryRuntimeInstall, refreshRuntimeStatus,
  refreshRuntimeCoreVersions, cancelRuntimeInstall, deleteRuntime, activateInstalledRuntime,
  detectRuntime,
} = makeRuntimeActions({
  app, message, dialog, t, currentLocale, runtimeMirror,
  runtimeManualChecking, runtimeDeleting, runtimeActivating,
  runtimeBusy, runtimeCurrentBackend, installedRuntimes, runtimeBackendLabel,
})

watch(() => app.runtimeInstallStatus, (status, previous) => {
  if (previous !== 'installing') return
  if (status === 'success') message.success(t('settings.runtimeInstallSuccess'))
  // Refresh on failure and cancellation too: an interrupted install leaves its venv behind,
  // and that leftover only becomes visible once sizes are re-measured.
  if (status === 'success' || status === 'error' || status === 'cancelled') {
    void app.loadRuntimeEnvSizes()
  }
})

// Reuse recent or in-flight reads when returning to this section.
watch(activeSection, (section) => {
  if (section === 'runtime') {
    void refreshRuntimeStatus(false)
    void refreshRuntimeCoreVersions()
  }
}, { immediate: true })

watch(
  () => [app.runtimeInfo?.installedBackend, app.runtimeInfo?.installState?.backend, app.runtimeInfo?.installedEnvironments?.length],
  () => {
    if (activeSection.value === 'runtime') {
      void refreshRuntimeCoreVersions()
    }
  },
)

const proxyModeOptions = computed(() => [
  { value: 'none' as const, label: t('settings.proxyModeNone') },
  { value: 'system' as const, label: t('settings.proxyModeSystem') },
  { value: 'custom' as const, label: t('settings.proxyModeCustom') },
])
const downloadMethodOptions = computed(() => [
  { value: 'aria2c' as const, label: t('settings.downloadMethodAria2c') },
  { value: 'urllib' as const, label: t('settings.downloadMethodUrllib') },
])

const { buildProxyTestSuggestion, testProxyConnection, formatConnectionInfo, resetProxyTest } = makeProxyTest({
  t, invoke, proxyMode, proxyUrl, proxyBypass, downloadSource,
  proxyTestStatus, proxyTestMessage, proxyTestSuggestion, proxyTestElapsed,
})
const settingsSections = computed(() => [
  { key: 'appearance' as const, label: t('settings.appearance'), icon: ColorPaletteOutline, hint: t('settings.appearanceNavHint') },
  { key: 'runtime' as const, label: t('settings.runtime'), icon: TerminalOutline, hint: t('settings.runtimeDesc') },
  { key: 'paths' as const, label: t('settings.dataDir'), icon: FolderOpenOutline, hint: t('settings.pathsNavHint') },
  { key: 'defaults' as const, label: t('settings.defaults'), icon: SettingsOutline, hint: t('settings.defaultsNavHint') },
  { key: 'about' as const, label: t('settings.about'), icon: InformationCircleOutline, hint: t('settings.aboutNavHint') },
])
const pymssCoreVersion = computed(() => {
  if (!app.envInfo) return t('settings.envNotChecked')
  if (!app.envInfo.pymssAvailable) return t('common.notAvailable')
  return app.envInfo.pymssVersion || t('common.unknown')
})
const workerVersion = computed(() => app.envInfo?.workerVersion || t('common.unknown'))
const buildCommitShort = computed(() => buildInfo.value?.gitCommit ? buildInfo.value.gitCommit.slice(0, 7) : '')
const buildRunLabel = computed(() => {
  const info = buildInfo.value
  if (!info?.runId) return ''
  return info.runAttempt ? `#${info.runId}.${info.runAttempt}` : `#${info.runId}`
})
const buildFingerprint = computed(() => {
  const info = buildInfo.value
  if (!info) return t('common.unknown')
  const parts = [info.gitTag || info.gitRef, buildCommitShort.value, info.variant, buildRunLabel.value, info.repository].filter(Boolean)
  return parts.length ? parts.join(' · ') : t('settings.buildFingerprintUnavailable')
})

function isOfficialRepositoryBuild(info: BuildInfo) {
  return info.official
    && info.repository === officialRepository
    && info.repositoryOwner === officialRepositoryOwner
    && Boolean(info.gitCommit && info.runId)
}
const buildVerification = computed(() => {
  const info = buildInfo.value
  if (!info) {
    return {
      label: t('settings.buildStatusUnknown'),
      tone: 'unknown',
      description: t('settings.buildStatusUnknownDesc'),
    }
  }
  if (isOfficialRepositoryBuild(info)) {
    return {
      label: t('settings.buildStatusOfficial'),
      tone: 'official',
      description: t('settings.buildStatusOfficialDesc'),
    }
  }
  if (info.gitCommit && info.runId) {
    return {
      label: t('settings.buildStatusCi'),
      tone: 'development',
      description: t('settings.buildStatusCiDesc'),
    }
  }
  return {
    label: t('settings.buildStatusDevelopment'),
    tone: 'development',
    description: t('settings.buildStatusDevelopmentDesc'),
  }
})
const aboutVersionItems = computed(() => [
  { label: t('settings.softwareVersion'), value: appVersion.value, meta: 'MediaForge' },
  { label: t('settings.pymssCoreVersion'), value: pymssCoreVersion.value, meta: t('settings.coreRuntime') },
  { label: t('settings.workerVersion'), value: workerVersion.value, meta: t('settings.pythonWorker') },
])
const runtimeSummaryItems = computed(() => [
  { label: t('settings.runtimeCurrentLabel'), value: runtimeCurrentLabel.value, meta: t('settings.runtimeCurrentMeta') },
  { label: 'Torch', value: app.runtimeInfo?.torchVersion || app.envInfo?.torchVersion || t('common.unknown'), meta: t('settings.runtimeTorchMeta') },
  { label: 'pymss', value: app.runtimeInfo?.pymssVersion || app.envInfo?.pymssVersion || t('common.unknown'), meta: t('settings.runtimePymssMeta') },
])
const aboutLinks = computed(() => [
  { label: t('settings.desktopRepository'), url: repoUrl, icon: LogoGithub },
  { label: t('settings.coreRepository'), url: coreRepoUrl, icon: LinkOutline },
  { label: t('settings.licenseLink'), url: licenseUrl, icon: DocumentTextOutline },
  { label: t('settings.coreLicenseLink'), url: coreLicenseUrl, icon: DocumentTextOutline },
])

async function loadBuildInfo() {
  try {
    buildInfo.value = await invoke<BuildInfo>('get_build_info')
    app.buildInfoVersion = buildInfo.value?.version || ''
    app.buildInfoVariant = buildInfo.value?.variant || ''
  } catch {
    buildInfo.value = null
  }
}

const languageSelectWrap = ref<HTMLElement | null>(null)
const {
  scaleFactorPercent, scaleSliderIndex, scaleSliderMarks, isDefaultScaleFactor,
  currentResolvedLanguageLabel, appearanceThemeAccentLabel,
  formatScaleFactorLabel, isSameScaleFactor, updateScaleFactor, resetScaleFactorToDefault,
  getEventOrigin, getElementOrigin, selectThemeMode, selectThemeAccent, selectLocale,
} = makeAppearance({
  computed, t, scaleFactor, themeMode, themeAccent, locale, currentLocale, animationsEnabled,
  languageSelectWrap, normalizeScaleFactor, DEFAULT_SCALE_FACTOR,
  applyTheme, setLocale, runRippleViewTransition,
})
const maxConcurrentSeparationsInput = computed({
  get: () => normalizeConcurrentSeparations(maxConcurrentSeparations.value),
  set: (value) => {
    maxConcurrentSeparations.value = normalizeConcurrentSeparations(value)
  },
})
const dataDirEntries = computed(() => [
  { key: 'settings.editorProjectsDir', value: editorProjectsDir.value, fallback: 'editor-projects' },
  { key: 'settings.settingsDir', value: settingsDir.value, fallback: 'settings' },
  { key: 'settings.logsDir', value: logsDir.value, fallback: 'logs' },
])
const hasActiveModelDirUsage = computed(() => {
  const hasRunningWorkerTask = activeWorkerTasks.value.length > 0
  const hasDownloadingModel = Object.values(downloadTasks.value).some((item) => ['preparing', 'downloading'].includes(item.status))
  return hasRunningWorkerTask || hasDownloadingModel || isModelDirMigrating.value
})
const isCheckingModelDir = ref(false)
const {
  modelDirMigrationVisible, modelDirMigrationProgress, modelDirMigrationHasConflict,
  modelDirMigrationHasResult, changeModelDir, changeOutputDir,
  closeModelDirMigrationDialog, confirmModelDirMigration, resolveModelDirConflict,
} = makeModelDirMigration({
  computed, t, settings, message, modelDirMigrationState,
  hasActiveModelDirUsage, isCheckingModelDir,
})


async function revealPath(path: string) {
  try {
    await task.revealPath(path)
  } catch (error) {
    message.error(error instanceof Error ? error.message : String(error))
  }
}


async function openExternalUrl(url: string) {
  try {
    await open(url)
  } catch (error) {
    message.error(error instanceof Error ? error.message : String(error))
  }
}



onMounted(async () => {
  await loadBuildInfo()
})

</script>

<template>
  <div class="page mf-page page--settings" data-tone="blue">
    <MediaPageHeader :title="t('settings.title')" :subtitle="t('settings.subtitle')" :icon="SettingsOutline" eyebrow="SETTINGS" tone="blue" />

    <div class="settings-shell">
      <aside class="settings-sidebar" :aria-label="t('settings.settingsNavigation')">
        <button
          v-for="section in settingsSections"
          :key="section.key"
          type="button"
          class="settings-nav-item"
          :class="{ active: activeSection === section.key }"
          @click="selectSettingsSection(section.key)"
        >
          <span class="settings-nav-item__icon"><n-icon :component="section.icon" size="18" /></span>
          <span class="settings-nav-item__copy">
            <strong>{{ section.label }}</strong>
            <small>{{ section.hint }}</small>
          </span>
        </button>
      </aside>

      <main class="settings-content">
        <section v-if="activeSection === 'about'" class="about-panel">
          <article class="about-hero">
            <div class="about-hero__main">
              <div class="about-logo-wrap">
                <img class="about-logo" :src="appLogo" alt="MediaForge" />
              </div>
              <div class="about-hero__copy">
                <span class="about-eyebrow">{{ t('settings.aboutEyebrow') }}</span>
                <h2 class="about-hero__title">MediaForge</h2>
              </div>
            </div>

            <div class="about-stats" :aria-label="t('settings.about')">
              <div v-for="item in aboutVersionItems" :key="item.label" class="about-stat">
                <span class="about-stat__label">{{ item.label }}</span>
                <strong class="about-stat__value">{{ item.value }}</strong>
                <small class="about-stat__meta">{{ item.meta }}</small>
              </div>
            </div>
          </article>

          <article class="about-build-card" :class="`about-build-card--${buildVerification.tone}`">
            <div class="about-build-card__head">
              <span>{{ t('settings.officialVerification') }}</span>
              <strong>{{ buildVerification.label }}</strong>
            </div>
            <p>{{ buildVerification.description }}</p>
            <div class="about-build-fingerprint">
              <span>{{ t('settings.buildFingerprint') }}</span>
              <code>{{ buildFingerprint }}</code>
            </div>
          </article>

          <article class="about-info-card about-guide-card" data-guide="guide-help">
            <div class="section-title section-title--plain">
              <span class="section-title__icon"><n-icon :component="CompassOutline" size="18" /></span>
              <span>{{ t('beginnerGuide.label') }}</span>
            </div>
            <div class="about-guide-card__actions">
              <n-button v-if="settings.beginnerGuideProgress.status === 'active'" type="primary" @click="beginnerGuide.start()">
                <template #icon><n-icon :component="ChevronForwardOutline" /></template>
                {{ t('beginnerGuide.resume') }}
              </n-button>
              <n-button type="primary" secondary @click="beginnerGuide.start(true)">
                <template #icon><n-icon :component="RefreshOutline" /></template>
                {{ t('beginnerGuide.restart') }}
              </n-button>
              <n-button secondary @click="showParameterHelp = true">
                <template #icon><n-icon :component="OptionsOutline" /></template>
                {{ t('parameterHelp.label') }}
              </n-button>
            </div>
          </article>

          <div class="about-detail-grid">
            <article class="about-info-card about-info-card--license">
              <div class="section-title section-title--plain">
                <span class="section-title__icon">
                  <n-icon :component="DocumentTextOutline" size="18" />
                </span>
                <span>{{ t('settings.license') }}</span>
              </div>
              <div class="license-stack">
                <div class="license-row">
                  <span>{{ t('settings.desktopLicense') }}</span>
                  <strong>{{ desktopLicense }}</strong>
                </div>
                <div class="license-row">
                  <span>{{ t('settings.coreLicense') }}</span>
                  <strong>{{ coreLicense }}</strong>
                </div>
              </div>
            </article>

            <article class="about-info-card about-info-card--links">
              <div class="section-title section-title--plain">
                <span class="section-title__icon">
                  <n-icon :component="LinkOutline" size="18" />
                </span>
                <span>{{ t('settings.relatedLinks') }}</span>
              </div>
              <div class="about-link-list">
                <button
                  v-for="link in aboutLinks"
                  :key="link.url"
                  type="button"
                  class="about-link-item"
                  @click="openExternalUrl(link.url)"
                >
                  <span class="about-link-item__icon"><n-icon :component="link.icon" size="18" /></span>
                  <span class="about-link-item__label">{{ link.label }}</span>
                  <n-icon class="about-link-item__open" :component="OpenOutline" size="16" />
                </button>
              </div>
            </article>
            <VersionUpdateCard />
          </div>
        </section>

        <!-- Appearance -->
        <section v-else-if="activeSection === 'appearance'" class="settings-section-panel">
        <n-card class="settings-card settings-card--compact settings-card--appearance" :bordered="true" size="small">
          <template #header>
            <div class="section-title">
              <span class="section-title__icon">
                <n-icon :component="ColorPaletteOutline" size="18" />
              </span>
              <span>{{ t('settings.appearance') }}</span>
            </div>
          </template>

          <div class="appearance-list">
            <p class="appearance-hint">{{ t('settings.appearanceHint') }}</p>

            <div class="setting-row">
              <div class="setting-row__label">
                <label class="setting-row__title">{{ t('settings.theme') }}</label>
              </div>
              <div class="setting-row__control">
                <div class="theme-switcher">
                  <button
                    type="button"
                    :class="{ active: themeMode === 'system' }"
                    @click="selectThemeMode('system', $event)"
                  >
                    {{ t('settings.themeSystem') }}
                  </button>
                  <button
                    type="button"
                    :class="{ active: themeMode === 'dark' }"
                    @click="selectThemeMode('dark', $event)"
                  >
                    {{ t('settings.themeDark') }}
                  </button>
                  <button
                    type="button"
                    :class="{ active: themeMode === 'light' }"
                    @click="selectThemeMode('light', $event)"
                  >
                    {{ t('settings.themeLight') }}
                  </button>
                </div>
              </div>
            </div>

            <div class="setting-row">
              <div class="setting-row__label">
                <label class="setting-row__title">{{ t('settings.themeColor') }}</label>
              </div>
              <div class="setting-row__control">
                <div class="accent-dots">
                  <button
                    v-for="accent in themeAccentOptions"
                    :key="accent.value"
                    type="button"
                    class="accent-dot"
                    :class="{ active: themeAccent === accent.value }"
                    :title="accent.label"
                    :aria-label="accent.label"
                    @click="selectThemeAccent(accent.value, $event)"
                  >
                    <span
                      class="accent-dot__fill"
                      :style="{ background: `linear-gradient(135deg, ${accent.preview[0]} 0 50%, ${accent.preview[1]} 50% 100%)` }"
                    />
                  </button>
                  <span class="accent-current">{{ appearanceThemeAccentLabel }}</span>
                </div>
              </div>
            </div>

            <div class="setting-row">
              <div class="setting-row__label">
                <label class="setting-row__title">{{ t('settings.language') }}</label>
                <p v-if="locale === SYSTEM_LOCALE" class="setting-row__hint">
                  {{ t('settings.languageFollowSystemHint', { locale: currentResolvedLanguageLabel }) }}
                </p>
              </div>
              <div class="setting-row__control">
                <div ref="languageSelectWrap" class="language-select-wrap">
                  <n-select
                    :value="locale"
                    :options="languageOptions"
                    :consistent-menu-width="false"
                    @update:value="selectLocale"
                  />
                </div>
              </div>
            </div>

            <div class="setting-row">
              <div class="setting-row__label">
                <label class="setting-row__title">{{ t('settings.scaleFactor') }}</label>
                <button
                  type="button"
                  class="scale-reset"
                  :disabled="isDefaultScaleFactor"
                  @click="resetScaleFactorToDefault"
                >
                  {{ t('settings.restoreDefaultScale') }}
                </button>
              </div>
              <div class="setting-row__control">
                <div class="scale-control">
                  <n-slider
                    v-model:value="scaleSliderIndex"
                    :min="0"
                    :max="5"
                    :step="1"
                    :marks="scaleSliderMarks"
                    :tooltip="false"
                  />
                  <span class="scale-value">{{ scaleFactorPercent }}</span>
                </div>
              </div>
            </div>

            <div class="setting-row">
              <div class="setting-row__label">
                <label class="setting-row__title">{{ t('settings.animations') }}</label>
                <p class="setting-row__hint">{{ t('settings.animationsHint') }}</p>
              </div>
              <div class="setting-row__control">
                <n-switch v-model:value="animationsEnabled" />
              </div>
            </div>
          </div>
        </n-card>
        </section>

        <!-- Runtime -->
        <section v-else-if="activeSection === 'runtime'" class="settings-section-panel runtime-section">
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
            <Transition name="runtime-content" mode="out-in" @after-enter="locateRuntimeBackend">
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

                <div v-if="card.installing" class="runtime-env-card__progress">
                  <n-spin size="small" />
                  <span class="runtime-env-card__progress-msg" :title="app.runtimeInstallMessage">{{ app.runtimeInstallMessage }}</span>
                  <div class="runtime-env-card__progress-actions">
                    <n-button size="tiny" tertiary @click="runtimeLogDialogVisible = true">
                      <template #icon><n-icon :component="DocumentTextOutline" /></template>
                      {{ t('settings.runtimeShowInstallLog') }}
                    </n-button>
                    <n-button size="tiny" tertiary @click="cancelRuntimeInstall">
                      {{ t('common.cancel') }}
                    </n-button>
                  </div>
                </div>
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
                    @click="runtimeLogDialogVisible = true"
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
              <n-button size="small" tertiary @click="runtimeLogDialogVisible = true">
                <template #icon><n-icon :component="DocumentTextOutline" /></template>
                {{ t('settings.runtimeShowInstallLog') }}
              </n-button>
            </div>
            </div>
          </article>
        </section>

        <!-- Paths -->
        <section v-else-if="activeSection === 'paths'" class="settings-section-panel">
        <n-card class="settings-card settings-card--feature settings-card--paths" :bordered="true" size="small">
          <template #header>
            <div class="section-title">
              <span class="section-title__icon">
                <n-icon :component="FolderOpenOutline" size="18" />
              </span>
              <span>{{ t('settings.dataDir') }}</span>
            </div>
          </template>

          <div class="path-panel">
            <div class="path-panel__intro">{{ t('settings.pathsHint') }}</div>

            <div class="path-root-block">
              <div class="path-root-row">
                <code class="path-root" :title="dataRoot || t('common.notSet')">{{ dataRoot || t('common.notSet') }}</code>
                <n-button secondary size="small" :disabled="!dataRoot" @click="revealPath(dataRoot)">
                  {{ t('common.open') }}
                </n-button>
              </div>
            </div>

            <div class="path-primary-grid" data-guide="settings-paths">
              <div class="path-item path-item--primary">
                <div class="path-item__head">
                  <div class="path-item__head-copy">
                    <strong>{{ t('settings.modelDir') }}</strong>
                  </div>
                  <div class="path-item__actions">
                    <n-tag v-if="hasActiveModelDirUsage && !isModelDirMigrating" :bordered="false" size="small" type="warning">
                      {{ t('settings.modelDirInUse') }}
                    </n-tag>
                    <n-button
                      secondary
                      type="primary"
                      size="small"
                      :loading="isCheckingModelDir"
                      :disabled="hasActiveModelDirUsage || isCheckingModelDir"
                      @click="changeModelDir"
                    >
                      <template #icon><n-icon :component="SwapHorizontalOutline" /></template>
                      {{ t('settings.changeModelDir') }}
                    </n-button>
                  </div>
                </div>
                <code class="path-item__value" :title="modelDir || t('common.notSet')">{{ modelDir || t('common.notSet') }}</code>
              </div>

              <div class="path-item path-item--primary">
                <div class="path-item__head">
                  <div class="path-item__head-copy">
                    <strong>{{ t('settings.outputDir') }}</strong>
                  </div>
                  <div class="path-item__actions">
                    <n-button secondary type="primary" size="small" @click="changeOutputDir">
                      <template #icon><n-icon :component="FolderOpenOutline" /></template>
                      {{ t('settings.changeOutputDir') }}
                    </n-button>
                  </div>
                </div>
                <code class="path-item__value" :title="outputDir || t('common.notSet')">{{ outputDir || t('common.notSet') }}</code>
              </div>
            </div>

            <div class="path-grid">
              <div v-for="entry in dataDirEntries" :key="entry.key" class="path-subcard">
                <span>{{ t(entry.key) }}</span>
                <code class="path-field__value" :title="entry.value || entry.fallback">{{ dirName(entry.value, entry.fallback) }}</code>
              </div>
            </div>
          </div>
        </n-card>
        </section>

        <!-- Defaults & Execution -->
        <section v-else-if="activeSection === 'defaults'" class="settings-section-panel">
        <n-card class="settings-card" :bordered="true" size="small">
          <template #header>
            <div class="section-title">
              <span class="section-title__icon">
                <n-icon :component="SettingsOutline" size="18" />
              </span>
              <span>{{ t('settings.defaults') }}</span>
            </div>
          </template>

          <div class="settings-merged-layout">
            <section class="settings-group">
              <div class="setting-field" data-guide="settings-device">
                <label class="text-muted text-sm">{{ t('settings.defaultDevice') }}</label>
                <n-select
                  v-model:value="defaultDevice"
                  :options="deviceOptions"
                />
              </div>
            </section>

            <section class="settings-group" data-guide="settings-downloads">
              <div class="setting-field">
                <label class="text-muted text-sm">{{ t('settings.downloadSource') }}</label>
                <n-select
                  v-model:value="downloadSource"
                  :options="[
                    { label: 'ModelScope', value: 'modelscope' },
                    { label: 'Hugging Face', value: 'huggingface' },
                    { label: 'HF Mirror', value: 'hf-mirror' },
                  ]"
                />
              </div>
              <div class="setting-field">
                <label class="text-muted text-sm">{{ t('settings.downloadMethod') }}</label>
                <n-select
                  v-model:value="downloadMethod"
                  :options="downloadMethodOptions"
                />
                <p class="text-muted text-sm setting-field__hint">{{ t('settings.downloadMethodHint') }}</p>
              </div>
            </section>

            <section class="settings-group">
              <div class="setting-field">
                <label class="text-muted text-sm">{{ t('settings.proxyMode') }}</label>
                <n-select
                  v-model:value="proxyMode"
                  :options="proxyModeOptions"
                  @update:value="resetProxyTest"
                />
                <p class="text-muted text-sm setting-field__hint">{{ t('settings.proxyModeHint') }}</p>
              </div>
              <div v-if="proxyMode === 'custom'" class="proxy-custom-grid">
                <div class="setting-field">
                  <label class="text-muted text-sm">{{ t('settings.proxyUrl') }}</label>
                  <n-input
                    v-model:value="proxyUrl"
                    :placeholder="t('settings.proxyUrlPlaceholder')"
                    clearable
                    size="small"
                    @update:value="resetProxyTest"
                  />
                </div>
                <div class="setting-field">
                  <label class="text-muted text-sm">{{ t('settings.proxyBypass') }}</label>
                  <n-input
                    v-model:value="proxyBypass"
                    :placeholder="t('settings.proxyBypassPlaceholder')"
                    size="small"
                  />
                </div>
              </div>
              <div class="setting-field">
                <label class="text-muted text-sm">{{ t('settings.proxyTest') }}</label>
                <div class="proxy-test-row">
                  <n-button
                    size="small"
                    secondary
                    :loading="proxyTestLoading"
                    :disabled="proxyMode === 'custom' && !proxyUrl.trim()"
                    @click="testProxyConnection"
                  >
                    {{ t('settings.proxyTest') }}
                  </n-button>
                  <span v-if="proxyTestStatus === 'success'" class="proxy-test-info">
                    <n-icon :component="CheckmarkCircleOutline" size="14" color="var(--success)" />
                    <span class="proxy-test-info__text">{{ proxyTestMessage }}</span>
                  </span>
                  <n-tag
                    v-else-if="proxyTestStatus === 'error'"
                    size="small"
                    type="error"
                    round
                    :bordered="false"
                  >{{ t('settings.proxyTestFailed') }}</n-tag>
                  <n-tag
                    v-else-if="proxyTestStatus === 'testing'"
                    size="small"
                    :bordered="false"
                  >{{ t('settings.proxyTesting') }}</n-tag>
                </div>
                <div v-if="proxyTestStatus === 'error' && proxyTestMessage" class="proxy-test-error">
                  <div class="proxy-test-error__head">
                    <n-icon :component="AlertCircleOutline" size="14" color="var(--danger)" />
                    <span>{{ proxyTestMessage }}</span>
                  </div>
                  <p v-if="proxyTestSuggestion" class="proxy-test-error__tip">{{ proxyTestSuggestion }}</p>
                </div>
              </div>
            </section>

            <section class="settings-group settings-group--soft">
              <div class="settings-group__head">
                <span class="settings-group__icon">
                  <n-icon :component="SpeedometerOutline" size="16" />
                </span>
                <span>{{ t('settings.execution') }}</span>
              </div>
              <div class="setting-field">
                <label class="text-muted text-sm">{{ t('settings.maxConcurrentSeparations') }}</label>
                <n-input-number
                  v-model:value="maxConcurrentSeparationsInput"
                  :min="1"
                  :max="settings.MAX_CONCURRENT_SEPARATIONS"
                  :precision="0"
                  :step="1"
                  clearable
                  style="width: 100%;"
                />
                <p class="text-muted text-sm setting-field__hint">
                  {{ t('settings.maxConcurrentSeparationsHint') }}
                </p>
              </div>
            </section>

            <section class="settings-group settings-group--soft">
              <div class="settings-group__head">
                <span class="settings-group__icon">
                  <n-icon :component="TerminalOutline" size="16" />
                </span>
                <span>{{ t('settings.developerMode') }}</span>
              </div>
              <div class="setting-field setting-field--switch">
                <div class="setting-switch-row">
                  <div class="setting-switch-row__copy">
                    <label class="text-muted text-sm">{{ t('settings.developerModeTitle') }}</label>
                    <p class="text-muted text-sm setting-field__hint">
                      {{ t('settings.developerModeHint') }}
                    </p>
                  </div>
                  <n-switch v-model:value="developerMode" />
                </div>
              </div>
            </section>
          </div>
        </n-card>
        </section>
      </main>
    </div>

    <n-modal
      :show="isCheckingModelDir"
      style="width:min(420px, 88vw)"
      preset="card"
      :title="t('settings.modelDirCheckingTitle')"
      :mask-closable="false"
      :closable="false"
    >
      <div class="checking-dialog">
        <n-spin size="large" />
        <p class="checking-dialog__text">{{ t('settings.modelDirCheckingHint') }}</p>
      </div>
    </n-modal>

    <n-modal
      :show="modelDirMigrationState.status === 'confirm'"
      style="width:min(720px, 92vw)"
      preset="card"
      :title="t('settings.modelDirMigrationConfirmTitle')"
      :mask-closable="false"
      :closable="false"
      @close="closeModelDirMigrationDialog"
    >
      <div class="migration-dialog">
        <p class="migration-dialog__lead">{{ t('settings.modelDirMigrationConfirmLead') }}</p>
        <div class="migration-summary-grid">
          <div class="migration-summary-card migration-summary-card--path">
            <span>{{ t('settings.modelDirMigrationSource') }}</span>
            <code :title="modelDirMigrationState.sourceModelDir">{{ modelDirMigrationState.sourceModelDir }}</code>
          </div>
          <div class="migration-summary-card migration-summary-card--path">
            <span>{{ t('settings.modelDirMigrationTarget') }}</span>
            <code :title="modelDirMigrationState.targetModelDir">{{ modelDirMigrationState.targetModelDir }}</code>
          </div>
          <div class="migration-summary-card">
            <span>{{ t('settings.modelDirMigrationFileCount') }}</span>
            <strong>{{ modelDirMigrationState.totalFiles }}</strong>
          </div>
          <div class="migration-summary-card">
            <span>{{ t('settings.modelDirMigrationTotalSize') }}</span>
            <strong>{{ formatBytes(modelDirMigrationState.totalBytes) }}</strong>
          </div>
        </div>

        <n-alert v-if="modelDirMigrationState.preparation?.diskInsufficient" type="error" :show-icon="true" style="margin-top: 12px">
          {{ t('settings.modelDirMigrationDiskInsufficientHint', { available: formatBytes(modelDirMigrationState.preparation?.diskAvailableBytes ?? 0), needed: formatBytes(modelDirMigrationState.totalBytes) }) }}
        </n-alert>
        <n-alert v-else type="warning" :show-icon="true" style="margin-top: 12px">
          {{ t('settings.modelDirMigrationCloseWarning') }}
        </n-alert>
        <n-alert v-if="(modelDirMigrationState.preparation?.conflictCount || 0) > 0" type="info" :show-icon="true" style="margin-top: 8px">
          {{ t('settings.modelDirMigrationConflictHint', { count: modelDirMigrationState.preparation?.conflictCount || 0 }) }}
        </n-alert>
      </div>
      <template #footer>
        <div class="migration-dialog__footer">
          <n-button @click="closeModelDirMigrationDialog">{{ t('common.cancel') }}</n-button>
          <n-button type="primary" :disabled="!!modelDirMigrationState.preparation?.diskInsufficient" @click="confirmModelDirMigration">{{ t('settings.modelDirMigrationStart') }}</n-button>
        </div>
      </template>
    </n-modal>

    <n-modal
      :show="modelDirMigrationVisible"
      style="width:min(760px, 92vw)"
      preset="card"
      :title="t('settings.modelDirMigrationTitle')"
      :mask-closable="false"
      :closable="modelDirMigrationHasResult"
      @close="closeModelDirMigrationDialog"
    >
      <div class="migration-dialog">
        <div class="migration-dialog__status">
          <div>
            <strong>{{ modelDirMigrationState.message || t('settings.modelDirMigrationPreparing') }}</strong>
            <p>{{ t('settings.modelDirMigrationProgressText', { completed: modelDirMigrationState.completedFiles, total: modelDirMigrationState.totalFiles }) }}</p>
          </div>
          <n-tag :bordered="false" size="small" :type="modelDirMigrationState.status === 'failed' ? 'error' : modelDirMigrationState.status === 'success' ? 'success' : modelDirMigrationState.status === 'aborted' ? 'default' : modelDirMigrationHasConflict ? 'warning' : 'info'">
            {{ modelDirMigrationState.status }}
          </n-tag>
        </div>

        <n-progress
          type="line"
          :percentage="modelDirMigrationProgress"
          :show-indicator="true"
          :height="12"
          :border-radius="8"
          status="default"
        />

        <div class="migration-progress-meta">
          <span>{{ t('settings.modelDirMigrationByteProgress', { copied: formatBytes(modelDirMigrationState.copiedBytes), total: formatBytes(modelDirMigrationState.totalBytes) }) }}</span>
          <span>{{ t('settings.modelDirMigrationProgressText', { completed: modelDirMigrationState.completedFiles, total: modelDirMigrationState.totalFiles }) }}</span>
        </div>

        <div class="migration-summary-grid">
          <div class="migration-summary-card">
            <span>{{ t('settings.modelDirMigrationSource') }}</span>
            <code>{{ modelDirMigrationState.sourceModelDir }}</code>
          </div>
          <div class="migration-summary-card">
            <span>{{ t('settings.modelDirMigrationTarget') }}</span>
            <code>{{ modelDirMigrationState.targetModelDir }}</code>
          </div>
        </div>

        <div v-if="modelDirMigrationState.currentPath" class="migration-current-path">
          <span>{{ t('settings.modelDirMigrationCurrentFile') }}</span>
          <code>{{ modelDirMigrationState.currentPath }}</code>
        </div>

        <n-alert v-if="modelDirMigrationHasConflict && modelDirMigrationState.conflict" type="warning" :show-icon="true">
          <template #header>{{ t('settings.modelDirMigrationConflictTitle') }}</template>
          <div class="migration-conflict">
            <p>{{ t('settings.modelDirMigrationConflictPrompt') }}</p>
            <code>{{ modelDirMigrationState.conflict.destinationPath }}</code>
            <small>{{ t('settings.modelDirMigrationConflictApplyAll') }}</small>
          </div>
        </n-alert>

        <n-alert v-if="modelDirMigrationState.status === 'failed'" type="error" :show-icon="true">
          {{ modelDirMigrationState.error || t('settings.modelDirMigrationFailedHint') }}
        </n-alert>

        <n-alert v-if="modelDirMigrationState.status === 'aborted'" type="default" :show-icon="true">
          {{ t('settings.modelDirMigrationAbortedHint') }}
        </n-alert>

        <n-alert v-if="modelDirMigrationState.status === 'success' && modelDirMigrationState.cleanupFailedFiles.length" type="warning" :show-icon="true">
          {{ t('settings.modelDirMigrationCleanupFailedHint', { count: modelDirMigrationState.cleanupFailedFiles.length }) }}
        </n-alert>
      </div>
      <template #footer>
        <div class="migration-dialog__footer">
          <template v-if="modelDirMigrationHasConflict">
            <n-button :loading="modelDirMigrationState.resolvingConflict" @click="resolveModelDirConflict('skip')">
              {{ t('settings.modelDirMigrationSkip') }}
            </n-button>
            <n-button type="warning" :loading="modelDirMigrationState.resolvingConflict" @click="resolveModelDirConflict('overwrite')">
              {{ t('settings.modelDirMigrationOverwrite') }}
            </n-button>
            <n-button type="error" secondary :loading="modelDirMigrationState.resolvingConflict" @click="resolveModelDirConflict('abort')">
              {{ t('settings.modelDirMigrationAbort') }}
            </n-button>
          </template>
          <template v-else-if="modelDirMigrationHasResult">
            <n-button type="primary" @click="closeModelDirMigrationDialog">{{ t('common.confirm') }}</n-button>
          </template>
          <template v-else>
            <n-button disabled>{{ t('settings.modelDirMigrationCloseBlocked') }}</n-button>
          </template>
        </div>
      </template>
    </n-modal>

    <n-modal
      v-model:show="runtimeLogDialogVisible"
      style="width:min(760px, 92vw)"
      preset="card"
      :title="t('settings.runtimeInstallLogTitle')"
    >
      <div class="runtime-log-dialog">
        <div v-if="runtimeInstalling" class="runtime-log-dialog__status">
          <n-spin size="small" />
          <span>{{ app.runtimeInstallMessage }}</span>
        </div>
        <n-alert v-else-if="app.runtimeInstallStatus === 'error'" type="error" :show-icon="true" style="margin-bottom: 8px">
          {{ app.runtimeInstallMessage }}
        </n-alert>
        <n-alert v-else-if="app.runtimeInstallStatus === 'success'" type="success" :show-icon="true" style="margin-bottom: 8px">
          {{ t('settings.runtimeInstallLogSuccess') }}
        </n-alert>
        <div v-if="app.runtimeInstallLogs.length" ref="runtimeLogPre" class="runtime-log-dialog__pre"><TaskLogConsole :lines="app.runtimeInstallLogs" embedded /></div>
        <div v-if="app.runtimeInfo?.logPath" class="runtime-log-dialog__path">
          <code>{{ app.runtimeInfo.logPath }}</code>
          <n-button size="tiny" tertiary @click="revealPath(app.runtimeInfo.logPath)">
            {{ t('settings.runtimeOpenInstallLog') }}
          </n-button>
        </div>
        <p v-if="!app.runtimeInstallLogs.length && !app.runtimeInfo?.logPath" class="runtime-log-dialog__empty">
          {{ t('settings.runtimeInstallLogEmpty') }}
        </p>
      </div>
    </n-modal>
    <InferenceParameterHelp v-model:show="showParameterHelp" :model-type="modelStore.models.find(entry => entry.name === modelStore.selectedModel)?.modelType" />
  </div>
</template>

<style scoped src="./settings/settings-view.css"></style>
