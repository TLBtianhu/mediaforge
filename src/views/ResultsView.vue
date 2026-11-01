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
} from '@vicons/ionicons5'
import { useTaskStore, type SeparationJob, type SeparationTask } from '@/stores/task'
import { useEditorStore, type OrphanedEditorProject } from '@/stores/editor'
import { usePagedSelection } from '@/composables/usePagedSelection'
import { resultType, RESULT_TYPE_KEYS, type ResultType } from '@/utils/resultType'
import { makeResultListing, type ResultGroup } from './results/listing'
import { NCheckbox, useDialog, useMessage } from 'naive-ui'

type ResultSort = 'time_desc' | 'time_asc' | 'name_asc' | 'name_desc'

const { t } = useI18n()
const task = useTaskStore()
const editor = useEditorStore()
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

// 删除结果时回收当前结果对应的输出文件；分层输出的结果目录仅在已空时清理。
function trashEmptyDirs(item: SeparationTask) {
  return task.resultTrashEmptyDirs(item)
}

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

async function removeResultRecords(items: SeparationTask[]) {
  if (!await cleanupLinkedEditorProjects(items)) return false
  try {
    await task.removeResults(items.map(item => item.id))
    return true
  } catch (error) {
    message.error(error instanceof Error ? error.message : t('results.removeRecordsFailed'))
    return false
  }
}

async function cleanupLinkedEditorProjects(items: SeparationTask[]) {
  try {
    const result = await editor.deleteProjectsForTasks(items.map(item => item.id))
    if (result.blockedProjectIds.length) {
      message.warning(t('results.removeEditorProjectsOpen', { count: result.blockedProjectIds.length }))
      return false
    }
    if (result.failedProjectIds.length) {
      message.error(t('results.removeEditorProjectsFailed', { count: result.failedProjectIds.length }))
      return false
    }
    return true
  } catch {
    message.error(t('results.removeEditorProjectsFailed', { count: items.length }))
    return false
  }
}

