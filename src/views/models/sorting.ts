/**
 * 模型库主列表的排序与分页：收藏置顶、各维度排序、页切片。
 *
 * 预计算排序键（favorite/category/type），避免比较器里重复求值。
 */
import type { ComputedRef } from 'vue'

type Deps = Record<string, any> & {
  computed: <T>(source: () => T) => ComputedRef<T>
}

export function makeModelSorting(deps: Deps) {
  const { computed } = deps

  const collator = computed(() => new Intl.Collator(deps.locale.value === 'zh-CN' ? 'zh-CN' : 'en', { numeric: true, sensitivity: 'base' }))

  function compareModelName(a: any, b: any) {
    return collator.value.compare(a.name, b.name)
  }

  function isFavoriteModel(model: any) {
    return Boolean(model?.name && deps.modelPreferences.value[model.name]?.favorite)
  }

  const sortedModels = computed(() => {
    const list = deps.downloadedOnly.value
      ? deps.filteredModels.value.filter((m: any) => m.downloaded)
      : deps.filteredModels.value
    const sort = deps.modelSort.value
    // 收藏置顶仅在默认/收藏排序下生效，其余排序纯按维度排列
    const pinFavorite = sort === 'default' || sort === 'favorite'
    const decorated = list.map((model: any) => ({
      model,
      favorite: isFavoriteModel(model),
      categoryKey: sort === 'category' ? deps.categoryLabel(model) : '',
      typeKey: sort === 'type' ? String(model.modelType || '') : '',
    }))
    decorated.sort((a: any, b: any) => {
      if (pinFavorite) {
        const favoriteDelta = Number(b.favorite) - Number(a.favorite)
        if (favoriteDelta) return favoriteDelta
      }
      switch (sort) {
        case 'name-desc':
          return compareModelName(b.model, a.model)
        case 'size-desc':
          return b.model.sizeBytes - a.model.sizeBytes || compareModelName(a.model, b.model)
        case 'size-asc':
          return a.model.sizeBytes - b.model.sizeBytes || compareModelName(a.model, b.model)
        case 'category':
          return collator.value.compare(a.categoryKey, b.categoryKey) || compareModelName(a.model, b.model)
        case 'type':
          return collator.value.compare(a.typeKey, b.typeKey) || compareModelName(a.model, b.model)
        case 'downloaded':
          if (a.model.downloaded !== b.model.downloaded) return a.model.downloaded ? -1 : 1
          return compareModelName(a.model, b.model)
        case 'favorite':
        case 'name-asc':
          return compareModelName(a.model, b.model)
        default:
          if (a.model.downloaded !== b.model.downloaded) return a.model.downloaded ? -1 : 1
          return compareModelName(a.model, b.model)
      }
    })
    return decorated.map((d: any) => d.model)
  })

  const pagedModels = computed(() => {
    const start = (deps.page.value - 1) * deps.pageSize.value
    return sortedModels.value.slice(start, start + deps.pageSize.value)
  })

  return { collator, compareModelName, isFavoriteModel, sortedModels, pagedModels }
}
