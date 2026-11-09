import { invoke } from '@tauri-apps/api/core'
import type { WorkerCommandPayload, WorkerCommandResult } from '@/types/workerContracts.generated'

export type MediaLibraryOperation = 'list' | 'stats' | 'import' | 'remove' | 'tags'

/**
 * The generated payload already carries the optional fields; the operation is
 * required here because every library call has to choose one.
 */
export type MediaLibraryRequest = WorkerCommandPayload<'media_library'> & {
  operation: MediaLibraryOperation
}

export type MediaLibraryResponse = WorkerCommandResult<'media_library'>

/** Typed wrapper around the `media_library` Tauri command. */
export function mediaLibrary(payload: MediaLibraryRequest): Promise<MediaLibraryResponse> {
  return invoke<MediaLibraryResponse>('media_library', { payload })
}
