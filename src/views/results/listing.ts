/**
 * 结果页的分组、筛选与排序：结果组构建、关键词/类型过滤、时间/名称排序。
 *
 * 纯派生，不碰 store 响应式状态；resultType 由视图注入（其实现模块在 Node 下不可解析）。
 */
import type { ComputedRef } from 'vue'
import type { SeparationJob, SeparationTask } from '../../stores/task.ts'
import type { ResultType } from '../../utils/resultType.ts'
import { isTerminalTaskStatus } from '../../features/tasks/lifecycle.ts'

export const RESULT_OUTCOME_KEYS = {
  completed: 'results.journal.status.completed', failed: 'results.journal.status.failed',
  cancelled: 'results.journal.status.cancelled', interrupted: 'results.journal.status.interrupted',
  queued: 'results.waiting', running: 'results.running', mixed: 'results.mixed',
} as const
export type ResultOutcome = keyof typeof RESULT_OUTCOME_KEYS
export type TaskResultOutcome = Exclude<ResultOutcome, 'mixed'>
export type ResultGroup = SeparationJob & {
  items: SeparationTask[]; resultType: ResultType; removable: boolean; outcome: ResultOutcome
  outcomeCounts: Partial<Record<TaskResultOutcome, number>>
}

/** Display the canonical interrupted state without changing the audio execution lifecycle. */
export function resultTaskOutcome(task: Pick<SeparationTask, 'status'>, canonicalStatus?: string): TaskResultOutcome {
  if (canonicalStatus && ['completed', 'failed', 'cancelled', 'interrupted', 'queued', 'running'].includes(canonicalStatus)) return canonicalStatus as TaskResultOutcome
  if (task.status === 'done') return 'completed'
  if (task.status === 'failed' || task.status === 'cancelled' || task.status === 'queued') return task.status
  return 'running'
}

type Deps = Record<string, any> & {
  computed: <T>(source: () => T) => ComputedRef<T>
}

export function makeResultListing(deps: Deps) {
  const { computed, t } = deps

  const resultGroups = computed<ResultGroup[]>(() => {
    return deps.task.resultJobs.map((job: SeparationJob) => {
      const outcomeCounts: ResultGroup['outcomeCounts'] = {}
      for (const item of job.tasks) {
        const outcome: TaskResultOutcome = deps.taskOutcome?.(item) || resultTaskOutcome(item)
        outcomeCounts[outcome] = (outcomeCounts[outcome] || 0) + 1
      }
      const outcomes = Object.keys(outcomeCounts) as TaskResultOutcome[]
      const outcome: ResultOutcome = outcomes.includes('running') ? 'running' : outcomes.includes('queued') ? 'queued'
        : outcomes.length === 1 ? outcomes[0]! : 'mixed'
      return { ...job, items: job.tasks, resultType: deps.resultType(job.primary), outcome, outcomeCounts,
        removable: job.tasks.length > 0 && job.tasks.every(item => isTerminalTaskStatus(item.status)) }
    })
  })

  function getFileName(path: string) {
    return path.split(/[/\\]/).pop() || path
  }

  function getGroupTitle(group: ResultGroup) {
    if (group.inputCount === 1) return getFileName(group.primary.input) || group.model
    return t('results.groupTitle', { count: group.inputCount, name: getFileName(group.primary.input) })
  }

  const filteredResults = computed<ResultGroup[]>(() => {
    const keyword = deps.search.value.trim().toLowerCase()
    const list = resultGroups.value.filter(group => {
      if (deps.typeFilter.value !== 'all' && group.resultType !== deps.typeFilter.value) return false
      if (!keyword) return true
      const haystack = [
        group.id,
        group.model,
        group.output,
        t(RESULT_OUTCOME_KEYS[group.outcome]),
        ...group.items.map(item => item.error || item.message),
        ...group.items.map(item => getFileName(item.input)),
        ...group.items.map(item => item.output),
        ...group.items.flatMap(item => item.outputs.map(output => output.stem)),
        ...group.items.flatMap(item => item.outputs.map(output => output.path)),
      ].join(' ').toLowerCase()
      return haystack.includes(keyword)
    })

    return [...list].sort((a, b) => {
      switch (deps.sortBy.value) {
        case 'time_asc':
          return a.updatedAt - b.updatedAt
        case 'name_asc':
          return getGroupTitle(a).localeCompare(getGroupTitle(b), 'zh-CN')
        case 'name_desc':
          return getGroupTitle(b).localeCompare(getGroupTitle(a), 'zh-CN')
        case 'time_desc':
        default:
          return b.updatedAt - a.updatedAt
      }
    })
  })

  return { resultGroups, filteredResults, getFileName, getGroupTitle }
}
