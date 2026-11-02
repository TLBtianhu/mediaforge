import { watch } from 'vue'
import { isTerminalDeleteTaskStatus, type DeleteTaskStatus } from './lifecycle.ts'

export type DeleteTaskWaitRecord = {
  taskId: string
  status: DeleteTaskStatus
  message?: string
}

/** 等待指定删除任务到达 done/error/cancelled；立即回调也安全。 */
export function waitForDeleteTask<T extends DeleteTaskWaitRecord>(
  read: () => T | undefined,
  taskId: string,
  timeoutMs: number,
): Promise<T> {
  return new Promise((resolve, reject) => {
    let timer: ReturnType<typeof setTimeout> | null = null
    let stop: (() => void) | undefined
    let settled = false
    const finish = (complete: () => void) => {
      if (settled) return
      settled = true
      if (timer) clearTimeout(timer)
      stop?.()
      complete()
    }

    timer = setTimeout(() => {
      finish(() => reject(new Error('Delete task timed out')))
    }, timeoutMs)
    stop = watch(
      read,
      (task) => {
        if (!task || task.taskId !== taskId || !isTerminalDeleteTaskStatus(task)) return
        if (task.status === 'done') {
          finish(() => resolve(task))
        } else {
          finish(() => reject(new Error(task.message || (task.status === 'cancelled' ? 'Delete cancelled' : 'Delete failed'))))
        }
      },
      { deep: true, immediate: true },
    )
    // immediate 回调可能先于 stop 赋值完成；此时补停 watcher。
    if (settled) stop()
  })
}
