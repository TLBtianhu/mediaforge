// DebugView 的调试模型目录编辑器：状态、校验、草稿与保存。
import { computed, ref, type Ref } from 'vue'
import { invoke } from '@tauri-apps/api/core'
import type { CatalogEditMode, DebugCatalogInfo } from '../debug_types'

type Options = {
  t: (key: string, params?: Record<string, unknown>) => string
  message: { success: (content: string) => void; error: (content: string) => void }
  modelStore: { loadModels: () => Promise<unknown> }
  downloadSource: Ref<string>
}

export function useCatalogEditor({ t, message, modelStore, downloadSource }: Options) {
const debugCatalogLoading = ref(false)
const debugCatalogError = ref('')
const debugCatalogSaving = ref(false)
const debugCatalogInfo = ref<DebugCatalogInfo | null>(null)
const debugCatalogText = ref('')
const debugCatalogObject = ref<Record<string, any>>({ schema_version: 1, models: [] })
const debugCatalogModels = ref<Record<string, any>[]>([])
const catalogEditMode = ref<CatalogEditMode>('simple')
const catalogSearch = ref('')
const selectedCatalogModelName = ref('')
const catalogModelDraft = ref<Record<string, any>>({})
const catalogModelDialogVisible = ref(false)

function catalogForEditor(result: DebugCatalogInfo) {
  return result.effectiveCatalog || result.baseCatalog || { schema_version: 1, models: [], removed: [] }
}
function cloneJson<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T
}

function stableJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map((item) => stableJson(item)).join(',')}]`
  if (value && typeof value === 'object') {
    return `{${Object.keys(value as Record<string, unknown>).sort().map((key) => `${JSON.stringify(key)}:${stableJson((value as Record<string, unknown>)[key])}`).join(',')}}`
  }
  return JSON.stringify(value)
}

function safeCatalogRelpath(modelName: string, field: string, value: unknown, required = false) {
  if (value == null) value = ''
  if (typeof value !== 'string') throw new Error(t('debug.catalogFieldMustBeString', { model: modelName, field }))
  const relpath = value.trim().replace(/\\/g, '/')
  if (required && !relpath) throw new Error(t('debug.catalogFieldRequired', { model: modelName, field }))
  const parts = relpath.split('/').filter(Boolean)
  if (relpath && (relpath.startsWith('/') || /^[a-zA-Z]:\//.test(relpath) || parts.includes('..'))) {
    throw new Error(t('debug.catalogFieldUnsafePath', { model: modelName, field }))
  }
  return relpath
}

function validateDebugCatalog(catalog: unknown) {
  if (!catalog || typeof catalog !== 'object' || Array.isArray(catalog)) throw new Error(t('debug.catalogPayloadMustBeObject'))
  const source = catalog as Record<string, any>
  if (!Array.isArray(source.models)) throw new Error(t('debug.catalogModelsMustBeArray'))
  if (source.removed != null && !Array.isArray(source.removed)) throw new Error(t('debug.catalogRemovedMustBeArray'))
  const seen = new Set<string>()
  const models = source.models.map((item: unknown, index: number) => {
    if (!item || typeof item !== 'object' || Array.isArray(item)) throw new Error(t('debug.catalogModelMustBeObject', { index }))
    const model = item as Record<string, any>
    const name = String(model.name || '').trim()
    if (!name) throw new Error(t('debug.catalogModelNameRequiredAt', { index }))
    if (seen.has(name)) throw new Error(t('debug.catalogModelDuplicate', { model: name }))
    seen.add(name)
    const auxiliaryRelpaths = model.auxiliary_relpaths ?? []
    if (!Array.isArray(auxiliaryRelpaths)) throw new Error(t('debug.catalogAuxiliaryMustBeArray', { model: name }))
    return {
      ...model,
      name,
      relpath: safeCatalogRelpath(name, 'relpath', model.relpath, true),
      config_relpath: safeCatalogRelpath(name, 'config_relpath', model.config_relpath),
      auxiliary_relpaths: auxiliaryRelpaths.map((relpath: unknown, auxiliaryIndex: number) => (
        safeCatalogRelpath(name, `auxiliary_relpaths[${auxiliaryIndex}]`, relpath, true)
      )),
    }
  })
  return { ...source, models }
}

function parseDebugCatalogText() {
  try {
    return validateDebugCatalog(JSON.parse(debugCatalogText.value || '{}'))
  } catch (error) {
    if (error instanceof SyntaxError) throw new Error(t('debug.catalogJsonSyntaxInvalid'))
    throw error
  }
}

function setCatalogEditor(catalog: Record<string, unknown>) {
  const cloned = cloneJson(catalog || { schema_version: 1, models: [] }) as Record<string, any>
  const models = Array.isArray(cloned.models) ? cloned.models.filter((item: unknown) => item && typeof item === 'object') : []
  debugCatalogObject.value = { ...cloned, models }
  debugCatalogModels.value = models
  debugCatalogText.value = JSON.stringify(debugCatalogObject.value, null, 2)
  if (!selectedCatalogModelName.value || !models.some((item: Record<string, any>) => item.name === selectedCatalogModelName.value)) {
    selectedCatalogModelName.value = String(models[0]?.name || '')
  }
  loadCatalogDraft(selectedCatalogModelName.value)
}

const visibleCatalogModels = computed(() => {
  const query = catalogSearch.value.trim().toLowerCase()
  if (!query) return debugCatalogModels.value
  return debugCatalogModels.value.filter((model) => [
    model.name,
    model.model_type,
    model.architecture,
    model.primary_category,
    model.primary_category_cn,
    model.secondary_category,
    model.secondary_category_cn,
  ].some((value) => String(value || '').toLowerCase().includes(query)))
})
const baseCatalogModelsByName = computed(() => {
  const models = debugCatalogInfo.value?.baseCatalog?.models
  const list = Array.isArray(models) ? models : []
  return new Map(list
    .filter((item: unknown): item is Record<string, any> => Boolean(item && typeof item === 'object' && (item as Record<string, any>).name))
    .map((item) => [String(item.name), item]))
})

function catalogModelStatus(model: Record<string, any>) {
  const base = baseCatalogModelsByName.value.get(String(model.name || ''))
  if (!base) return 'added'
  return stableJson(model) === stableJson(base) ? 'normal' : 'modified'
}

function syncCatalogTextFromSimple() {
  debugCatalogObject.value = { ...debugCatalogObject.value, models: debugCatalogModels.value }
  debugCatalogText.value = JSON.stringify(debugCatalogObject.value, null, 2)
}

function loadCatalogDraft(name: string) {
  const model = debugCatalogModels.value.find((item) => item.name === name)
  catalogModelDraft.value = model
    ? { ...cloneJson(model), aliasesText: Array.isArray(model.aliases) ? model.aliases.join(', ') : '' }
    : {}
}

function selectCatalogModel(name: string) {
  selectedCatalogModelName.value = name
  loadCatalogDraft(name)
  catalogModelDialogVisible.value = true
}

function addCatalogModel() {
  const nextName = `debug_model_${debugCatalogModels.value.length + 1}.ckpt`
  catalogModelDraft.value = {
    name: nextName,
    aliasesText: nextName.replace(/\.[^.]+$/, ''),
    model_type: '',
    architecture: '',
    supported: true,
    unsupported_reason: '',
    relpath: '',
    config_relpath: '',
    auxiliary_relpaths: [],
    size_bytes: 0,
    sha256: '',
    primary_category: 'custom',
    primary_category_cn: '自定义',
    secondary_category: '',
    secondary_category_cn: '',
    target_stem: '',
    config_instruments: '',
    config_target_instrument: '',
    classification_confidence: 'manual',
    classification_basis: 'debug catalog',
  }
  selectedCatalogModelName.value = ''
  catalogModelDialogVisible.value = true
}

function saveCatalogDraft() {
  const draft = cloneJson(catalogModelDraft.value) as Record<string, any>
  const name = String(draft.name || '').trim()
  if (!name) {
    message.error(t('debug.catalogModelNameRequired'))
    return
  }
  const aliases = String(draft.aliasesText || '')
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean)
  delete draft.aliasesText
  draft.name = name
  draft.aliases = aliases
  draft.supported = Boolean(draft.supported)
  draft.size_bytes = Number.parseInt(String(draft.size_bytes || 0), 10) || 0
  const existingIndex = debugCatalogModels.value.findIndex((item) => item.name === selectedCatalogModelName.value || item.name === name)
  if (existingIndex >= 0) debugCatalogModels.value.splice(existingIndex, 1, draft)
  else debugCatalogModels.value.unshift(draft)
  selectedCatalogModelName.value = name
  catalogModelDraft.value = { ...cloneJson(draft), aliasesText: aliases.join(', ') }
  syncCatalogTextFromSimple()
  catalogModelDialogVisible.value = false
}

function removeCatalogModel(name: string) {
  if (!name) return
  debugCatalogModels.value = debugCatalogModels.value.filter((item) => item.name !== name)
  selectedCatalogModelName.value = debugCatalogModels.value[0]?.name || ''
  loadCatalogDraft(selectedCatalogModelName.value)
  syncCatalogTextFromSimple()
}

function switchCatalogEditMode(mode: CatalogEditMode) {
  if (mode === catalogEditMode.value) return
  if (mode === 'simple') {
    try {
      setCatalogEditor(parseDebugCatalogText())
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error))
      return
    }
  } else {
    syncCatalogTextFromSimple()
  }
  catalogEditMode.value = mode
}

function quoteCatalogRelpath(path: string) {
  return String(path || '')
    .split('/')
    .map((part) => encodeURIComponent(part))
    .join('/')
}

function currentCatalogDownloadUrl(relpath: unknown) {
  const path = String(relpath || '').trim().replace(/\\/g, '/')
  if (!path) return ''
  const repositories = (debugCatalogInfo.value?.effectiveCatalog?.source_repository
    || debugCatalogInfo.value?.baseCatalog?.source_repository
    || {}) as Record<string, unknown>
  const quotedPath = quoteCatalogRelpath(path)
  if (downloadSource.value === 'huggingface') {
    const base = String(repositories.huggingface || '').replace(/\/$/, '')
    return base ? `${base}/resolve/main/${quotedPath}` : ''
  }
  if (downloadSource.value === 'hf-mirror') {
    const hfBase = String(repositories.huggingface || '')
    const match = hfBase.match(/huggingface\.co\/(.+)$/)
    return match?.[1] ? `https://hf-mirror.com/${match[1].replace(/\/$/, '')}/resolve/main/${quotedPath}` : ''
  }
  const base = String(repositories.modelscope || '').replace(/\/$/, '')
  return base ? `${base}/resolve/master/${quotedPath}` : ''
}

