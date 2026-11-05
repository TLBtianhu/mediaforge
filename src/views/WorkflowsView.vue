<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { useDialog, useMessage, type DropdownOption } from 'naive-ui'
import { useI18n } from 'vue-i18n'
import {
  AlertCircleOutline,
  CheckmarkCircle,
  CubeOutline,
  EllipsisHorizontalOutline,
  GitNetworkOutline,
  ListOutline,
  MusicalNotesOutline,
  OpenOutline,
  PlayOutline,
  SearchOutline,
  SettingsOutline,
} from '@vicons/ionicons5'
import { invoke } from '@tauri-apps/api/core'
import { storeToRefs } from 'pinia'
import WorkflowCreateChooser from '@/components/workflow/WorkflowCreateChooser.vue'
import WorkflowOverviewPreview from '@/components/workflow/WorkflowOverviewPreview.vue'
import MediaPageHeader from '@/components/media/MediaPageHeader.vue'
import { useModelStore } from '@/stores/model'
import {
  WorkflowRevisionConflictError,
  useWorkflowStore,
  type WorkflowEntry,
} from '@/stores/workflow'
import { makeWorkflowListing } from './workflows/listing'
import {
  analyzeComfyOverview,
  analyzeSimpleWorkflow,
  hydrateSimpleWorkflow,
  type SimpleWorkflowReasonCode,
} from '@/utils/workflowSimple'
import { WorkflowEditorInUseError } from '@/utils/workflowEditorState'
import { themeIsDark } from '@/utils/theme'
import { hasInvalidSimpleStructure, isGraphWorkflowDefinition, isSimpleWorkflowDefinition } from '@/workflows/formats'
import { litegraphToComfy } from '@/litegraph/graphAdapter'
import {
  countWorkflowSaveOutputs,
  getWorkflowDefinitionIssue,
} from '@/workflows/runtimeDefinition'

const { t } = useI18n()
const message = useMessage()
const dialog = useDialog()
const workflow = useWorkflowStore()
const model = useModelStore()
const { workflows, selectedWorkflowId, selectedWorkflow, simpleEditorOpenWorkflowId } = storeToRefs(workflow)
const { downloadedModels } = storeToRefs(model)
const editingId = ref('')
const name = ref('')
const description = ref('')
const query = ref('')
type WorkflowTypeFilter = 'all' | 'simple' | 'advanced'
type WorkflowKind = Exclude<WorkflowTypeFilter, 'all'> | 'invalid'
const typeFilter = ref<WorkflowTypeFilter>('all')
const importFileInputRef = ref<HTMLInputElement | null>(null)
const createChooserOpen = ref(false)
const settingsOpen = ref(false)
type WorkflowCreateType = 'simple' | 'advanced'
const contextMenuX = ref(0)
const contextMenuY = ref(0)
const contextMenuVisible = ref(false)
const contextWorkflow = ref<WorkflowEntry | null>(null)
type WorkflowDefaultsPatch = {
  defaultDevice?: string
  defaultFormat?: string
  defaultNormalize?: boolean | null
}
const updatingWorkflowDefaults = ref(false)
const pendingWorkflowDefaults = new Map<string, WorkflowDefaultsPatch>()
const workflowDefaultsQueue: string[] = []
let unmounted = false

const deviceOptions = [
  { label: 'Auto', value: 'auto' },
  { label: 'CPU', value: 'cpu' },
  { label: 'CUDA', value: 'cuda' },
  { label: 'MPS', value: 'mps' },
  { label: 'MLX', value: 'mlx' },
]
const formatOptions = [
  { label: 'WAV', value: 'wav' },
  { label: 'FLAC', value: 'flac' },
  { label: 'MP3', value: 'mp3' },
  { label: 'M4A', value: 'm4a' },
]
const normalizeOptions = computed(() => [
  { label: t('workflows.inferenceFollowGlobal'), value: 'global' },
  { label: t('workflows.inferenceEnabled'), value: 'enabled' },
  { label: t('workflows.inferenceDisabled'), value: 'disabled' },
])

const {
  normalizeMode, workflowKind, workflowTypeCounts, workflowTypeFilterOptions,
  filteredWorkflows, workflowGroups, workflowListMetaMap, workflowListMeta,
  workflowTypeLabel, workflowDefinitionError, workflowRunBlocked, workflowStatusMap,
  isWorkflowBlocked, isNodeEditorOpen, isAnyEditorOpen, setWorkflowTypeFilter,
} = makeWorkflowListing({
  computed, t, workflows, query, typeFilter, workflow, selectedWorkflowId,
  isSimpleWorkflowDefinition, isGraphWorkflowDefinition, analyzeComfyOverview, hydrateSimpleWorkflow, countWorkflowSaveOutputs, getWorkflowDefinitionIssue,
})

