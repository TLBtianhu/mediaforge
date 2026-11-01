/**
 * 任务/资产事件的域级判断。
 *
 * 管道上只有 manifest/protocol.json 里声明的规范名（`<域>.<动词>`）：
 *
 *   - 任务域：task.started / stage / progress / log / cancelled / result
 *   - 资产域：asset.started / stage / progress / log / done
 *
 * 终态只有一个名字 `task.result`（原 `task.done` 已收敛进来）。
 * `task_*` 与 `download_*` 是历史写法，scripts/check-architecture.py 会在
 * Python 侧拒绝它们；前端这里也只认规范名。新代码只写
 * `isTaskProgress(...)` / `isTaskResult(...)`，加事件类型时改这里的集合。
 */
const TASK_PROGRESS_TYPES = new Set(['task.progress'])
const TASK_RESULT_TYPES = new Set(['task.result'])
const TASK_LIFECYCLE_TYPES = new Set([
  'task.started', 'task.stage', 'task.progress', 'task.log',
  'task.result', 'task.cancelled',
])

export function isTaskProgress(type: string | null | undefined): boolean {
  return typeof type === 'string' && TASK_PROGRESS_TYPES.has(type)
}

export function isTaskResult(type: string | null | undefined): boolean {
  return typeof type === 'string' && TASK_RESULT_TYPES.has(type)
}

/** 当前事件是否属于任务域（生命周期、进度、结果都算）。 */
export function isTaskEvent(type: string | null | undefined): boolean {
  return typeof type === 'string' && TASK_LIFECYCLE_TYPES.has(type)
}
