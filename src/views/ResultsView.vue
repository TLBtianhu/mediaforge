<script setup lang="ts">
import { computed, h, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  CheckmarkOutline,
  ChevronDownOutline,
  FolderOpenOutline,
  FolderOutline,
  DocumentTextOutline,
  ColorWandOutline,
  SearchOutline,
  SwapVerticalOutline,
  TimeOutline,
  TrashOutline,
  RefreshOutline,
} from '@vicons/ionicons5'
import { useTaskStore, type SeparationJob, type SeparationTask } from '@/stores/task'
import { useEditorStore, type OrphanedEditorProject } from '@/stores/editor'
import { usePagedSelection } from '@/composables/usePagedSelection'
import { resultType, RESULT_TYPE_KEYS, type ResultType } from '@/utils/resultType'
import { makeResultListing, type ResultGroup } from './results/listing'
import { NCheckbox, useDialog, useMessage, type DialogReactive } from 'naive-ui'
import type { ResultRemovalResponse } from '@/features/tasks/journal'
import { useMediaJobsStore } from '@/stores/mediaJobs'
import JournalResults from './results/JournalResults.vue'
import { buildJournalResults, filterJournalResults } from './results/journalResults'

type ResultSort = 'time_desc' | 'time_asc' | 'name_asc' | 'name_desc'

const { t } = useI18n()
const task = useTaskStore()
const editor = useEditorStore()
const mediaJobs = useMediaJobsStore()
const message = useMessage()
const dialog = useDialog()

const search = ref('')
const typeFilter = ref<ResultType | 'all'>('all')
const typeOptions = computed(() => [
  { label: t('results.typeAll'), value: 'all' },
  ...Object.entries(RESULT_TYPE_KEYS).filter(([value]) => value !== 'unknown' || resultGroups.value.some(group => group.resultType === 'unknown'))
    .map(([value, key]) => ({ label: t(key), value })),
])
const sortBy = ref<ResultSort>('time_desc')
const expandedIds = ref<string[]>([])
const orphanedEditorProjects = ref<OrphanedEditorProject[]>([])
const orphanedProjectsLoading = ref(false)
const orphanedProjectsCleaning = ref(false)

const sortOptions = [
  { label: t('results.sortTimeDesc'), value: 'time_desc' },
  { label: t('results.sortTimeAsc'), value: 'time_asc' },
  { label: t('results.sortNameAsc'), value: 'name_asc' },
  { label: t('results.sortNameDesc'), value: 'name_desc' },
]

const { resultGroups, filteredResults, getFileName, getGroupTitle } = makeResultListing({
  computed, t, task, search, typeFilter, sortBy, resultType,
})
const journalResults = computed(() => buildJournalResults(mediaJobs.jobs,
  new Set(task.tasks.flatMap(item => [item.id, item.jobId].filter((id): id is string => Boolean(id))))))
const filteredJournalResults = computed(() => filterJournalResults(journalResults.value, search.value, sortBy.value))
async function refreshJournalResults() {
  try { await mediaJobs.refresh() } catch { /* The shared store exposes the refresh error. */ }
}
const pagedSelection = usePagedSelection(filteredResults, {
  initialPageSize: 24,
  pageSizeOptions: [12, 24, 48, 96],
})
const {
  selecting,
  selectedIds: selectedResultIds,
  selectedSet: selectedResultSet,
  page,
  pageSize,
  pageSizeOptions,
  allSelected: allResultsSelected,
  someSelected: someResultsSelected,
  toggleSelecting,
  toggleSelection: toggleResultSelection,
  toggleSelectPage: toggleSelectAllResults,
  ensureItemPage,
} = pagedSelection
const pagedResults = computed(() => pagedSelection.pagedItems.value)

const RESULT_DRAG_HOLD_MS = 260
type ResultDragSelection = {
  pointerId: number
  startId: string
  lastId: string
  targetSelected: boolean
  active: boolean
  visited: Set<string>
}

let resultDragSelection: ResultDragSelection | null = null
let resultDragTimer: ReturnType<typeof setTimeout> | null = null
let suppressNextResultClick = false

watch([search, sortBy, typeFilter, pageSize], () => {
  page.value = 1
})

watch(selecting, (value) => {
  if (!value) finishResultDragSelection()
})

function resultCardId(item: Pick<ResultGroup, 'id'>) {
  return `result-card-${item.id}`
}