function workflowMenuOptionsFor(current: WorkflowEntry | null): DropdownOption[] {
  if (!current) return []
  const isSimpleDefinition = isSimpleWorkflowDefinition(current.definition)
  const canEditSimple = isSimpleDefinition && analyzeSimpleWorkflow(current.definition).editable
  const definitionError = workflowDefinitionError(current.definition)
  const locked = Boolean(workflow.editorFor(current.id))
  const options: DropdownOption[] = [
    {
      key: 'edit',
      label: isSimpleDefinition ? t('workflows.simpleMode') : t('workflows.openAdvancedEditor'),
      disabled: locked || (isSimpleDefinition && !canEditSimple),
    },
  ]
  options.push(
    { type: 'divider', key: 'workflow-actions-divider' },
    { key: 'duplicate', label: t('workflows.duplicate') },
    { key: 'export', label: t('workflows.exportWorkflow') },
    { key: 'delete', label: t('workflows.deleteConfirm'), disabled: locked },
  )
  return options
}

const workflowMenuOptions = computed(() => workflowMenuOptionsFor(contextWorkflow.value))
const selectedWorkflowMenuOptions = computed(() => workflowMenuOptionsFor(selectedWorkflow.value))
const selectedDescriptionPreview = computed(() => selectedWorkflow.value?.description.replace(/\s+/g, ' ').trim() || '')

// ---- Selected workflow overview data (simple-mode details; comfy graphs
// show a read-only overview since their structure is free-form) ----
const selectedComfyOverview = computed(() => analyzeComfyOverview(selectedWorkflow.value?.definition))
const isComfyWorkflow = computed(() => isGraphWorkflowDefinition(selectedWorkflow.value?.definition))
const isSimpleWorkflow = computed(() => isSimpleWorkflowDefinition(selectedWorkflow.value?.definition))
const selectedWorkflowKind = computed<WorkflowKind>(() => selectedWorkflow.value
  ? workflowKind(selectedWorkflow.value)
  : 'invalid')
const selectedDraft = computed(() =>
  isSimpleWorkflowDefinition(selectedWorkflow.value?.definition)
    ? hydrateSimpleWorkflow(selectedWorkflow.value?.definition)
    : null)
const selectedSummary = computed((): { error: string } | null =>
  selectedWorkflow.value ? { error: workflowDefinitionError(selectedWorkflow.value.definition) } : null)
const selectedModels = computed(() => {
  if (isComfyWorkflow.value) {
    const downloaded = new Set(downloadedModels.value.map(item => item.name))
    return (selectedComfyOverview.value?.models || []).map(name => ({ name, downloaded: downloaded.has(name) }))
  }
  const draft = selectedDraft.value
  if (!draft || !draft.steps.length) return [] as { name: string; downloaded: boolean }[]
  const downloaded = new Set(downloadedModels.value.map(item => item.name))
  const seen = new Set<string>()
  const list: { name: string; downloaded: boolean }[] = []
  for (const step of draft.steps) {
    const modelName = step.model.trim()
    if (!modelName || seen.has(modelName)) continue
    seen.add(modelName)
    list.push({ name: modelName, downloaded: downloaded.has(modelName) })
  }
  return list
})
const selectedError = computed(() => selectedSummary.value?.error || '')
const missingModelCount = computed(() => selectedModels.value.filter(item => !item.downloaded).length)
const selectedReady = computed(() => Boolean(selectedWorkflow.value) && !selectedError.value)
const selectedSimpleAnalysis = computed(() => selectedWorkflow.value
  ? analyzeSimpleWorkflow(selectedWorkflow.value.definition)
  : null)
const selectedSimpleReasons = computed(() => selectedSimpleAnalysis.value?.reasonCodes || [])

const simpleReasonKeys: Record<SimpleWorkflowReasonCode, string> = {
  graph_workflow: 'workflows.simpleReasonGraphWorkflow',
  advanced_parameters: 'workflows.simpleReasonAdvancedParameters',
  invalid_definition: 'workflows.simpleReasonInvalidDefinition',
}

function simpleReasonLabel(reason: SimpleWorkflowReasonCode) {
  return t(simpleReasonKeys[reason])
}

