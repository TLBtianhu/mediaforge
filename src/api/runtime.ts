import { invoke } from '@tauri-apps/api/core'
import type { WorkerCommandPayload, WorkerCommandResult } from '@/types/workerContracts.generated'

export type RuntimeCoreVersionsRequest = WorkerCommandPayload<'runtime_core_versions'>
export type RuntimeCoreVersionsResponse = WorkerCommandResult<'runtime_core_versions'>
export type RuntimeEnvSizesResponse = WorkerCommandResult<'runtime_env_sizes'>

/** Typed wrappers for the runtime read commands used by the app store. */
export function runtimeCoreVersions(payload: RuntimeCoreVersionsRequest): Promise<RuntimeCoreVersionsResponse> {
  return invoke<RuntimeCoreVersionsResponse>('runtime_core_versions', { payload })
}

export function runtimeEnvSizes(): Promise<RuntimeEnvSizesResponse> {
  return invoke<RuntimeEnvSizesResponse>('runtime_env_sizes')
}
