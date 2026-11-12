import { invoke } from '@tauri-apps/api/core'
import type {
  EditorProjectCleanupResult,
  ImportEditorAssetsResult,
  OrphanedEditorProject,
  PersistedSession,
  RelinkEditorSourcesResult,
} from '@/stores/editorSupport'
import type { EditorProjectSummary } from '@/types/editor'

/**
 * Typed wrappers for native Tauri commands that are not part of the worker protocol.
 *
 * This module owns the invoke call for every command it wraps: views, stores and
 * composables must call the function here instead of `invoke('<name>', ...)`.
 * `scripts/check-frontend-invokes.py` derives that rule from this file, so adding a
 * wrapper immediately forbids direct invokes of the same command everywhere else.
 */

// ─── 任务 / 路径 ───────────────────────────────────────────────────────────

export function cancelTask(taskId: string): Promise<boolean> {
  return invoke<boolean>('cancel_task', { taskId })
}

export function revealPath(path: string): Promise<void> {
  return invoke('reveal_path', { path })
}

/** `[path, kind]` pairs; kind is `file`, `directory` or `missing`. */
export type PathKind = [string, string]

export function pathKinds(paths: string[]): Promise<PathKind[]> {
  return invoke<PathKind[]>('path_kinds', { paths })
}

// ─── 文件与目录选择 ────────────────────────────────────────────────────────

/** Cancelled pickers resolve to `null`; multi-file pickers resolve to `[]`. */
export function pickAudioFiles(): Promise<string[]> {
  return invoke('pick_audio_files')
}

export function pickMediaFiles(): Promise<string[]> {
  return invoke('pick_media_files')
}

export function pickSingleAudioFile(): Promise<string | null> {
  return invoke('pick_single_audio_file')
}

export function pickInputFolder(): Promise<string | null> {
  return invoke('pick_input_folder')
}

export function pickOutputFolder(): Promise<string | null> {
  return invoke('pick_output_folder')
}

export function pickModelWeightsFile(title?: string | null): Promise<string | null> {
  return invoke('pick_model_weights_file', { title: title ?? null })
}

export function pickModelConfigFile(title?: string | null): Promise<string | null> {
  return invoke('pick_model_config_file', { title: title ?? null })
}

/** Writes `content` to the path chosen in a "Save As" dialog. */
export function saveTextFileDialog(defaultName: string, content: string): Promise<string | null> {
  return invoke('save_text_file_dialog', { defaultName, content })
}

// ─── 路径扫描与回收 ────────────────────────────────────────────────────────

export type ScanPathsResult = {
  files: string[]
  warnings: string[]
}

export function scanMediaPaths(paths: string[]): Promise<ScanPathsResult> {
  return invoke('scan_media_paths', { paths })
}

export function scanImagePaths(paths: string[]): Promise<ScanPathsResult> {
  return invoke('scan_image_paths', { paths })
}

export function scanAudioPathsWithOptions(
  paths: string[],
  options: { recursive?: boolean; sortFiles?: boolean } = {},
): Promise<ScanPathsResult> {
  return invoke('scan_audio_paths_with_options', {
    paths,
    recursive: options.recursive ?? true,
    sortFiles: options.sortFiles ?? true,
  })
}

export type TrashResult = {
  trashed: string[]
  failed: string[]
}

export function movePathsToTrash(paths: string[], emptyDirs: string[] = []): Promise<TrashResult> {
  return invoke('move_paths_to_trash', { paths, emptyDirs })
}

// ─── 窗口与构建信息 ────────────────────────────────────────────────────────

export function closeCurrentWindow(): Promise<void> {
  return invoke('close_current_window')
}

export function minimizeCurrentWindow(): Promise<void> {
  return invoke('minimize_current_window')
}

export function toggleMaximizeCurrentWindow(): Promise<boolean> {
  return invoke('toggle_maximize_current_window')
}

export function isCurrentWindowMaximized(): Promise<boolean> {
  return invoke('is_current_window_maximized')
}

export function startDragCurrentWindow(): Promise<void> {
  return invoke('start_drag_current_window')
}

