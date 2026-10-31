<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { invoke } from '@tauri-apps/api/core'
import { storeToRefs } from 'pinia'
import { useI18n } from 'vue-i18n'
import { useMessage } from 'naive-ui'
import { useRouter } from 'vue-router'
import {
  AlertCircleOutline,
  CheckmarkCircleOutline,
  CloudDownloadOutline,
  FolderOpenOutline,
  InformationCircleOutline,
  LockClosedOutline,
  PulseOutline,
  RefreshOutline,
  TerminalOutline,
  TimeOutline,
  WarningOutline,
} from '@vicons/ionicons5'
import { useAppStore } from '@/stores/app'
import { useSettingsStore } from '@/stores/settings'
import { useTaskStore } from '@/stores/task'
import { useModelStore, type DownloadTask } from '@/stores/model'
import { useWorkflowStore } from '@/stores/workflow'
import { useOptionalRuntimePackage } from '@/features/audio-tools/optionalRuntimePackage'
import { buildDiagnosticChecks, isDiagnosticIssueEvent, summarizeDiagnosticChecks, type DiagnosticState } from '@/utils/diagnostics'
import { detectRuntimePlatform } from '@/utils/runtime'
import { isTauriRuntime } from '@/utils/appStore'
import TaskLogConsole from '@/components/TaskLogConsole.vue'
import { formatBytes } from '@/utils/format'
import { normalizeLogRecord, formatLogRecord, logContextPreview, logMessagePreview, locationLabel, logClock, sourceLabel, type LogRecord } from '@/utils/logRecord'

const { t } = useI18n()
const message = useMessage()
const router = useRouter()
const app = useAppStore()
const settings = useSettingsStore()
const task = useTaskStore()
const modelStore = useModelStore()
const workflow = useWorkflowStore()
const funAsr = useOptionalRuntimePackage('funasr')

const { developerMode, dataRoot, modelDir, outputDir, settingsDir, editorProjectsDir, logsDir, tempDir, defaultDevice, downloadSource, downloadMethod, maxConcurrentSeparations, updateEndpointOverride } = storeToRefs(settings)
const { activeWorkerTasks, runningTasks } = storeToRefs(task)
const { downloadTasks } = storeToRefs(modelStore)

import type {
  CatalogEditMode, DebugCatalogInfo, DebugLogCategoryFilter, DebugLogContent, DebugLogInfo,
  DebugLogLevelFilter, DebugLogReport, DebugModelConfig, DebugRuntimeFileInfo, DebugRuntimePointers,
  DebugTab, DebugWorkerEvent, DebugWorkerEventGroup, ParsedDebugLogLine, RuntimeDebugEnvironmentRow,
  RuntimeTreeChild, RuntimeTreeNode,
} from './debug_types'
import { useCatalogEditor } from './debug/useCatalogEditor'

const {
  debugCatalogLoading,
  debugCatalogError,
  debugCatalogSaving,
  debugCatalogInfo,
  debugCatalogText,
  catalogEditMode,
  catalogSearch,
  selectedCatalogModelName,
  catalogModelDraft,
  catalogModelDialogVisible,
  visibleCatalogModels,
  catalogModelStatus,
  selectCatalogModel,
  addCatalogModel,
  saveCatalogDraft,
  removeCatalogModel,
  switchCatalogEditMode,
  currentCatalogDownloadUrl,
  loadDebugCatalog,
  saveDebugCatalog,
  resetDebugCatalog,
} = useCatalogEditor({ t, message, modelStore, downloadSource })

const activeTab = ref<DebugTab>('overview')
const diagnosticDetailsOpen = ref(false)
const selectedDebugModel = ref('')
const debugConfigLoading = ref(false)
const debugConfigSaving = ref(false)
const debugModelConfig = ref<DebugModelConfig | null>(null)
const debugConfigText = ref('')
const workerEventDialogVisible = ref(false)
const selectedWorkerEvent = ref<any | null>(null)
const runtimeDebugLoading = ref(false)
const runtimeDebugError = ref('')
const runtimeDebugSaving = ref('')
const runtimeDebugInfo = ref<DebugRuntimePointers | null>(null)
const runtimeDebugEditorVisible = ref(false)
const runtimeDebugEditingPath = ref('')
const runtimeDebugEditingContent = ref('')
const runtimeOverrideBackend = ref('cuda')
const runtimeOverridePythonPath = ref('')
const runtimeOverrideDirty = ref(false)
const updateEndpointEditing = ref(false)
const updateEndpointDraft = ref('')
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

const checkingDiagnostics = ref(false)
const optionalChecking = ref(false)
const lastCheckedAt = ref<number | null>(null)
const activityLoadError = ref('')
const eventQuery = ref('')
const eventIssuesOnly = ref(false)
const debugLogError = ref('')
const executionLogVisible = ref(false)
const executionLogTaskId = ref('')
const executionDownloadSnapshot = ref<DownloadTask | null>(null)
const executionLogBody = ref<HTMLElement | null>(null)
const failedTaskPage = ref(1)
const failedTaskPageSize = 5
const failedTaskScroller = ref<HTMLElement | null>(null)
const executionLogTask = computed(() => task.tasks.find(item => item.id === executionLogTaskId.value))
const liveExecutionDownload = computed(() => Object.values(downloadTasks.value).find(item => item.taskId === executionLogTaskId.value))
watch(liveExecutionDownload, (current, previous) => {
  if (current) executionDownloadSnapshot.value = current
  else if (previous?.taskId === executionLogTaskId.value && modelStore.downloadStates[previous.model] === 'done') {
    executionDownloadSnapshot.value = { ...previous, status: 'done', progress: 100, speedBytesPerSecond: 0 }
  }
}, { flush: 'sync' })
const executionLogDownload = computed(() => liveExecutionDownload.value || executionDownloadSnapshot.value)
const executionLogName = computed(() => executionLogTask.value?.runConfig?.workflowName || executionLogTask.value?.model || executionLogDownload.value?.model || executionLogTaskId.value)
const executionLogs = computed(() => executionLogTask.value?.logs || (executionLogDownload.value?.logs || []).map(entry => entry.record || normalizeLogRecord({
  message: entry.message, level: entry.level, timestamp: Number.isFinite(entry.ts) ? new Date(entry.ts).toISOString() : '',
  source: 'worker', taskId: executionLogTaskId.value,
})))
const executionNodeLogs = computed(() => executionLogTask.value?.nodeLogs || {})
const executionLogError = computed(() => executionLogTask.value?.error
  || (executionLogTask.value?.status === 'failed' ? executionLogTask.value.message : '')
  || executionLogDownload.value?.errorMessage
  || (executionLogDownload.value?.status === 'error' ? executionLogDownload.value.message : '')
  || '')
const executionFailedNodes = computed(() => Object.entries(executionLogTask.value?.nodeStates || {})
  .filter(([, state]) => state.status === 'failed').map(([id]) => id))
const executionLogStatus = computed(() => executionLogTask.value ? t(taskStatusKeys[executionLogTask.value.status] || 'debug.stateUnknown')
  : executionLogDownload.value ? downloadStatusLabel(executionLogDownload.value) : t('debug.stateUnknown'))
const canReturnToTask = computed(() => executionLogTaskId.value && (executionLogTask.value || executionLogDownload.value))
const isMac = computed(() => detectRuntimePlatform(app.runtimeInfo).isMac)
const diagnostics = computed(() => buildDiagnosticChecks({
  env: app.envInfo, runtime: app.runtimeInfo, envError: app.envError, runtimeError: app.runtimeInfoError,
  connection: app.workerEventConnectionStatus, connectionError: app.workerEventConnectionError,
  optionalPackage: funAsr.status.value, optionalError: funAsr.error.value, isMac: isMac.value,
}))
const diagnosticSummary = computed(() => summarizeDiagnosticChecks(diagnostics.value))
watch([developerMode, () => diagnosticSummary.value.state], ([enabled, state], [wasEnabled]) => {
  if (!enabled) diagnosticDetailsOpen.value = false
  else if (!wasEnabled) diagnosticDetailsOpen.value = state === 'error' || state === 'warn'
  else if (state === 'error' || state === 'warn') diagnosticDetailsOpen.value = true
}, { immediate: true })
const diagnosticStateLabels: Record<DiagnosticState, string> = {
  ok: 'debug.stateOk', warn: 'debug.stateWarning', error: 'debug.stateError', unknown: 'debug.stateUnknown', optional: 'debug.stateOptional',
}
const diagnosticGroups = computed(() => [
  { key: 'runtime', title: t('debug.environment'), hint: t('debug.groupRuntimeHint'), icon: PulseOutline },
  { key: 'capabilities', title: t('debug.capabilities'), hint: t('debug.groupCapabilitiesHint'), icon: CheckmarkCircleOutline },
  { key: 'connection', title: t('debug.connection'), hint: t('debug.groupConnectionHint'), icon: TerminalOutline },
].map(group => ({ ...group, checks: diagnostics.value.filter(check => check.group === group.key) })))
const diagnosticBusy = computed(() => checkingDiagnostics.value || optionalChecking.value || app.envLoading || app.runtimeInfoLoading)
const failedTasks = computed(() => [...task.failedTasks].sort((a, b) => b.updatedAt - a.updatedAt))
const latestFailedTask = computed(() => failedTasks.value[0])
const failedTaskPageCount = computed(() => Math.max(1, Math.ceil(failedTasks.value.length / failedTaskPageSize)))
const pagedFailedTasks = computed(() => {
  const start = (failedTaskPage.value - 1) * failedTaskPageSize
  return failedTasks.value.slice(start, start + failedTaskPageSize)
})
watch(failedTaskPageCount, (count) => {
  failedTaskPage.value = Math.min(failedTaskPage.value, count)
})
watch(failedTaskPage, async () => {
  await nextTick()
  if (failedTaskScroller.value) failedTaskScroller.value.scrollTop = 0
})
const env = computed(() => app.envInfo)
const workerEventGroups = computed(() => groupWorkerEvents(app.workerEvents as DebugWorkerEvent[]))
const recentWorkerEventGroups = computed(() => {
  const query = eventQuery.value.trim().toLowerCase()
  return workerEventGroups.value.filter(group => {
    if (eventIssuesOnly.value && !group.events.some(isDiagnosticIssueEvent)) return false
    return !query || [group.taskId, ...Object.keys(group.typeCounts), ...group.events.map(event => formatPayload(event.payload))].join('\n').toLowerCase().includes(query)
  }).slice(0, 80)
})
const debugTabs = computed(() => [
  { key: 'overview', label: t('debug.tabOverview') },
  { key: 'activity', label: t('debug.tabActivity') },
  { key: 'runtime', label: t('debug.tabRuntime') },
  { key: 'logs', label: t('debug.tabLogs') },
] as Array<{ key: DebugTab; label: string }>)
const advancedTabs = computed(() => [
  { key: 'models', label: t('debug.tabModels') },
  { key: 'events', label: t('debug.tabEvents') },
  { key: 'runtime-tools', label: t('debug.tabRuntimeTools') },
  { key: 'updates', label: t('debug.updateEndpointTitle') },
] as Array<{ key: DebugTab; label: string }>)
const debugModelOptions = computed(() => modelStore.models
  .filter((model) => model.downloaded || model.source === 'user')
  .map((model) => ({
    label: `${model.name}${model.source === 'user' ? ` · ${t('debug.userModelReadonly')}` : ''}`,
    value: model.name,
  })))
