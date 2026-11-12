import { invoke } from '@tauri-apps/api/core'

/**
 * Typed wrappers for native Tauri commands that are not part of the worker protocol.
 *
 * This module owns the invoke call for every command it wraps: views, stores and
 * composables must call the function here instead of `invoke('<name>', ...)`.
 * `scripts/check-frontend-invokes.py` derives that rule from this file, so adding a
 * wrapper immediately forbids direct invokes of the same command everywhere else.
 */

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