/** Build metadata embedded by the release build (camelCase on the wire). */
export type BuildInfo = {
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

export function getBuildInfo(): Promise<BuildInfo> {
  return invoke('get_build_info')
}

// ─── 应用存储 ──────────────────────────────────────────────────────────────

export function loadNativeAppStore<T>(name: string): Promise<T | null> {
  return invoke('load_app_store', { name })
}

export function saveNativeAppStore(name: string, data: unknown): Promise<void> {
  return invoke('save_app_store', { name, data })
}

// ─── 编辑器工程 ────────────────────────────────────────────────────────────

export function createEditorProjectFromTask(payload: {
  taskId: string
  input: string
  outputDir: string
  outputs: unknown[]
}): Promise<PersistedSession> {
  return invoke('create_editor_project_from_task', { payload })
}

export function createBlankEditorProject(payload: {
  name: string
  locale?: string
}): Promise<PersistedSession> {
  return invoke('create_blank_editor_project', { payload })
}

export function openEditorWindow(projectId: string): Promise<unknown> {
  return invoke('open_editor_window', { payload: { projectId } })
}

export function listEditorProjects(): Promise<EditorProjectSummary[]> {
  return invoke('list_editor_projects')
}

export function deleteEditorProject(projectId: string): Promise<boolean> {
  return invoke('delete_editor_project', { projectId })
}

export function editorProjectIdsForTasks(taskIds: string[]): Promise<string[]> {
  return invoke('editor_project_ids_for_tasks', { taskIds })
}

export function deleteEditorProjectsForTasks(taskIds: string[]): Promise<EditorProjectCleanupResult> {
  return invoke('delete_editor_projects_for_tasks', { taskIds })
}

export function listOpenEditorProjectsForTasks(taskIds: string[]): Promise<string[]> {
  return invoke('list_open_editor_projects_for_tasks', { taskIds })
}

export function listOrphanedEditorProjects(): Promise<OrphanedEditorProject[]> {
  return invoke('list_orphaned_editor_projects')
}

export function deleteOrphanedEditorProjects(projectIds: string[]): Promise<EditorProjectCleanupResult> {
  return invoke('delete_orphaned_editor_projects', { projectIds })
}

export function loadEditorProject(projectId: string): Promise<PersistedSession> {
  return invoke('load_editor_project', { projectId })
}

export function saveEditorProject(project: unknown): Promise<PersistedSession> {
  return invoke('save_editor_project', { project })
}

export function importEditorAssets(projectId: string, paths: string[]): Promise<ImportEditorAssetsResult> {
  return invoke('import_editor_assets', { projectId, paths })
}

// ─── 编辑器录音 ────────────────────────────────────────────────────────────

export type EditorRecordingHandle = {
  recordingId: string
}

export type EditorRecordingResult = {
  path: string
  name: string
  duration: number
  sampleRate: number
  channels: number
}

export function beginEditorRecording(projectId: string): Promise<EditorRecordingHandle> {
  return invoke('begin_editor_recording', { projectId })
}

export function appendEditorRecordingChunk(
  projectId: string,
  recordingId: string,
  dataBase64: string,
): Promise<void> {
  return invoke('append_editor_recording_chunk', { projectId, recordingId, dataBase64 })
}

export function finishEditorRecording(
  projectId: string,
  recordingId: string,
  sampleRate: number,
  channels: number,
): Promise<EditorRecordingResult> {
  return invoke('finish_editor_recording', { projectId, recordingId, sampleRate, channels })
}

export function cancelEditorRecording(projectId: string, recordingId: string): Promise<void> {
  return invoke('cancel_editor_recording', { projectId, recordingId })
}

export function discardEditorRecording(projectId: string, recordingId: string): Promise<void> {
  return invoke('discard_editor_recording', { projectId, recordingId })
}

// ─── 编辑器预览、播放调试与插件渲染 ────────────────────────────────────────

export function editorPreviewChunk(
  path: string,
  index: number,
  sampleRate: number,
  channels: number,
): Promise<ArrayBuffer | number[]> {
  return invoke('editor_preview_chunk', { path, index, sampleRate, channels })
}

export function debugEditorPlayback(snapshot: Record<string, unknown>): Promise<void> {
  return invoke('debug_editor_playback', { snapshot })
}

export type PluginRenderResult = {
  path: string
  duration: number
  sampleRate: number
  channels: number
  tailLimited?: boolean
}

export function pluginHostRender(args: {
  project: unknown
  taskId: string
  sampleRate?: number
}): Promise<PluginRenderResult> {
  return invoke('plugin_host_render', args)
}

export function pluginHostCancelRender(taskId: string): Promise<boolean> {
  return invoke('plugin_host_cancel_render', { taskId })
}

export function pluginHostCleanupRender(taskId: string): Promise<void> {
  return invoke('plugin_host_cleanup_render', { taskId })
}

export function cancelEditorMixExport(
  taskId: string,
  projectId: string,
  exportDir?: string,
): Promise<boolean> {
  return invoke('cancel_editor_mix_export', { taskId, projectId, exportDir })
}

// ─── 插件宿主与插件操作 ────────────────────────────────────────────────────

/** Scan-related commands return host-shaped values; callers name the concrete type. */
export function pluginHostCachedScanReport<T>(paths: string[]): Promise<T> {
  return invoke('plugin_host_cached_scan_report', { paths })
}

export function pluginHostScanReport<T>(
  paths: string[],
  options: { force?: boolean; scanId?: string } = {},
): Promise<T> {
  return invoke('plugin_host_scan_report', { paths, force: options.force, scanId: options.scanId })
}

export function pluginHostScanStatus<T>(scanId: string): Promise<T | null> {
  return invoke('plugin_host_scan_status', { scanId })
}

export function pluginHostScanBlacklist<T>(): Promise<T[]> {
  return invoke('plugin_host_scan_blacklist')
}

export function pluginHostSkipScan(scanId: string, path: string): Promise<{ skipped: boolean }> {
  return invoke('plugin_host_skip_scan', { scanId, path })
}

export function pluginHostCancelScan(scanId: string): Promise<{ cancelled: boolean }> {
  return invoke('plugin_host_cancel_scan', { scanId })
}

export function pluginHostRemoveScanBlacklist(path: string): Promise<void> {
  return invoke('plugin_host_remove_scan_blacklist', { path })
}

export function pluginHostRequest<T>(request: unknown): Promise<T> {
  return invoke('plugin_host_request', { request })
}

export function pluginHostCapabilitySnapshot<T>(): Promise<T> {
  return invoke('plugin_host_capabilities')
}

export function startOperationTask(payload: unknown): Promise<unknown> {
  return invoke('start_operation_task', { payload })
}

export function setPluginViewRoot(root: string): Promise<unknown> {
  return invoke('set_plugin_view_root', { root })
}

// ─── 版本、运行时资产与可选组件 ────────────────────────────────────────────

export function versionsCheck<T = unknown>(): Promise<T> {
  return invoke('versions_check')
}

export function versionsAcceptRuntime(): Promise<unknown> {
  return invoke('versions_accept_runtime')
}

export function runtimeAssetInstall(): Promise<unknown> {
  return invoke('runtime_asset_install')
}

export function runtimeAssetStatus<T>(): Promise<T> {
  return invoke('runtime_asset_status')
}

export function optionalRuntimePackageStatus<T>(pkg: string): Promise<T> {
  return invoke('optional_runtime_package_status', { package: pkg })
}

export function manageOptionalRuntimePackage<T = unknown>(payload: unknown): Promise<T> {
  return invoke('manage_optional_runtime_package', { payload })
}

// ─── 任务账本 ──────────────────────────────────────────────────────────────

export function agentJobs<T = unknown>(known?: Record<string, number>): Promise<T> {
  return invoke('agent_jobs', known ? { known } : {})
}

export function enqueueAudioJobs<T = unknown>(payload: unknown): Promise<T> {
  return invoke('enqueue_audio_jobs', { payload })
}

export function removeAudioJobs<T = unknown>(
  jobIds: string[],
  options: { deleteFiles?: boolean; checkOnly?: boolean } = {},
): Promise<T> {
  return invoke('remove_audio_jobs', {
    jobIds,
    deleteFiles: options.deleteFiles === true,
    checkOnly: options.checkOnly === true,
  })
}

export function removeResultJobs<T = unknown>(
  jobIds: string[],
  options: { deleteFiles?: boolean; checkOnly?: boolean } = {},
): Promise<T> {
  return invoke('remove_result_jobs', {
    jobIds,
    deleteFiles: options.deleteFiles === true,
    checkOnly: options.checkOnly === true,
  })
}

export function startAudioTool<T = unknown>(payload: unknown): Promise<T> {
  return invoke('start_audio_tool', { payload })
}

export function cancelAgentJob<T = unknown>(jobId: string): Promise<T> {
  return invoke('cancel_agent_job', { jobId })
}

// ─── 环境、代理与应用路径 ──────────────────────────────────────────────────

export function getEnvInfo<T>(): Promise<T> {
  return invoke('get_env_info')
}

export function startEnvCheck(requestId: string): Promise<void> {
  return invoke('start_env_check', { requestId })
}

export function getAppPaths<T>(): Promise<T> {
  return invoke('get_app_paths')
}

export function updateProxySettings(payload: {
  mode: string
  url: string
  bypass: string
}): Promise<unknown> {
  return invoke('update_proxy_settings', { payload })
}

export function writeFrontendLog(record: unknown): Promise<void> {
  return invoke('write_frontend_log', { record })
}

/** Moves a rejected settings file aside after the user explicitly confirms. */
export function recoverAppSettings<T>(): Promise<T> {
  return invoke('recover_app_settings', { confirm: true })
}

// ─── 模型目录迁移 ──────────────────────────────────────────────────────────

export function prepareModelDirChange<T>(payload: {
  currentModelDir: string
  targetModelDir: string
}): Promise<T> {
  return invoke('prepare_model_dir_change', { payload })
}

export function startModelDirMigration(payload: {
  taskId: string
  currentModelDir: string
  targetModelDir: string
}): Promise<unknown> {
  return invoke('start_model_dir_migration', { payload })
}

export function respondModelDirMigrationConflict(payload: {
  taskId: string
  resolution: unknown
}): Promise<unknown> {
  return invoke('respond_model_dir_migration_conflict', { payload })
}

export function confirmModelDirMigrationSwitch(payload: { taskId: string }): Promise<unknown> {
  return invoke('confirm_model_dir_migration_switch', { payload })
}

export function cancelModelDirMigration(payload: { taskId: string }): Promise<unknown> {
  return invoke('cancel_model_dir_migration', { payload })
}

// ─── 工作流存储与编辑器窗口 ────────────────────────────────────────────────

export function getWorkflowEditors<T>(): Promise<T> {
  return invoke('get_workflow_editors')
}

export function mutateWorkflowStore<T>(payload: unknown): Promise<T> {
  return invoke('mutate_workflow_store', { payload })
}

export function mutateMediaWorkflows<T>(payload: unknown): Promise<T> {
  return invoke('mutate_media_workflows', { payload })
}

export function startCustomModelImport(payload: unknown): Promise<unknown> {
  return invoke('start_custom_model_import', { payload })
}

// ─── 调试日志与运行时指针 ──────────────────────────────────────────────────

export function debugLogInfo<T>(): Promise<T> {
  return invoke('debug_log_info')
}

export function debugLogRead<T>(): Promise<T> {
  return invoke('debug_log_read')
}

export function debugLogClear<T>(): Promise<T> {
  return invoke('debug_log_clear')
}

export function debugLogCreateReport<T>(): Promise<T> {
  return invoke('debug_log_create_report')
}

export function debugRuntimePointers<T>(): Promise<T> {
  return invoke('debug_runtime_pointers')
}

// ─── 编辑器源引用 ──────────────────────────────────────────────────────────

export function relinkEditorSources(payload: {
  projectId: string
  sourceId: string
  pickedPath: string
}): Promise<RelinkEditorSourcesResult> {
  return invoke('relink_editor_sources', { payload })
}