const effectiveUpdateEndpoint = computed(() => updateEndpointOverride.value.trim() || t('debug.updateEndpointManaged'))
const updateEndpointMode = computed(() => updateEndpointOverride.value.trim() ? t('debug.updateEndpointCustom') : t('debug.updateEndpointDefault'))

function beginUpdateEndpointEdit() {
  updateEndpointDraft.value = updateEndpointOverride.value.trim()
  updateEndpointEditing.value = true
}

function cancelUpdateEndpointEdit() {
  updateEndpointDraft.value = ''
  updateEndpointEditing.value = false
}

function saveUpdateEndpointOverride() {
  const endpoint = updateEndpointDraft.value.trim()
  if (!endpoint) {
    updateEndpointOverride.value = ''
    cancelUpdateEndpointEdit()
    message.success(t('debug.updateEndpointRestored'))
    return
  }
  try {
    new URL(endpoint)
  } catch {
    message.error(t('debug.updateEndpointInvalid'))
    return
  }
  updateEndpointOverride.value = endpoint
  cancelUpdateEndpointEdit()
  message.success(t('debug.updateEndpointSaved'))
}

function resetUpdateEndpointOverride() {
  updateEndpointOverride.value = ''
  updateEndpointDraft.value = ''
  updateEndpointEditing.value = false
  message.success(t('debug.updateEndpointRestored'))
}

const runningDownloadTasks = computed(() => Object.values(downloadTasks.value).filter((item) => ['preparing', 'downloading'].includes(item.status)))
const runtimeDevice = computed(() => settings.getRuntimeDeviceConfig(app.envInfo))
const cudaDevices = computed(() => env.value?.cudaDevices || [])
const runtimeRows = computed(() => [
  { label: t('settings.workerVersion'), value: env.value?.workerVersion },
  { label: 'pymss-core', value: app.runtimeInfo?.pymssCoreVersion },
  { label: t('debug.runtimeManifest'), value: app.runtimeInfo?.manifestVersion },
  { label: t('debug.defaultDevice'), value: defaultDevice.value },
  { label: t('debug.resolvedDevice'), value: `${runtimeDevice.value.device} [${runtimeDevice.value.deviceIds.join(', ')}]` },
  { label: t('debug.downloadSource'), value: downloadSource.value },
  { label: t('debug.downloadMethod'), value: downloadMethod.value },
  { label: t('debug.maxConcurrent'), value: String(maxConcurrentSeparations.value) },
])
const pathRows = computed(() => [
  { label: t('debug.dataRoot'), value: dataRoot.value },
  { label: t('debug.modelDir'), value: modelDir.value },
  { label: t('debug.outputDir'), value: outputDir.value },
  { label: t('debug.settingsDir'), value: settingsDir.value },
  { label: t('debug.editorProjectsDir'), value: editorProjectsDir.value },
  { label: t('debug.logsDir'), value: logsDir.value },
  { label: t('debug.tempDir'), value: tempDir.value },
])
const runtimeTree = computed<RuntimeTreeNode[]>(() => {
  const info = app.runtimeInfo
  const environments = info?.installedEnvironments || []
  return [
    {
      name: 'bootstrap',
      role: t('debug.runtimeTreeBootstrapRole'),
      path: info?.bootstrapPython || '-',
      source: t('debug.runtimeTreeAppSource'),
      children: [],
    },
    {
      name: 'runtime-envs',
      role: t('debug.runtimeTreeManagedRootRole'),
      path: info?.runtimeEnvsDir || '-',
      source: t('debug.runtimeTreeAppSource'),
      children: environments.map((entry) => ({
        name: String(entry.backend || '-'),
        role: t('debug.runtimeTreeManagedRole'),
        path: entry.pythonPath || '-',
        source: t('debug.runtimeTreeAppSource'),
        children: [
          { name: 'python', role: t('debug.runtimeTreePythonRole'), path: entry.pythonPath || '-' },
          { name: 'install.log', role: t('debug.runtimeTreeLogRole'), path: entry.logPath || '-' },
        ],
      })),
    },
    {
      name: 'active-runtime.json',
      role: t('debug.runtimeTreeActiveRole'),
      path: info?.activeRuntimeFile || '-',
      source: t('debug.runtimeTreeAppSource'),
      children: [],
    },
  ]
})
const runtimeStatusRows = computed(() => [
  { label: t('debug.runtimeCurrentBackend'), value: app.runtimeInfo?.installedBackend || '-' },
  { label: t('debug.runtimeCurrentPython'), value: app.runtimeInfo?.installState?.pythonPath || '-' },
  { label: t('debug.runtimeInstalledCount'), value: String(app.runtimeInfo?.installedEnvironments?.length || 0) },
])
const runtimePointerRows = computed(() => [
  { label: t('debug.runtimeDebugUserRoot'), value: runtimeDebugInfo.value?.runtimeEnvsDir || app.runtimeInfo?.runtimeEnvsDir || '' },
  { label: t('debug.runtimeDebugUserActive'), value: runtimeDebugInfo.value?.activeRuntimeFile || app.runtimeInfo?.activeRuntimeFile || '' },
  { label: t('debug.runtimeBootstrapPython'), value: app.runtimeInfo?.bootstrapPython || '' },
])
const runtimeDebugFiles = computed(() => runtimeDebugInfo.value?.files || [])
const runtimeDebugActiveFile = computed(() => runtimeDebugFiles.value.find((file) => file.kind === 'active-runtime') || null)
const runtimeDebugFileLookup = computed(() => new Map(runtimeDebugFiles.value.map((file) => [file.path, file])))
const runtimeDebugEnvironments = computed<RuntimeDebugEnvironmentRow[]>(() => (app.runtimeInfo?.installedEnvironments || []).map((entry) => {
  const pythonPath = String(entry.pythonPath || '')
  const separator = pythonPath.includes('\\') ? '\\' : '/'
  const editablePath = pythonPath
    .replace(/[\\/]Scripts[\\/]python(?:\.exe)?$/i, `${separator}pyvenv.cfg`)
    .replace(/[\\/]bin[\\/]python(?:\d+(?:\.\d+)?)?$/i, `${separator}pyvenv.cfg`)
  const editableFile = editablePath !== pythonPath ? runtimeDebugFileLookup.value.get(editablePath) || null : null
  return {
    backend: String(entry.backend || '-'),
    pythonPath,
    editablePath: editableFile?.path || editablePath,
    editableFile,
  }
}))
const runtimeBackendOptions = [
  { label: 'CPU', value: 'cpu' },
  { label: 'CUDA', value: 'cuda' },
  { label: 'ROCm', value: 'rocm' },
  { label: 'MLX', value: 'mlx' },
]
const currentRuntimeOverrideDefaults = computed(() => ({
  backend: String(app.runtimeInfo?.installedBackend || app.runtimeInfo?.installState?.backend || runtimeOverrideBackend.value || 'cuda'),
  pythonPath: String(app.runtimeInfo?.installState?.pythonPath || ''),
}))
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
const statusCards = computed(() => [
  { label: t('debug.envStatus'), value: t(diagnosticStateLabels[diagnosticSummary.value.state]), tone: diagnosticSummary.value.state, detail: t('debug.checkCounts', { ok: diagnosticSummary.value.ok, total: diagnostics.value.length }), tab: 'overview' as DebugTab },
  { label: t('debug.runningTasks'), value: String(runningTasks.value.length), tone: 'neutral', detail: [t('debug.workflowCount', { count: runningTasks.value.filter(item => item.runConfig?.runMode === 'workflow').length }), t('debug.queuedCount', { count: task.queuedTasks.length })].join(' · '), tab: 'activity' as DebugTab },
  { label: t('debug.downloadingModels'), value: String(runningDownloadTasks.value.length), tone: 'neutral', detail: t('debug.openActivity'), tab: 'activity' as DebugTab },
])

function downloadStatusLabel(item: DownloadTask) {
  const keys: Record<string, string> = {
    preparing: 'tasks.statusPreparing', downloading: 'models.downloadStatusDownloading', done: 'models.downloaded',
    error: 'models.downloadStatusError', paused: 'models.downloadStatusPaused', cancelled: 'models.downloadStatusCancelled',
    interrupted: 'models.downloadInterrupted', idle: 'models.downloadStatusIdle',
  }
  return keys[item.status] ? t(keys[item.status]!) : item.status
}

function openStatusCard(tab: DebugTab) {
  activeTab.value = tab
  if (tab === 'overview') diagnosticDetailsOpen.value = true
}

function diagnosticIcon(level: DiagnosticState) {
  if (level === 'ok') return CheckmarkCircleOutline
  if (level === 'warn') return WarningOutline
  return level === 'error' ? AlertCircleOutline : InformationCircleOutline
}

function eventTime(value: unknown) {
  const time = Date.parse(String(value || ''))
  if (!Number.isFinite(time)) return '-'
  return new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' }).format(time)
}

function shortTaskId(value: unknown) {
  const text = String(value || '').trim()
  if (!text) return '-'
  return text.length > 28 ? `${text.slice(0, 14)}…${text.slice(-10)}` : text
}

function groupWorkerEvents(events: DebugWorkerEvent[]): DebugWorkerEventGroup[] {
  const groups: DebugWorkerEventGroup[] = []
  const taskGroups = new Map<string, DebugWorkerEventGroup>()

  for (const event of events) {
    const type = String(event?.type || 'unknown')
    const taskId = String(event?.taskId || '').trim()
    const timestamp = event?.timestamp
    if (!taskId) {
      groups.push({
        key: `event:${groups.length}:${timestamp || ''}:${type}`,
        taskId: null,
        latestType: type,
        firstTimestamp: timestamp,
        lastTimestamp: timestamp,
        eventCount: 1,
        latestPayload: event?.payload,
        typeCounts: { [type]: 1 },
        events: [event],
      })
      continue
    }

    let group = taskGroups.get(taskId)
    if (!group) {
      group = {
        key: `task:${taskId}`,
        taskId,
        latestType: type,
        firstTimestamp: timestamp,
        lastTimestamp: timestamp,
        eventCount: 0,
        latestPayload: event?.payload,
        typeCounts: {},
        events: [],
      }
      taskGroups.set(taskId, group)
      groups.push(group)
    }

    group.eventCount += 1
    group.typeCounts[type] = (group.typeCounts[type] || 0) + 1
    group.events.push(event)
    if (group.events.length === 1 || isNewerTimestamp(timestamp, group.lastTimestamp)) {
      group.latestType = type
      group.lastTimestamp = timestamp
      group.latestPayload = event?.payload
    }
    if (!group.firstTimestamp || isNewerTimestamp(group.firstTimestamp, timestamp)) {
      group.firstTimestamp = timestamp
    }
  }

  return groups
}

