/**
 * 下载与删除任务的终态：done / error / cancelled。
 *
 * 这两条后台任务轴共用同一套终态词表。暂停、中断和 deleting 都不是终态，
 * 因为它们仍可能被恢复或继续推进。
 */
export type DownloadStatus = 'idle' | 'preparing' | 'downloading' | 'done' | 'error' | 'cancelled' | 'paused' | 'interrupted'
export type DeleteTaskStatus = 'deleting' | 'done' | 'error' | 'cancelled'

export const TERMINAL_DOWNLOAD_STATUSES = ['done', 'error', 'cancelled'] as const satisfies readonly DownloadStatus[]

export function isTerminalOperationStatus(status: DownloadStatus | DeleteTaskStatus): boolean {
  return (TERMINAL_DOWNLOAD_STATUSES as readonly string[]).includes(status)
}

export function isTerminalDownloadStatus(status: DownloadStatus): boolean {
  return isTerminalOperationStatus(status)
}

export function isTerminalDeleteTaskStatus(task?: { status: DeleteTaskStatus } | null): boolean {
  return task ? isTerminalOperationStatus(task.status) : false
}