function openResultDir(group: ResultGroup) {
  task.revealPath(group.primary.outputs[0]?.path || group.output)
}

async function openInEditor(item: SeparationTask) {
  try {
    const project = await editor.ensureProjectForTask(item, { loadIntoSession: false })
    await editor.openProjectWindow(project.id)
  } catch (error) {
    message.error(error instanceof Error ? error.message : t('editor.notFound'))
  }
}

const removingResults = ref(false)
const resultRemovalDialogOpen = ref(false)
const resultRemovalBlocked = computed(() => removingResults.value || resultRemovalDialogOpen.value)
let resultRemovalDialog: DialogReactive | null = null
let recordOnlyDialog: DialogReactive | null = null
let cancelRecordOnlyConfirmation: (() => void) | null = null
let resultsDisposed = false

function groupTaskIds(group: ResultGroup) {
  return group.items.map(item => item.id)
}

function groupTaskIdTitle(group: ResultGroup) {
  return groupTaskIds(group).join('\n')
}

function removalItems(ids: string[]) {
  try { return task.getRemovalTasks(ids) }
  catch (error) { message.warning(String(error instanceof Error ? error.message : error)); return null }
}

async function prepareResultRemoval(items: SeparationTask[], deleteFiles: boolean) {
  try {
    await task.checkRemoval(items.map(item => item.id), deleteFiles)
    return true
  } catch {
    message.warning(t('results.removeNotReady'))
    return false
  }
}

async function refreshOrphanedEditorProjects(force = false) {
  if (orphanedProjectsLoading.value || (!force && orphanedProjectsCleaning.value)) return
  if (!task.initialized) {
    orphanedEditorProjects.value = []
    return
  }
  orphanedProjectsLoading.value = true
  try {
    orphanedEditorProjects.value = await editor.listOrphanedProjects(task.resultTasks.map(item => item.id))
  } catch {
    orphanedEditorProjects.value = []
  } finally {
    orphanedProjectsLoading.value = false
  }
}

function handleCleanupOrphanedEditorProjects() {
  const count = orphanedEditorProjects.value.length
  if (!count || orphanedProjectsCleaning.value) return
  const previewProjects = orphanedEditorProjects.value.slice(0, 6)
  dialog.warning({
    title: t('results.cleanupEditorProjectsTitle'),
    content: () => h('div', { style: 'display:grid;gap:10px;' }, [
      h('span', t('results.cleanupEditorProjectsContent', { count })),
      h('ul', { style: 'margin:0;padding-left:20px;color:var(--on-surface-muted);' }, previewProjects.map(project => (
        h('li', { key: project.projectId }, project.name)
      ))),
      count > previewProjects.length
        ? h('span', { style: 'color:var(--on-surface-muted);font-size:12px;' }, t('results.cleanupEditorProjectsMore', {
            count: count - previewProjects.length,
          }))
        : null,
    ]),
    positiveText: t('results.cleanupEditorProjectsAction'),
    negativeText: t('common.cancel'),
    positiveButtonProps: { type: 'error' },
    onPositiveClick: async () => {
      orphanedProjectsCleaning.value = true
      try {
        const result = await editor.deleteOrphanedProjects(task.resultTasks.map(item => item.id))
        if (result.blockedProjectIds.length) {
          message.warning(t('results.removeEditorProjectsOpen', { count: result.blockedProjectIds.length }))
          return false
        }
        if (result.failedProjectIds.length) {
          await refreshOrphanedEditorProjects(true)
          message.error(t('results.removeEditorProjectsFailed', { count: result.failedProjectIds.length }))
          return false
        }
        const removedCount = result.deletedProjectIds.length
        await refreshOrphanedEditorProjects(true)
        message.success(t('results.cleanupEditorProjectsSuccess', { count: removedCount }))
      } catch {
        message.error(t('results.cleanupEditorProjectsFailed'))
        return false
      } finally {
        orphanedProjectsCleaning.value = false
      }
    },
  })
}

type RemoveMessageMode = 'single' | 'multiple'
type ResultRemovalMode = 'single' | 'selected' | 'all'

function showResultRemovalDialog(options: Parameters<typeof dialog.warning>[0]) {
  resultRemovalDialogOpen.value = true
  const entry = dialog.warning({
    ...options,
    onAfterLeave: () => {
      // The first dialog may finish leaving after the record-only fallback opens.
      if (resultRemovalDialog === entry) {
        resultRemovalDialog = null
        resultRemovalDialogOpen.value = false
      }
      options.onAfterLeave?.()
    },
  })
  resultRemovalDialog = entry
  return entry
}