async function flushWorkflowDefaultsQueue() {
  if (updatingWorkflowDefaults.value) return
  updatingWorkflowDefaults.value = true
  try {
    while (workflowDefaultsQueue.length) {
      const workflowId = workflowDefaultsQueue[0]
      const patch = pendingWorkflowDefaults.get(workflowId)
      pendingWorkflowDefaults.delete(workflowId)
      if (!patch) {
        workflowDefaultsQueue.shift()
        continue
      }
      const current = workflows.value.find(item => item.id === workflowId)
      const analysis = current ? analyzeSimpleWorkflow(current.definition) : null
      if (!current || !analysis?.editable) {
        workflowDefaultsQueue.shift()
        continue
      }
      const draft = hydrateSimpleWorkflow(current.definition)
      const definition = JSON.parse(JSON.stringify(current.definition)) as Record<string, unknown>
      const defaults = definition.defaults && typeof definition.defaults === 'object' && !Array.isArray(definition.defaults)
        ? definition.defaults as Record<string, unknown>
        : {}
      defaults.device = patch.defaultDevice ?? draft.defaultDevice
      defaults.output_format = patch.defaultFormat ?? draft.defaultFormat
      const inference = defaults.inference_params && typeof defaults.inference_params === 'object' && !Array.isArray(defaults.inference_params)
        ? { ...(defaults.inference_params as Record<string, unknown>) }
        : {}
      const normalize = patch.defaultNormalize !== undefined ? patch.defaultNormalize : draft.defaultNormalize
      if (normalize === null) delete inference.normalize
      else inference.normalize = normalize
      if (Object.keys(inference).length) defaults.inference_params = inference
      else delete defaults.inference_params
      definition.defaults = defaults
      try {
        const entry = await workflow.saveWorkflow({
          id: current.id,
          name: current.name,
          description: current.description,
          definition,
          expectedUpdatedAt: current.updatedAt,
        })
        // The selected workflow may change while persistence is in flight. Do
        // not overwrite the newly selected editor fields with the old entry.
        if (selectedWorkflowId.value === current.id) editWorkflow(entry)
      } catch (error) {
        pendingWorkflowDefaults.delete(workflowId)
        workflowDefaultsQueue.shift()
        message.error(error instanceof WorkflowEditorInUseError ? t('workflows.editorInUseHint') : error instanceof Error ? error.message : String(error))
        continue
      }
      if (!pendingWorkflowDefaults.has(workflowId)) workflowDefaultsQueue.shift()
    }
  } finally {
    updatingWorkflowDefaults.value = false
    if (workflowDefaultsQueue.length) void flushWorkflowDefaultsQueue()
  }
}

function updateSelectedWorkflowDefaults(patch: WorkflowDefaultsPatch) {
  const current = selectedWorkflow.value
  if (!current || !selectedDraft.value || !selectedSimpleAnalysis.value?.editable || isNodeEditorOpen.value) return
  const pending = pendingWorkflowDefaults.get(current.id)
  if (!pending) workflowDefaultsQueue.push(current.id)
  pendingWorkflowDefaults.set(current.id, { ...pending, ...patch })
  void flushWorkflowDefaultsQueue()
}

function updateSelectedDefaultDevice(value: string | number | null) {
  updateSelectedWorkflowDefaults({ defaultDevice: String(value || 'auto') })
}

function updateSelectedDefaultFormat(value: string | number | null) {
  updateSelectedWorkflowDefaults({ defaultFormat: String(value || 'wav') })
}

function updateSelectedDefaultNormalize(value: string | number | boolean | null) {
  updateSelectedWorkflowDefaults({
    defaultNormalize: value === 'global' ? null : typeof value === 'boolean' ? value : value === 'enabled',
  })
}


// ---- Selection + quick meta edit ----
function syncWorkflowDetails(item: WorkflowEntry | null) {
  editingId.value = item?.id || ''
  name.value = item?.name || ''
  description.value = item?.description || ''
}

function editWorkflow(item: WorkflowEntry) {
  syncWorkflowDetails(item)
  workflow.selectWorkflow(item.id)
}

async function saveMeta() {
  const current = selectedWorkflow.value
  if (!current || isNodeEditorOpen.value) return
  const targetId = current.id
  const trimmedName = name.value.trim()
  if (!trimmedName) {
    name.value = current.name
    return
  }
  if (trimmedName === current.name && description.value.trim() === current.description) return
  try {
    const entry = await workflow.saveWorkflow({
      id: targetId,
      name: trimmedName,
      description: description.value,
      definition: current.definition,
      expectedUpdatedAt: current.updatedAt,
    })
    // Guard against a race: if the user switched workflows while saveWorkflow was
    // awaiting, do not clobber the newly selected workflow's displayed fields.
    if (selectedWorkflowId.value !== targetId) return
    editingId.value = entry.id
    name.value = entry.name
    description.value = entry.description
  } catch (error) {
    if (error instanceof WorkflowRevisionConflictError && selectedWorkflowId.value === targetId) {
      syncWorkflowDetails(selectedWorkflow.value)
    }
    message.error(error instanceof WorkflowEditorInUseError ? t('workflows.editorInUseHint') : error instanceof WorkflowRevisionConflictError
      ? t('workflows.revisionConflictTitle')
      : error instanceof Error ? error.message : t('workflows.saveFailed'))
  }
}

