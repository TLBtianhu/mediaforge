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
import { SYSTEM_LOCALE, setLocale } from '@/i18n'
import { useSettingsStore } from '@/stores/settings'
import { isTauriRuntime } from '@/utils/appStore'
import { useAppStore } from '@/stores/app'
import { makeRuntimeState } from './settings/runtimeState'
import { makeRuntimeActions } from './settings/runtimeActions'
import { makeProxyTest } from './settings/proxyTest'
import { makeAppearance } from './settings/appearance'
import { makeModelDirMigration } from './settings/modelDirMigration'
import { useModelStore } from '@/stores/model'
import { useTaskStore } from '@/stores/task'
import { normalizeConcurrentSeparations } from '@/features/tasks/concurrency'
import { formatBytes } from '@/utils/format'
import { DEFAULT_SCALE_FACTOR, normalizeScaleFactor } from '@/utils/appZoom'
import {
  applyTheme,
  getThemeAccentPreview,
  resolvedIsDark,
  runRippleViewTransition,
  THEME_ACCENTS,
} from '@/utils/theme'
import {
  ColorPaletteOutline,
  FolderOpenOutline,
  TerminalOutline,
  SettingsOutline,
  LogoGithub,
  InformationCircleOutline,
  DocumentTextOutline,
  LinkOutline,
} from '@vicons/ionicons5'
import SettingsAboutPanel from '@/components/settings/SettingsAboutPanel.vue'
import SettingsPathsPanel from '@/components/settings/SettingsPathsPanel.vue'
import SettingsAppearancePanel from '@/components/settings/SettingsAppearancePanel.vue'
import SettingsDefaultsPanel from '@/components/settings/SettingsDefaultsPanel.vue'
import SettingsRuntimePanel from '@/components/settings/SettingsRuntimePanel.vue'

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
  runtimeCurrentLabel, installedRuntimes,
  runtimeBackendCatalog, runtimeBackendCards, runtimeCoreVersionLabel,
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
  installBackend, retryRuntimeInstall, refreshRuntimeStatus,
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

