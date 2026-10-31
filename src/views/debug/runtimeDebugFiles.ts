// DebugView 的运行时文件编辑/恢复/覆盖写：每条路径先过平台能力闸门，再落状态。
import type { DebugRuntimeFileInfo, DebugRuntimePointers } from '../debug_types'

type Deps = Record<string, any>

export function makeRuntimeDebugFiles(deps: Deps) {
  function openRuntimeDebugEditor(file: DebugRuntimeFileInfo) {
    if (!deps.app.runtimeDebugSupported || !deps.developerMode.value || !file.editable) return
    deps.runtimeDebugEditingPath.value = file.path
    deps.runtimeDebugEditingContent.value = file.content || ''
    deps.runtimeDebugEditorVisible.value = true
  }

  async function saveRuntimeDebugEditor() {
    if (!deps.app.runtimeDebugSupported) return
    const path = deps.runtimeDebugEditingPath.value
    if (!path) return
    deps.runtimeDebugSaving.value = path
    try {
      const info = await deps.app.updateDebugRuntime('debug_runtime_write_file', {
        path, content: deps.runtimeDebugEditingContent.value || '', backup: true,
      }) as DebugRuntimePointers
      deps.setRuntimeDebugInfo(info)
      deps.runtimeDebugEditorVisible.value = false
      deps.runtimeDebugEditingPath.value = ''
      deps.runtimeDebugEditingContent.value = ''
      deps.message.success(deps.t('debug.runtimeDebugSaved'))
    } catch (error) {
      deps.message.error(error instanceof Error ? error.message : String(error))
    } finally {
      deps.runtimeDebugSaving.value = ''
    }
  }

  async function restoreRuntimeDebugFile(file: DebugRuntimeFileInfo) {
    if (!deps.app.runtimeDebugSupported || !deps.developerMode.value || !file.backupExists) return
    deps.runtimeDebugSaving.value = file.path
    try {
      const info = await deps.app.updateDebugRuntime('debug_runtime_restore_file', { path: file.path }) as DebugRuntimePointers
      deps.setRuntimeDebugInfo(info)
      deps.message.success(deps.t('debug.runtimeDebugRestored'))
    } catch (error) {
      deps.message.error(error instanceof Error ? error.message : String(error))
    } finally {
      deps.runtimeDebugSaving.value = ''
    }
  }

  async function overrideActiveRuntimePointer() {
    if (!deps.app.runtimeDebugSupported || !deps.developerMode.value || !deps.runtimeOverrideBackend.value || !deps.runtimeOverridePythonPath.value.trim()) return
    deps.runtimeDebugSaving.value = 'active-runtime'
    try {
      const info = await deps.app.updateDebugRuntime('debug_runtime_override_active', {
        backend: deps.runtimeOverrideBackend.value, pythonPath: deps.runtimeOverridePythonPath.value.trim(),
      }) as DebugRuntimePointers
      deps.setRuntimeDebugInfo(info)
      deps.runtimeOverrideDirty.value = false
      deps.syncRuntimeOverrideFromCurrent(true)
      deps.message.success(deps.t('debug.runtimeDebugOverrideSaved'))
    } catch (error) {
      deps.message.error(error instanceof Error ? error.message : String(error))
    } finally {
      deps.runtimeDebugSaving.value = ''
    }
  }

  return { openRuntimeDebugEditor, saveRuntimeDebugEditor, restoreRuntimeDebugFile, overrideActiveRuntimePointer }
}