function confirmRemoveListOnly(failedCount: number, mode: RemoveMessageMode) {
  return new Promise<boolean>((resolve) => {
    let settled = false
    const finish = (value: boolean) => {
      if (settled) return
      settled = true
      cancelRecordOnlyConfirmation = null
      resolve(value)
    }
    cancelRecordOnlyConfirmation = () => finish(false)
    const entry = dialog.warning({
      title: t('results.removeFilesConfirmTitle'),
      content: t(mode === 'single' ? 'results.removeFilesConfirmContentSingle' : 'results.removeFilesConfirmContentMultiple', {
        failed: failedCount,
      }),
      positiveText: t('results.removeListOnlyAction'),
      negativeText: t('results.keepResultAction'),
      positiveButtonProps: { type: 'error' },
      negativeButtonProps: { secondary: true },
      onPositiveClick: () => finish(true),
      onNegativeClick: () => finish(false),
      onClose: () => finish(false),
      onAfterLeave: () => { if (recordOnlyDialog === entry) recordOnlyDialog = null },
    })
    recordOnlyDialog = entry
  })
}

async function executeResultRemoval(items: SeparationTask[], deleteFiles: boolean, mode: ResultRemovalMode) {
  const ids = items.map(item => item.id)
  const messageMode = mode === 'single' ? 'single' : 'multiple'
  const retained = new Set<string>()
  async function remove(files: boolean): Promise<ResultRemovalResponse | null> {
    // The native catalog owns output references and validates the complete batch.
    if (resultsDisposed || !await prepareResultRemoval(items, files)) return null
    if (resultsDisposed) return null
    return task.removeResults(ids, { deleteFiles: files })
  }
  try {
    let result = await remove(deleteFiles)
    if (!result) return false
    result.retained.forEach(item => retained.add(item.path))
    if (result.failed.length) {
      if (resultsDisposed) return false
      // A failed native recycling operation keeps every selected job record.
      // Never turn an IPC exception into an implicit record-only deletion.
      if (!await confirmRemoveListOnly(result.failed.length, messageMode)) {
        if (!resultsDisposed) {
          message.warning(t(messageMode === 'single' ? 'results.removeFilesKeptSingle' : 'results.removeFilesKeptMultiple'))
          if (retained.size) message.warning(t('results.removeFilesRetained', { count: retained.size }))
        }
        return false
      }
      result = await remove(false)
      if (!result) return false
      result.retained.forEach(item => retained.add(item.path))
    }
    if (!ids.every(id => result.removedJobIds.includes(id))) throw new Error(t('results.removeRecordsFailed'))
    if (!resultsDisposed && retained.size) message.warning(t('results.removeFilesRetained', { count: retained.size }))
    return true
  } catch (error) {
    if (!resultsDisposed) message.error(error instanceof Error ? error.message : t('results.removeRecordsFailed'))
    return false
  }
}

function confirmResultRemoval(groups: ResultGroup[], mode: ResultRemovalMode) {
  if (resultRemovalBlocked.value || resultsDisposed || !groups.length) return
  const items = removalItems(groups.flatMap(groupTaskIds))
  if (!items?.length) return
  const deleteFiles = ref(false)
  const count = groups.length
  const labels = mode === 'single'
    ? { title: 'results.removeTitle', content: 'results.removeContent', positive: 'results.removeAction', success: 'results.removeSuccess' }
    : mode === 'selected'
      ? { title: 'results.removeSelectedTitle', content: 'results.removeSelectedContent', positive: 'results.removeSelectedPositive', success: 'results.removeSelectedSuccess' }
      : { title: 'results.clearTitle', content: 'results.clearContent', positive: 'results.clearPositive', success: 'results.clearSuccess' }
  let confirming = false
  showResultRemovalDialog({
    title: t(labels.title),
    content: () => h('div', { style: 'display:grid;gap:12px;' }, [
      h('span', t(labels.content, { count })),
      h('span', { class: 'result-remove__editor-hint' }, t('results.removeEditorProjectHint')),
      h('span', { class: 'result-remove__editor-hint' }, t('results.journal.removeProtectedHint')),
      h(NCheckbox, {
        checked: deleteFiles.value,
        disabled: removingResults.value,
        'onUpdate:checked': (value: boolean) => { if (!removingResults.value) deleteFiles.value = value },
      }, { default: () => t('results.removeDeleteFiles') }),
    ]),
    positiveText: t(labels.positive),
    negativeText: t('common.cancel'),
    positiveButtonProps: { type: 'error' },
    onPositiveClick: async () => {
      if (confirming || resultsDisposed) return false
      confirming = true
      removingResults.value = true
      if (resultRemovalDialog) {
        resultRemovalDialog.closable = false
        resultRemovalDialog.maskClosable = false
        resultRemovalDialog.closeOnEsc = false
        resultRemovalDialog.negativeButtonProps = { disabled: true }
      }
      try {
        if (!await executeResultRemoval(items, deleteFiles.value, mode)) return false
        if (mode !== 'single' && !resultsDisposed) {
          selectedResultIds.value = []
          selecting.value = false
        }
        if (!resultsDisposed) message.success(t(labels.success, { count }))
      } finally {
        confirming = false
        removingResults.value = false
        if (resultRemovalDialog && !resultsDisposed) {
          resultRemovalDialog.closable = true
          resultRemovalDialog.maskClosable = true
          resultRemovalDialog.closeOnEsc = true
          resultRemovalDialog.negativeButtonProps = {}
        }
      }
    },
  })
}