async function ensureLinkedEditorProjectsClosed(items: SeparationTask[]) {
  try {
    const openProjectIds = await editor.listOpenProjectsForTasks(items.map(item => item.id))
    if (!openProjectIds.length) return true
    message.warning(t('results.removeEditorProjectsOpen', { count: openProjectIds.length }))
  } catch {
    message.error(t('results.removeEditorProjectsFailed', { count: items.length }))
  }
  return false
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

function trashTargetsForItems(items: SeparationTask[]) {
  return task.resultTrashTargetsForTasks(items)
}

function trashEmptyDirsForItems(items: SeparationTask[]) {
  const seen = new Set<string>()
  return items.flatMap((item) => trashEmptyDirs(item)).filter((path) => {
    if (seen.has(path)) return false
    seen.add(path)
    return true
  })
}

type RemoveMessageMode = 'single' | 'multiple'

function confirmRemoveListOnly(failedCount: number, totalCount: number, mode: RemoveMessageMode) {
  return new Promise<boolean>((resolve) => {
    let settled = false
    const finish = (value: boolean) => {
      if (settled) return
      settled = true
      resolve(value)
    }
    dialog.warning({
      title: t('results.removeFilesConfirmTitle'),
      content: t(mode === 'single' ? 'results.removeFilesConfirmContentSingle' : 'results.removeFilesConfirmContentMultiple', {
        failed: failedCount,
        total: totalCount,
      }),
      positiveText: t('results.removeListOnlyAction'),
      negativeText: t('results.keepResultAction'),
      positiveButtonProps: { type: 'error' },
      negativeButtonProps: { secondary: true },
      onPositiveClick: () => finish(true),
      onNegativeClick: () => finish(false),
      onClose: () => finish(false),
    })
  })
}

function handleRemoveResult(group: ResultGroup) {
  const ids = groupTaskIds(group)
  const items = removalItems(ids)
  if (!items?.length) return
  const deleteFiles = ref(false)
  dialog.warning({
    title: t('results.removeTitle'),
    content: () => h('div', { style: 'display:grid;gap:12px;' }, [
      h('span', t('results.removeContent')),
      h('span', { class: 'result-remove__editor-hint' }, t('results.removeEditorProjectHint')),
      h(NCheckbox, {
        checked: deleteFiles.value,
        'onUpdate:checked': (value: boolean) => { deleteFiles.value = value },
      }, { default: () => t('results.removeDeleteFiles') }),
    ]),
    positiveText: t('results.removeAction'),
    negativeText: t('common.cancel'),
    positiveButtonProps: { type: 'error' },
    onPositiveClick: async () => {
      const finishRemove = async () => {
        return removeResultRecords(items)
      }
      if (!deleteFiles.value) {
        if (!await finishRemove()) return false
        message.success(t('results.removeSuccess'))
        return
      }
      if (!await ensureLinkedEditorProjectsClosed(items)) return false

      const { paths: targets, retained } = trashTargetsForItems(items)
      if (!targets.length) {
        if (!await finishRemove()) return false
        if (retained) message.success(t('results.removeFilesRetained', { count: retained }))
        else message.warning(t('results.removeNoFilesSingle'))
        return
      }

      try {
        const result = await task.trashResultPaths(targets, trashEmptyDirsForItems(items))
        if (!result.failed.length) {
          if (!await finishRemove()) return false
          if (retained) message.success(t('results.removeFilesRetained', { count: retained }))
          else message.success(t('results.removeFilesSuccessSingle'))
          return
        }

        const removeListOnly = await confirmRemoveListOnly(result.failed.length, targets.length, 'single')
        if (!removeListOnly) {
          message.warning(t('results.removeFilesKeptSingle'))
          return
        }
        if (!await finishRemove()) return false
        if (result.failed.length === targets.length) {
          message.warning(t('results.removeFilesAllFailedListOnly'))
        } else {
          message.warning(t('results.removeFilesPartialListOnly', { count: result.failed.length + retained }))
        }
      } catch (error) {
        const removeListOnly = await confirmRemoveListOnly(targets.length, targets.length, 'single')
        if (!removeListOnly) {
          message.error(error instanceof Error ? error.message : t('results.removeFilesFailed'))
          return
        }
        if (!await finishRemove()) return false
        message.warning(t('results.removeFilesAllFailedListOnly'))
      }
    },
  })
}

function handleRemoveSelected() {
  const ids = [...selectedResultIds.value]
  if (!ids.length) return
  const groups = resultGroups.value.filter((group) => ids.includes(group.id))
  const taskIds = groups.flatMap(groupTaskIds)
  const items = removalItems(taskIds)
  if (!items?.length) return
  const deleteFiles = ref(false)
  dialog.warning({
    title: t('results.removeSelectedTitle'),
    content: () => h('div', { style: 'display:grid;gap:12px;' }, [
      h('span', t('results.removeSelectedContent', { count: ids.length })),
      h('span', { class: 'result-remove__editor-hint' }, t('results.removeEditorProjectHint')),
      h(NCheckbox, {
        checked: deleteFiles.value,
        'onUpdate:checked': (value: boolean) => { deleteFiles.value = value },
      }, { default: () => t('results.removeDeleteFiles') }),
    ]),
    positiveText: t('results.removeSelectedPositive'),
    negativeText: t('common.cancel'),
    positiveButtonProps: { type: 'error' },
    onPositiveClick: async () => {
      const finishRemove = async (silent = false) => {
        if (!await removeResultRecords(items)) return false
        if (!silent) {
          message.success(t('results.removeSelectedSuccess', { count: ids.length }))
        }
        selectedResultIds.value = []
        selecting.value = false
        return true
      }

      if (!deleteFiles.value) {
        return await finishRemove() ? undefined : false
      }
      if (!await ensureLinkedEditorProjectsClosed(items)) return false

      const { paths: targets, retained } = trashTargetsForItems(items)
      if (!targets.length) {
        if (!await finishRemove(true)) return false
        if (retained) message.success(t('results.removeFilesRetained', { count: retained }))
        else message.warning(t('results.removeNoFilesMultiple'))
        return
      }

      try {
        const result = await task.trashResultPaths(targets, trashEmptyDirsForItems(items))
        if (!result.failed.length) {
          if (!await finishRemove(true)) return false
          if (retained) message.success(t('results.removeFilesRetained', { count: retained }))
          else message.success(t('results.removeFilesSuccessMultiple'))
          return
        }

        const removeListOnly = await confirmRemoveListOnly(result.failed.length, targets.length, 'multiple')
        if (!removeListOnly) {
          message.warning(t('results.removeFilesKeptMultiple'))
          return
        }
        if (!await finishRemove(true)) return false
        if (result.failed.length === targets.length) {
          message.warning(t('results.removeFilesAllFailedListOnly'))
        } else {
          message.warning(t('results.removeFilesPartialListOnly', { count: result.failed.length + retained }))
        }
      } catch (error) {
        const removeListOnly = await confirmRemoveListOnly(targets.length, targets.length, 'multiple')
        if (!removeListOnly) {
          message.error(error instanceof Error ? error.message : t('results.removeFilesFailed'))
          return
        }
        if (!await finishRemove(true)) return false
        message.warning(t('results.removeFilesAllFailedListOnly'))
      }
    },
  })
}

function handleClearResults() {
  const groups = [...resultGroups.value]
  if (!groups.length) return
  const items = removalItems(groups.flatMap(groupTaskIds))
  if (!items?.length) return
  const deleteFiles = ref(false)
  dialog.warning({
    title: t('results.clearTitle'),
    content: () => h('div', { style: 'display:grid;gap:12px;' }, [
      h('span', t('results.clearContent', { count: groups.length })),
      h('span', { class: 'result-remove__editor-hint' }, t('results.removeEditorProjectHint')),
      h(NCheckbox, {
        checked: deleteFiles.value,
        'onUpdate:checked': (value: boolean) => { deleteFiles.value = value },
      }, { default: () => t('results.removeDeleteFiles') }),
    ]),
    positiveText: t('results.clearPositive'),
    negativeText: t('common.cancel'),
    positiveButtonProps: { type: 'error' },
    onPositiveClick: async () => {
      const finishClear = async (silent = false) => {
        if (!await removeResultRecords(items)) return false
        if (!silent) {
          message.success(t('results.clearSuccess', { count: groups.length }))
        }
        selectedResultIds.value = []
        selecting.value = false
        return true
      }

      if (!deleteFiles.value) {
        return await finishClear() ? undefined : false
      }
      if (!await ensureLinkedEditorProjectsClosed(items)) return false

      const { paths: targets, retained } = trashTargetsForItems(items)
      if (!targets.length) {
        if (!await finishClear(true)) return false
        if (retained) message.success(t('results.removeFilesRetained', { count: retained }))
        else message.warning(t('results.removeNoFilesMultiple'))
        return
      }

      try {
        const result = await task.trashResultPaths(targets, trashEmptyDirsForItems(items))
        if (!result.failed.length) {
          if (!await finishClear(true)) return false
          if (retained) message.success(t('results.removeFilesRetained', { count: retained }))
          else message.success(t('results.removeFilesSuccessMultiple'))
          return
        }

        const removeListOnly = await confirmRemoveListOnly(result.failed.length, targets.length, 'multiple')
        if (!removeListOnly) {
          message.warning(t('results.removeFilesKeptMultiple'))
          return
        }
        if (!await finishClear(true)) return false
        if (result.failed.length === targets.length) {
          message.warning(t('results.removeFilesAllFailedListOnly'))
        } else {
          message.warning(t('results.removeFilesPartialListOnly', { count: result.failed.length + retained }))
        }
      } catch (error) {
        const removeListOnly = await confirmRemoveListOnly(targets.length, targets.length, 'multiple')
        if (!removeListOnly) {
          message.error(error instanceof Error ? error.message : t('results.removeFilesFailed'))
          return
        }
        if (!await finishClear(true)) return false
        message.warning(t('results.removeFilesAllFailedListOnly'))
      }
    },
  })
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
            :disabled="!selectedResultIds.length"
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
            @click="handleClearResults"
          >
            <template #icon><n-icon :component="TrashOutline" /></template>
            {{ t('results.clearAction') }}
          </n-button>
        </template>
      </div>
    </MediaPageHeader>

    <div v-if="task.resultTasks.length" class="results-toolbar">
      <n-input
        v-model:value="search"
        class="results-toolbar__search"
        clearable
        :placeholder="t('results.searchPlaceholder')"
      >
        <template #prefix><n-icon :component="SearchOutline" /></template>
      </n-input>

      <n-select v-model:value="typeFilter" class="results-toolbar__type" size="small" :options="typeOptions" :aria-label="t('results.typeFilter')" />
      <n-select
        v-model:value="sortBy"
        class="results-toolbar__sort"
        size="small"
        :options="sortOptions"
      >
        <template #arrow><n-icon :component="SwapVerticalOutline" /></template>
      </n-select>

      <span class="results-toolbar__count">{{ filteredResults.length }} / {{ resultGroups.length }}</span>
    </div>

    <div v-if="!task.resultTasks.length" class="results-empty" data-guide="results-empty">
      <n-icon :component="FolderOutline" size="46" />
      <strong>{{ t('results.empty') }}</strong>
      <span>{{ t('results.emptyHint') }}</span>
    </div>

    <div v-else-if="!filteredResults.length" class="results-empty" data-guide="results-empty">
      <n-icon :component="SearchOutline" size="42" />
      <strong>{{ t('results.noMatchTitle') }}</strong>
      <span>{{ t('results.noMatchHint') }}</span>
    </div>

    <div v-else class="results-list">
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
          <n-button v-if="item.inputCount === 1" size="small" type="primary" @click.stop="openInEditor(item.primary)">
            <template #icon><n-icon :component="ColorWandOutline" /></template>
            {{ t('results.openInEditor') }}
          </n-button>
          <n-button size="small" type="primary" secondary @click.stop="openResultDir(item)">
            <template #icon><n-icon :component="FolderOpenOutline" /></template>
            {{ t('results.openDirectory') }}
          </n-button>
          <n-button size="small" quaternary type="error" @click.stop="handleRemoveResult(item)">
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
                  <n-button size="tiny" secondary @click.stop="openInEditor(result)">
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
