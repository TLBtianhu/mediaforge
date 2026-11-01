/**
 * 模型库「存储管理」区块的派生状态：目录/工具模型的存储列表、筛选排序、
 * 勾选合计、批量删除进度。
 *
 * 只读 modelStore 的存储摘要与删除任务，选择状态由视图持有并注入。
 */
import type { ComputedRef } from 'vue'
import type { ModelStorageDeleteTarget, ToolModelStorageItem } from '../../stores/model.ts'

type Deps = Record<string, any> & {
  computed: <T>(source: () => T) => ComputedRef<T>
}

export type StorageListItem = ModelStorageDeleteTarget & {
  selectionKey: string
  downloaded: boolean
  sizeBytes: number
  expectedSizeBytes: number
  fileCount: number
  role?: ToolModelStorageItem['role']
}

export function makeModelStorage(deps: Deps) {
  const { computed } = deps

  const allStorageModels = computed<StorageListItem[]>(() => {
    const catalogModels: StorageListItem[] = (deps.modelStorageSummary.value?.models || []).map((item: any) => ({
      kind: 'catalog',
      id: item.name,
      name: item.name,
      selectionKey: `catalog:${item.name}`,
      downloaded: item.downloaded,
      sizeBytes: item.sizeBytes,
      expectedSizeBytes: item.expectedSizeBytes,
      fileCount: item.files.filter((file: any) => file.exists).length,
    }))
    const toolModels: StorageListItem[] = (deps.modelStorageSummary.value?.toolModels || []).map((item: any) => ({
      kind: 'tool',
      id: item.id,
      name: item.name,
      tool: item.tool,
      role: item.role,
      selectionKey: `tool:${item.tool}:${item.id}`,
      downloaded: true,
      sizeBytes: item.sizeBytes,
      expectedSizeBytes: item.sizeBytes,
      fileCount: item.fileCount,
    }))
    return [...catalogModels, ...toolModels]
  })

  const storageModels = computed<StorageListItem[]>(() => {
    const q = deps.storageSearch.value.trim().toLowerCase()
    return [...allStorageModels.value]
      .filter((item: StorageListItem) => (!deps.storageDownloadedOnly.value || item.downloaded)
        && (!q || item.name.toLowerCase().includes(q) || item.tool?.includes(q)))
      .sort((a, b) => {
        if (deps.storageSort.value === 'size-desc') return b.sizeBytes - a.sizeBytes
        if (deps.storageSort.value === 'size-asc') return a.sizeBytes - b.sizeBytes
        if (deps.storageSort.value === 'name-desc') return deps.collator.value.compare(b.name, a.name)
        return deps.collator.value.compare(a.name, b.name)
      })
  })

  const allStorageModelsByKey = computed(() => new Map(allStorageModels.value.map(item => [item.selectionKey, item])))

  const selectedStorageBytes = computed(() => deps.selectedStorageModels.value
    .map((key: string) => allStorageModelsByKey.value.get(key))
    .filter((item: any): item is NonNullable<typeof item> => Boolean(item))
    .reduce((sum: number, item: StorageListItem) => sum + item.sizeBytes, 0))

  const hasDeletingSelectedStorageModels = computed(() => deps.selectedStorageModels.value.some((key: string) => {
    const item = allStorageModelsByKey.value.get(key)
    return item?.kind === 'catalog' && deps.isDeletingModel(item.id)
  }))

  const batchDeleteProgress = computed(() => {
    if (!deps.batchDeleteState.value.totalModels) return 0
    const currentTask = deps.batchDeleteState.value.currentModel ? deps.deleteTasks.value[deps.batchDeleteState.value.currentModel] : null
    const currentFraction = currentTask && currentTask.totalFiles > 0
      ? currentTask.completedFiles / currentTask.totalFiles
      : 0
    return Math.max(0, Math.min(100, Math.round(((deps.batchDeleteState.value.completedModels + currentFraction) / deps.batchDeleteState.value.totalModels) * 100)))
  })

  function setStorageSelected(name: string, checked: boolean) {
    deps.selectedStorageModels.value = checked
      ? Array.from(new Set([...deps.selectedStorageModels.value, name]))
      : deps.selectedStorageModels.value.filter((item: string) => item !== name)
  }

  return {
    allStorageModels, storageModels, allStorageModelsByKey, selectedStorageBytes,
    hasDeletingSelectedStorageModels, batchDeleteProgress, setStorageSelected,
  }
}
