import { invoke } from '@tauri-apps/api/core'
import { invokeWorkerCommand } from './worker.ts'
import type { WorkerCommandPayload, WorkerCommandResult } from '@/types/workerContracts.generated'

export type RuntimeCoreVersionsRequest = WorkerCommandPayload<'runtime_core_versions'>
export type RuntimeCoreVersionsResponse = WorkerCommandResult<'runtime_core_versions'>
export type RuntimeEnvSizesResponse = WorkerCommandResult<'runtime_env_sizes'>
export type RuntimeInfoResponse = WorkerCommandResult<'runtime_info'>
export type RuntimeMutationResponse = WorkerCommandResult<'activate_runtime'>

/** Typed wrappers for the runtime read commands used by the app store. */
export function runtimeCoreVersions(payload: RuntimeCoreVersionsRequest): Promise<RuntimeCoreVersionsResponse> {
  return invoke<RuntimeCoreVersionsResponse>('runtime_core_versions', { payload })
}

export function runtimeEnvSizes(): Promise<RuntimeEnvSizesResponse> {
  return invoke<RuntimeEnvSizesResponse>('runtime_env_sizes')
}

export function runtimeInfo(payload: WorkerCommandPayload<'runtime_info'>): Promise<RuntimeInfoResponse> {
  return invokeWorkerCommand('runtime_info', payload)
}

export function activateRuntime(
  payload: WorkerCommandPayload<'activate_runtime'>,
): Promise<WorkerCommandResult<'activate_runtime'>> {
  return invokeWorkerCommand('activate_runtime', payload)
}

export function deleteRuntime(
  payload: WorkerCommandPayload<'delete_runtime'>,
): Promise<WorkerCommandResult<'delete_runtime'>> {
  return invokeWorkerCommand('delete_runtime', payload)
}