async function openEditorWindow(kind: 'simple' | 'node', workflowId: string, newWorkflow: boolean) {
  try {
    if (typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window) {
      const result = await invoke(`open_workflow_${kind}_editor_window`, { payload: { workflowId, newWorkflow } })
      workflow.recordEditorOpened(result, kind === 'node' ? 'advanced' : kind, newWorkflow ? '__new__' : workflowId)
    } else {
      const query = newWorkflow ? 'new=1' : `workflowId=${encodeURIComponent(workflowId)}`
      const popup = window.open(`#/workflow-${kind}-editor?${query}`, `workflow-${kind}-editor-${workflowId || 'new'}`, 'popup=yes,width=1440,height=900')
      if (!popup) throw new Error(t('workflows.editorWindowOpenFailed'))
      popup.focus()
    }
    if (!(typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window)) workflow.recordEditorOpened(null, kind === 'node' ? 'advanced' : kind, newWorkflow ? '__new__' : workflowId)
  } catch (error) {
    message.error(error instanceof Error ? error.message : t('workflows.editorWindowOpenFailed'))
  }
}

async function openSimpleEditor(options: { forceNew?: boolean; workflowId?: string } = {}) {
  const forceNew = options.forceNew === true
  const workflowId = forceNew ? '' : (options.workflowId || selectedWorkflow.value?.id || selectedWorkflowId.value || '')
  const isNewWorkflow = forceNew || !workflowId
  await openEditorWindow('simple', workflowId, isNewWorkflow)
}

function createSimpleWorkflow() {
  void openSimpleEditor({ forceNew: true })
}

function openWorkflowCreateChooser() {
  if (isAnyEditorOpen.value) return
  createChooserOpen.value = true
}

async function createAdvancedWorkflow() {
  editingId.value = ''
  name.value = ''
  description.value = ''
  await openNodeEditor({ forceNew: true, skipWarning: true })
}

function selectWorkflowCreateType(type: WorkflowCreateType) {
  if (type === 'simple') {
    createSimpleWorkflow()
    return
  }
  void createAdvancedWorkflow()
}

function editSimpleWorkflow(item: WorkflowEntry) {
  if (!analyzeSimpleWorkflow(item.definition).editable) return
  void openSimpleEditor({ workflowId: item.id })
}

function selectWorkflowFromList(item: WorkflowEntry) {
  // Selecting a row only changes the overview. Opening an editor is an
  // explicit action from the overview or the context menu.
  editWorkflow(item)
}

function openWorkflowContextMenu(event: MouseEvent, item: WorkflowEntry) {
  contextWorkflow.value = item
  contextMenuVisible.value = false
  contextMenuX.value = event.clientX
  contextMenuY.value = event.clientY
  window.requestAnimationFrame(() => {
    contextMenuVisible.value = true
  })
}

function closeWorkflowContextMenu() {
  contextMenuVisible.value = false
  contextWorkflow.value = null
}

function openWorkflowEditor(item: WorkflowEntry) {
  editWorkflow(item)
  if (isSimpleWorkflowDefinition(item.definition)) {
    if (!analyzeSimpleWorkflow(item.definition).editable) {
      message.warning(t('workflows.simpleEditUnsupported'))
      return
    }
    editSimpleWorkflow(item)
    return
  }
  void openNodeEditor({ workflowId: item.id })
}

function reopenActiveEditor() {
  const editor = workflow.editorFor(selectedWorkflowId.value) || workflow.openedEditors.at(-1)
  if (!editor) return
  if (editor.kind === 'simple') void openSimpleEditor({ workflowId: editor.workflowId, forceNew: !editor.workflowId })
  else void openNodeEditor({ workflowId: editor.workflowId, forceNew: !editor.workflowId })
}

function handleWorkflowMenuSelect(key: string | number, current: WorkflowEntry | null) {
  closeWorkflowContextMenu()
  if (!current) return

  if (key === 'edit') {
    openWorkflowEditor(current)
    return
  }
  if (key === 'run') {
    editWorkflow(current)
    openWorkflowEditor(current)
    return
  }
  if (key === 'duplicate') {
    void duplicateWorkflow(current)
    return
  }
  if (key === 'export') {
    void exportWorkflowEntry(current)
    return
  }
  if (key === 'delete') {
    deleteWorkflow(current)
  }
}

function confirmAdvancedWorkflowWarning() {
  return new Promise<boolean>((resolve) => {
    let settled = false
    const finish = (value: boolean) => {
      if (settled) return
      settled = true
      resolve(value)
    }
    dialog.warning({
      title: t('workflows.advancedWarningTitle'),
      content: t('workflows.advancedWarningContent'),
      positiveText: t('workflows.advancedWarningContinue'),
      negativeText: t('workflows.advancedWarningCancel'),
      positiveButtonProps: { type: 'warning' },
      negativeButtonProps: { secondary: true },
      onPositiveClick: () => finish(true),
      onNegativeClick: () => finish(false),
      onClose: () => finish(false),
    })
  })
}

