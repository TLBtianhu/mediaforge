// SeparateView 的输出 stem 选择：清空标记、可选 stem、数量与全选/清空。
type Deps = Record<string, any>

export function makeStemSelection(deps: Deps) {
  const outputStemSelectionCleared = deps.ref(false)
  const checkedOutputStems = deps.computed({
    get() {
      if (outputStemSelectionCleared.value) return []
      if (!deps.selectedStems.value.length) return [...deps.availableStemNames.value]
      return deps.selectedStems.value
    },
    set(value: string[]) {
      const allowed = new Set(deps.availableStemNames.value)
      const next = value.filter((stem: string) => allowed.has(stem))
      const cleared = next.length === 0
      outputStemSelectionCleared.value = cleared
      deps.persistedOutputStemSelectionCleared.value = cleared
      deps.selectedStems.value = next.length === deps.availableStemNames.value.length ? [] : next
    },
  })
  const selectedOutputStemCount = deps.computed(() => checkedOutputStems.value.length)
  const hasOutputStemSelection = deps.computed(() => (
    !deps.availableStemNames.value.length || selectedOutputStemCount.value > 0
  ))
  function selectAllOutputStems() {
    outputStemSelectionCleared.value = false
    deps.persistedOutputStemSelectionCleared.value = false
    deps.selectedStems.value = []
  }
  function clearOutputStems() {
    outputStemSelectionCleared.value = true
    deps.persistedOutputStemSelectionCleared.value = true
    deps.selectedStems.value = []
  }
  return {
    outputStemSelectionCleared, checkedOutputStems, selectedOutputStemCount,
    hasOutputStemSelection, selectAllOutputStems, clearOutputStems,
  }
}
