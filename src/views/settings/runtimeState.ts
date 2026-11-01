/**
 * 设置页「运行时」区块的派生状态：当前后端、平台可选后端、每张卡片。
 *
 * 这里只做纯推导（读 app 的运行时快照 + 几个进行中的标记），副作用在
 * runtimeActions.ts。视图把 deps 传进来，测试直接实例化工厂，不再从 SFC 抠声明。
 */
import type { ComputedRef } from 'vue'
import type { InstalledRuntime, RuntimeBackend } from '../../stores/app.ts'
import {
  detectRuntimePlatform,
  isKnownRuntimeBackend,
  recommendedRuntimeBackend,
  runtimeBackendLabel as runtimeBackendName,
  runtimeEnvironmentForBackend,
  runtimeLoadError,
  runtimeManifestStatus,
  runtimeSizeHint,
} from '../../utils/runtime.ts'
import { formatBytes } from '../../utils/format.ts'

type Deps = {
  computed: <T>(source: () => T) => ComputedRef<T>
  t: (key: string, params?: Record<string, unknown>) => string
  app: Record<string, any>
  runtimeManualChecking: { value: boolean }
  runtimeActivating: { value: string | null }
  runtimeDeleting: { value: string | null }
}

export type BackendCardState = 'active' | 'installed' | 'not_installed'

export type BackendCardBase = {
  backend: RuntimeBackend
  label: string
  description: string
  offCatalog: boolean
}

export type RuntimeBackendCard = {
  backend: RuntimeBackend
  key: string
  label: string
  description: string
  offCatalog?: boolean
  env?: InstalledRuntime
  state: BackendCardState
  recommended: boolean
  manifestOutdated: boolean
  manifestNewer: boolean
  workflowGraphUnavailable: boolean
  health: string
  leftover: boolean
  leftoverLabel: string
  vendorMissing: boolean
  sizeHint: string
  diskLabel: string
  installing: boolean
  pymssVersion: string
  pymssCoreVersion: string
}

const RUNTIME_BACKEND_DESC_KEYS: Record<RuntimeBackend, string> = {
  cpu: 'onboarding.runtimeCpu',
  cuda: 'onboarding.runtimeCuda',
  rocm: 'onboarding.runtimeRocm',
  mlx: 'onboarding.runtimeMlx',
}

