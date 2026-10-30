/**
 * 任务事件的域级判断。
 *
 * P2 之前，媒体任务复用音频的事件名（`audio_tool_progress` / `audio_tool_result`），
 * 于是每个视图都要自己认这两个字符串。现在后端同时发规范名（`task.progress` /
 * `task.result`），前端统一从这里判断：
 *
 *   - 新代码只写 `isTaskProgress(...)`，不再关心事件叫哪个名字；
 *   - P7 删掉历史名时，只需要改这个文件里的两个集合。
 */
const TASK_PROGRESS_TYPES = new Set(['task.progress', 'audio_tool_progress'])
const TASK_RESULT_TYPES = new Set(['task.result', 'audio_tool_result'])

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
