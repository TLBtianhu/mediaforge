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

// ─── 编辑器源引用 ──────────────────────────────────────────────────────────

export function relinkEditorSources(payload: {
  projectId: string
  sourceId: string
  pickedPath: string
}): Promise<RelinkEditorSourcesResult> {
  return invoke('relink_editor_sources', { payload })
}