// ---- Node editor bridge ----
async function openNodeEditor(options: { forceNew?: boolean; workflowId?: string; skipWarning?: boolean } = {}) {
  if (!options.skipWarning) {
    const confirmed = await confirmAdvancedWorkflowWarning()
    if (!confirmed) return
  }

  const forceNew = options.forceNew === true
  // The overview's selected entry is the authoritative target. `editingId`
  // can briefly lag while the store reloads after a standalone editor closes;
  // preferring it here could reopen a different (or legacy) graph as blank.
  const workflowId = forceNew
    ? ''
    : (options.workflowId || selectedWorkflow.value?.id || selectedWorkflowId.value || editingId.value || '')
  const isNewWorkflow = forceNew || !workflowId
  await openEditorWindow('node', workflowId, isNewWorkflow)
}

function syncRefreshedWorkflowDetails(selected: WorkflowEntry | null) {
  const target = selected
    || workflows.value.find(item => item.id === editingId.value)
    || workflows.value[0]
  syncWorkflowDetails(target || null)
}

// ---- Actions ----
async function duplicateWorkflow(item: WorkflowEntry) {
  const current = item
  if (!current) return
  const entry = await workflow.duplicateWorkflow(current.id)
  if (entry) {
    editWorkflow(entry)
    message.success(t('workflows.duplicated'))
  }
}

function deleteWorkflow(item: WorkflowEntry) {
  const current = item
  if (!current) return
  dialog.warning({
    title: t('workflows.deleteTitle'),
    content: t('workflows.deleteHint', { name: current.name }),
    positiveText: t('workflows.deleteConfirm'),
    negativeText: t('common.cancel'),
    onPositiveClick: async () => {
      const deletedId = current.id
      try {
        await workflow.deleteWorkflow(current.id)
        if (simpleEditorOpenWorkflowId.value === deletedId) workflow.markSimpleEditorClosed()
        message.success(t('workflows.deleted'))
      } catch (error) {
        message.error(error instanceof WorkflowEditorInUseError ? t('workflows.editorInUseHint') : error instanceof WorkflowRevisionConflictError
          ? t('workflows.revisionConflictTitle')
          : error instanceof Error ? error.message : t('workflows.saveFailed'))
      }
    },
  })
}

function runSelected() {
  const current = selectedWorkflow.value
  if (!current) return
  if (selectedError.value) {
    message.warning(selectedError.value)
    return
  }
  workflow.selectWorkflow(current.id)
  openWorkflowEditor(current)
}

// ---- comfy-mss import / export ----
function triggerImportWorkflow() {
  importFileInputRef.value?.click()
}

function workflowFileBasename(fileName: string) {
  return fileName
    .replace(/\.(?:comfy-mss|pymss-workflow)\.json$/iu, '')
    .replace(/\.[^.]+$/u, '')
    .trim()
}

