/**
 * 设置页「数据目录」区块：模型目录迁移的进度/冲突/结果派生，以及换目录的动作。
 *
 * 迁移本身由 settings store 的状态机驱动；这里只做视图投影与对话框分派。
 */
type Deps = Record<string, any>

export function makeModelDirMigration(deps: Deps) {
  const { computed, t, settings, message } = deps

  const modelDirMigrationVisible = computed(() => deps.modelDirMigrationState.value.status !== 'idle' && deps.modelDirMigrationState.value.status !== 'confirm')
  const modelDirMigrationProgress = computed(() => {
    const state = deps.modelDirMigrationState.value
    if (['ready_to_switch', 'finalizing_cleanup', 'success'].includes(state.status)) return 100
    if (state.totalBytes > 0) return Math.max(0, Math.min(99, Math.round((state.copiedBytes / state.totalBytes) * 100)))
    if (state.totalFiles > 0) return Math.max(0, Math.min(99, Math.round((state.completedFiles / state.totalFiles) * 100)))
    return 0
  })
  const modelDirMigrationHasConflict = computed(() => deps.modelDirMigrationState.value.status === 'conflict' && !!deps.modelDirMigrationState.value.conflict)
  const modelDirMigrationHasResult = computed(() => ['success', 'failed', 'aborted'].includes(deps.modelDirMigrationState.value.status))

  async function changeModelDir() {
    if (deps.hasActiveModelDirUsage.value) {
      message.warning(t('settings.modelDirChangeBlocked'))
      return
    }
    const folder = await settings.pickModelDir()
    if (!folder) return
    deps.isCheckingModelDir.value = true
    try {
      const result = await settings.prepareModelDirChange(folder)
      if (result.outcome === 'noop') {
        message.info(t('settings.modelDirSamePath'))
        return
      }
      if (result.outcome === 'switched') {
        message.success(t('settings.modelDirChanged'))
      }
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error))
    } finally {
      deps.isCheckingModelDir.value = false
    }
  }

  async function changeOutputDir() {
    try {
      await settings.pickOutputDir()
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error))
    }
  }

  function closeModelDirMigrationDialog() {
    if (deps.modelDirMigrationState.value.status === 'confirm') {
      settings.cancelModelDirChangeConfirmation()
      return
    }
    if (modelDirMigrationHasResult.value) {
      settings.clearModelDirMigrationState()
    }
  }

  async function confirmModelDirMigration() {
    try {
      await settings.confirmModelDirMigration()
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error))
    }
  }

  async function resolveModelDirConflict(action: 'overwrite' | 'skip' | 'abort') {
    try {
      await settings.resolveModelDirConflict(action)
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error))
    }
  }

  return {
    modelDirMigrationVisible, modelDirMigrationProgress, modelDirMigrationHasConflict,
    modelDirMigrationHasResult, changeModelDir, changeOutputDir,
    closeModelDirMigrationDialog, confirmModelDirMigration, resolveModelDirConflict,
  }
}
