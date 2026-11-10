import type { Ref } from 'vue'
import { getModelCategoryLabel } from '@/utils/modelCategory'

type Deps = {
  t: (key: string, params?: Record<string, unknown>) => string
  locale: Ref<string>
  model: any
  listedDownloadedModels: Ref<any[]>
  selectedModelName: Ref<string>
  ensembleEnabled: Ref<boolean>
  ensembleModels: Ref<string[]>
  shouldPrefetchAdvancedParams: Ref<boolean>
  detailLoading: Ref<boolean>
  isLoading: Ref<boolean>
}

/** Labels and selection behaviour for the model picker. */
export function makeModelPanel({
  t, locale, model, listedDownloadedModels, selectedModelName,
  ensembleEnabled, ensembleModels, shouldPrefetchAdvancedParams, detailLoading, isLoading,
}: Deps) {
  function categoryLabel(item: { categoryCn?: string; category?: string; primaryCategoryCn?: string; primaryCategory?: string } | null | undefined) {
    return getModelCategoryLabel(item, locale.value, t('common.notSet'))
  }

  function modelTargetLabel(item: { targetStem?: string; configTargetInstrument?: string } | null | undefined) {
    return item?.targetStem || item?.configTargetInstrument || t('common.notSet')
  }

  function modelArchitectureLabel(item: { architecture?: string; modelType?: string | null } | null | undefined) {
    return item?.architecture || item?.modelType || t('common.notSet')
  }

  function modelUseCount(name: string) {
    return model.getModelUseCount(name)
  }

  function modelMetaLine(item: { targetStem?: string; configTargetInstrument?: string; architecture?: string; modelType?: string | null }) {
    return `${modelTargetLabel(item)} · ${modelArchitectureLabel(item)}`
  }

  function handleSelectModel(item: (typeof listedDownloadedModels.value)[number]) {
    if (ensembleEnabled.value) {
      const next = ensembleModels.value.includes(item.name)
        ? ensembleModels.value.filter(name => name !== item.name)
        : [...ensembleModels.value, item.name]
      ensembleModels.value = next
      return
    }
    model.selectModel(item).catch(() => {})
  }

  function prefetchSelectedModelAdvancedParams() {
    if (!shouldPrefetchAdvancedParams.value || detailLoading.value || isLoading.value || !selectedModelName.value) return
    model.selectModel(selectedModelName.value).catch(() => {})
  }

  return {
    categoryLabel, modelTargetLabel, modelArchitectureLabel, modelUseCount,
    modelMetaLine, handleSelectModel, prefetchSelectedModelAdvancedParams,
  }
}
