/**
 * 模型库的备注与使用统计：备注读写、使用次数、收藏切换、备注编辑器开关与保存。
 *
 * 收藏本体在 models/sorting.ts（isFavoriteModel），这里管备注与统计。
 */
import type { Ref } from 'vue'
import type { MessageApi } from 'naive-ui'
import type { ModelEntry, ModelPreference, useModelStore } from '@/stores/model'

type Deps = {
  modelPreferences: Ref<Record<string, ModelPreference>>
  modelStore: ReturnType<typeof useModelStore>
  noteEditorModel: Ref<ModelEntry | null>
  noteDraft: Ref<string>
  showNoteEditor: Ref<boolean>
  message: MessageApi
  t: (key: string, named?: Record<string, unknown>) => string
}

export function makeModelNotes(deps: Deps) {
  function modelNote(modelOrName: ModelEntry | string) {
    const name = typeof modelOrName === 'string' ? modelOrName : modelOrName?.name
    return name ? deps.modelPreferences.value[name]?.note || '' : ''
  }

  function modelUseCount(modelOrName: ModelEntry | string) {
    const name = typeof modelOrName === 'string' ? modelOrName : modelOrName?.name
    return name ? deps.modelStore.getModelUseCount(name) : 0
  }

  function modelNameParts(name: string) {
    return name.split(/(?<=[_-])/u)
  }

  function toggleFavorite(model: ModelEntry, event?: MouseEvent) {
    event?.stopPropagation()
    deps.modelStore.toggleModelFavorite(model.name)
  }

  function openModelNoteEditor(model: ModelEntry) {
    deps.noteEditorModel.value = model
    deps.noteDraft.value = modelNote(model)
    deps.showNoteEditor.value = true
  }

  function closeModelNoteEditor() {
    deps.showNoteEditor.value = false
    deps.noteEditorModel.value = null
  }

  function handleNoteEditorUpdate(value: boolean) {
    deps.showNoteEditor.value = value
    if (!value) deps.noteEditorModel.value = null
  }

  function saveModelNote() {
    const model = deps.noteEditorModel.value
    if (!model) return
    deps.modelStore.setModelNote(model.name, deps.noteDraft.value)
    closeModelNoteEditor()
    deps.message.success(deps.t('models.noteSaved'))
  }

  return {
    modelNote, modelUseCount, modelNameParts, toggleFavorite,
    openModelNoteEditor, closeModelNoteEditor, handleNoteEditorUpdate, saveModelNote,
  }
}