const { testProxyConnection, resetProxyTest } = makeProxyTest({
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

function setLanguageSelectWrap(element: HTMLElement | null) {
  languageSelectWrap.value = element
}

function setScaleSliderIndex(index: number) {
  scaleSliderIndex.value = index
}

function setAnimationsEnabled(enabled: boolean) {
  animationsEnabled.value = enabled
}
const {
  scaleFactorPercent, scaleSliderIndex, scaleSliderMarks, isDefaultScaleFactor,
  currentResolvedLanguageLabel, appearanceThemeAccentLabel,
  resetScaleFactorToDefault,
  selectThemeMode, selectThemeAccent, selectLocale,
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


const recoveringSettings = computed(() => settings.recoveryState.status === 'running')
const settingsRecoveryBackup = computed(() => settings.recoveryState.backupPath || '')
// Browser previews have no file to back up; only the desktop app can recover.
const settingsRecoverySupported = isTauriRuntime()

// The stored file is unsupported. Nothing is migrated or overwritten here: the
// user explicitly chooses to back up the original bytes and reset to defaults.
function requestSettingsRecovery() {
  if (recoveringSettings.value) return
  dialog.warning({
    title: t('settings.schemaRecoverTitle'),
    content: t('settings.schemaRecoverConfirm'),
    positiveText: t('settings.schemaRecover'),
    negativeText: t('common.cancel'),
    positiveButtonProps: { type: 'error' },
    onPositiveClick: async () => {
      if (recoveringSettings.value) return false
      try {
        const response = await settings.recoverSettings()
        if (response.recovered) message.success(t('settings.schemaRecovered', { path: settingsRecoveryBackup.value }))
        else message.info(t('settings.schemaRecoverNotNeeded'))
      } catch (error) {
        message.error(error instanceof Error ? error.message : String(error))
        return false
      }
      return true
    },
  })
}



onMounted(async () => {
  await loadBuildInfo()
})

</script>

<template>
  <div class="page mf-page page--settings" data-tone="blue">
    <MediaPageHeader :title="t('settings.title')" :subtitle="t('settings.subtitle')" :icon="SettingsOutline" eyebrow="SETTINGS" tone="blue" />

    <n-alert v-if="settings.loadError" type="error" class="settings-schema-alert" :title="t('settings.schemaRejectedTitle')">
      <p class="settings-schema-alert__hint">{{ t('settings.schemaRejectedHint') }}</p>
      <details class="settings-schema-alert__details">
        <summary>{{ t('common.technicalDetails') }}</summary>
        <pre>{{ settings.loadError }}</pre>
      </details>
      <div class="settings-schema-alert__actions">
        <n-button
          v-if="settingsRecoverySupported"
          type="error"
          size="small"
          :loading="recoveringSettings"
          @click="requestSettingsRecovery"
        >
          {{ t('settings.schemaRecover') }}
        </n-button>
        <span v-else class="settings-schema-alert__hint">{{ t('settings.schemaRecoverDesktopOnly') }}</span>
        <span v-if="settingsRecoveryBackup" class="settings-schema-alert__backup">
          {{ t('settings.schemaBackupPath', { path: settingsRecoveryBackup }) }}
        </span>
      </div>
    </n-alert>

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
        <SettingsAboutPanel
          v-if="activeSection === 'about'"
          :version-items="aboutVersionItems"
          :build-verification="buildVerification"
          :build-fingerprint="buildFingerprint"
          :guide-active="settings.beginnerGuideProgress.status === 'active'"
          :about-links="aboutLinks"
          :desktop-license="desktopLicense"
          :core-license="coreLicense"
          @start-guide="beginnerGuide.start($event)"
          @parameter-help="showParameterHelp = true"
          @open-link="openExternalUrl"
        />

        <!-- Appearance -->
        <SettingsAppearancePanel
          v-else-if="activeSection === 'appearance'"
          :theme-mode="themeMode"
          :theme-accent="themeAccent"
          :theme-accent-options="themeAccentOptions"
          :appearance-theme-accent-label="appearanceThemeAccentLabel"
          :locale="locale"
          :system-locale="SYSTEM_LOCALE"
          :current-resolved-language-label="currentResolvedLanguageLabel"
          :language-options="languageOptions"
          :scale-slider-index="scaleSliderIndex"
          :scale-slider-marks="scaleSliderMarks"
          :scale-factor-percent="scaleFactorPercent"
          :is-default-scale-factor="isDefaultScaleFactor"
          :animations-enabled="animationsEnabled"
          :set-language-select-wrap="setLanguageSelectWrap"
          @select-theme-mode="selectThemeMode"
          @select-theme-accent="selectThemeAccent"
          @select-locale="selectLocale"
          @reset-scale-factor="resetScaleFactorToDefault"
          @update-scale-slider-index="setScaleSliderIndex"
          @update-animations-enabled="setAnimationsEnabled"
        />

        <!-- Runtime -->
        <SettingsRuntimePanel
          v-else-if="activeSection === 'runtime'"
          v-model:runtime-mirror="runtimeMirror"
          :runtime-detecting="runtimeDetecting"
          :runtime-busy="runtimeBusy"
          :runtime-current-error="runtimeCurrentError"
          :runtime-current-label="runtimeCurrentLabel"
          :runtime-summary-items="runtimeSummaryItems"
          :runtime-backend-catalog="runtimeBackendCatalog"
          :runtime-backend-cards="runtimeBackendCards"
          :runtime-disk-measuring="runtimeDiskMeasuring"
          :runtime-disk-total-label="runtimeDiskTotalLabel"
          :runtime-installing="runtimeInstalling"
          :runtime-activating="runtimeActivating"
          :runtime-deleting="runtimeDeleting"
          :runtime-core-version-label="runtimeCoreVersionLabel"
          :detect-runtime="detectRuntime"
          :install-backend="installBackend"
          :activate-installed-runtime="activateInstalledRuntime"
          :delete-runtime="deleteRuntime"
          :cancel-runtime-install="cancelRuntimeInstall"
          :retry-runtime-install="retryRuntimeInstall"
          :reveal-path="revealPath"
          @show-logs="runtimeLogDialogVisible = true"
        />

        <!-- Paths -->
        <SettingsPathsPanel
          v-else-if="activeSection === 'paths'"
          :data-root="dataRoot"
          :model-dir="modelDir"
          :output-dir="outputDir"
          :data-dir-entries="dataDirEntries"
          :has-active-model-dir-usage="hasActiveModelDirUsage"
          :is-model-dir-migrating="isModelDirMigrating"
          :is-checking-model-dir="isCheckingModelDir"
          @reveal-path="revealPath"
          @change-model-dir="changeModelDir"
          @change-output-dir="changeOutputDir"
        />

        <!-- Defaults & Execution -->
        <SettingsDefaultsPanel
          v-else-if="activeSection === 'defaults'"
          v-model:default-device="defaultDevice"
          v-model:download-source="downloadSource"
          v-model:download-method="downloadMethod"
          v-model:proxy-mode="proxyMode"
          v-model:proxy-url="proxyUrl"
          v-model:proxy-bypass="proxyBypass"
          v-model:max-concurrent-separations-input="maxConcurrentSeparationsInput"
          v-model:developer-mode="developerMode"
          :device-options="deviceOptions"
          :download-method-options="downloadMethodOptions"
          :proxy-mode-options="proxyModeOptions"
          :max-concurrent-separations="settings.MAX_CONCURRENT_SEPARATIONS"
          :proxy-test-loading="proxyTestLoading"
          :proxy-test-status="proxyTestStatus"
          :proxy-test-message="proxyTestMessage"
          :proxy-test-suggestion="proxyTestSuggestion"
          :test-proxy-connection="testProxyConnection"
          :reset-proxy-test="resetProxyTest"
        />
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

<style scoped src="./settings/settings-shared.css"></style>
<style scoped src="./settings/settings-view.css"></style>