async function loadDebugCatalog() {
  debugCatalogLoading.value = true
  debugCatalogError.value = ''
  try {
    const result = await invoke<DebugCatalogInfo>('debug_catalog_info')
    debugCatalogInfo.value = result
    setCatalogEditor(catalogForEditor(result))
  } catch (error) {
    debugCatalogError.value = error instanceof Error ? error.message : String(error)
  } finally {
    debugCatalogLoading.value = false
  }
}

async function saveDebugCatalog() {
  debugCatalogSaving.value = true
  try {
    if (catalogEditMode.value === 'simple') syncCatalogTextFromSimple()
    const catalog = parseDebugCatalogText()
    const result = await invoke<DebugCatalogInfo>('debug_catalog_save', { payload: { catalog, fullCatalog: true } })
    debugCatalogInfo.value = result
    setCatalogEditor(catalogForEditor(result))
    await modelStore.loadModels()
    message.success(t('debug.debugCatalogSaved'))
  } catch (error) {
    message.error(error instanceof Error ? error.message : String(error))
  } finally {
    debugCatalogSaving.value = false
  }
}

async function resetDebugCatalog() {
  debugCatalogSaving.value = true
  try {
    const result = await invoke<DebugCatalogInfo>('debug_catalog_reset')
    debugCatalogInfo.value = result
    setCatalogEditor(catalogForEditor(result))
    await modelStore.loadModels()
    message.success(t('debug.debugCatalogReset'))
  } catch (error) {
    message.error(error instanceof Error ? error.message : String(error))
  } finally {
    debugCatalogSaving.value = false
  }
}

  return {
    debugCatalogLoading,
    debugCatalogError,
    debugCatalogSaving,
    debugCatalogInfo,
    debugCatalogText,
    debugCatalogObject,
    debugCatalogModels,
    catalogEditMode,
    catalogSearch,
    selectedCatalogModelName,
    catalogModelDraft,
    catalogModelDialogVisible,
    catalogForEditor,
    cloneJson,
    stableJson,
    safeCatalogRelpath,
    validateDebugCatalog,
    parseDebugCatalogText,
    setCatalogEditor,
    visibleCatalogModels,
    baseCatalogModelsByName,
    catalogModelStatus,
    syncCatalogTextFromSimple,
    loadCatalogDraft,
    selectCatalogModel,
    addCatalogModel,
    saveCatalogDraft,
    removeCatalogModel,
    switchCatalogEditMode,
    quoteCatalogRelpath,
    currentCatalogDownloadUrl,
    loadDebugCatalog,
    saveDebugCatalog,
    resetDebugCatalog,
  }
}