export function makeRuntimeState(deps: Deps) {
  const { computed, t, app } = deps

  const runtimeInstalling = computed(() => app.runtimeInstallStatus === 'installing')
  const runtimeDetecting = computed(() => app.runtimeInfoLoading || deps.runtimeManualChecking.value)
  // Runtime detection and install/switch/delete all access the runtime state, so only one may run at a time.
  const runtimeBusy = computed(() => runtimeDetecting.value || runtimeInstalling.value || deps.runtimeActivating.value !== null || deps.runtimeDeleting.value !== null)
  const runtimeCurrentError = computed(() => {
    const error = runtimeLoadError(app.runtimeInfo, app.envInfo)
    return error.toLowerCase().includes('winerror 1455') ? t('settings.runtimePageFileError') : error
  })
  const runtimeCurrentBackend = computed<RuntimeBackend | null>(() => {
    const detected = String(app.runtimeInfo?.torchBackend || app.envInfo?.torchBackend || '')
    if (detected.toLowerCase().startsWith('error:')) return null
    const recorded = String(app.runtimeInfo?.installedBackend || app.runtimeInfo?.installState?.backend || '')
    if (isKnownRuntimeBackend(recorded)) return recorded
    if (app.runtimeInstalledBackend && isKnownRuntimeBackend(app.runtimeInstalledBackend)) return app.runtimeInstalledBackend
    return isKnownRuntimeBackend(detected) ? detected : null
  })
  const runtimeCurrentLabel = computed(() => {
    if (runtimeCurrentError.value) return t('settings.runtimeLoadFailed')
    return runtimeCurrentBackend.value ? runtimeBackendName(runtimeCurrentBackend.value) : t('settings.envNotChecked')
  })
  const installedRuntimes = computed(() => app.runtimeInfo?.installedEnvironments || [])
  const runtimePlatform = computed(() => detectRuntimePlatform(app.runtimeInfo))
  const runtimeRecommendedBackend = computed(() => recommendedRuntimeBackend(app.runtimeInfo))

  function runtimeBackendDescription(backend: RuntimeBackend | string) {
    if (backend === 'rocm' && app.runtimeInfo?.platform === 'linux') return t('onboarding.runtimeRocmLinux')
    const key = RUNTIME_BACKEND_DESC_KEYS[backend as RuntimeBackend]
    return key ? t(key) : ''
  }

  const runtimeBackendCatalog = computed<BackendCardBase[]>(() => {
    const { isMac } = runtimePlatform.value
    const backends: RuntimeBackend[] = isMac ? ['mlx'] : ['cpu']
    if (!isMac) backends.push('cuda', 'rocm')
    return backends.map((backend) => ({
      backend,
      label: runtimeBackendName(backend),
      description: runtimeBackendDescription(backend),
      offCatalog: false,
    }))
  })

  const runtimeBackendCards = computed(() => {
    const catalog = [...runtimeBackendCatalog.value]
    // Keep environments that exist on disk but are not offered on this platform
    // (e.g. left over after moving the data root between machines) reachable.
    for (const entry of installedRuntimes.value as InstalledRuntime[]) {
      const backend = String(entry.backend || '')
      if (!backend || catalog.some((item) => item.backend === backend)) continue
      catalog.push({
        backend: backend as RuntimeBackend,
        label: runtimeBackendName(backend),
        description: runtimeBackendDescription(backend),
        offCatalog: true,
      })
    }
    const cards: RuntimeBackendCard[] = []
    for (const item of catalog) {
      const matchingEnvs = (installedRuntimes.value as InstalledRuntime[]).filter((entry) => entry.backend === item.backend)
      const preferredEnv = runtimeEnvironmentForBackend(app.runtimeInfo, item.backend)
      const envs = preferredEnv
        ? [preferredEnv, ...matchingEnvs.filter((entry) => entry !== preferredEnv)]
        : matchingEnvs
      const displayEnvs = envs.length ? envs : [undefined]

      for (const env of displayEnvs) {
        const isActive = Boolean(env && runtimeCurrentBackend.value === item.backend && runtimeEnvironmentForBackend(app.runtimeInfo, item.backend) === env)
        const state: BackendCardState = isActive ? 'active' : env ? 'installed' : 'not_installed'
        const diskBytes = app.runtimeEnvSizes[String(item.backend)]
        const pymssVersion = env?.pymssVersion || env?.packageVersions?.pymss || ''
        const pymssCoreVersion = env?.pymssCoreVersion || env?.packageVersions?.['pymss-core'] || ''
        const manifestStatus = runtimeManifestStatus(env, app.runtimeInfo?.manifestVersion)
        const manifestOutdated = manifestStatus === 'older' || manifestStatus === 'newer'
        const manifestNewer = manifestStatus === 'newer'
        const workflowGraphUnavailable = env?.pymssGraphAvailable === false
        const health = String(env?.health || 'unknown')
        // A manifest mismatch can mean that the environment was created by an
        // older app even when PyPI already reports the same pymss/core versions.
        // Offer the no-Torch-reinstall core sync so the environment metadata and
        // any manifest-declared pymss extras are brought up to date.
        const gpuBackend = item.backend === 'cuda' || item.backend === 'rocm' || item.backend === 'mlx'
        // A cancelled or failed install leaves its venv behind without an install state, so the
        // backend reads as not installed while still holding gigabytes. Surface it so the space
        // can be reclaimed instead of being stranded.
        const leftover = !env && app.runtimeIncompleteBackends.includes(String(item.backend))
        cards.push({
          ...item,
          key: `${item.backend}:${env?.pythonPath || 'none'}`,
          env,
          state,
          recommended: runtimeRecommendedBackend.value === item.backend,
          manifestOutdated,
          manifestNewer,
          workflowGraphUnavailable,
          health,
          leftover,
          leftoverLabel: leftover && diskBytes ? formatBytes(diskBytes) : '',
          // Only warn on GPU backends: it explains why a card is there without hiding it, and
          // detection missing a card must never stop someone installing what they need.
          // Off-catalog cards already carry their own "not for this platform" line; a second,
          // near-identical explanation on top of it is just noise.
          vendorMissing: gpuBackend
            && !item.offCatalog
            && runtimeRecommendedBackend.value !== null
            && runtimeRecommendedBackend.value !== item.backend,
          sizeHint: runtimeSizeHint(item.backend),
          // Absent means "not measured", and zero means the walk
          // could not read anything. Neither is a usable number, and formatBytes renders both as
          // an em dash, so only show the row when there is a real size to show.
          diskLabel: diskBytes ? formatBytes(diskBytes) : '',
          installing: runtimeInstalling.value && app.runtimeInstallBackend === item.backend,
          pymssVersion,
          pymssCoreVersion,
        })
      }
    }
    return cards
  })

  function runtimeCoreVersionLabel(card: { pymssVersion?: string; pymssCoreVersion?: string }) {
    return t('settings.runtimeCoreVersion', {
      pymss: card.pymssVersion || t('settings.runtimeCoreVersionUnknown'),
      core: card.pymssCoreVersion || t('settings.runtimeCoreVersionUnknown'),
    })
  }

  // Same naming as the cards ("NVIDIA CUDA", not "CUDA"), so a confirmation dialog never appears
  // to be talking about something other than the card that opened it.
  function runtimeBackendLabel(value: RuntimeBackend | string | null | undefined) {
    return value ? runtimeBackendName(value) : t('settings.envNotChecked')
  }

  const runtimeDiskTotalLabel = computed(() => {
    const values = Object.values(app.runtimeEnvSizes as Record<string, number>).filter((value) => value > 0)
    // A single environment already shows its own size on the card; a total only adds
    // information once more than one is installed.
    if (values.length < 2) return ''
    return formatBytes(values.reduce((sum, value) => sum + value, 0))
  })

  // Walking several multi-GB venvs takes visible time on a slow disk; say so rather than
  // leaving the section silently size-less.
  const runtimeDiskMeasuring = computed(() => app.runtimeEnvSizesLoading)

  return {
    runtimeInstalling, runtimeDetecting, runtimeBusy, runtimeCurrentError, runtimeCurrentBackend,
    runtimeCurrentLabel, installedRuntimes, runtimePlatform, runtimeRecommendedBackend,
    runtimeBackendDescription, runtimeBackendCatalog, runtimeBackendCards, runtimeCoreVersionLabel,
    runtimeBackendLabel, runtimeDiskTotalLabel, runtimeDiskMeasuring,
  }
}
