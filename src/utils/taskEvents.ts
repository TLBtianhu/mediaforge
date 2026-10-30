/**
 * 任务事件的域级判断。
 *
 * 后端在边界处把历史事件名（`audio_tool_*`）统一映射成 `task.progress` /
 * `task.result`，管道上只有规范名。前端只认规范名：
 *
 *   - 新代码只写 `isTaskProgress(...)` / `isTaskResult(...)`；
 *   - 再加事件类型时改这里的集合，不需要动各个视图。
 */
const TASK_PROGRESS_TYPES = new Set(['task.progress'])
const TASK_RESULT_TYPES = new Set(['task.result'])

export function isTaskProgress(type: string | null | undefined): boolean {
  return typeof type === 'string' && TASK_PROGRESS_TYPES.has(type)
}

export function isTaskResult(type: string | null | undefined): boolean {
  return typeof type === 'string' && TASK_RESULT_TYPES.has(type)
}

/** 当前事件是否属于任务域（进度、结果、失败都算）。 */
export function isTaskEvent(type: string | null | undefined): boolean {
  return typeof type === 'string' && (type.startsWith('task.') || TASK_PROGRESS_TYPES.has(type) || TASK_RESULT_TYPES.has(type))
}
