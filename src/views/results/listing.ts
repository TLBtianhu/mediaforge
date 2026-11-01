/**
 * 结果页的分组、筛选与排序：结果组构建、关键词/类型过滤、时间/名称排序。
 *
 * 纯派生，不碰 store 响应式状态；resultType 由视图注入（其实现模块在 Node 下不可解析）。
 */
import type { ComputedRef } from 'vue'
import type { SeparationJob, SeparationTask } from '../../stores/task.ts'
import type { ResultType } from '../../utils/resultType.ts'

export type ResultGroup = SeparationJob & { items: SeparationTask[]; resultType: ResultType }

type Deps = Record<string, any> & {
  computed: <T>(source: () => T) => ComputedRef<T>
}

export function makeResultListing(deps: Deps) {
  const { computed, t } = deps

  const resultGroups = computed<ResultGroup[]>(() => {
    return deps.task.resultJobs.map((job: any) => ({ ...job, items: job.tasks, resultType: deps.resultType(job.primary) }))
  })

  function getFileName(path: string) {
    return path.split(/[/\\]/).pop() || path
  }

  function getGroupTitle(group: any) {
    if (group.inputCount === 1) return getFileName(group.primary.input) || group.model
    return t('results.groupTitle', { count: group.inputCount, name: getFileName(group.primary.input) })
  }

  const filteredResults = computed<ResultGroup[]>(() => {
    const keyword = deps.search.value.trim().toLowerCase()
    const list = resultGroups.value.filter((group: any) => {
      if (deps.typeFilter.value !== 'all' && group.resultType !== deps.typeFilter.value) return false
      if (!keyword) return true
      const haystack = [
        group.id,
        group.model,
        group.output,
        ...group.items.map((item: any) => getFileName(item.input)),
        ...group.items.map((item: any) => item.output),
        ...group.items.flatMap((item: any) => item.outputs.map((output: any) => output.stem)),
        ...group.items.flatMap((item: any) => item.outputs.map((output: any) => output.path)),
      ].join(' ').toLowerCase()
      return haystack.includes(keyword)
    })

    return [...list].sort((a: any, b: any) => {
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
