import { invoke } from '@tauri-apps/api/core'
import type { WorkerCommandPayload, WorkerCommandResult } from '@/types/workerContracts.generated'

export type ModelInfoRequest = WorkerCommandPayload<'model_info'>
export type ModelInfoResponse = WorkerCommandResult<'model_info'>
export type ModelStorageSummaryResponse = WorkerCommandResult<'model_storage_summary'>
export type CustomModelInspectionResponse = WorkerCommandResult<'inspect_custom_model'>
export type CustomModelRemovalResponse = WorkerCommandResult<'unregister_custom_model'>
export type CustomModelRelinkResponse = WorkerCommandResult<'relink_custom_model'>

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
