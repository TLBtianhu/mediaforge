// SeparateView 的模型列表：搜索、分类过滤、排序与分页。
import { computed, ref, type Ref } from 'vue'
import { SEPARATE_MODEL_PAGE_SIZES } from '@/utils/pagination'
import { matchesModelQuery } from '@/utils/modelSearch'
import type { ModelListSortMode } from '@/stores/task'
import type { ModelEntry } from '@/stores/model'
import { buildModelCategoryOptionsFromModels } from '@/utils/modelCategory'

type ModelPreferences = Record<string, { favorite?: boolean; useCount?: number; lastUsedAt?: number }>

type Options = {
  t: (key: string, params?: Record<string, unknown>) => string
  locale: Ref<string>
  downloadedModels: Ref<ModelEntry[]>
  selectedModelName: Ref<string>
  modelPreferences: Ref<ModelPreferences>
  modelListSortMode: Ref<ModelListSortMode>
  modelPageSize: Ref<number>
  isLoading: Ref<boolean>
  modelsLoaded: Ref<boolean>
  modelNote: (name: string) => string
  app: { envLoading: boolean }
  modelError: Ref<string | null>
}

export function useModelList({
  t, locale, downloadedModels, selectedModelName, modelPreferences, modelListSortMode,
  modelPageSize, isLoading, modelsLoaded, modelNote, app, modelError,
}: Options) {
const modelSearch = ref('')

const modelCategoryFilter = ref('')

const modelPage = ref(1)

const modelSortOptions = computed(() => [
  { label: t('separate.modelSortUsage'), value: 'usage' },
  { label: t('separate.modelSortRecent'), value: 'recent' },
  { label: t('separate.modelSortFavorite'), value: 'favorite' },
  { label: t('separate.modelSortNameAsc'), value: 'name-asc' },
  { label: t('separate.modelSortNameDesc'), value: 'name-desc' },
] as Array<{ label: string; value: ModelListSortMode }>)

const listedDownloadedModels = computed(() => {
  return [...downloadedModels.value].sort((a, b) => {
    const prefA = modelPreferences.value[a.name] || {}
    const prefB = modelPreferences.value[b.name] || {}
    const favoriteDelta = Number(Boolean(prefB.favorite)) - Number(Boolean(prefA.favorite))
    const useDelta = Number(prefB.useCount || 0) - Number(prefA.useCount || 0)
    const recentDelta = Number(prefB.lastUsedAt || 0) - Number(prefA.lastUsedAt || 0)
    if (modelListSortMode.value === 'favorite') return favoriteDelta || recentDelta || compareModelName(a.name, b.name)
    if (modelListSortMode.value === 'recent') return recentDelta || useDelta || favoriteDelta || compareModelName(a.name, b.name)
    if (modelListSortMode.value === 'name-desc') return compareModelName(b.name, a.name)
    if (modelListSortMode.value === 'name-asc') return compareModelName(a.name, b.name)
    return useDelta || recentDelta || favoriteDelta || compareModelName(a.name, b.name)
  })
})

const selectedModelListItem = computed(() => listedDownloadedModels.value.find(item => item.name === selectedModelName.value) || null)

const modelDownloaded = computed(() => Boolean(selectedModelListItem.value))

const modelPanelHasModels = computed(() => downloadedModels.value.length > 0)

const modelPanelLoading = computed(() => !modelPanelHasModels.value && !modelsLoaded.value && (isLoading.value || app.envLoading) && !modelError.value)

const modelCategoryOptions = computed(() => [
  { label: t('common.all'), value: '' },
  ...buildModelCategoryOptionsFromModels(listedDownloadedModels.value, locale.value),
])

const filteredDownloadedModels = computed(() => {
  const query = modelSearch.value.trim().toLowerCase()
  const selectedCategory = modelCategoryFilter.value.trim().toLowerCase()
  return listedDownloadedModels.value.filter((item) => {
    // Notes are searchable here too: picking a model to run is exactly when "the one that was
    // good for vocals" is how someone remembers it.
    const matchesQuery = matchesModelQuery(item, query, modelNote(item.name))
    const matchesCategory = !selectedCategory
      || item.category.toLowerCase() === selectedCategory
      || item.primaryCategory.toLowerCase() === selectedCategory
      || item.secondaryCategory.toLowerCase() === selectedCategory
    return matchesQuery && matchesCategory
  })
})

const pagedDownloadedModels = computed(() => {
  const start = (modelPage.value - 1) * modelPageSize.value
  return filteredDownloadedModels.value.slice(start, start + modelPageSize.value)
})

const modelPageSizeOptions = [...SEPARATE_MODEL_PAGE_SIZES]

  const compareModelName = (a: string, b: string) => a.localeCompare(b, locale.value === 'zh-CN' ? 'zh-CN' : 'en')
  return {
    modelSearch, modelCategoryFilter, modelPage, modelSortOptions, listedDownloadedModels,
    selectedModelListItem, modelDownloaded, modelPanelHasModels, modelPanelLoading,
    modelCategoryOptions, filteredDownloadedModels, pagedDownloadedModels, modelPageSizeOptions,
  }
}