function downloadJsonFile(fileName: string, payload: Record<string, unknown>) {
  const blob = new Blob([`${JSON.stringify(payload, null, 2)}\n`], { type: 'application/json;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = fileName
  anchor.click()
  URL.revokeObjectURL(url)
}

function workflowSlug(value: string) {
  const normalized = value.trim().replace(/[<>:"/\\|?*\x00-\x1f]+/g, '_')
  return normalized || 'workflow'
}

async function handleImportWorkflow(event: Event) {
  const input = event.target as HTMLInputElement | null
  const file = input?.files?.[0]
  if (!file) return
  try {
    const text = await file.text()
    const parsed = JSON.parse(text) as Record<string, unknown>
    if (!isGraphWorkflowDefinition(parsed) && !isSimpleWorkflowDefinition(parsed)) {
      throw new Error(t('workflows.workflowImportInvalid'))
    }
    if (isSimpleWorkflowDefinition(parsed) && hasInvalidSimpleStructure(parsed)) {
      throw new Error(t('workflows.workflowImportInvalid'))
    }
    const entry = await workflow.saveWorkflow({
      name: workflowFileBasename(file.name) || t('workflows.newWorkflow'),
      description: '',
      definition: parsed,
    })
    editWorkflow(entry)
    message.success(t('workflows.workflowImportSuccess'))
  } catch (error) {
    message.error(`${t('workflows.workflowImportFailed')}: ${error instanceof Error ? error.message : String(error)}`)
  } finally {
    if (input) input.value = ''
  }
}

async function exportWorkflowDefinition(
  workflowName: string,
  definition: Record<string, unknown>,
) {
  try {
    const payload = isGraphWorkflowDefinition(definition)
      ? { ...definition, ...litegraphToComfy(definition) }
      : definition
    const suffix = isGraphWorkflowDefinition(definition)
      ? 'comfy-mss.json'
      : 'pymss-workflow.json'
    const fileName = `${workflowSlug(workflowName || t('workflows.untitled'))}.${suffix}`
    const isTauri = typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window
    if (isTauri) {
      const content = `${JSON.stringify(payload, null, 2)}\n`
      const savedPath = await invoke<string | null>('save_text_file_dialog', {
        defaultName: fileName,
        content,
      })
      if (!savedPath) return
    } else {
      downloadJsonFile(fileName, payload)
    }
    message.success(t('workflows.workflowExportSuccess'))
  } catch (error) {
    message.error(`${t('workflows.workflowExportFailed')}: ${error instanceof Error ? error.message : String(error)}`)
  }
}

async function exportWorkflowEntry(current: WorkflowEntry) {
  if (!current) return
  await exportWorkflowDefinition(current.name, current.definition)
}

watch(selectedWorkflowId, () => { settingsOpen.value = false })

const stopEditorCloseSubscription = workflow.$onAction(({ name: action, after }) => {
  if (action !== 'handleEditorClosed') return
  after(() => {
    if (unmounted) return
    syncRefreshedWorkflowDetails(workflow.selectedWorkflow)
  })
})

onUnmounted(() => {
  unmounted = true
  stopEditorCloseSubscription()
  closeWorkflowContextMenu()
})

watch([workflows, selectedWorkflowId], () => {
  // A reload restores the editor's persisted selection. Hydrate the overview
  // without selecting (and persisting) an older entry during that refresh.
  syncRefreshedWorkflowDetails(selectedWorkflow.value)
}, { immediate: true, deep: true })
</script>

<template>
  <div class="page mf-page workflows-page" data-tone="violet">
    <input
      ref="importFileInputRef"
      class="wf-hidden-file-input"
      type="file"
      accept=".json,application/json"
      @change="handleImportWorkflow"
    >

    <MediaPageHeader
      :title="t('workflows.title')"
      :subtitle="t('workflows.subtitle')"
      :icon="GitNetworkOutline"
      eyebrow="WORKFLOWS"
      tone="violet"
    >
      <div class="workflows-page__header-actions" data-guide="workflow-actions">
        <n-button secondary @click="triggerImportWorkflow">
          <template #icon><n-icon :component="OpenOutline" /></template>
          {{ t('workflows.importWorkflow') }}
        </n-button>
        <n-button class="workflow-create-button" :type="themeIsDark ? 'default' : 'primary'" :secondary="themeIsDark" :disabled="isAnyEditorOpen" @click="openWorkflowCreateChooser">
          <span class="workflow-create-button__mark" aria-hidden="true" />
          {{ t('workflows.newWorkflow') }}
        </n-button>
      </div>
    </MediaPageHeader>

    <div class="console">
      <aside class="console__rail">
        <div class="wf-list-head">
          <n-input v-model:value="query" clearable :placeholder="t('workflows.searchPlaceholder')">
            <template #prefix><n-icon :component="SearchOutline" /></template>
          </n-input>
          <div class="wf-type-filter" role="tablist" :aria-label="t('workflows.typeFilterLabel')">
            <button
              v-for="option in workflowTypeFilterOptions"
              :key="option.value"
              type="button"
              role="tab"
              :aria-selected="typeFilter === option.value"
              :class="{ active: typeFilter === option.value }"
              @click="setWorkflowTypeFilter(option.value)"
            >
              <span>{{ option.label }}</span>
              <small>{{ option.count }}</small>
            </button>
          </div>
        </div>
        <div class="wf-list-scroll">
          <div v-if="filteredWorkflows.length" class="wf-list">
            <section v-for="group in workflowGroups" :key="group.kind" class="wf-list-group">
              <header class="wf-list-group__head">
                <span>{{ group.label }}</span>
                <small>{{ group.items.length }}</small>
              </header>
              <button
                v-for="item in group.items"
                :key="item.id"
                type="button"
                class="wf-row"
                :class="[
                  `wf-row--${workflowKind(item)}`,
                  { 'wf-row--active': item.id === selectedWorkflowId },
                ]"
                @click="selectWorkflowFromList(item)"
                @contextmenu.stop.prevent="openWorkflowContextMenu($event, item)"
              >
                <span class="wf-row__icon">
                  <n-icon :component="workflowKind(item) === 'simple' ? ListOutline : GitNetworkOutline" />
                </span>
                <span class="wf-row__main">
                  <strong>{{ item.name }}</strong>
                  <span class="wf-row__meta">
                    <small class="wf-row__type">{{ workflowTypeLabel(item) }}</small>
                    <small class="wf-row__summary">{{ workflowListMeta(item) }}</small>
                  </span>
                </span>
                <span
                  class="wf-row__dot"
                  :class="isWorkflowBlocked(item) ? 'wf-row__dot--warn' : 'wf-row__dot--ok'"
                  :title="isWorkflowBlocked(item) ? t('workflows.workflowValidationTitle') : t('workflows.statusReady')"
                />
              </button>
            </section>
          </div>
          <div v-else class="wf-empty">
            <n-icon :component="GitNetworkOutline" />
            <strong>{{ t('workflows.emptyTitle') }}</strong>
            <span>{{ t('workflows.emptyDesc') }}</span>
            <n-button class="workflow-create-button" :type="themeIsDark ? 'default' : 'primary'" :secondary="themeIsDark" size="small" @click="openWorkflowCreateChooser">
              <span class="workflow-create-button__mark" aria-hidden="true" />
              {{ t('workflows.newWorkflow') }}
            </n-button>
          </div>
        </div>
      </aside>

      <main class="console__stage" data-guide="workflow-overview">
        <div v-if="!selectedWorkflow" class="wf-overview-empty">
          <n-icon :component="GitNetworkOutline" />
          <strong>{{ t('workflows.overviewEmptyTitle') }}</strong>
          <span>{{ t('workflows.overviewEmptyDesc') }}</span>
          <n-button class="workflow-create-button" type="primary" @click="openWorkflowCreateChooser">
            <span class="workflow-create-button__mark" aria-hidden="true" />
            {{ t('workflows.newWorkflow') }}
          </n-button>
        </div>

        <template v-else>
          <div class="wf-overview">
            <div class="wf-overview__top">
              <div class="wf-overview__heading">
                <span class="wf-overview__icon" :class="`wf-overview__icon--${selectedWorkflowKind}`">
                  <n-icon :component="isSimpleWorkflow ? ListOutline : GitNetworkOutline" />
                </span>
                <h2 class="wf-overview__name" :title="selectedWorkflow.name">{{ selectedWorkflow.name }}</h2>
                <span class="wf-kind-badge" :class="`wf-kind-badge--${selectedWorkflowKind}`">
                  <n-icon :component="isSimpleWorkflow ? ListOutline : GitNetworkOutline" />
                  {{ selectedWorkflow ? workflowTypeLabel(selectedWorkflow) : '' }}
                </span>
                <span
                  class="wf-status"
                  :class="selectedReady ? 'wf-status--ok' : 'wf-status--warn'"
                >
                  <n-icon :component="selectedReady ? CheckmarkCircle : AlertCircleOutline" />
                  {{ selectedReady ? t('workflows.statusReady') : t('workflows.workflowValidationTitle') }}
                </span>
              </div>
              <p v-if="selectedDescriptionPreview" class="wf-overview__description" :title="selectedWorkflow.description">{{ selectedDescriptionPreview }}</p>
              <n-button class="wf-settings-button" secondary :disabled="isNodeEditorOpen" @click="settingsOpen = true">
                <template #icon><n-icon :component="SettingsOutline" /></template>
                {{ t('workflows.settingsTitle') }}
              </n-button>
            </div>

            <WorkflowOverviewPreview :key="selectedWorkflow.id" :definition="selectedWorkflow.definition" />

            <button v-if="missingModelCount" class="wf-model-warning" type="button" @click="settingsOpen = true">
              <n-icon :component="AlertCircleOutline" />
              <span>{{ t('workflows.modelsMissingCount', { count: missingModelCount }) }}</span>
            </button>

            <div v-if="selectedError" class="wf-validation">
              <n-icon :component="AlertCircleOutline" />
              <span>{{ selectedError }}</span>
            </div>

            <section v-if="isSimpleWorkflow && selectedSimpleAnalysis && !selectedSimpleAnalysis.editable" class="wf-simple-blockers">
              <strong>{{ t('workflows.advancedModeRequired') }}</strong>
              <ul>
                <li v-for="reason in selectedSimpleReasons" :key="reason">{{ simpleReasonLabel(reason) }}</li>
              </ul>
            </section>
          </div>

          <footer class="wf-actionbar">
            <div class="wf-actionbar__primary">
              <n-button v-if="selectedSimpleAnalysis?.editable || isComfyWorkflow" type="primary" size="large" @click="openWorkflowEditor(selectedWorkflow)">
                <template #icon><n-icon :component="selectedSimpleAnalysis?.editable ? ListOutline : GitNetworkOutline" /></template>
                {{ t('workflows.openEditorAction') }}
              </n-button>
            </div>
            <div class="wf-actionbar__more">
              <n-dropdown :key="selectedWorkflow.id" trigger="click" placement="top-end" to="body"
                :options="selectedWorkflowMenuOptions" @select="(key: string | number) => handleWorkflowMenuSelect(key, selectedWorkflow)">
                <n-button quaternary @click="closeWorkflowContextMenu">
                  <template #icon><n-icon :component="EllipsisHorizontalOutline" /></template>
                  {{ t('workflows.moreActions') }}
                </n-button>
              </n-dropdown>
            </div>
          </footer>

        </template>

        <div v-if="isNodeEditorOpen" class="wf-lock">
          <div class="wf-lock__card">
            <n-icon :component="GitNetworkOutline" />
            <strong>{{ t('workflows.nodeEditorOpenedTitle') }}</strong>
            <span>{{ t('workflows.nodeEditorOpenedHint') }}</span>
            <n-button secondary @click="reopenActiveEditor">{{ t('workflows.backToNodeEditor') }}</n-button>
          </div>
        </div>
      </main>
    </div>

    <n-dropdown
      placement="bottom-start"
      trigger="manual"
      :x="contextMenuX"
      :y="contextMenuY"
      :options="workflowMenuOptions"
      :show="contextMenuVisible"
      @clickoutside="closeWorkflowContextMenu"
      @select="(key: string | number) => handleWorkflowMenuSelect(key, contextWorkflow)"
    />

    <WorkflowCreateChooser
      v-model:show="createChooserOpen"
      @select="selectWorkflowCreateType"
    />

    <n-modal v-model:show="settingsOpen" preset="card" class="wf-settings-modal" :title="t('workflows.settingsTitle')"
      style="width: var(--modal-width-md); max-height: calc(100dvh - 112px)"
      :content-style="{ minHeight: 0, overflow: 'auto' }"
      :bordered="false" role="dialog" aria-modal="true" :aria-label="t('workflows.settingsTitle')">
        <div v-if="selectedWorkflow" class="wf-settings">
          <p class="wf-muted">{{ t('workflows.settingsAutosave') }}</p>
          <section class="wf-section">
            <label for="workflow-settings-name">{{ t('workflows.settingsName') }}</label>
            <n-input :input-props="{ id: 'workflow-settings-name' }" v-model:value="name" :disabled="isNodeEditorOpen || updatingWorkflowDefaults"
              :placeholder="t('workflows.untitled')" @blur="saveMeta" @keydown.enter="(event: KeyboardEvent) => (event.target as HTMLElement)?.blur()" />
            <label for="workflow-settings-description">{{ t('workflows.settingsDescription') }}</label>
            <n-input :input-props="{ id: 'workflow-settings-description' }" v-model:value="description" type="textarea" :autosize="{ minRows: 2, maxRows: 5 }"
              :disabled="isNodeEditorOpen || updatingWorkflowDefaults" :placeholder="t('workflows.descriptionPlaceholder')" @blur="saveMeta" />
          </section>
          <section v-if="selectedDraft && selectedSimpleAnalysis?.editable" class="wf-section">
            <h3>{{ t('workflows.runParams') }}</h3>
            <div class="wf-param-grid">
              <div class="wf-param">
                <label>{{ t('workflows.defaultDevice') }}</label>
                <n-select :value="selectedDraft.defaultDevice" :options="deviceOptions" :disabled="isNodeEditorOpen || updatingWorkflowDefaults"
                  :loading="updatingWorkflowDefaults" :aria-label="t('workflows.defaultDevice')" @update:value="updateSelectedDefaultDevice" />
              </div>
              <div class="wf-param">
                <label>{{ t('workflows.defaultFormat') }}</label>
                <n-select :value="selectedDraft.defaultFormat" :options="formatOptions" :disabled="isNodeEditorOpen || updatingWorkflowDefaults"
                  :loading="updatingWorkflowDefaults" :aria-label="t('workflows.defaultFormat')" @update:value="updateSelectedDefaultFormat" />
              </div>
              <div class="wf-param">
                <label>{{ t('workflows.defaultNormalize') }}</label>
                <n-select :value="normalizeMode(selectedDraft.defaultNormalize)" :options="normalizeOptions" :disabled="isNodeEditorOpen || updatingWorkflowDefaults"
                  :loading="updatingWorkflowDefaults" :aria-label="t('workflows.defaultNormalize')" @update:value="updateSelectedDefaultNormalize" />
              </div>
            </div>
          </section>
          <p v-else-if="isComfyWorkflow" class="wf-muted">{{ t('workflows.comfyReadOnlyHint') }}</p>
          <section class="wf-section">
            <h3>{{ t('workflows.modelsUsed') }}</h3>
            <div v-if="selectedModels.length" class="wf-chips">
              <span v-for="item in selectedModels" :key="item.name" class="wf-chip" :class="{ 'wf-chip--warn': !item.downloaded }" :title="item.name">
                <n-icon :component="CubeOutline" />
                <span class="wf-chip__name">{{ item.name }}</span>
                <small v-if="!item.downloaded">{{ t('workflows.modelNotDownloadedShort') }}</small>
              </span>
            </div>
            <p v-else class="wf-muted">{{ t('workflows.noModelsConfigured') }}</p>
          </section>
          <section v-if="isComfyWorkflow && selectedComfyOverview?.inputSlots?.length" class="wf-section">
            <h3>{{ t('workflows.inputSlots') }}</h3>
            <div class="wf-chips">
              <span v-for="slot in selectedComfyOverview.inputSlots" :key="slot" class="wf-chip" :title="slot">
                <n-icon :component="MusicalNotesOutline" />
                <span class="wf-chip__name">{{ slot }}</span>
              </span>
            </div>
          </section>
        </div>
    </n-modal>

  </div>
</template>

<style scoped src="./workflows/workflows-view.css"></style>
