import { computed, nextTick, ref, type Ref } from 'vue'
import { invoke } from '@tauri-apps/api/core'
import { formatBytes } from '@/utils/format'
import {
  formatLogRecord,
  logClock,
  logContextPreview,
  locationLabel,
  normalizeLogRecord,
  sourceLabel,
  type LogRecord,
} from '@/utils/logRecord'
import type {
  DebugLogCategoryFilter,
  DebugLogContent,
  DebugLogInfo,
  DebugLogLevelFilter,
  DebugLogReport,
  ParsedDebugLogLine,
} from '../debug_types'

type Deps = {
  t: (key: string, params?: Record<string, unknown>) => string
  message: { success: (text: string) => void; error: (text: string) => void }
  developerMode: Ref<boolean>
  revealPath: (path: string) => void | Promise<void>
}

function shortTaskId(value: unknown) {
  const text = String(value || '').trim()
  if (!text) return '-'
  return text.length > 28 ? `${text.slice(0, 14)}…${text.slice(-10)}` : text
}

/**
 * Debug page session-log state: loading, parsing, filtering, copying and report
 * creation. The view only keeps the tab decision and the file-scope links.
 */
export function useDebugLogs({ t, message, developerMode, revealPath }: Deps) {
  const debugLogLoading = ref(false)
  const debugLogClearing = ref(false)
  const debugLogReportLoading = ref(false)
  const debugLogInfo = ref<DebugLogInfo | null>(null)
  const debugLogContent = ref<DebugLogContent | null>(null)
  const debugLogScroller = ref<HTMLElement | null>(null)
  const debugLogQuery = ref('')
  const debugLogLevel = ref<DebugLogLevelFilter>('all')
  const debugLogSource = ref('all')
  const debugLogCategory = ref<DebugLogCategoryFilter>('all')
  const debugLogTask = ref('all')
  const expandedDebugLogLines = ref<Record<string, boolean>>({})
  const debugLogError = ref('')

  const debugLogRows = computed(() => [
    { label: t('debug.currentLogPath'), value: debugLogInfo.value?.path || '' },
    { label: t('debug.currentLogSize'), value: `${formatBytes(debugLogInfo.value?.sizeBytes || 0)} / ${formatBytes(debugLogInfo.value?.maxBytes || 0)}` },
    { label: t('debug.persistentLogPath'), value: debugLogInfo.value?.persistentPath || debugLogContent.value?.path || '' },
    { label: t('debug.persistentLogSize'), value: `${formatBytes(debugLogInfo.value?.persistentSizeBytes || debugLogContent.value?.sizeBytes || 0)} / ${formatBytes(debugLogInfo.value?.persistentMaxBytes || 0)}` },
    { label: t('debug.reportPath'), value: debugLogInfo.value?.reportPath || '' },
    { label: t('debug.reportSize'), value: debugLogInfo.value?.reportExists ? formatBytes(debugLogInfo.value?.reportSizeBytes || 0) : '-' },
    { label: t('debug.logMode'), value: developerMode.value ? t('debug.logModeVerbose') : t('debug.logModeRelease') },
  ])

  const parsedDebugLogLines = computed(() => (debugLogContent.value?.content || '')
    .split(/\r?\n/)
    .filter(Boolean)
    .map((line, index) => parseDebugLogLine(line, index)))

  const debugLogLevelOptions = computed(() => [
    { label: t('debug.logFilterAllLevels'), value: 'all' },
    { label: 'ERROR', value: 'error' },
    { label: 'WARN', value: 'warn' },
    { label: 'INFO', value: 'info' },
    { label: 'DEBUG', value: 'debug' },
    { label: t('debug.logUnclassified'), value: 'unclassified' },
  ] as Array<{ label: string; value: DebugLogLevelFilter }>)

  const debugLogSourceOptions = computed(() => {
    const sources = Array.from(new Set(parsedDebugLogLines.value.map((line) => line.sourceKind).filter(Boolean))).sort()
    return [
      { label: t('debug.logFilterAllSources'), value: 'all' },
      ...sources.map((source) => ({ label: logSourceLabel(source), value: source })),
    ]
  })

  const debugLogCategoryOptions = computed(() => [
    { label: t('debug.logCategoryAll'), value: 'all' },
    { label: t('debug.logCategoryIssues'), value: 'issues' },
    { label: t('debug.logCategoryTraceback'), value: 'traceback' },
    { label: 'pymss', value: 'pymss' },
    { label: 'Worker', value: 'worker' },
    { label: 'App', value: 'app' },
    { label: t('debug.logCategoryRuntime'), value: 'runtime' },
    { label: t('debug.logCategoryStore'), value: 'store' },
    { label: t('debug.logCategoryOther'), value: 'other' },
  ] as Array<{ label: string; value: DebugLogCategoryFilter }>)

  const debugLogTaskOptions = computed(() => {
    const tasks = Array.from(new Set(parsedDebugLogLines.value.flatMap((line) => [line.taskId, ...(Array.isArray(line.record.context.taskIds) ? line.record.context.taskIds.filter((id): id is string => typeof id === 'string') : [])]).filter(Boolean))).sort()
    if (debugLogTask.value !== 'all' && !tasks.includes(debugLogTask.value)) tasks.unshift(debugLogTask.value)
    return [
      { label: t('debug.logFilterAllTasks'), value: 'all' },
      ...tasks.map((taskId) => ({ label: shortTaskId(taskId), value: taskId })),
    ]
  })

  const filteredDebugLogLines = computed(() => {
    const query = debugLogQuery.value.trim().toLowerCase()
    return parsedDebugLogLines.value.filter((line) => {
      if (debugLogLevel.value !== 'all' && line.severity !== debugLogLevel.value) return false
      if (debugLogSource.value !== 'all' && line.sourceKind !== debugLogSource.value) return false
      if (debugLogTask.value !== 'all' && line.taskId !== debugLogTask.value && !(Array.isArray(line.record.context.taskIds) && line.record.context.taskIds.includes(debugLogTask.value))) return false
      if (debugLogCategory.value === 'issues' && !line.hasIssue) return false
      if (debugLogCategory.value === 'traceback' && !line.traceback) return false
      if (!['all', 'issues', 'traceback'].includes(debugLogCategory.value) && line.category !== debugLogCategory.value) return false
      if (!query) return true
      return [line.timestamp, line.level, line.source, line.taskId, line.command, line.stage, line.message, line.details, line.location, line.traceback, line.raw]
        .join('\n')
        .toLowerCase()
        .includes(query)
    })
  })

  const debugLogIssueCounts = computed(() => {
    const errors = parsedDebugLogLines.value.filter((line) => line.severity === 'error').length
    const warnings = parsedDebugLogLines.value.filter((line) => line.severity === 'warn').length
    const tracebacks = parsedDebugLogLines.value.filter((line) => line.traceback).length
    return { errors, warnings, tracebacks }
  })

  async function scrollDebugLogToLatest() {
    await nextTick()
    const scroller = debugLogScroller.value
    if (scroller) scroller.scrollTop = scroller.scrollHeight
  }

  async function loadDebugLog() {
    if (!developerMode.value || debugLogLoading.value) return
    const scroller = debugLogScroller.value
    const followLatest = !scroller || scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 48
    debugLogLoading.value = true
    debugLogError.value = ''
    try {
      const [info, content] = await Promise.all([
        invoke<DebugLogInfo>('debug_log_info'),
        invoke<DebugLogContent>('debug_log_read'),
      ])
      debugLogInfo.value = info
      debugLogContent.value = content
      if (followLatest) await scrollDebugLogToLatest()
    } catch (error) {
      debugLogError.value = error instanceof Error ? error.message : String(error)
    } finally {
      debugLogLoading.value = false
    }
  }

  async function clearDebugLog() {
    if (!developerMode.value) return
    debugLogClearing.value = true
    try {
      debugLogInfo.value = await invoke<DebugLogInfo>('debug_log_clear')
      debugLogContent.value = await invoke<DebugLogContent>('debug_log_read')
      await scrollDebugLogToLatest()
      message.success(t('debug.logCleared'))
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error))
    } finally {
      debugLogClearing.value = false
    }
  }

  async function copyDebugLog() {
    const content = filteredDebugLogLines.value.map(line => formatLogRecord(line.record)).join('\n')
    if (!content) return
    try {
      await navigator.clipboard.writeText(content)
      message.success(t('debug.logCopied'))
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error))
    }
  }

  async function createDebugLogReport() {
    if (!developerMode.value || debugLogReportLoading.value || debugLogClearing.value) return
    debugLogReportLoading.value = true
    try {
      const report = await invoke<DebugLogReport>('debug_log_create_report')
      message.success(t('debug.reportCreated'))
      await loadDebugLog()
      await revealPath(report.path)
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error))
    } finally {
      debugLogReportLoading.value = false
    }
  }

  function parseDebugLogLine(raw: string, index: number): ParsedDebugLogLine {
    const record = normalizeLogRecord(raw, index)
    const severity: DebugLogLevelFilter = record.level === 'fatal' ? 'error'
      : record.level === 'trace' ? 'debug' : record.level || 'unclassified'
    const fields = Object.fromEntries(Object.entries({ ...record.context, taskId: record.taskId, nodeId: record.nodeId, jobId: record.jobId, requestId: record.requestId }).filter(([, value]) => value != null).map(([key, value]) => [key, typeof value === 'string' ? value : JSON.stringify(value)]))
    const details = Object.entries(fields).map(([key, value]) => `${key}=${value}`).join('\n')
    return {
      record, id: record.id, timestamp: logClock(record.timestamp), severity,
      level: record.level?.toUpperCase() || t('debug.logUnclassified'),
      source: (record.logger === 'logger' && record.location?.file ? record.location.file.replaceAll('\\', '/').split('/').pop() : record.logger) || record.event, sourceKind: record.source,
      category: inferLogCategory(record.event, record.source, '', fields),
      taskId: record.taskId || record.jobId || '', command: fields.command || '', stage: fields.stage || '',
      message: record.message, messageContext: logContextPreview(record), details, location: locationLabel(record),
      traceback: record.detail || '', hasIssue: severity === 'error' || severity === 'warn', fields, raw,
    }
  }

  function inferLogCategory(source: string, sourceKind: string, message: string, fields: Record<string, string>): DebugLogCategoryFilter {
    const text = `${source} ${sourceKind} ${message} ${fields.command || ''} ${fields.stage || ''}`.toLowerCase()
    if (sourceKind === 'pymss' || text.includes('pymss') || text.includes('separator.py')) return 'pymss'
    if (text.includes('runtime') || text.includes('env_info') || text.includes('pythonpath')) return 'runtime'
    if (text.includes('app.store') || text.includes('store.')) return 'store'
    if (sourceKind === 'worker' || text.includes('worker.')) return 'worker'
    if (sourceKind === 'studio' || sourceKind === 'app' || text.includes('app.')) return 'app'
    return 'other'
  }

  function logSourceLabel(source: string) {
    return source === 'third_party' ? t('debug.logSourceExternal') : sourceLabel(source as LogRecord['source'])
  }

  function canExpandDebugLogLine(line: ParsedDebugLogLine) {
    return Boolean(line.raw || line.traceback || line.location || line.details)
  }

  function logCategoryLabel(category: DebugLogCategoryFilter) {
    const option = debugLogCategoryOptions.value.find((item) => item.value === category)
    return option?.label || category
  }

  function toggleDebugLogLine(line: ParsedDebugLogLine) {
    if (!canExpandDebugLogLine(line)) return
    expandedDebugLogLines.value = {
      ...expandedDebugLogLines.value,
      [line.id]: !expandedDebugLogLines.value[line.id],
    }
  }

  function clearDebugLogFilters() {
    debugLogQuery.value = ''
    debugLogLevel.value = 'all'
    debugLogCategory.value = 'all'
    debugLogSource.value = 'all'
    debugLogTask.value = 'all'
  }

  return {
    debugLogLoading, debugLogClearing, debugLogReportLoading,
    debugLogInfo, debugLogContent, debugLogScroller,
    debugLogQuery, debugLogLevel, debugLogSource, debugLogCategory, debugLogTask,
    expandedDebugLogLines, debugLogError,
    debugLogRows, parsedDebugLogLines, debugLogLevelOptions, debugLogSourceOptions,
    debugLogCategoryOptions, debugLogTaskOptions, filteredDebugLogLines, debugLogIssueCounts,
    loadDebugLog, clearDebugLog, copyDebugLog, createDebugLogReport,
    toggleDebugLogLine, canExpandDebugLogLine, logSourceLabel, logCategoryLabel,
    clearDebugLogFilters, scrollDebugLogToLatest,
  }
}
