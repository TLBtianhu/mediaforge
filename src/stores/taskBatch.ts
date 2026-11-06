import { createRunId, resolveOutputDirectory } from './taskOutputPaths.ts'
import { normalizeOutputLayout, normalizeOutputNaming } from './taskNormalize.ts'
import type { OutputLayout, OutputNamingConfig, SeparationTask } from './task.ts'

export type TaskBatchOptions = {
  outputDir?: string
  outputLayout?: OutputLayout
  outputNaming?: OutputNamingConfig
}

export type TaskBatchPreparation = {
  jobId: string
  jobOutput: string
  outputLayout: OutputLayout
  outputNaming: OutputNamingConfig | undefined
}

/** 所有执行入口共享的批次准备：只解析一次 jobId 与输出语义。 */
export function prepareTaskBatch(
  options: TaskBatchOptions,
  fallbackOutputDir: string,
  windowsPaths: boolean,
): TaskBatchPreparation {
  return {
    jobId: createRunId('job'),
    jobOutput: resolveOutputDirectory(options.outputDir, fallbackOutputDir, windowsPaths),
    outputLayout: normalizeOutputLayout(options.outputLayout),
    outputNaming: normalizeOutputNaming(options.outputNaming),
  }
}

/** 创建任务后的统一收尾：按需持久化、入队并返回同一种批次结果。 */
export function finalizeTaskBatch(
  batch: TaskBatchPreparation,
  tasks: SeparationTask[],
  options: { schedule: () => void; persist?: () => void },
) {
  options.persist?.()
  options.schedule()
  return completeTaskBatch(batch.jobId, tasks)
}


export function completeTaskBatch(jobId: string, tasks: SeparationTask[]) {
  return {
    succeeded: tasks.length,
    failed: 0,
    total: tasks.length,
    jobId,
    tasks,
  }
}