function handleRemoveResult(group: ResultGroup) {
  confirmResultRemoval([group], 'single')
}

function handleRemoveSelected() {
  const selected = new Set(selectedResultIds.value)
  confirmResultRemoval(resultGroups.value.filter(group => selected.has(group.id)), 'selected')
}

function handleClearResults() {
  confirmResultRemoval([...resultGroups.value], 'all')
}

function setResultSelected(id: string, selected: boolean) {
  if (selectedResultSet.value.has(id) !== selected) toggleResultSelection(id)
}

function applyResultDragSelection(id: string) {
  const state = resultDragSelection
  if (!state?.active || state.visited.has(id)) return
  state.visited.add(id)
  setResultSelected(id, state.targetSelected)
}

function activateResultDragSelection() {
  const state = resultDragSelection
  if (!state || state.active) return
  state.active = true
  if (!selecting.value) selecting.value = true
  document.documentElement.classList.add('results-drag-selecting')
  applyResultDragSelection(state.startId)
  applyResultDragSelection(state.lastId)
}

function handleResultIconPointerDown(event: PointerEvent, id: string) {
  if (event.pointerType !== 'mouse' || event.button !== 0) return
  event.preventDefault()
  finishResultDragSelection()
  window.getSelection()?.removeAllRanges()
  resultDragSelection = {
    pointerId: event.pointerId,
    startId: id,
    lastId: id,
    targetSelected: selecting.value ? !selectedResultSet.value.has(id) : true,
    active: false,
    visited: new Set(),
  }
  resultDragTimer = setTimeout(activateResultDragSelection, RESULT_DRAG_HOLD_MS)
}

function handleResultIconPointerEnter(event: PointerEvent, id: string) {
  if (!resultDragSelection) return
  if ((event.buttons & 1) === 0) {
    finishResultDragSelection()
    return
  }
  resultDragSelection.lastId = id
  applyResultDragSelection(id)
}

function finishResultDragSelection(event?: PointerEvent) {
  const state = resultDragSelection
  if (!state || (event && event.pointerId !== state.pointerId)) return
  if (resultDragTimer) {
    clearTimeout(resultDragTimer)
    resultDragTimer = null
  }
  if (state.active) {
    suppressNextResultClick = true
    window.setTimeout(() => { suppressNextResultClick = false }, 0)
  }
  resultDragSelection = null
  document.documentElement.classList.remove('results-drag-selecting')
}

function hasSelectedResultText() {
  const selection = window.getSelection()
  return Boolean(selection && !selection.isCollapsed && selection.toString())
}

function handleResultRowClick(id: string) {
  if (suppressNextResultClick) {
    suppressNextResultClick = false
    return
  }
  if (hasSelectedResultText()) return
  if (selecting.value) toggleResultSelection(id)
  else toggleExpanded(id)
}

function handleResultsKeydown(event: KeyboardEvent) {
  if (event.defaultPrevented || event.key !== 'Escape' || !selecting.value) return
  if (document.querySelector('[role="dialog"], .n-modal-mask')) return
  event.preventDefault()
  finishResultDragSelection()
  toggleSelecting()
}