function isNewerTimestamp(left: unknown, right: unknown) {
  const leftTime = Date.parse(String(left || ''))
  const rightTime = Date.parse(String(right || ''))
  if (!Number.isFinite(leftTime)) return false
  if (!Number.isFinite(rightTime)) return true
  return leftTime > rightTime
}

function workerEventGroupTitle(group: DebugWorkerEventGroup | null) {
  if (!group) return t('debug.workerEvents')
  return group.taskId ? t('debug.workerEventTaskGroup') : group.latestType
}

function workerEventGroupSummary(group: DebugWorkerEventGroup | null) {
  if (!group) return ''
  return Object.entries(group.typeCounts)
    .sort((left, right) => right[1] - left[1] || left[0].localeCompare(right[0]))
    .map(([type, count]) => `${type}: ${count}`)
    .join(' · ')
}

function workerEventGroupPayload(group: DebugWorkerEventGroup | null) {
  if (!group) return ''
  if (!group.taskId) return formatPayload(group.latestPayload)
  return formatPayload({
    taskId: group.taskId,
    eventCount: group.eventCount,
    firstEventAt: group.firstTimestamp,
    latestEventAt: group.lastTimestamp,
    latestType: group.latestType,
    typeCounts: group.typeCounts,
    latestPayload: group.latestPayload,
    events: [...group.events].reverse(),
  })
}

function formatPayload(payload: unknown) {
  if (!payload || typeof payload !== 'object') return ''
  try {
    return JSON.stringify(payload, null, 2)
  } catch {
    return String(payload)
  }
}

async function checkEnv() {
  if (!developerMode.value || diagnosticBusy.value) return
  checkingDiagnostics.value = true
  try {
    const results = await Promise.allSettled([app.checkRuntimeInfo(), app.checkEnv()])
    await refreshOptionalDependency()
    lastCheckedAt.value = Date.now()
    if (results.some(result => result.status === 'rejected') || (!isMac.value && funAsr.error.value)) message.warning(t('debug.checkPartialFailure'))
    else message.success(t('debug.envCheckDone'))
    if (activeTab.value === 'runtime-tools') await loadRuntimeDebugPointers()
  } finally {
    checkingDiagnostics.value = false
  }
}

async function refreshOptionalDependency() {
  if (isMac.value || !isTauriRuntime() || optionalChecking.value) return
  optionalChecking.value = true
  try { await funAsr.refresh() }
  finally { optionalChecking.value = false }
}

function openDiagnosticAction(action: 'runtime' | 'tools' | 'events') {
  if (action === 'events') activeTab.value = 'events'
  else if (action === 'runtime') void router.push({ name: 'settings', query: { section: 'runtime' } })
  else void router.push({ name: 'tools' })
}

async function reconnectWorkerEvents() {
  if (!isTauriRuntime() || app.workerEventConnectionStatus === 'connecting') return
  try {
    const { connectWorkerEvents } = await import('@/utils/events')
    await connectWorkerEvents(app)
  } catch (error) {
    message.error(error instanceof Error ? error.message : String(error))
  }
}

async function copyText(text: string, success: string) {
  try {
    await navigator.clipboard.writeText(text)
    message.success(t(success))
  } catch (error) {
    message.error(error instanceof Error ? error.message : String(error))
  }
}

function copyDiagnosticSummary() {
  const content = [
    `MediaForge ${app.buildInfoVersion || '-'} (${app.buildInfoVariant || '-'})`,
    `${t('debug.envStatus')}: ${t(diagnosticStateLabels[diagnosticSummary.value.state])}`,
    `${t('debug.lastChecked')}: ${lastCheckedAt.value ? new Date(lastCheckedAt.value).toISOString() : t('debug.stateUnknown')}`,
    ...diagnostics.value.map(check => `${check.label.startsWith('debug.') ? t(check.label) : check.label}: ${t(diagnosticStateLabels[check.state])}${check.value ? ` · ${check.value}` : ''}${check.detail && check.state !== 'ok' ? `\n  ${check.detail.replace(/\n/g, '\n  ')}` : ''}`),
    `${t('debug.activeWorkerTasks')}: ${activeWorkerTasks.value.length}; ${t('debug.queuedCount', { count: task.queuedTasks.length })}`,
    `${t('debug.workflowTasks')}: ${runningTasks.value.filter(item => item.runConfig?.runMode === 'workflow').length}`,
  ].join('\n')
  void copyText(content, 'debug.summaryCopied')
}

async function showTaskLogs(taskId: string) {
  clearDebugLogFilters()
  debugLogTask.value = taskId
  activeTab.value = 'logs'
  await loadDebugLog()
}

function showExecutionLogs(taskId: string) {
  executionLogTaskId.value = taskId
  executionDownloadSnapshot.value = Object.values(downloadTasks.value).find(item => item.taskId === taskId)
    || (executionDownloadSnapshot.value?.taskId === taskId ? executionDownloadSnapshot.value : null)
  executionLogVisible.value = true
}

async function showExecutionApplicationLogs() {
  executionLogVisible.value = false
  await showTaskLogs(executionLogTaskId.value)
}

function showExecutionEvents() {
  executionLogVisible.value = false
  eventQuery.value = executionLogTaskId.value
  eventIssuesOnly.value = false
  activeTab.value = 'events'
}

function showFailedNodeLogs(nodeId: string) {
  const details = Array.from(executionLogBody.value?.querySelectorAll<HTMLDetailsElement>('.debug-node-logs') || [])
    .find(element => element.dataset.nodeId === nodeId)
  if (!details) return
  details.open = true
  void nextTick(() => details.scrollIntoView({ block: 'start' }))
}

const executionLogText = computed(() => [
  ...executionLogs.value,
  ...Object.entries(executionNodeLogs.value).flatMap(([id, lines]) => [`[node:${id}]`, ...lines]),
].map(entry => formatLogRecord(normalizeLogRecord(entry))).join('\n'))

const taskStatusKeys: Record<string, string> = {
  queued: 'tasks.statusQueued', preparing: 'tasks.statusPreparing', validating_input: 'tasks.statusValidatingInput',
  downloading_model: 'tasks.statusCheckingModel', ensuring_model: 'tasks.statusCheckingModel', loading_model: 'tasks.statusLoadingModel',
  separating: 'tasks.statusSeparating', writing_output: 'tasks.statusWritingOutput', done: 'tasks.statusDone', failed: 'tasks.statusFailed', cancelled: 'tasks.statusCancelled',
}

async function loadActivity() {
  activityLoadError.value = ''
  try {
    await workflow.initialize()
    await workflow.refreshEditorWindows()
  } catch (error) {
    activityLoadError.value = error instanceof Error ? error.message : String(error)
  }
}

function openWorkerEvent(group: DebugWorkerEventGroup) {
  selectedWorkerEvent.value = group
  workerEventDialogVisible.value = true
}


async function loadDebugModelConfig() {
  if (!selectedDebugModel.value) return false
  debugConfigLoading.value = true
  try {
    const result = await invoke<DebugModelConfig>('debug_model_config', {
      payload: { action: 'read', model: selectedDebugModel.value, modelDir: settings.modelDir || null },
    })
    debugModelConfig.value = result
    debugConfigText.value = result.effectiveContent || ''
    return true
  } catch (error) {
    message.error(error instanceof Error ? error.message : String(error))
    return false
  } finally {
    debugConfigLoading.value = false
  }
}

async function saveDebugModelConfig() {
  if (!selectedDebugModel.value || debugModelConfig.value?.readOnly) return
  debugConfigSaving.value = true
  try {
    const result = await invoke<DebugModelConfig>('debug_model_config', {
      payload: {
        action: 'save',
        model: selectedDebugModel.value,
        modelDir: settings.modelDir || null,
        content: debugConfigText.value,
      },
    })
    debugModelConfig.value = result
    debugConfigText.value = result.effectiveContent || ''
    await modelStore.loadModels()
    message.success(t('debug.downloadedConfigSaved'))
  } catch (error) {
    message.error(error instanceof Error ? error.message : String(error))
  } finally {
    debugConfigSaving.value = false
  }
}

async function resetDebugModelConfig() {
  if (!selectedDebugModel.value || debugModelConfig.value?.readOnly) return
  if (await loadDebugModelConfig()) message.success(t('debug.modelConfigReset'))
}

async function revealPath(path: string) {
  if (!path) return
  try {
    await task.revealPath(path)
  } catch (error) {
    message.error(error instanceof Error ? error.message : String(error))
  }
}

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

function setRuntimeDebugInfo(info: DebugRuntimePointers) {
  runtimeDebugInfo.value = info
  if (runtimeDebugEditingPath.value && !info.files.some((file) => file.path === runtimeDebugEditingPath.value)) {
    runtimeDebugEditorVisible.value = false
    runtimeDebugEditingPath.value = ''
    runtimeDebugEditingContent.value = ''
  }
}

async function loadRuntimeDebugPointers() {
  if (!developerMode.value) {
    runtimeDebugInfo.value = null
    return
  }
  runtimeDebugLoading.value = true
  runtimeDebugError.value = ''
  try {
    const info = await invoke<DebugRuntimePointers>('debug_runtime_pointers')
    setRuntimeDebugInfo(info)
  } catch (error) {
    runtimeDebugError.value = error instanceof Error ? error.message : String(error)
  } finally {
    runtimeDebugLoading.value = false
  }
}

function openRuntimeDebugEditor(file: DebugRuntimeFileInfo) {
  if (!app.runtimeDebugSupported || !developerMode.value || !file.editable) return
  runtimeDebugEditingPath.value = file.path
  runtimeDebugEditingContent.value = file.content || ''
  runtimeDebugEditorVisible.value = true
}

