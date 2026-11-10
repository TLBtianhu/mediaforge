import { invoke } from '@tauri-apps/api/core'
import { invokeWorkerCommand } from './worker.ts'
import type { WorkerCommandPayload, WorkerCommandResult } from '@/types/workerContracts.generated'

export type ModelInfoRequest = WorkerCommandPayload<'model_info'>
export type ModelInfoResponse = WorkerCommandResult<'model_info'>
export type ModelStorageSummaryResponse = WorkerCommandResult<'model_storage_summary'>
export type CustomModelInspectionResponse = WorkerCommandResult<'inspect_custom_model'>
export type CustomModelRemovalResponse = WorkerCommandResult<'unregister_custom_model'>
export type CustomModelRelinkResponse = WorkerCommandResult<'relink_custom_model'>
export type ModelStartResponse = { taskId: string; started: boolean }
export type DeleteToolModelResponse = WorkerCommandResult<'delete_tool_model'>
export type ListModelsResponse = WorkerCommandResult<'list_models'>
export type ModelHubListResult = Extract<WorkerCommandResult<'model_hub'>, { models: unknown[] }>

/** Typed wrapper around the Rust `get_model_info` command (worker `model_info`). */
export function getModelInfo(payload: ModelInfoRequest): Promise<ModelInfoResponse> {
  return invoke<ModelInfoResponse>('get_model_info', { payload })
}

/** Typed wrapper around the Rust `get_model_storage_summary` command. */
export function getModelStorageSummary(
  payload: WorkerCommandPayload<'model_storage_summary'>,
): Promise<ModelStorageSummaryResponse> {
  return invoke<ModelStorageSummaryResponse>('get_model_storage_summary', { payload })
}

/** Typed wrapper around the Rust `inspect_custom_model` command. */
export function inspectCustomModel(
  payload: WorkerCommandPayload<'inspect_custom_model'>,
): Promise<CustomModelInspectionResponse> {
  return invoke<CustomModelInspectionResponse>('inspect_custom_model', { payload })
}

/** Start the background import command; the worker finishes via custom_model_import_* events. */
export function startCustomModelImport(
  payload: WorkerCommandPayload<'import_custom_model'>,
): Promise<unknown> {
  return invoke('start_custom_model_import', { payload })
}

/** Typed wrapper around the Rust `unregister_custom_model` command. */
export function unregisterCustomModel(
  payload: WorkerCommandPayload<'unregister_custom_model'>,
): Promise<CustomModelRemovalResponse> {
  return invoke<CustomModelRemovalResponse>('unregister_custom_model', { payload })
}

/** Typed wrapper around the Rust `relink_custom_model` command. */
export function relinkCustomModel(
  payload: WorkerCommandPayload<'relink_custom_model'>,
): Promise<CustomModelRelinkResponse> {
  return invoke<CustomModelRelinkResponse>('relink_custom_model', { payload })
}

/** Start a model-hub download; completion arrives as asset.* events. */
export function startModelHubDownload(payload: WorkerCommandPayload<'model_download'>): Promise<ModelStartResponse> {
  return invoke<ModelStartResponse>('start_model_hub_download', { payload })
}

/** Start a catalog model deletion; completion arrives as model_delete_* events. */
export function startModelDelete(payload: WorkerCommandPayload<'delete_model'>): Promise<ModelStartResponse> {
  return invoke<ModelStartResponse>('start_model_delete', { payload })
}

/** Start residual-file cleanup; completion arrives as model_residual_cleanup_* events. */
export function startModelResidualCleanup(
  payload: WorkerCommandPayload<'cleanup_model_residual_files'>,
): Promise<unknown> {
  return invoke('start_cleanup_model_residual_files', { payload })
}

/** Delete one tool model synchronously through the worker contract. */
export function deleteToolModel(
  payload: WorkerCommandPayload<'delete_tool_model'>,
): Promise<DeleteToolModelResponse> {
  return invokeWorkerCommand('delete_tool_model', payload)
}

export function remapCustomModelPaths(
  payload: WorkerCommandPayload<'remap_custom_model_paths'>,
): Promise<WorkerCommandResult<'remap_custom_model_paths'>> {
  return invokeWorkerCommand('remap_custom_model_paths', payload)
}

/** Rust `list_models` exposes the full audio-model rows for the main library. */
export function listModels(payload: WorkerCommandPayload<'list_models'>): Promise<ListModelsResponse> {
  return invokeWorkerCommand('list_models', payload)
}

/** Media model hub listing (audio/image/video); the main audio library uses listModels. */
export function modelHub(
  payload: WorkerCommandPayload<'model_hub'>,
): Promise<WorkerCommandResult<'model_hub'>> {
  return invokeWorkerCommand('model_hub', payload)
}
