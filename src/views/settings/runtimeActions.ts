/**
 * 设置页「运行时」区块的动作：安装/重装、取消、删除、切换、检测、核心版本刷新。
 *
 * 所有动作先过一遍 runtimeBusy 与平台闸门，再走三选一确认（confirmRuntimeAction），
 * 确认被关掉（Esc / 遮罩 / 关闭）一律当取消。派生状态见 runtimeState.ts。
 */
// 本模块会被 Node 原生测试直接导入，内部导入必须用相对路径加 .ts 扩展名；
// 不要改成 @/ 别名。
import { runtimeAssetInstall, runtimeAssetStatus, type RuntimeAssetStatus } from '../../api/native.ts'
import type { Ref } from 'vue'
import type { DialogApi, MessageApi } from 'naive-ui'
import type { InstalledRuntime, RuntimeBackend, useAppStore } from '../../stores/app.ts'
import { runtimeBackendsForTarget, runtimeEnvironmentForBackend, runtimeSizeHint } from '../../utils/runtime.ts'
import { formatBytes } from '../../utils/format.ts'

type RuntimeAssetNative = {
  status: (backend: RuntimeBackend) => Promise<RuntimeAssetStatus>
  install: (backend: RuntimeBackend) => Promise<unknown>
}

type Deps = {
  app: ReturnType<typeof useAppStore>
  message: MessageApi
  dialog: DialogApi
  t: (key: string, named?: Record<string, unknown>) => string
  currentLocale: Ref<string>
  installedRuntimes: Ref<InstalledRuntime[]>
  runtimeActivating: Ref<string | null>
  runtimeBackendLabel: (backend: RuntimeBackend | string | null | undefined) => string
  runtimeBusy: Ref<boolean>
  runtimeCurrentBackend: Ref<RuntimeBackend | null>
  runtimeDeleting: Ref<string | null>
  runtimeManualChecking: Ref<boolean>
  runtimeMirror: Ref<string>
  runtimeAssets?: RuntimeAssetNative
}

