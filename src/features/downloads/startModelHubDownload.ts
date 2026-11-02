import { invoke } from '@tauri-apps/api/core'

export type ModelHubDownloadRequest = {
  requestId: string
  modelId: string
  modelDir: string | null
  source: string
}

/** Tauri 外层参数名是 payload；Rust 再从其中读取 requestId 等字段。 */
export async function startModelHubDownload(request: ModelHubDownloadRequest) {
  return invoke<{ taskId: string; started: boolean }>('start_model_hub_download', {
    payload: request,
  })
}
