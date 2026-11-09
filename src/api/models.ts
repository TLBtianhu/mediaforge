import { invoke } from '@tauri-apps/api/core'
import type { WorkerCommandPayload, WorkerCommandResult } from '@/types/workerContracts.generated'

export type ModelInfoRequest = WorkerCommandPayload<'model_info'>
export type ModelInfoResponse = WorkerCommandResult<'model_info'>
export type ModelStorageSummaryResponse = WorkerCommandResult<'model_storage_summary'>

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