function handleResultsWindowBlur() {
  finishResultDragSelection()
}

function isExpanded(id: string) {
  return expandedIds.value.includes(id)
}

function toggleExpanded(id: string) {
  expandedIds.value = isExpanded(id)
    ? expandedIds.value.filter((item) => item !== id)
    : [...expandedIds.value, id]
}

function ensureExpanded(id: string) {
  if (!isExpanded(id)) {
    expandedIds.value = [...expandedIds.value, id]
  }
}

function shortenPath(value: string) {
  if (value.length <= 92) return value
  return `${value.slice(0, 34)}…${value.slice(-44)}`
}

async function copyNodeValue(value: string) {
  try {
    await navigator.clipboard.writeText(value)
    message.success(t('common.copied'))
  } catch {
    message.error(t('common.copyFailed'))
  }
}

function outputFileName(value: string) {
  return getFileName(value) || shortenPath(value)
}

function scrollToFocusedResult(id: string | null) {
  if (!id) return
  const group = resultGroups.value.find(group => group.id === id || group.items.some(item => item.id === id))
  const groupId = group?.id || id
  ensureExpanded(groupId)
  ensureItemPage(groupId)
  nextTick(() => {
    const target = document.getElementById(resultCardId({ id: groupId }))
    target?.scrollIntoView({ block: 'center', behavior: 'smooth' })
  })
}

watch(() => task.focusedResultTaskId, (value) => {
  if (value) {
    scrollToFocusedResult(value)
    task.focusResultTask(null)
  }
})

onMounted(() => {
  void mediaJobs.initialize().catch(() => {})
  window.addEventListener('keydown', handleResultsKeydown)
  window.addEventListener('pointerup', finishResultDragSelection)
  window.addEventListener('pointercancel', finishResultDragSelection)
  window.addEventListener('blur', handleResultsWindowBlur)
  void refreshOrphanedEditorProjects()
  if (task.focusedResultTaskId) {
    scrollToFocusedResult(task.focusedResultTaskId)
    task.focusResultTask(null)
  }
})

onBeforeUnmount(() => {
  resultsDisposed = true
  cancelRecordOnlyConfirmation?.()
  recordOnlyDialog?.destroy()
  resultRemovalDialog?.destroy()
  window.removeEventListener('keydown', handleResultsKeydown)
  window.removeEventListener('pointerup', finishResultDragSelection)
  window.removeEventListener('pointercancel', finishResultDragSelection)
  window.removeEventListener('blur', handleResultsWindowBlur)
  finishResultDragSelection()
})

function formatTime(value: number) {
  return new Date(value).toLocaleString()
}

function taskDurationMs(item: SeparationTask) {
  if (typeof item.durationMs === 'number' && Number.isFinite(item.durationMs) && item.durationMs >= 0) return item.durationMs
  if (item.startedAt && item.finishedAt) return Math.max(0, item.finishedAt - item.startedAt)
  return Math.max(0, item.updatedAt - item.createdAt)
}

function formatDurationMs(value: number | undefined) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return t('results.durationUnknown')
  return t('results.durationSeconds', { seconds: Math.max(0, Math.round(value / 1000)) })
}
</script>