async function saveRuntimeDebugEditor() {
  if (!app.runtimeDebugSupported) return
  const path = runtimeDebugEditingPath.value
  if (!path) return
  runtimeDebugSaving.value = path
  try {
    const info = await app.updateDebugRuntime<DebugRuntimePointers>('debug_runtime_write_file', {
      path, content: runtimeDebugEditingContent.value || '', backup: true,
    })
    setRuntimeDebugInfo(info)
    runtimeDebugEditorVisible.value = false
    runtimeDebugEditingPath.value = ''
    runtimeDebugEditingContent.value = ''
    message.success(t('debug.runtimeDebugSaved'))
  } catch (error) {
    message.error(error instanceof Error ? error.message : String(error))
  } finally {
    runtimeDebugSaving.value = ''
  }
}

async function restoreRuntimeDebugFile(file: DebugRuntimeFileInfo) {
  if (!app.runtimeDebugSupported || !developerMode.value || !file.backupExists) return
  runtimeDebugSaving.value = file.path
  try {
    const info = await app.updateDebugRuntime<DebugRuntimePointers>('debug_runtime_restore_file', { path: file.path })
    setRuntimeDebugInfo(info)
    message.success(t('debug.runtimeDebugRestored'))
  } catch (error) {
    message.error(error instanceof Error ? error.message : String(error))
  } finally {
    runtimeDebugSaving.value = ''
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

function syncRuntimeOverrideFromCurrent(force = false) {
  if (!force && runtimeOverrideDirty.value) return
  const defaults = currentRuntimeOverrideDefaults.value
  runtimeOverrideBackend.value = defaults.backend
  runtimeOverridePythonPath.value = defaults.pythonPath
  runtimeOverrideDirty.value = false
}

function resetRuntimeOverrideForm() {
  syncRuntimeOverrideFromCurrent(true)
}

function markRuntimeOverrideDirty() {
  runtimeOverrideDirty.value = true
}

async function overrideActiveRuntimePointer() {
  if (!app.runtimeDebugSupported || !developerMode.value || !runtimeOverrideBackend.value || !runtimeOverridePythonPath.value.trim()) return
  runtimeDebugSaving.value = 'active-runtime'
  try {
    const info = await app.updateDebugRuntime<DebugRuntimePointers>('debug_runtime_override_active', {
      backend: runtimeOverrideBackend.value, pythonPath: runtimeOverridePythonPath.value.trim(),
    })
    setRuntimeDebugInfo(info)
    runtimeOverrideDirty.value = false
    syncRuntimeOverrideFromCurrent(true)
    message.success(t('debug.runtimeDebugOverrideSaved'))
  } catch (error) {
    message.error(error instanceof Error ? error.message : String(error))
  } finally {
    runtimeDebugSaving.value = ''
  }
}

function loadDebugPageData() {
  if (!developerMode.value) return
  if (!app.envLoading && !app.envInfo) app.checkEnvInBackground().catch(() => {})
  app.ensureRuntimeInfo().then(() => refreshOptionalDependency()).catch(() => {})
  loadTabData(activeTab.value)
}

function loadTabData(tab: DebugTab) {
  if (!developerMode.value || !isTauriRuntime()) return
  if (tab === 'runtime-tools' && !runtimeDebugInfo.value && !runtimeDebugLoading.value) void loadRuntimeDebugPointers()
  if (tab === 'logs' && !debugLogContent.value && !debugLogLoading.value) void loadDebugLog()
  if (tab === 'models' && !debugCatalogInfo.value && !debugCatalogLoading.value) void loadDebugCatalog()
  if (tab === 'models' && !modelStore.models.length) modelStore.loadModels().catch(() => {})
  if (tab === 'activity') void loadActivity()
}

onMounted(() => {
  loadDebugPageData()
})

watch(selectedDebugModel, () => {
  if (!developerMode.value) return
  loadDebugModelConfig().catch(() => {})
})

watch(debugModelOptions, (options) => {
  if (selectedDebugModel.value && !options.some((item) => item.value === selectedDebugModel.value)) {
    selectedDebugModel.value = ''
    debugModelConfig.value = null
    debugConfigText.value = ''
  }
})

watch(currentRuntimeOverrideDefaults, () => {
  syncRuntimeOverrideFromCurrent()
}, { immediate: true })

watch(activeTab, (tab) => {
  loadTabData(tab)
  if (tab === 'logs') scrollDebugLogToLatest().catch(() => {})
})

watch(developerMode, (enabled) => {
  if (enabled) {
    loadDebugPageData()
  }
  else {
    app.clearWorkerEvents()
    workerEventDialogVisible.value = false
    selectedWorkerEvent.value = null
    runtimeDebugEditorVisible.value = false
    catalogModelDialogVisible.value = false
    executionLogVisible.value = false
    runtimeDebugInfo.value = null
    debugLogInfo.value = null
    debugLogContent.value = null
  }
})
</script>

<template>
  <div class="page debug-page" :class="{ 'debug-page--locked': !developerMode }">
    <div class="page-header-compact debug-page__header">
      <div>
        <h1>{{ t('debug.title') }}</h1>
        <p>{{ t('debug.subtitle') }}</p>
      </div>
      <div class="debug-header-actions">
        <n-button secondary :disabled="!developerMode || diagnosticBusy" @click="copyDiagnosticSummary">{{ t('debug.copySummary') }}</n-button>
        <n-button secondary :disabled="!developerMode || debugLogClearing" :loading="debugLogReportLoading" @click="createDebugLogReport">{{ t('debug.createReport') }}</n-button>
        <n-button type="primary" :disabled="!developerMode || diagnosticBusy" :loading="diagnosticBusy" @click="checkEnv">
          <template #icon><n-icon :component="RefreshOutline" /></template>
          {{ diagnosticBusy ? t('settings.checkingEnv') : t('settings.checkEnv') }}
        </n-button>
      </div>
    </div>

    <div v-if="!developerMode" class="debug-page__lock" aria-live="polite">
      <section class="debug-lock-card" aria-labelledby="debug-lock-title">
        <div class="debug-lock-card__icon">
          <n-icon :component="LockClosedOutline" />
        </div>
        <div class="debug-lock-card__copy">
          <strong id="debug-lock-title">{{ t('debug.disabledTitle') }}</strong>
          <p>{{ t('debug.disabledHint') }}</p>
        </div>
      </section>
    </div>

    <div class="debug-page__content" :class="{ 'debug-page__content--locked': !developerMode }" :inert="!developerMode || undefined">
    <div class="debug-navigation">
    <nav class="debug-tabs" :aria-label="t('debug.sectionNavigation')">
      <button
        v-for="tab in debugTabs"
        :key="tab.key"
        type="button"
        class="debug-tab"
        :class="{ 'debug-tab--active': activeTab === tab.key }"
        :aria-current="activeTab === tab.key ? 'page' : undefined"
        @click="activeTab = tab.key"
      >
        {{ tab.label }}
      </button>
    </nav>
    <nav class="debug-advanced-nav" :aria-label="t('debug.advancedTools')">
      <button v-for="tab in advancedTabs" :key="tab.key" type="button" class="debug-tab" :class="{ 'debug-tab--active': activeTab === tab.key }" :aria-current="activeTab === tab.key ? 'page' : undefined" @click="activeTab = tab.key">{{ tab.label }}</button>
    </nav>
    </div>

    <section v-if="activeTab === 'overview'" class="debug-overview-summary">
    <div class="debug-status-grid">
      <button v-for="item in statusCards" :key="item.label" type="button" class="debug-status-card" :class="`debug-status-card--${item.tone}`" @click="openStatusCard(item.tab)">
        <span>{{ item.label }}</span>
        <strong>{{ item.value }}</strong>
        <small>{{ item.detail }}</small>
      </button>
    </div>

    <div class="debug-check-status" aria-live="polite">
      <span>{{ diagnosticBusy ? t('settings.checkingEnv') : lastCheckedAt ? `${t('debug.lastChecked')} ${eventTime(new Date(lastCheckedAt).toISOString())}` : t('debug.cachedSnapshot') }}</span>
      <span>{{ t('debug.issueCounts', { errors: diagnosticSummary.error, warnings: diagnosticSummary.warn, unknown: diagnosticSummary.unknown }) }}</span>
    </div>
    <article v-if="latestFailedTask" class="debug-latest-failure">
      <n-icon :component="AlertCircleOutline" />
      <div>
        <span>{{ t('debug.latestFailure') }} · {{ new Date(latestFailedTask.updatedAt).toLocaleString() }}</span>
        <strong>{{ latestFailedTask.runConfig?.workflowName || latestFailedTask.model }}</strong>
        <p :title="latestFailedTask.error || latestFailedTask.message">{{ latestFailedTask.error || latestFailedTask.message }}</p>
      </div>
      <n-button size="small" secondary @click="showExecutionLogs(latestFailedTask.id)">{{ t('debug.viewTaskDetails') }}</n-button>
    </article>
    <p v-else class="debug-overview-empty">{{ t('debug.noFailures') }}</p>
    </section>

    <details v-if="activeTab === 'overview'" class="debug-health-details" :open="diagnosticDetailsOpen" @toggle="diagnosticDetailsOpen = ($event.target as HTMLDetailsElement).open">
    <summary>{{ t('debug.diagnosticDetails') }}<span>{{ t('debug.checkCounts', { ok: diagnosticSummary.ok, total: diagnostics.length }) }}</span></summary>
    <section v-if="activeTab === 'overview'" class="debug-health-grid" :aria-busy="diagnosticBusy">
      <n-card v-for="group in diagnosticGroups" :key="group.key" class="debug-card" :bordered="true" size="small">
        <template #header>
          <div class="debug-section-title">
            <n-icon :component="group.icon" />
            <span>{{ group.title }}</span>
          </div>
        </template>
        <p class="debug-group-hint">{{ group.hint }}</p>
        <div class="diagnostic-list">
          <div v-for="item in group.checks" :key="item.key" class="diagnostic-row" :class="`diagnostic-row--${item.state}`">
            <n-icon :component="diagnosticIcon(item.state)" />
            <div class="diagnostic-row__main">
              <div class="diagnostic-row__head">
                <strong>{{ item.label.startsWith('debug.') ? t(item.label) : item.label }}</strong>
                <span class="diagnostic-state">{{ item.state === 'unknown' && diagnosticBusy ? t('settings.checkingEnv') : t(diagnosticStateLabels[item.state]) }}</span>
              </div>
              <span v-if="item.value">{{ item.value }}</span>
              <code v-if="item.detail">{{ item.detail }}</code>
              <p v-if="item.hint && ['warn', 'error', 'optional'].includes(item.state)">{{ t(item.hint) }}</p>
              <button v-if="['warn', 'error', 'optional'].includes(item.state) || item.detail && item.state === 'unknown'" type="button" class="diagnostic-action" @click="openDiagnosticAction(item.action)">{{ t(item.action === 'runtime' ? 'debug.manageRuntime' : item.action === 'tools' ? 'debug.openTools' : 'debug.tabEvents') }}</button>
              <n-button v-if="item.key === 'connection' && item.state !== 'ok' && isTauriRuntime()" size="tiny" tertiary :loading="app.workerEventConnectionStatus === 'connecting'" :disabled="app.workerEventConnectionStatus === 'connecting'" @click="reconnectWorkerEvents">{{ t('debug.reconnectEvents') }}</n-button>
            </div>
          </div>
        </div>
      </n-card>
    </section>
    </details>

    <section v-if="activeTab === 'runtime'" class="debug-grid debug-environment-grid">
      <n-card class="debug-card" :bordered="true" size="small">
        <template #header>
          <div class="debug-section-title">
            <n-icon :component="InformationCircleOutline" />
            <span>{{ t('debug.runtimeParams') }}</span>
          </div>
        </template>
        <div class="kv-list">
          <div v-for="row in [...runtimeStatusRows, ...runtimeRows]" :key="row.label" class="kv-row">
            <span>{{ row.label }}</span>
            <code>{{ row.value || '-' }}</code>
          </div>
        </div>
        <div v-if="cudaDevices.length" class="cuda-list">
          <h3>{{ t('debug.cudaDevices') }}</h3>
          <div v-for="gpu in cudaDevices" :key="gpu.id" class="cuda-row">
            <span>CUDA {{ gpu.id }}</span>
            <strong>{{ gpu.name }}</strong>
            <code v-if="gpu.totalMemoryBytes">{{ formatBytes(gpu.totalMemoryBytes) }}</code>
          </div>
        </div>
      </n-card>

    <n-card class="debug-card" :bordered="true" size="small">
      <template #header>
        <div class="debug-section-title">
          <n-icon :component="FolderOpenOutline" />
          <span>{{ t('debug.paths') }}</span>
        </div>
      </template>
      <div class="path-debug-grid">
        <div v-for="row in pathRows" :key="row.label" class="path-debug-row">
          <span>{{ row.label }}</span>
          <code :title="row.value || '-'">{{ row.value || '-' }}</code>
          <n-button size="tiny" tertiary :disabled="!row.value" @click="copyText(row.value, 'debug.pathCopied')">{{ t('common.copy') }}</n-button>
          <n-button size="tiny" secondary :disabled="!row.value" @click="revealPath(row.value)">
            {{ t('common.open') }}
          </n-button>
        </div>
      </div>
    </n-card>

    </section>

      <n-card v-if="activeTab === 'updates'" class="debug-card" :bordered="true" size="small">
        <template #header>
          <div class="debug-section-title">
            <n-icon :component="CloudDownloadOutline" />
            <span>{{ t('debug.updateEndpointTitle') }}</span>
          </div>
        </template>
        <div class="update-endpoint-panel">
          <div class="update-endpoint-panel__head">
            <n-tag size="small" :bordered="false" :type="updateEndpointOverride ? 'warning' : 'success'">
              {{ updateEndpointMode }}
            </n-tag>
            <div class="update-endpoint-panel__actions">
              <n-button size="tiny" secondary :disabled="!updateEndpointOverride" @click="resetUpdateEndpointOverride">
                {{ t('debug.updateEndpointRestore') }}
              </n-button>
            </div>
          </div>
          <p>{{ t('debug.updateEndpointHint') }}</p>
          <n-input
            v-if="updateEndpointEditing"
            v-model:value="updateEndpointDraft"
            size="small"
            :placeholder="t('debug.updateEndpointPlaceholder')"
            @keydown.enter.prevent="saveUpdateEndpointOverride"
            @keydown.esc.prevent="cancelUpdateEndpointEdit"
          />
          <n-input
            v-else
            :value="effectiveUpdateEndpoint"
            size="small"
            readonly
            @click="beginUpdateEndpointEdit"
          />
          <template v-if="updateEndpointEditing">
            <div class="update-endpoint-panel__actions update-endpoint-panel__actions--right">
              <n-button size="small" secondary @click="cancelUpdateEndpointEdit">{{ t('common.cancel') }}</n-button>
              <n-button size="small" type="primary" secondary @click="saveUpdateEndpointOverride">{{ t('common.save') }}</n-button>
            </div>
          </template>
        </div>
      </n-card>

    <n-card v-if="activeTab === 'runtime-tools'" class="debug-card" :bordered="true" size="small">
      <template #header>
        <div class="runtime-card-header">
          <div class="debug-section-title">
            <n-icon :component="FolderOpenOutline" />
            <span>{{ t('debug.runtimePageTitle') }}</span>
          </div>
          <n-button size="tiny" secondary :loading="runtimeDebugLoading || diagnosticBusy" :disabled="runtimeDebugLoading || diagnosticBusy" @click="checkEnv">
            {{ t('common.refresh') }}
          </n-button>
        </div>
      </template>
      <n-alert v-if="runtimeDebugError" type="error" class="debug-log-read-error">{{ runtimeDebugError }}</n-alert>
      <n-alert v-if="!app.runtimeDebugSupported" type="info" :title="t('settings.runtimeDebugWindowsOnly')">
        {{ t('settings.runtimeDebugReadOnlyHint') }}
      </n-alert>
      <section class="runtime-status-panel">
        <div class="runtime-panel-copy">
          <strong>{{ t('debug.runtimeCurrentTitle') }}</strong>
          <p>{{ t('debug.runtimeCurrentHint') }}</p>
        </div>
        <div class="runtime-status-grid">
          <div v-for="row in runtimeStatusRows" :key="row.label" class="runtime-status-item">
            <span>{{ row.label }}</span>
            <code :title="row.value || '-'">{{ row.value || '-' }}</code>
          </div>
        </div>
      </section>

      <div class="runtime-page-grid">
        <section class="runtime-info-section runtime-info-section--wide">
          <div class="runtime-section-head">
            <div>
              <strong>{{ t('debug.runtimePointerTitle') }}</strong>
              <p>{{ t('debug.runtimePointerHint') }}</p>
            </div>
          </div>
          <div class="kv-list runtime-pointer-list">
            <div v-for="row in runtimePointerRows" :key="row.label" class="kv-row">
              <span>{{ row.label }}</span>
              <code :title="row.value || '-'">{{ row.value || '-' }}</code>
            </div>
          </div>
        </section>

        <section class="runtime-info-section">
          <div class="runtime-section-head">
            <div>
              <strong>{{ t('debug.runtimeEditableTitle') }}</strong>
              <p>{{ t('debug.runtimeEditableHint') }}</p>
            </div>
          </div>
          <div class="runtime-file-editor-list">
            <article v-if="runtimeDebugActiveFile" class="runtime-file-editor runtime-file-editor--primary">
              <div class="runtime-file-editor__head">
                <div class="runtime-file-editor__title">
                  <strong>active-runtime</strong>
                  <span>{{ runtimeDebugActiveFile.source }} · {{ runtimeDebugActiveFile.exists ? t('debug.runtimeDebugFileExists') : t('debug.runtimeDebugFileMissing') }}</span>
                </div>
                <div class="runtime-file-editor__actions">
                  <n-button size="tiny" secondary :disabled="!runtimeDebugActiveFile.path" @click="revealPath(runtimeDebugActiveFile.path)">{{ t('common.open') }}</n-button>
                  <n-button size="tiny" secondary :disabled="!app.runtimeDebugSupported || !developerMode || !runtimeDebugActiveFile.editable" @click="openRuntimeDebugEditor(runtimeDebugActiveFile)">{{ t('debug.runtimeDebugEdit') }}</n-button>
                  <n-button size="tiny" secondary :disabled="!app.runtimeDebugSupported || !developerMode || !runtimeDebugActiveFile.backupExists" :loading="runtimeDebugSaving === runtimeDebugActiveFile.path" @click="restoreRuntimeDebugFile(runtimeDebugActiveFile)">{{ t('debug.runtimeDebugRestore') }}</n-button>
                </div>
              </div>
              <button type="button" class="runtime-file-editor__path-button" :disabled="!app.runtimeDebugSupported || !developerMode || !runtimeDebugActiveFile.editable" :title="t('debug.runtimeDebugEdit')" @click="openRuntimeDebugEditor(runtimeDebugActiveFile)">
                <code class="runtime-file-editor__path">{{ runtimeDebugActiveFile.path }}</code>
              </button>
            </article>
            <article v-for="envItem in runtimeDebugEnvironments" :key="`${envItem.backend}-${envItem.pythonPath}`" class="runtime-file-editor">
              <div class="runtime-file-editor__head">
                <div class="runtime-file-editor__title">
                  <strong>{{ envItem.backend }}</strong>
                   <span>{{ envItem.editableFile ? t('debug.runtimeDebugConfigFile') : t('debug.runtimeDebugNoConfigFile') }}</span>
                </div>
                <div class="runtime-file-editor__actions">
                  <n-button size="tiny" secondary :disabled="!envItem.pythonPath" @click="revealPath(envItem.pythonPath)">{{ t('debug.runtimeOpenPython') }}</n-button>
                  <n-button size="tiny" secondary :disabled="!app.runtimeDebugSupported || !developerMode || !envItem.editableFile?.editable" @click="envItem.editableFile && openRuntimeDebugEditor(envItem.editableFile)">{{ t('debug.runtimeDebugEdit') }}</n-button>
                  <n-button size="tiny" secondary :disabled="!app.runtimeDebugSupported || !developerMode || !envItem.editableFile?.backupExists" :loading="runtimeDebugSaving === envItem.editablePath" @click="envItem.editableFile && restoreRuntimeDebugFile(envItem.editableFile)">{{ t('debug.runtimeDebugRestore') }}</n-button>
                </div>
              </div>
              <div class="runtime-file-editor__paths">
                <div>
                  <span>{{ t('debug.runtimePythonExecutable') }}</span>
                  <code :title="envItem.pythonPath || '-'">{{ envItem.pythonPath || '-' }}</code>
                </div>
                <button v-if="envItem.editableFile" type="button" class="runtime-file-editor__path-button" :disabled="!app.runtimeDebugSupported || !developerMode || !envItem.editableFile.editable" :title="t('debug.runtimeDebugEdit')" @click="openRuntimeDebugEditor(envItem.editableFile)">
                  <span>{{ t('debug.runtimeVenvConfig') }}</span>
                  <code class="runtime-file-editor__path runtime-file-editor__path--config">{{ envItem.editablePath }}</code>
                </button>
              </div>
            </article>
            <n-empty v-if="!runtimeDebugActiveFile && !runtimeDebugEnvironments.length" :description="t('debug.runtimeDebugNoFiles')" />
          </div>
        </section>

        <section class="runtime-info-section">
          <div class="runtime-section-head">
            <div>
              <strong>{{ t('debug.runtimeOverrideTitle') }}</strong>
              <p>{{ t('debug.runtimeOverrideHint') }}</p>
            </div>
          </div>
          <div v-if="app.runtimeDebugSupported" class="runtime-override-form">
            <label>
              <span>{{ t('debug.runtimeOverrideBackend') }}</span>
              <n-select v-model:value="runtimeOverrideBackend" size="small" :options="runtimeBackendOptions" @update:value="markRuntimeOverrideDirty" />
            </label>
            <label>
              <span>{{ t('debug.runtimeOverridePythonPath') }}</span>
              <n-input v-model:value="runtimeOverridePythonPath" size="small" :placeholder="t('debug.runtimeDebugPythonPathPlaceholder')" @update:value="markRuntimeOverrideDirty" />
            </label>
            <div class="runtime-override-actions">
              <n-button size="small" secondary @click="resetRuntimeOverrideForm">
                {{ t('debug.runtimeOverrideReset') }}
              </n-button>
              <n-button size="small" type="primary" secondary :loading="runtimeDebugSaving === 'active-runtime'" :disabled="!developerMode || !runtimeOverridePythonPath.trim()" @click="overrideActiveRuntimePointer">
                {{ t('debug.runtimeDebugOverrideActive') }}
              </n-button>
            </div>
          </div>
        </section>

        <section class="runtime-info-section runtime-info-section--wide">
          <div class="runtime-section-head">
            <div>
              <strong>{{ t('debug.runtimeTreeTitle') }}</strong>
            </div>
          </div>
          <div class="runtime-tree">
            <div v-for="node in runtimeTree" :key="node.name" class="runtime-tree__node">
              <div class="runtime-tree__head">
                <span class="runtime-tree__chevron" aria-hidden="true">›</span>
                <strong>{{ node.name }}</strong>
                <span class="runtime-tree__role">{{ node.role }}</span>
                <em>{{ node.source }}</em>
              </div>
              <code class="runtime-tree__path" :title="node.path">{{ node.path }}</code>
              <div v-if="node.children.length" class="runtime-tree__children">
                <div v-for="child in node.children" :key="`${node.name}-${child.name}`" class="runtime-tree__child">
                  <span class="runtime-tree__branch" aria-hidden="true">└</span>
                  <div class="runtime-tree__child-main">
                    <div class="runtime-tree__child-head">
                      <strong>{{ child.name }}</strong>
                      <span>{{ child.role }}</span>
                    </div>
                    <code :title="child.path">{{ child.path }}</code>
                  </div>
                </div>
              </div>
            </div>
            <n-empty v-if="!app.runtimeInfo" :description="t('debug.envNotChecked')" />
          </div>
        </section>
      </div>
    </n-card>

    <n-modal v-model:show="runtimeDebugEditorVisible" preset="card" style="width: min(760px, 92vw)" :mask-closable="true" @after-leave="runtimeDebugEditingPath = ''; runtimeDebugEditingContent = ''">
      <template #header>
        <div class="debug-section-title">
          <n-icon :component="TerminalOutline" />
          <span>{{ t('debug.runtimeDebugEditorTitle') }}</span>
        </div>
      </template>
      <div class="runtime-debug-editor-modal">
        <div class="kv-list">
          <div class="kv-row">
            <span>{{ t('debug.runtimeDebugEditPath') }}</span>
            <code :title="runtimeDebugEditingPath">{{ runtimeDebugEditingPath || '-' }}</code>
          </div>
        </div>
        <n-input
          v-model:value="runtimeDebugEditingContent"
          type="textarea"
          size="small"
          :autosize="{ minRows: 12, maxRows: 24 }"
          :placeholder="t('debug.runtimeDebugEmptyFile')"
        />
      </div>
      <template #footer>
        <div class="runtime-debug-editor-modal__footer">
          <n-button secondary @click="runtimeDebugEditorVisible = false">{{ t('common.close') }}</n-button>
          <n-button type="primary" secondary :disabled="!app.runtimeDebugSupported" :loading="runtimeDebugSaving === runtimeDebugEditingPath" @click="saveRuntimeDebugEditor">
            {{ t('common.save') }}
          </n-button>
        </div>
      </template>
    </n-modal>

    <section v-if="activeTab === 'models'" class="debug-model-workbench">
      <n-card class="debug-card" :bordered="true" size="small">
        <template #header>
          <div class="debug-section-title">
            <n-icon :component="TerminalOutline" />
            <span>{{ t('debug.debugCatalogTitle') }}</span>
          </div>
        </template>
        <n-alert v-if="debugCatalogError" type="error" class="debug-log-read-error">{{ debugCatalogError }}</n-alert>
        <div class="debug-editor-meta">
          <div class="kv-row">
            <span>{{ t('debug.debugCatalogPath') }}</span>
            <code>{{ debugCatalogInfo?.debugCatalogPath || '-' }}</code>
          </div>
          <div class="debug-task-context debug-directory-row">
            <span>{{ t('debug.debugDir') }} · {{ debugCatalogInfo?.debugDir || '-' }}</span>
            <div class="debug-directory-actions">
              <n-button size="tiny" secondary :disabled="!debugCatalogInfo?.debugDir" @click="revealPath(debugCatalogInfo?.debugDir || '')">{{ t('common.open') }}</n-button>
              <n-button size="tiny" tertiary :disabled="!debugCatalogInfo?.debugDir" @click="copyText(debugCatalogInfo?.debugDir || '', 'debug.pathCopied')">{{ t('common.copy') }}</n-button>
            </div>
          </div>
          <n-alert type="info" :show-icon="true">
            {{ t('debug.debugCatalogHint') }}
          </n-alert>
        </div>
        <div class="catalog-mode-switch">
          <n-button-group size="small">
            <n-button :type="catalogEditMode === 'simple' ? 'primary' : 'default'" secondary @click="switchCatalogEditMode('simple')">
              {{ t('debug.catalogSimpleMode') }}
            </n-button>
            <n-button :type="catalogEditMode === 'advanced' ? 'primary' : 'default'" secondary @click="switchCatalogEditMode('advanced')">
              {{ t('debug.catalogAdvancedMode') }}
            </n-button>
          </n-button-group>
          <n-button size="small" secondary @click="addCatalogModel">
            {{ t('debug.addCatalogModel') }}
          </n-button>
        </div>
        <div v-if="catalogEditMode === 'simple'" class="catalog-simple-list">
          <n-input v-model:value="catalogSearch" size="small" clearable :placeholder="t('debug.catalogSearch')" />
          <button
            v-for="model in visibleCatalogModels"
            :key="model.name"
            type="button"
            class="catalog-model-row"
            @click="selectCatalogModel(model.name)"
          >
            <div class="catalog-model-row__main">
              <strong>{{ model.name }}</strong>
              <span>{{ model.model_type || model.architecture || '-' }} · {{ model.primary_category || '-' }}</span>
            </div>
            <n-tag v-if="catalogModelStatus(model) === 'modified'" size="small" type="error" :bordered="false">
              {{ t('debug.catalogStatusModified') }}
            </n-tag>
            <n-tag v-else-if="catalogModelStatus(model) === 'added'" size="small" type="error" :bordered="false">
              {{ t('debug.catalogStatusAdded') }}
            </n-tag>
          </button>
          <n-empty v-if="!visibleCatalogModels.length" :description="t('debug.catalogNoModels')" />
        </div>
        <n-input
          v-else
          v-model:value="debugCatalogText"
          type="textarea"
          class="debug-code-editor"
          :autosize="{ minRows: 14, maxRows: 24 }"
          spellcheck="false"
        />
        <n-modal
          v-model:show="catalogModelDialogVisible"
          preset="card"
          :title="t('debug.catalogModelDialogTitle')"
          :style="{ width: 'min(820px, calc(100vw - 48px))' }"
        >
          <div class="catalog-model-form">
            <div class="catalog-form-grid">
              <label>
                <span>{{ t('debug.catalogFieldName') }}</span>
                <n-input v-model:value="catalogModelDraft.name" size="small" />
              </label>
              <label>
                <span>{{ t('debug.catalogFieldAliases') }}</span>
                <n-input v-model:value="catalogModelDraft.aliasesText" size="small" />
              </label>
              <label>
                <span>{{ t('debug.catalogFieldModelType') }}</span>
                <n-input v-model:value="catalogModelDraft.model_type" size="small" />
              </label>
              <label>
                <span>{{ t('debug.catalogFieldArchitecture') }}</span>
                <n-input v-model:value="catalogModelDraft.architecture" size="small" />
              </label>
              <label class="catalog-form-grid__wide">
                <span>{{ t('debug.catalogFieldRelpath') }}</span>
                <n-input v-model:value="catalogModelDraft.relpath" size="small" />
              </label>
              <label class="catalog-form-grid__wide">
                <span>{{ t('debug.catalogFieldDownloadUrl') }}</span>
                <n-input :value="currentCatalogDownloadUrl(catalogModelDraft.relpath) || '-'" size="small" readonly />
              </label>
              <label class="catalog-form-grid__wide">
                <span>{{ t('debug.catalogFieldConfigRelpath') }}</span>
                <n-input v-model:value="catalogModelDraft.config_relpath" size="small" />
              </label>
              <label>
                <span>{{ t('debug.catalogFieldPrimaryCategory') }}</span>
                <n-input v-model:value="catalogModelDraft.primary_category" size="small" />
              </label>
              <label>
                <span>{{ t('debug.catalogFieldPrimaryCategoryCn') }}</span>
                <n-input v-model:value="catalogModelDraft.primary_category_cn" size="small" />
              </label>
              <label>
                <span>{{ t('debug.catalogFieldSecondaryCategory') }}</span>
                <n-input v-model:value="catalogModelDraft.secondary_category" size="small" />
              </label>
              <label>
                <span>{{ t('debug.catalogFieldTargetStem') }}</span>
                <n-input v-model:value="catalogModelDraft.target_stem" size="small" />
              </label>
              <label>
                <span>{{ t('debug.catalogFieldSize') }}</span>
                <n-input-number v-model:value="catalogModelDraft.size_bytes" size="small" :min="0" class="catalog-number" />
              </label>
              <label class="catalog-checkbox-row">
                <n-checkbox v-model:checked="catalogModelDraft.supported">
                  {{ t('debug.catalogFieldSupported') }}
                </n-checkbox>
              </label>
            </div>
          </div>
          <template #footer>
            <div class="debug-editor-actions">
              <n-button secondary :disabled="!selectedCatalogModelName" @click="removeCatalogModel(selectedCatalogModelName); catalogModelDialogVisible = false">
                {{ t('debug.removeCatalogModel') }}
              </n-button>
              <n-button secondary @click="catalogModelDialogVisible = false">
                {{ t('common.cancel') }}
              </n-button>
              <n-button type="primary" @click="saveCatalogDraft">
                {{ t('debug.applyCatalogModel') }}
              </n-button>
            </div>
          </template>
        </n-modal>
        <div class="debug-editor-actions">
          <n-button secondary :loading="debugCatalogLoading" @click="loadDebugCatalog">
            {{ t('common.refresh') }}
          </n-button>
          <n-button secondary type="warning" :loading="debugCatalogSaving" @click="resetDebugCatalog">
            {{ t('debug.restorePymssCatalog') }}
          </n-button>
          <n-button type="primary" :loading="debugCatalogSaving" @click="saveDebugCatalog">
            {{ t('debug.saveDebugCatalog') }}
          </n-button>
        </div>
      </n-card>

      <n-card class="debug-card" :bordered="true" size="small">
        <template #header>
          <div class="debug-section-title">
            <n-icon :component="FolderOpenOutline" />
            <span>{{ t('debug.modelConfigTitle') }}</span>
          </div>
        </template>
        <div class="debug-config-controls">
          <n-select
            v-model:value="selectedDebugModel"
            :options="debugModelOptions"
            filterable
            clearable
            :placeholder="t('debug.pickModel')"
          />
          <n-button secondary :disabled="!selectedDebugModel" :loading="debugConfigLoading" @click="loadDebugModelConfig">
            {{ t('common.refresh') }}
          </n-button>
        </div>
        <div v-if="debugModelConfig" class="debug-editor-meta">
          <n-alert v-if="debugModelConfig.readOnly" type="warning" :show-icon="true">
            {{ t('debug.userConfigReadonlyHint') }}
          </n-alert>
          <div class="kv-row">
            <span>{{ t('debug.effectiveConfigPath') }}</span>
            <code>{{ debugModelConfig.effectiveConfigPath || '-' }}</code>
          </div>
          <div class="kv-row">
            <span>{{ t('debug.baseConfigPath') }}</span>
            <code>{{ debugModelConfig.baseConfigPath || '-' }}</code>
          </div>
        </div>
        <n-input
          v-model:value="debugConfigText"
          type="textarea"
          class="debug-code-editor"
          :autosize="{ minRows: 14, maxRows: 24 }"
          spellcheck="false"
          :disabled="debugModelConfig?.readOnly"
        />
        <div class="debug-editor-actions">
          <n-button secondary :disabled="!debugModelConfig || debugModelConfig.readOnly" :loading="debugConfigLoading" @click="resetDebugModelConfig">
            {{ t('debug.restoreModelConfig') }}
          </n-button>
          <n-button type="primary" :disabled="!debugModelConfig || debugModelConfig.readOnly || !debugModelConfig.baseConfigPath" :loading="debugConfigSaving" @click="saveDebugModelConfig">
            {{ t('debug.saveDebugConfig') }}
          </n-button>
        </div>
      </n-card>
    </section>

    <section v-if="activeTab === 'activity'" class="debug-grid debug-activity-grid">
      <n-alert v-if="activityLoadError" class="debug-activity-error" type="error">{{ activityLoadError }}</n-alert>
      <n-card class="debug-card" :bordered="true" size="small">
        <template #header>
          <div class="debug-section-title">
            <n-icon :component="TimeOutline" />
            <span>{{ t('debug.activeTasks') }}</span>
          </div>
        </template>
        <div class="task-debug-list">
          <div v-for="item in runningTasks" :key="item.id" class="debug-task-entry">
            <div class="debug-task-entry__head">
              <strong>{{ item.runConfig?.workflowName || item.model }}</strong>
              <span>{{ t(taskStatusKeys[item.status]) }} · {{ Math.round(item.progress || 0) }}%</span>
            </div>
            <div class="debug-task-entry__meta">
              <span>{{ t(item.runConfig?.runMode === 'workflow' ? 'debug.workflowTasks' : 'debug.modelTask') }}</span>
              <code :title="item.id">{{ shortTaskId(item.id) }}</code>
              <span v-if="item.batchId">{{ t('debug.batchTask') }}{{ item.batchIndex != null ? ` #${item.batchIndex + 1}` : '' }}</span>
            </div>
            <code class="debug-task-entry__input" :title="item.input">{{ item.input }}</code>
            <p v-if="item.stageLabel || item.progressDetail">{{ item.stageLabel }} {{ item.progressDetail }}</p>
            <div class="debug-task-entry__actions">
              <n-button size="tiny" secondary @click="showExecutionLogs(item.id)">{{ t('debug.viewTaskDetails') }}</n-button>
            </div>
          </div>
          <n-empty v-if="!runningTasks.length" :description="t('debug.noActiveTasks')" />
        </div>
      </n-card>

      <n-card class="debug-card" :bordered="true" size="small">
        <template #header>
          <div class="debug-section-title">
            <n-icon :component="CloudDownloadOutline" />
            <span>{{ t('debug.downloadTasks') }}</span>
          </div>
        </template>
        <div class="task-debug-list">
          <div v-for="item in Object.values(downloadTasks)" :key="item.taskId" class="debug-task-entry">
            <div class="debug-task-entry__head"><strong>{{ item.model }}</strong><span>{{ downloadStatusLabel(item) }} · {{ Math.round(item.progress || 0) }}%</span></div>
            <div class="debug-task-entry__meta"><code>{{ item.completedFiles }} / {{ item.totalFiles }}</code><code :title="item.taskId">{{ shortTaskId(item.taskId) }}</code></div>
            <n-button size="tiny" secondary @click="showExecutionLogs(item.taskId)">{{ t('debug.viewTaskDetails') }}</n-button>
          </div>
          <n-empty v-if="!Object.values(downloadTasks).length" :description="t('debug.noDownloadTasks')" />
        </div>
      </n-card>

      <n-card class="debug-card debug-failed-tasks" :bordered="true" size="small">
        <template #header><div class="debug-section-title"><n-icon :component="AlertCircleOutline" /><span>{{ t('debug.recentFailures') }}</span></div></template>
        <div ref="failedTaskScroller" class="task-debug-list debug-failed-task-list" :tabindex="failedTasks.length ? 0 : undefined" role="list" :aria-label="t('debug.recentFailures')">
          <div v-for="item in pagedFailedTasks" :key="item.id" class="debug-task-entry" role="listitem">
            <div class="debug-task-entry__head"><strong>{{ item.runConfig?.workflowName || item.model }}</strong><span>{{ new Date(item.updatedAt).toLocaleString() }}</span></div>
            <code :title="item.id">{{ shortTaskId(item.id) }}</code>
            <p class="debug-task-entry__error" :title="item.error || item.message">{{ item.error || item.message }}</p>
            <div class="debug-task-entry__actions">
              <n-button size="tiny" secondary @click="showExecutionLogs(item.id)">{{ t('debug.viewTaskDetails') }}</n-button>
            </div>
          </div>
          <n-empty v-if="!failedTasks.length" :description="t('debug.noFailures')" />
        </div>
        <div v-if="failedTasks.length" class="debug-failed-task-pagination">
          <span aria-live="polite">{{ t('debug.failurePageSummary', {
            start: (failedTaskPage - 1) * failedTaskPageSize + 1,
            end: Math.min(failedTaskPage * failedTaskPageSize, failedTasks.length),
            total: failedTasks.length,
          }) }}</span>
          <n-pagination
            v-if="failedTasks.length > failedTaskPageSize"
            v-model:page="failedTaskPage"
            :page-size="failedTaskPageSize"
            :item-count="failedTasks.length"
            :page-slot="5"
            size="small"
          />
        </div>
      </n-card>

      <n-card class="debug-card" :bordered="true" size="small">
        <template #header><div class="runtime-card-header"><div class="debug-section-title"><n-icon :component="InformationCircleOutline" /><span>{{ t('debug.editorWindows') }}</span></div><n-button size="tiny" secondary @click="loadActivity">{{ t('common.refresh') }}</n-button></div></template>
        <div class="task-debug-list">
          <div v-for="editor in workflow.openedEditors" :key="editor.label" class="debug-task-entry">
            <div class="debug-task-entry__head"><strong>{{ workflow.workflows.find(item => item.id === editor.workflowId)?.name || editor.workflowId || t('debug.unsavedWorkflow') }}</strong><span>{{ t(editor.kind === 'advanced' ? 'debug.advancedEditor' : 'debug.simpleEditor') }}</span></div>
            <code>{{ editor.label }}</code>
          </div>
          <n-empty v-if="!workflow.openedEditors.length" :description="t('debug.noEditors')" />
        </div>
      </n-card>
    </section>

    <n-modal v-model:show="executionLogVisible" preset="card" :title="t('debug.taskDetails')" class="debug-execution-log-modal" :style="{ width: 'min(960px, calc(100vw - 48px))' }">
      <div class="worker-event-modal-meta">
        <strong :title="executionLogName">{{ executionLogName }}</strong>
        <span>{{ executionLogStatus }}</span>
        <code :title="executionLogTaskId">{{ shortTaskId(executionLogTaskId) }}</code>
        <n-button size="tiny" tertiary :disabled="!executionLogText" @click="copyText(executionLogText, 'debug.logCopied')">{{ t('common.copy') }}</n-button>
      </div>
      <div class="debug-task-detail-actions">
        <n-button size="small" secondary @click="showExecutionApplicationLogs">{{ t('debug.taskLogs') }}</n-button>
        <n-button size="small" quaternary @click="showExecutionEvents">{{ t('debug.viewRawEvents') }}</n-button>
      </div>
      <div ref="executionLogBody" class="debug-execution-log-body" role="region" :aria-label="t('tasks.logs')" tabindex="0">
        <p class="debug-task-detail-hint">{{ t('debug.taskDetailLogHint') }}</p>
        <p v-if="executionLogTask?.input" class="debug-task-detail-input">{{ executionLogTask.input }}</p>
        <p v-if="executionLogError" class="debug-execution-log-error" tabindex="0">{{ executionLogError }}</p>
        <div v-if="executionFailedNodes.length" class="debug-failed-nodes">
          <n-button v-for="nodeId in executionFailedNodes" :key="nodeId" size="tiny" secondary type="error" :disabled="!executionNodeLogs[nodeId]?.length" @click="showFailedNodeLogs(nodeId)">{{ t('debug.failedNode', { node: nodeId }) }}</n-button>
        </div>
        <h3 class="debug-task-output-title">{{ t('debug.executionLogs') }}</h3>
        <TaskLogConsole :lines="executionLogs" embedded />
        <details v-for="(lines, nodeId) in executionNodeLogs" :key="nodeId" class="debug-node-logs" :data-node-id="nodeId">
          <summary>{{ t('debug.nodeLogs', { node: nodeId, count: lines.length }) }}</summary>
          <TaskLogConsole :lines="lines" compact embedded />
        </details>
      </div>
    </n-modal>

    <n-card v-if="activeTab === 'events'" class="debug-card debug-card--events" :bordered="true" size="small">
      <template #header>
        <div class="debug-section-title">
          <n-icon :component="TerminalOutline" />
          <span>{{ t('debug.workerEvents') }}</span>
        </div>
      </template>
      <div class="debug-event-toolbar">
        <n-input v-model:value="eventQuery" clearable :placeholder="t('debug.eventSearch')" :input-props="{ 'aria-label': t('debug.eventSearch') }" />
        <n-checkbox v-model:checked="eventIssuesOnly">{{ t('debug.logCategoryIssues') }}</n-checkbox>
        <span>{{ t('debug.eventGroupCount', { shown: recentWorkerEventGroups.length, total: workerEventGroups.length }) }}</span>
      </div>
      <div v-if="eventQuery === executionLogTaskId && canReturnToTask" class="debug-task-context">
        <span>{{ t('debug.logScope', { id: executionLogTaskId }) }}</span>
        <n-button size="tiny" tertiary @click="showExecutionLogs(executionLogTaskId)">{{ t('debug.returnToTask') }}</n-button>
      </div>
      <div class="worker-event-list">
        <button
          v-for="group in recentWorkerEventGroups"
          :key="group.key"
          type="button"
          class="worker-event-row"
          @click="openWorkerEvent(group)"
        >
          <code>{{ group.taskId ? t('debug.workerEventTaskGroup') : group.latestType }}</code>
          <span>{{ shortTaskId(group.taskId) }}</span>
          <span>{{ workerEventGroupSummary(group) }}</span>
          <time>{{ eventTime(group.lastTimestamp) }}</time>
          <strong>{{ group.eventCount }}</strong>
        </button>
        <n-empty v-if="!recentWorkerEventGroups.length" :description="t('settings.developerNoWorkerEvents')" />
      </div>
      <n-modal
        v-model:show="workerEventDialogVisible"
        preset="card"
        :title="workerEventGroupTitle(selectedWorkerEvent)"
        :style="{ width: 'min(920px, calc(100vw - 48px))' }"
      >
        <div class="worker-event-modal-meta">
          <span>{{ shortTaskId(selectedWorkerEvent?.taskId) }}</span>
          <span>{{ t('debug.workerEventCount', { count: selectedWorkerEvent?.eventCount || 0 }) }}</span>
          <span>{{ workerEventGroupSummary(selectedWorkerEvent) }}</span>
          <time>{{ eventTime(selectedWorkerEvent?.lastTimestamp) }}</time>
          <n-button size="tiny" tertiary @click="copyText(workerEventGroupPayload(selectedWorkerEvent), 'debug.eventCopied')">{{ t('common.copy') }}</n-button>
        </div>
        <pre class="worker-event-modal-payload">{{ workerEventGroupPayload(selectedWorkerEvent) }}</pre>
      </n-modal>
    </n-card>

    <n-card v-if="activeTab === 'logs'" class="debug-card debug-card--logs" :bordered="true" size="small">
      <template #header>
        <div class="debug-section-title">
          <n-icon :component="TerminalOutline" />
          <span>{{ t('debug.sessionLog') }}</span>
        </div>
      </template>
      <n-alert v-if="debugLogError" type="error" class="debug-log-read-error">{{ debugLogError }}</n-alert>
      <div class="debug-log-console-head">
        <div class="debug-log-toolbar">
          <n-button size="small" secondary :loading="debugLogLoading" @click="loadDebugLog">
            {{ t('common.refresh') }}
          </n-button>
          <n-button size="small" secondary :disabled="!debugLogInfo?.logsDir" @click="revealPath(debugLogInfo?.logsDir || logsDir)">
            {{ t('debug.openLogsDir') }}
          </n-button>
          <n-button size="small" secondary :disabled="!filteredDebugLogLines.length" @click="copyDebugLog">
            {{ t('debug.copyLog') }}
          </n-button>
          <span class="debug-log-toolbar__spacer" />
          <n-popconfirm @positive-click="clearDebugLog">
            <template #trigger><n-button size="small" secondary type="warning" :disabled="debugLogReportLoading" :loading="debugLogClearing">{{ t('debug.clearLog') }}</n-button></template>
            {{ t('debug.confirmClearLog') }}
          </n-popconfirm>
        </div>
        <details class="debug-log-meta-panel">
          <summary>{{ t('debug.logFileDetails') }}</summary>
          <div class="kv-list debug-log-meta">
            <div v-for="row in debugLogRows" :key="row.label" class="kv-row">
              <span>{{ row.label }}</span>
              <code :title="row.value || '-'">{{ row.value || '-' }}</code>
            </div>
          </div>
        </details>
      </div>
      <n-alert v-if="debugLogContent?.truncated" type="warning" :show-icon="true">
        {{ t('debug.logTailHint') }}
      </n-alert>
      <div v-if="debugLogTask !== 'all'" class="debug-task-context">
        <span>{{ t('debug.logScope', { id: debugLogTask }) }}</span>
        <n-button v-if="debugLogTask === executionLogTaskId && canReturnToTask" size="tiny" tertiary @click="showExecutionLogs(executionLogTaskId)">{{ t('debug.returnToTask') }}</n-button>
      </div>
      <section v-if="parsedDebugLogLines.length" class="debug-log-insights">
        <button class="debug-log-insight debug-log-insight--error" type="button" @click="debugLogLevel = 'error'; debugLogCategory = 'all'">
          <span>{{ t('debug.logErrors') }}</span>
          <strong>{{ debugLogIssueCounts.errors }}</strong>
        </button>
        <button class="debug-log-insight debug-log-insight--warn" type="button" @click="debugLogLevel = 'warn'; debugLogCategory = 'all'">
          <span>{{ t('debug.logWarnings') }}</span>
          <strong>{{ debugLogIssueCounts.warnings }}</strong>
        </button>
        <button class="debug-log-insight debug-log-insight--traceback" type="button" @click="debugLogCategory = 'traceback'; debugLogLevel = 'all'">
          <span>{{ t('debug.logTracebacks') }}</span>
          <strong>{{ debugLogIssueCounts.tracebacks }}</strong>
        </button>
      </section>
      <section v-if="parsedDebugLogLines.length" class="debug-log-viewer">
        <div class="debug-log-filters">
          <n-input v-model:value="debugLogQuery" size="small" clearable :placeholder="t('debug.logSearchPlaceholder')" />
          <n-select v-model:value="debugLogLevel" size="small" :options="debugLogLevelOptions" />
          <n-select v-model:value="debugLogCategory" size="small" :options="debugLogCategoryOptions" />
          <n-select v-model:value="debugLogTask" size="small" filterable :options="debugLogTaskOptions" />
          <n-select v-model:value="debugLogSource" size="small" :options="debugLogSourceOptions" />
          <n-button size="small" secondary @click="clearDebugLogFilters">
            {{ t('debug.logClearFilters') }}
          </n-button>
        </div>
        <div class="debug-log-viewer__head">
          <span>{{ t('debug.logFilteredCount', { shown: filteredDebugLogLines.length, total: parsedDebugLogLines.length }) }}</span>
          <span>{{ t('debug.logLatestHint') }}</span>
        </div>
        <div v-if="filteredDebugLogLines.length" ref="debugLogScroller" class="debug-log-stream" role="log" aria-live="polite">
          <div class="debug-log-table__head" aria-hidden="true">
            <span>{{ t('debug.logColumnTime') }}</span>
            <span>{{ t('debug.logColumnLevel') }}</span>
            <span>{{ t('debug.logColumnSource') }}</span>
            <span>{{ t('debug.logColumnModule') }}</span>
            <span>{{ t('debug.logColumnScope') }}</span>
            <span>{{ t('debug.logColumnMessage') }}</span>
          </div>
          <article
            v-for="line in filteredDebugLogLines"
            :key="line.id"
            class="debug-log-row"
            :class="[`debug-log-row--${line.severity}`, `log-source--${line.sourceKind}`, { 'debug-log-row--expandable': canExpandDebugLogLine(line) }]"
            :title="logMessagePreview(line.record)"
            tabindex="0"
            :aria-expanded="Boolean(expandedDebugLogLines[line.id])"
            @keydown.enter.prevent="toggleDebugLogLine(line)"
            @keydown.space.prevent="toggleDebugLogLine(line)"
            @click="toggleDebugLogLine(line)"
          >
            <code class="debug-log-row__time">{{ line.timestamp || '-' }}</code>
            <span class="debug-log-row__level">{{ line.level || line.severity }}</span>
            <code class="debug-log-row__source" :title="line.source || '-'">{{ logSourceLabel(line.sourceKind) }}</code>
            <code class="debug-log-row__module" :title="line.source">{{ line.source || '—' }}</code>
            <code class="debug-log-row__location" :title="[line.taskId, line.record.nodeId].filter(Boolean).join(' / ')">{{ line.record.nodeId || shortTaskId(line.taskId) || '—' }}</code>
            <span class="debug-log-row__message"><span class="debug-log-row__message-text">{{ line.message || line.record.event || line.raw }}</span><span v-if="line.messageContext" class="debug-log-row__message-context"> · {{ line.messageContext }}</span></span>
            <div v-if="expandedDebugLogLines[line.id]" class="debug-log-row__detail" @click.stop @keydown.stop>
              <div v-if="line.message" class="debug-log-row__full-message">
                <span>{{ t('debug.logColumnMessage') }}</span>
                <pre>{{ line.message }}</pre>
              </div>
              <div v-if="line.location" class="debug-log-row__location-detail">
                <span>{{ t('debug.logLocation') }}</span>
                <code>{{ line.location }}</code>
              </div>
              <pre v-if="line.traceback">{{ line.traceback }}</pre>
              <pre v-if="line.details">{{ line.details }}</pre>
              <div class="debug-log-row__raw">
                <div class="debug-log-row__raw-head">
                  <span>{{ t('debug.logRawLine') }}</span>
                  <n-button size="tiny" quaternary @click.stop="copyText(line.raw, 'debug.logCopied')">{{ t('common.copy') }}</n-button>
                </div>
                <pre>{{ line.raw }}</pre>
              </div>
            </div>
          </article>
        </div>
        <n-empty v-else :description="t('debug.logNoFilteredResults')">
          <template #extra>
            <n-button size="small" secondary @click="clearDebugLogFilters">
              {{ t('debug.logClearFilters') }}
            </n-button>
          </template>
        </n-empty>
      </section>
      <n-empty v-else :description="t('debug.logEmpty')" />
    </n-card>
    </div>
  </div>
</template>

<style scoped src="./DebugView.css"></style>