export function makeRuntimeActions(deps: Deps) {
  const { app, message, dialog, t } = deps
  const assets = deps.runtimeAssets ?? { status: runtimeAssetStatus, install: runtimeAssetInstall }
  let choosingInstall = false

  function confirmRuntimeAction(title: string, content: string, positiveText: string) {
    return new Promise<boolean>((resolve) => {
      dialog.warning({
        title,
        content,
        positiveText,
        negativeText: t('common.cancel'),
        onPositiveClick: () => resolve(true),
        onNegativeClick: () => resolve(false),
        onClose: () => resolve(false),
        onMaskClick: () => resolve(false),
        onEsc: () => resolve(false),
      })
    })
  }

  async function installBackend(backend: RuntimeBackend) {
    if (choosingInstall || deps.runtimeBusy.value) return
    choosingInstall = true
    try {
      const status = await assets.status(backend)
      if (deps.runtimeBusy.value) return
      const managed = status.available && status.backend === backend
      const worker = backend !== 'mlx' && app.runtimeManagementSupported
        && runtimeBackendsForTarget(status.platform, status.arch).includes(backend)
        && (backend !== 'cuda' || status.platform === 'linux')
      if (!managed && !worker) {
        message.error(status.reason || t('settings.runtimeInstallTargetUnsupported'))
        return
      }
      const target = deps.runtimeBackendLabel(backend)
      const sizeHint = t('settings.runtimeInstallSizeHint', { size: managed ? formatBytes(status.size) : runtimeSizeHint(backend) })
      const reinstall = deps.installedRuntimes.value.some((entry: InstalledRuntime) => entry.backend === backend)
      const switchHint = deps.runtimeCurrentBackend.value === backend ? '' : ` ${t('settings.runtimeInstallActivatesHint')}`
      const confirmed = await confirmRuntimeAction(
        reinstall ? t('settings.runtimeReinstallTitle') : t('settings.runtimeInstallTitle'),
        reinstall
          ? `${t('settings.runtimeReinstallContent', { current: deps.runtimeBackendLabel(deps.runtimeCurrentBackend.value), target })} ${sizeHint}${switchHint}`
          : `${t('settings.runtimeInstallContent', { backend: target })} ${sizeHint}${switchHint}`,
        t('common.confirm'),
      )
      if (!confirmed || deps.runtimeBusy.value) return
      if (managed) {
        await assets.install(backend)
      } else {
        await app.installRuntime(backend, deps.runtimeMirror.value, deps.currentLocale.value)
      }
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error))
    } finally {
      choosingInstall = false
    }
  }

  async function retryRuntimeInstall() {
    const backend = app.runtimeInstallBackend as RuntimeBackend | null
    if (!backend) {
      message.error(t('settings.runtimeRetryUnavailable'))
      return
    }
    await installBackend(backend)
  }

  async function refreshRuntimeStatus(showSuccess = false) {
    if (showSuccess) deps.runtimeManualChecking.value = true
    const info = showSuccess ? app.checkRuntimeInfo() : app.ensureRuntimeInfo()
    // Startup owns the detailed device probe. Page visits only refresh the overview;
    // explicit detection still checks the active interpreter and all its devices.
    const env = showSuccess ? app.checkEnv() : Promise.resolve()
    // Disk usage updates separately and does not hold the detection button open.
    void (showSuccess ? app.loadRuntimeEnvSizes() : app.ensureRuntimeEnvSizes())
    try {
      const results = await Promise.allSettled([info, env])
      const failed = results.find(result => result.status === 'rejected')
      if (failed?.status === 'rejected') throw failed.reason
      if (showSuccess) message.success(t('settings.runtimeDetectSuccess'))
    } catch (error) {
      if (showSuccess) message.error(error instanceof Error ? error.message : String(error))
    } finally {
      if (showSuccess) deps.runtimeManualChecking.value = false
    }
  }

  async function refreshRuntimeCoreVersions(fresh = false) {
    if (!app.runtimeCoreUpdateSupported) return
    try {
      await (fresh ? app.loadRuntimeCoreVersions() : app.ensureRuntimeCoreVersions())
    } catch {
      // Network availability is optional for the settings page; the installed version still shows.
    }
  }

  async function cancelRuntimeInstall() {
    try {
      await app.cancelRuntimeInstall()
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error))
    }
  }

  async function deleteRuntime(card: { backend: RuntimeBackend; env?: InstalledRuntime }) {
    if (!app.runtimeManagementSupported || deps.runtimeBusy.value) return
    const backend = card.backend
    const active = runtimeEnvironmentForBackend(app.runtimeInfo, backend)
    if (card.env && active === card.env && deps.runtimeCurrentBackend.value === backend) {
      message.warning(t('settings.runtimeDeleteActiveError'))
      return
    }
    // Cleaning up an unfinished install discards nothing usable, so it must not be described
    // as losing an environment.
    const leftoverOnly = app.runtimeIncompleteBackends.includes(backend)
      && !deps.installedRuntimes.value.some((entry: InstalledRuntime) => entry.backend === backend)
    const confirmed = await confirmRuntimeAction(
      leftoverOnly ? t('settings.runtimeCleanLeftoverTitle') : t('settings.runtimeDeleteTitle'),
      leftoverOnly
        ? t('settings.runtimeCleanLeftoverContent', { backend: deps.runtimeBackendLabel(backend) })
        : t('settings.runtimeDeleteContent', { backend: deps.runtimeBackendLabel(backend) }),
      leftoverOnly ? t('settings.runtimeCleanLeftover') : t('settings.runtimeDelete'),
    )
    if (!confirmed) return
    deps.runtimeDeleting.value = backend
    try {
      await app.deleteRuntime(backend, {
        pythonPath: card.env?.pythonPath,
      })
      await Promise.all([app.checkRuntimeInfo(), app.checkEnv(), app.loadRuntimeEnvSizes()])
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error))
    } finally {
      deps.runtimeDeleting.value = null
    }
  }

  async function activateInstalledRuntime(card: { backend: RuntimeBackend; env?: InstalledRuntime }) {
    if (!app.runtimeManagementSupported) return
    const backend = card.backend
    const active = runtimeEnvironmentForBackend(app.runtimeInfo, backend)
    if (deps.runtimeBusy.value || (deps.runtimeCurrentBackend.value === backend && active === card.env)) return
    const confirmed = await confirmRuntimeAction(
      t('settings.runtimeSwitchTitle'),
      t('settings.runtimeSwitchContent', {
        current: deps.runtimeBackendLabel(deps.runtimeCurrentBackend.value),
        target: deps.runtimeBackendLabel(backend),
      }),
      t('common.confirm'),
    )
    if (!confirmed) return
    deps.runtimeActivating.value = backend
    try {
      await app.activateRuntime(backend, {
        pythonPath: card.env?.pythonPath,
      })
      message.success(t('settings.runtimeActivateSuccess'))
      await Promise.all([app.checkRuntimeInfo(), app.loadRuntimeCoreVersions()])
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error))
    } finally {
      deps.runtimeActivating.value = null
    }
  }

  async function detectRuntime() {
    if (deps.runtimeBusy.value) return
    void refreshRuntimeCoreVersions(true)
    await refreshRuntimeStatus(true)
  }

  return {
    confirmRuntimeAction, installBackend, retryRuntimeInstall, refreshRuntimeStatus,
    refreshRuntimeCoreVersions, cancelRuntimeInstall, deleteRuntime, activateInstalledRuntime,
    detectRuntime,
  }
}