<template>
  <div class="page mf-page results-page" data-tone="violet">
    <MediaPageHeader :title="t('results.title')" :subtitle="t('results.subtitle')" :icon="FolderOpenOutline" eyebrow="RESULTS" tone="violet">
      <div
        class="results-page__header-actions"
        :class="{ 'results-page__header-actions--selecting': selecting }"
      >
        <template v-if="selecting">
          <n-checkbox
            :checked="allResultsSelected"
            :indeterminate="someResultsSelected"
            @update:checked="toggleSelectAllResults"
          >
            {{ t('results.selectPage') }}
          </n-checkbox>
          <span class="results-page__selection-count" aria-live="polite">
            {{ t('results.selectedCount', { count: selectedResultIds.length }) }}
          </span>
          <n-button
            secondary
            type="error"
            :disabled="!selectedResultIds.length || resultRemovalBlocked"
            @click="handleRemoveSelected"
          >
            <template #icon><n-icon :component="TrashOutline" /></template>
            {{ t('results.removeSelectedCount', { count: selectedResultIds.length }) }}
          </n-button>
          <n-button secondary @click="toggleSelecting">
            {{ t('results.batchExit') }}
          </n-button>
        </template>
        <template v-else>
          <n-button quaternary :loading="mediaJobs.loading" :aria-label="t('results.journal.refresh')" @click="refreshJournalResults">
            <template #icon><n-icon :component="RefreshOutline" /></template>
          </n-button>
          <n-button
            v-if="orphanedEditorProjects.length"
            secondary
            type="warning"
            :loading="orphanedProjectsCleaning"
            @click="handleCleanupOrphanedEditorProjects"
          >
            <template #icon><n-icon :component="TrashOutline" /></template>
            {{ t('results.cleanupEditorProjectsAction') }}
            <span class="results-page__cleanup-count">{{ orphanedEditorProjects.length }}</span>
          </n-button>
          <n-button v-if="task.resultTasks.length" secondary @click="toggleSelecting">
            {{ t('results.batchSelect') }}
          </n-button>
          <n-button
            v-if="task.resultTasks.length"
            secondary
            type="error"
            :disabled="resultRemovalBlocked"
            @click="handleClearResults"
          >
            <template #icon><n-icon :component="TrashOutline" /></template>
            {{ t('results.clearAction') }}
          </n-button>
        </template>
      </div>
    </MediaPageHeader>

    <div v-if="task.resultTasks.length || journalResults.length" class="results-toolbar">
      <n-input
        v-model:value="search"
        class="results-toolbar__search"
        clearable
        :placeholder="t('results.searchPlaceholder')"
      >
        <template #prefix><n-icon :component="SearchOutline" /></template>
      </n-input>

      <n-select v-model:value="typeFilter" class="results-toolbar__type" size="small" :options="typeOptions" :disabled="!task.resultTasks.length" :aria-label="t('results.typeFilter')" />
      <n-select
        v-model:value="sortBy"
        class="results-toolbar__sort"
        size="small"
        :options="sortOptions"
      >
        <template #arrow><n-icon :component="SwapVerticalOutline" /></template>
      </n-select>

      <span class="results-toolbar__count">{{ filteredResults.length + filteredJournalResults.length }} / {{ resultGroups.length + journalResults.length }}</span>
    </div>

    <JournalResults
      v-if="journalResults.length || mediaJobs.loading || mediaJobs.error"
      :results="filteredJournalResults" :total="journalResults.length" :loading="mediaJobs.loading" :error="mediaJobs.error || ''"
      @refresh="refreshJournalResults"
    />

    <h2 v-if="task.resultTasks.length" class="results-section-title">{{ t('results.audioSection') }}</h2>

    <div v-if="!task.resultTasks.length && !journalResults.length && !mediaJobs.loading && !mediaJobs.error" class="results-empty" data-guide="results-empty">
      <n-icon :component="FolderOutline" size="46" />
      <strong>{{ t('results.empty') }}</strong>
      <span>{{ t('results.emptyHint') }}</span>
    </div>

    <div v-else-if="task.resultTasks.length && !filteredResults.length" class="results-empty" data-guide="results-empty">
      <n-icon :component="SearchOutline" size="42" />
      <strong>{{ t('results.noMatchTitle') }}</strong>
      <span>{{ t('results.noMatchHint') }}</span>
    </div>

    <div v-else-if="filteredResults.length" class="results-list">
      <section
        v-for="item in pagedResults"
        :id="resultCardId(item)"
        :key="item.id"
        class="result-row" data-guide="result-item"
        :class="{ 'result-row--selected': selectedResultSet.has(item.id) }"
      >
        <button
          class="result-row__main"
          :class="{ 'result-row__main--selecting': selecting }"
          :aria-pressed="selecting ? selectedResultSet.has(item.id) : undefined"
          type="button"
          @click="handleResultRowClick(item.id)"
        >
          <span
            class="result-row__icon"
            @pointerdown="handleResultIconPointerDown($event, item.id)"
            @pointerenter="handleResultIconPointerEnter($event, item.id)"
          >
            <span
              v-if="selecting"
              class="result-row__selection-marker"
              :class="{ 'result-row__selection-marker--checked': selectedResultSet.has(item.id) }"
              aria-hidden="true"
            >
              <n-icon v-if="selectedResultSet.has(item.id)" :component="CheckmarkOutline" size="14" />
            </span>
            <n-icon v-else :component="DocumentTextOutline" size="18" />
          </span>

          <span class="result-row__body">
            <strong data-result-text>{{ getGroupTitle(item) }}</strong>
            <span class="result-row__meta">
              <span data-result-text class="result-row__type">{{ t(RESULT_TYPE_KEYS[item.resultType]) }}</span>
              <span data-result-text class="result-row__model" :title="groupTaskIdTitle(item)">{{ item.model }}</span>
              <span data-result-text>{{ item.inputCount }} {{ t('results.inputUnit') }}</span>
              <span data-result-text>{{ item.outputCount }} {{ t('results.stemUnit') }}</span>
              <span data-result-text>{{ formatDurationMs(item.durationMs) }}</span>
              <span data-result-text class="result-row__time"><n-icon :component="TimeOutline" /> {{ formatTime(item.updatedAt) }}</span>
            </span>
            <span data-result-text class="result-row__path">{{ shortenPath(item.output) }}</span>
          </span>

          <span
            v-if="!selecting"
            class="result-row__toggle"
            :class="{ 'result-row__toggle--open': isExpanded(item.id) }"
          >
            <n-icon :component="ChevronDownOutline" />
          </span>
        </button>

        <div v-if="!selecting" class="result-row__actions" data-guide="result-actions">
          <n-button v-if="item.inputCount === 1" size="small" type="primary" :disabled="removingResults" @click.stop="openInEditor(item.primary)">
            <template #icon><n-icon :component="ColorWandOutline" /></template>
            {{ t('results.openInEditor') }}
          </n-button>
          <n-button size="small" type="primary" secondary @click.stop="openResultDir(item)">
            <template #icon><n-icon :component="FolderOpenOutline" /></template>
            {{ t('results.openDirectory') }}
          </n-button>
          <n-button size="small" quaternary type="error" :disabled="resultRemovalBlocked" @click.stop="handleRemoveResult(item)">
            <template #icon><n-icon :component="TrashOutline" /></template>
            {{ t('results.removeAction') }}
          </n-button>
        </div>

        <n-collapse-transition class="result-row__collapse" :show="isExpanded(item.id)">
          <div class="result-row__details">
            <section
              v-for="result in item.items"
              :key="result.id"
              :class="['result-detail-card', { 'result-detail-card--compact': item.items.length === 1 }]"
            >
              <div v-if="item.items.length > 1" class="result-detail-card__head">
                <div>
                  <strong>{{ getFileName(result.input) }}</strong>
                  <span>{{ result.outputs.length }} {{ t('results.stemUnit') }} · {{ formatDurationMs(taskDurationMs(result)) }}</span>
                </div>
                <div class="result-detail-card__actions">
                  <n-button size="tiny" secondary :disabled="removingResults" @click.stop="openInEditor(result)">
                    <template #icon><n-icon :component="ColorWandOutline" /></template>
                    {{ t('results.openInEditor') }}
                  </n-button>
                  <n-button size="tiny" tertiary @click.stop="task.revealPath(result.outputs[0]?.path || result.output)">
                    <template #icon><n-icon :component="FolderOpenOutline" /></template>
                    {{ t('results.openDirectory') }}
                  </n-button>
                </div>
              </div>
              <div class="result-detail-card__stems">
                <div v-for="output in result.outputs" :key="output.path" class="stem-line" :title="output.path">
                  <span class="stem-line__stem">{{ output.stem }}</span>
                  <span class="stem-line__path">{{ outputFileName(output.path) }}</span>
                </div>
              </div>
              <div v-if="result.nodeValues?.length" class="result-detail-card__node-values">
                <div class="node-value-line__header">{{ t('results.nodeValues') }}</div>
                <div
                  v-for="(entry, index) in result.nodeValues"
                  :key="`${entry.nodeId}-${entry.slot}-${index}`"
                  class="node-value-line"
                >
                  <span class="node-value-line__label">{{ entry.nodeType }}</span>
                  <span class="node-value-line__value">{{ entry.value }}</span>
                  <n-button size="tiny" tertiary @click.stop="copyNodeValue(entry.value)">
                    {{ t('common.copy') }}
                  </n-button>
                </div>
              </div>
            </section>
          </div>
        </n-collapse-transition>
      </section>
    </div>

    <div v-if="filteredResults.length" class="results-pagination">
      <n-pagination
        v-model:page="page"
        v-model:page-size="pageSize"
        :item-count="filteredResults.length"
        :page-sizes="pageSizeOptions"
        show-size-picker
      />
    </div>
  </div>
</template>

<style scoped src="./results/results-view.css"></style>
