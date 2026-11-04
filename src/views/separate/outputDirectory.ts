// SeparateView 的输出目录：默认值、平台校验、预览、提交入口。
import type { StemOutput } from '@/stores/task'

import { computed } from 'vue'
import type { launchTask } from '@/features/tasks/launch'

type OutputDirectoryDeps = Record<string, any> & { computed: typeof computed; launchTask: typeof launchTask }

export function makeOutputDirectory(deps: OutputDirectoryDeps) {
  const outputDirectoryInput = deps.computed({
    get: () => deps.temporaryOutputDir.value || deps.normalizedOutputDir.value,
    set: (value: string | null) => { deps.temporaryOutputDir.value = value || '' },
  })
  const outputDirectoryPlatform = deps.computed(() => {
    const reported = String(deps.app.envInfo?.platform || '').trim().toLowerCase()
    if (reported === 'win32' || reported === 'windows') return 'windows' as const
    if (reported === 'darwin' || reported === 'macos' || reported === 'mac') return 'macos' as const
    if (reported === 'linux') return 'linux' as const
    const browserPlatform = String(typeof navigator !== 'undefined' ? navigator.platform : '').toLowerCase()
    if (browserPlatform.includes('win')) return 'windows' as const
    if (browserPlatform.includes('mac')) return 'macos' as const
    return 'linux' as const
  })
  const outputDirectoryError = deps.computed(() => validateOutputDirectory(deps.normalizedOutputDir.value, outputDirectoryPlatform.value))
  const outputPreview = deps.computed(() => {
    const windows = outputDirectoryPlatform.value === 'windows'
    const base = deps.normalizedOutputDir.value.replace(windows ? /[\\/]$/ : /[/]$/, '')
    const separator = windows && base.includes('\\') ? '\\' : '/'
    const previewName = deps.outputNamingPreviewFiles.value[0] || deps.t('separate.outputFilePreview')
    if (deps.effectiveOutputLayout.value === 'flat') {
      return `${base}${separator}${previewName}`
    }
    return `${base}${separator}${deps.t('separate.resultFolderPreview')}${separator}${previewName}`
  })

  function validateOutputDirectory(value: string, platform: 'windows' | 'macos' | 'linux') {
    const path = String(value || '').trim()
    if (!path) return ''
    if (/[\u0000-\u001f]/u.test(path)) {
      return deps.t('separate.outputDirectoryInvalidCharacters')
    }

    if (platform !== 'windows') {
      if (path.includes('\0')) return deps.t('separate.outputDirectoryInvalidCharacters')
      return path.startsWith('/') ? '' : deps.t('separate.outputDirectoryAbsoluteRequired')
    }

    if (/[<>"|?*]/u.test(path)) return deps.t('separate.outputDirectoryInvalidCharacters')
    const normalized = path.replace(/\//g, '\\')
    if (/^[A-Za-z]:[^\\]/u.test(normalized)) return deps.t('separate.outputDirectoryIncomplete')
    const withoutRoot = normalized
      .replace(/^[A-Za-z]:\\/u, '')
      .replace(/^\\\\[^\\]+\\[^\\]+\\?/u, '')
      .replace(/^\\+/u, '')
    const segments = withoutRoot.split(/\\+/u).filter(Boolean)
    if (segments.some(segment => segment.includes(':'))) return deps.t('separate.outputDirectoryInvalidCharacters')
    if (segments.some(segment => /[ .]$/u.test(segment))) return deps.t('separate.outputDirectoryTrailingCharacter')
    if (segments.some(segment => deps.WINDOWS_RESERVED_FILENAMES.has(segment.split('.', 1)[0].toUpperCase()))) {
      return deps.t('separate.outputDirectoryReservedName')
    }
    if (/^[A-Za-z]:$/u.test(path) || /^\\\\[^\\]+$/u.test(path)) return deps.t('separate.outputDirectoryIncomplete')
    const isAbsolute = /^[A-Za-z]:\\/u.test(normalized) || /^\\\\[^\\]+\\[^\\]+(?:\\|$)/u.test(normalized)
    return isAbsolute ? '' : deps.t('separate.outputDirectoryAbsoluteRequired')
  }

  const canStart = deps.computed(() => (
    !deps.submittingJob.value
    && !outputDirectoryError.value
    && (deps.ensembleEnabled.value
      ? deps.ensembleReady.value
      : (deps.modelDownloaded.value && deps.inputFiles.value.length > 0 && deps.hasOutputStemSelection.value))
  ))

  async function start() {
    if (deps.submittingJob.value) return
    if (outputDirectoryError.value) {
      deps.message.warning(outputDirectoryError.value)
      return
    }
    if (deps.ensembleEnabled.value && !deps.ensembleReady.value) {
      deps.message.warning(deps.t('separate.ensembleNotReady'))
      return
    }
    if (!deps.inputFiles.value.length) {
      deps.message.warning(deps.t('separate.startHintNoInput'))
      return
    }
    if (!deps.ensembleEnabled.value && !deps.modelDownloaded.value) {
      deps.message.warning(deps.t('separate.startHintModelMissing'))
      return
    }
    if (!deps.ensembleEnabled.value && deps.currentChunkIssue.value) {
      deps.message.warning(deps.t('inference.invalidChunkSize', deps.currentChunkIssue.value))
      return
    }
    if (!deps.ensembleEnabled.value && !deps.hasOutputStemSelection.value) {
      deps.message.warning(deps.t('separate.startHintNoOutputStems'))
      return
    }
    deps.submittingJob.value = true
    try {
      const options = {
        outputDir: deps.normalizedOutputDir.value,
        outputLayout: deps.effectiveOutputLayout.value,
        outputNaming: deps.outputNamingConfig.value,
      }
      const result = deps.ensembleEnabled.value
        ? await deps.launchTask(deps.task, { kind: 'workflow', workflow: deps.buildEnsembleWorkflow(), options })
        : await deps.launchTask(deps.task, { kind: 'separation', options })
      deps.focusedSeparationJobId.value = result?.jobId || deps.newestRunningJob.value?.id || deps.focusedSeparationJobId.value
      deps.composingNewJob.value = false
      if (deps.ensembleEnabled.value && result) {
        deps.ensembleModels.value.forEach((name: string) => deps.model.recordModelUse(name))
      }
      if (deps.settings.clearInputAfterSubmit) deps.task.clearInputFiles()
      if (result && result.failed > 0) {
        deps.message.warning(deps.t('separate.batchPartial', { succeeded: result.succeeded, failed: result.failed }))
      } else {
        deps.message.success(deps.t('separate.batchStarted', { count: result?.succeeded ?? 1 }))
      }
    } catch (err) {
      deps.message.error(err instanceof Error ? err.message : deps.t('toast.taskFailed'))
    } finally {
      deps.submittingJob.value = false
    }
  }

  return {
    outputDirectoryInput, outputDirectoryPlatform, outputDirectoryError, outputPreview,
    validateOutputDirectory, canStart, start,
  }
}
