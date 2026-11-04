import type { OutputLayout, OutputNamingConfig, SeparationTask } from '@/stores/task'
import type { WorkflowEntry } from '@/stores/workflow'
import type { WorkflowRuntimeBindings } from '@/workflows/workbench'

/** 所有任务入口共享的输出选项，调用方不需要知道后端的执行方式。 */
export type TaskLaunchOptions = {
  outputDir?: string
  outputLayout?: OutputLayout
  outputNaming?: OutputNamingConfig
}

export type TaskLaunchResult = {
  jobId: string
  tasks: SeparationTask[]
  total: number
  succeeded: number
  failed: number
}

type TaskLaunchStoreResult = Omit<TaskLaunchResult, 'succeeded' | 'failed'> & {
  succeeded?: number
  failed?: number
}

export type TaskLaunchRequest =
  | { kind: 'separation'; options?: TaskLaunchOptions }
  | { kind: 'workflow'; workflow: WorkflowEntry; options?: TaskLaunchOptions }
  | { kind: 'workbench'; workflow: WorkflowEntry; bindings: WorkflowRuntimeBindings }

/** 仅声明任务提交能力，避免 UI 直接依赖整个 Pinia store。 */
export interface TaskLaunchStore {
  startSeparation(options?: TaskLaunchOptions): Promise<TaskLaunchStoreResult>
  startWorkflowInference(workflow: WorkflowEntry, options?: TaskLaunchOptions): Promise<TaskLaunchStoreResult>
  startWorkbenchInference(workflow: WorkflowEntry, bindings: WorkflowRuntimeBindings): Promise<TaskLaunchStoreResult>
}

/** 统一任务提交语义；旧 store 方法仅作为适配层存在。 */
export async function launchTask(store: TaskLaunchStore, request: TaskLaunchRequest): Promise<TaskLaunchResult> {
  const result = await (async () => {
    switch (request.kind) {
      case 'separation':
        return store.startSeparation(request.options)
      case 'workflow':
        return store.startWorkflowInference(request.workflow, request.options)
      case 'workbench':
        return store.startWorkbenchInference(request.workflow, request.bindings)
      default:
        throw new Error('Unsupported task launch request')
    }
  })()
  return {
    ...result,
    succeeded: result.succeeded ?? result.total,
    failed: result.failed ?? 0,
  }
}
