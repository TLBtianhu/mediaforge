<script setup lang="ts">
import { computed, h, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { convertFileSrc } from '@tauri-apps/api/core'
import { useI18n } from 'vue-i18n'
import { NCheckbox, useDialog, useMessage, type DialogReactive } from 'naive-ui'
import { FolderOpenOutline, RefreshOutline, TrashOutline } from '@vicons/ionicons5'
import type { JournalResult } from './journalResults'
import { formatBytes } from '@/utils/format'
import { useMediaJobsStore } from '@/stores/mediaJobs'
import { removeResultJobs, type ResultRemovalResponse } from '@/features/tasks/journal'
import { revealPath } from '@/api/native'

const props = defineProps<{ results: JournalResult[]; total: number; loading?: boolean; error?: string }>()
defineEmits<{ refresh: [] }>()
const { t } = useI18n()
const message = useMessage()
const dialog = useDialog()
const mediaJobs = useMediaJobsStore()
const previews = ref<Record<string, string>>({})
const selecting = ref(false)
const selectedIds = ref<string[]>([])
const removing = ref(false)
const confirmationOpen = ref(false)
const blocked = computed(() => Boolean(props.loading || props.error || removing.value || confirmationOpen.value))
const selected = computed(() => new Set(selectedIds.value))
const page = ref(1)
const pageSize = 12
const pageResults = computed(() => props.results.slice((page.value - 1) * pageSize, page.value * pageSize))
watch(() => props.results.map(result => result.id).join('|'), () => { page.value = 1; previews.value = {} })
watch(() => props.results.map(result => result.id), ids => {
  const available = new Set(ids)
  selectedIds.value = selectedIds.value.filter(id => available.has(id))
})
const pageSelected = computed(() => pageResults.value.length > 0 && pageResults.value.every(result => selected.value.has(result.id)))
let disposed = false
let activeDialog: DialogReactive | undefined
let fallbackDialog: DialogReactive | undefined
let cancelFallback: (() => void) | undefined
onBeforeUnmount(() => {
  disposed = true
  const pendingDialog = fallbackDialog
  cancelFallback?.()
  pendingDialog?.destroy()
  activeDialog?.destroy()
})
const operationKeys: Record<string, string> = {
  media_image: 'results.journal.image', media_video: 'results.journal.video', media_workflow: 'results.journal.workflow',
  media_convert: 'results.journal.convert', infer: 'results.journal.separate', infer_workflow: 'results.journal.audioWorkflow',
  export_editor_mix: 'results.journal.editorExport',
}
function operationLabel(operation: string) { return operationKeys[operation] ? t(operationKeys[operation]) : operation }
function statusLabel(status: string) { return t(`results.journal.status.${status === 'done' ? 'completed' : status}`) }
function time(value: number) { return value ? new Date(value).toLocaleString() : '—' }
async function reveal(path: string) {
  try { await revealPath(path) }
  catch (error) { message.error(error instanceof Error ? error.message : t('results.journal.openFailed')) }
}
function togglePreview(id: string, path: string) { if (!removing.value) previews.value[id] = previews.value[id] === path ? '' : path }
function toggleSelecting() {
  if (blocked.value) return
  selecting.value = !selecting.value
  if (!selecting.value) selectedIds.value = []
}
function toggleSelection(id: string, value: boolean) {
  if (blocked.value || !props.results.some(result => result.id === id)) return
  selectedIds.value = value ? [...new Set([...selectedIds.value, id])] : selectedIds.value.filter(selectedId => selectedId !== id)
}
function selectPage() {
  if (blocked.value) return
  const pageIds = new Set(pageResults.value.map(result => result.id))
  selectedIds.value = pageSelected.value ? selectedIds.value.filter(id => !pageIds.has(id)) : [...new Set([...selectedIds.value, ...pageIds])]
}
function setDialogBusy(value: boolean) {
  if (!activeDialog || disposed) return
  activeDialog.loading = value
  activeDialog.closable = !value
  activeDialog.maskClosable = !value
  activeDialog.closeOnEsc = !value
  activeDialog.negativeButtonProps = { disabled: value }
}
function confirmRecordsOnly(failed: number): Promise<boolean> {
  if (disposed) return Promise.resolve(false)
  return new Promise(resolve => {
    let settled = false
    const finish = (value: boolean) => {
      if (settled) return
      settled = true
      cancelFallback = undefined
      fallbackDialog = undefined
      resolve(value)
    }
    cancelFallback = () => finish(false)
    fallbackDialog = dialog.warning({
      title: t('results.removeFilesConfirmTitle'),
      content: t('results.journal.removeFilesFailed', { count: failed }),
      positiveText: t('results.removeListOnlyAction'), negativeText: t('results.keepResultAction'),
      positiveButtonProps: { type: 'error' },
      onPositiveClick: () => finish(true), onNegativeClick: () => finish(false), onClose: () => finish(false),
    })
  })
}
async function requestRemoval(ids: string[], deleteFiles: boolean): Promise<ResultRemovalResponse | null> {
  if (disposed) return null
  try {
    const checked = await removeResultJobs(ids, { deleteFiles, checkOnly: true })
    if (disposed) return null
    if (!ids.every(id => checked.checkedJobIds.includes(id))) throw new Error('Incomplete result preflight')
  } catch {
    if (!disposed) message.warning(t('results.removeNotReady'))
    return null
  }
  return removeResultJobs(ids, { deleteFiles })
}
function handleRemove(rows: JournalResult[]) {
  if (disposed || blocked.value) return
  const visible = new Set(props.results.map(result => result.id))
  const ids = [...new Set(rows.map(row => row.id).filter(id => visible.has(id)))]
  if (!ids.length) return
  const deleteFiles = ref(false)
  confirmationOpen.value = true
  let currentDialog: DialogReactive
  currentDialog = dialog.warning({
    title: t('results.journal.removeTitle'),
    content: () => h('div', { style: 'display:grid;gap:12px;' }, [
      h('p', t('results.journal.removeContent', { count: ids.length })),
      h(NCheckbox, { checked: deleteFiles.value, disabled: removing.value,
        'onUpdate:checked': (value: boolean) => { if (!removing.value) deleteFiles.value = value } },
      { default: () => t('results.removeDeleteFiles') }),
      h('p', { style: 'margin:0;color:var(--on-surface-muted);font-size:12px;' }, t('results.journal.removeProtectedHint')),
    ]),
    positiveText: t('results.removeAction'), negativeText: t('common.cancel'), positiveButtonProps: { type: 'error' },
    onNegativeClick: () => { confirmationOpen.value = false },
    onClose: () => { confirmationOpen.value = false },
    onAfterLeave: () => { if (activeDialog === currentDialog) activeDialog = undefined },
    onPositiveClick: async () => {
      if (removing.value || disposed) return false
      removing.value = true
      setDialogBusy(true)
      try {
        if (deleteFiles.value) {
          previews.value = Object.fromEntries(Object.entries(previews.value).filter(([id]) => !ids.includes(id)))
          await nextTick()
        }
        let response = await requestRemoval(ids, deleteFiles.value)
        if (!response) return false
        const retained = new Set(response.retained.map(item => item.path))
        if (response.failed.length) {
          if (!await confirmRecordsOnly(response.failed.length)) {
            if (!disposed) message.warning(t('results.journal.recordsKept'))
            confirmationOpen.value = false
            return
          }
          response = await requestRemoval(ids, false)
          if (!response) return false
          response.retained.forEach(item => retained.add(item.path))
        }
        if (response.failed.length || !ids.every(id => response.removedJobIds.includes(id))) {
          throw new Error(t('results.journal.removeFailed'))
        }
        // Only the backend acknowledgement may remove rows or install tombstones.
        mediaJobs.forget(response.removedJobIds)
        if (!disposed) {
          selectedIds.value = selectedIds.value.filter(id => !response!.removedJobIds.includes(id))
          if (!selectedIds.value.length) selecting.value = false
          message.success(t('results.journal.removeSuccess', { count: response.removedJobIds.length }))
          if (retained.size) message.warning(t('results.journal.filesRetained', { count: retained.size }))
        }
        confirmationOpen.value = false
      } catch (cause) {
        if (!disposed) message.error(cause instanceof Error ? cause.message : t('results.journal.removeFailed'))
        return false
      } finally {
        removing.value = false
        setDialogBusy(false)
      }
    },
  })
  activeDialog = currentDialog
}
function removeSelected() { handleRemove(props.results.filter(result => selected.value.has(result.id))) }
</script>

<template>
  <section class="journal-results" :aria-label="t('results.journal.title')">
    <header class="journal-results__head">
      <div><h2>{{ t('results.journal.title') }}</h2><p>{{ t('results.journal.hint') }}</p></div>
      <div class="journal-results__tools">
        <span>{{ results.length }} / {{ total }}</span>
        <template v-if="selecting">
          <n-button size="small" secondary :disabled="blocked || !pageResults.length" @click="selectPage">{{ t('results.selectPage') }}</n-button>
          <n-button size="small" type="error" secondary :disabled="blocked || !selectedIds.length" @click="removeSelected">{{ t('results.journal.removeSelected', { count: selectedIds.length }) }}</n-button>
        </template>
        <n-button size="small" secondary :disabled="blocked || !results.length" @click="toggleSelecting">{{ selecting ? t('common.cancel') : t('results.journal.select') }}</n-button>
        <n-button quaternary size="small" :loading="loading" :aria-label="t('results.journal.refresh')" @click="$emit('refresh')"><template #icon><n-icon :component="RefreshOutline" /></template></n-button>
      </div>
    </header>
    <n-alert v-if="error" type="error" :bordered="false">{{ error }}</n-alert>
    <div v-if="!results.length" class="journal-results__empty">{{ loading ? t('results.journal.loading') : total ? t('results.noMatchTitle') : t('results.journal.empty') }}</div>
    <article v-for="result in pageResults" :key="result.id" class="journal-result" :data-journal-result="result.id">
      <div class="journal-result__head">
        <div class="journal-result__identity"><strong :title="result.inputs[0] || result.title">{{ result.title }}</strong><span>{{ operationLabel(result.operation) }} · {{ time(result.updatedAt) }}</span></div>
        <div class="journal-result__tags"><n-checkbox v-if="selecting" :checked="selected.has(result.id)" :disabled="blocked" :aria-label="t('results.journal.selectResult', { name: result.title })" @update:checked="value => toggleSelection(result.id, value)" /><n-tag size="small" :bordered="false">{{ t(`results.journal.source.${result.source}`) }}</n-tag><n-tag size="small" :type="['done', 'completed'].includes(result.status) ? 'success' : 'warning'" :bordered="false">{{ statusLabel(result.status) }}</n-tag><n-button quaternary size="small" :disabled="blocked" :aria-label="t('results.journal.removeNamed', { name: result.title })" @click="handleRemove([result])"><template #icon><n-icon :component="TrashOutline" /></template></n-button></div>
      </div>
      <p v-if="result.status !== 'completed' && result.status !== 'done'" class="journal-result__partial">{{ result.outputs.length ? t('results.journal.partial') : t('results.noFiles') }}<span v-if="result.message"> · {{ result.message }}</span></p>
      <p v-else-if="!result.outputs.length" class="journal-result__partial">{{ t('results.noFiles') }}</p>
      <details v-if="result.outputs.length" class="journal-result__files" :open="result.outputs.length <= 3">
      <summary>{{ t('results.journal.outputs', { count: result.outputs.length }) }}</summary>
      <ul class="journal-result__outputs">
        <li v-for="output in result.outputs" :key="output.path">
          <div class="journal-output__row">
            <div class="journal-output__name"><strong>{{ output.name }}</strong><span :title="output.path">{{ output.path }}</span></div>
            <span v-if="output.size !== undefined" class="journal-output__size">{{ formatBytes(output.size) }}</span>
            <n-button v-if="output.preview" size="small" secondary :disabled="removing" @click="togglePreview(result.id, output.path)">{{ previews[result.id] === output.path ? t('results.journal.closePreview') : t('results.journal.preview') }}</n-button>
            <n-button size="small" secondary :disabled="removing" :aria-label="t('results.journal.openOutput', { name: output.name })" @click="reveal(output.path)"><template #icon><n-icon :component="FolderOpenOutline" /></template>{{ t('results.journal.openLocation') }}</n-button>
          </div>
          <div v-if="previews[result.id] === output.path" class="journal-output__preview">
            <img v-if="output.preview === 'image'" :src="convertFileSrc(output.path)" :alt="output.name" />
            <video v-else-if="output.preview === 'video'" :src="convertFileSrc(output.path)" controls preload="metadata" />
            <audio v-else-if="output.preview === 'audio'" :src="convertFileSrc(output.path)" controls preload="metadata" />
          </div>
        </li>
      </ul>
      </details>
    </article>
    <n-pagination v-if="results.length > pageSize" v-model:page="page" :page-size="pageSize" :item-count="results.length" class="journal-results__pagination" />
  </section>
</template>

<style scoped>
.journal-results { display: grid; gap: 12px; }
.journal-results__head, .journal-results__tools, .journal-result__head, .journal-result__tags { display: flex; align-items: center; gap: 10px; }
.journal-results__head, .journal-result__head { justify-content: space-between; }
.journal-results__head, .journal-result__tags { flex-wrap: wrap; }
.journal-result__tags { justify-content: flex-end; }
.journal-results__tools { justify-content: flex-end; flex-wrap: wrap; }
.journal-results__head h2 { margin: 0; color: var(--on-surface-heading); font-size: 14px; }
.journal-results__head p { margin: 5px 0 0; color: var(--on-surface-muted); font-size: 12px; }
.journal-results__tools, .journal-results__empty { color: var(--on-surface-muted); font-size: 12px; }
.journal-result { min-width: 0; padding: 17px 18px; border-radius: 18px; background: var(--surface-1); box-shadow: inset 0 0 0 1px var(--outline), var(--shadow-panel); }
.journal-result__identity, .journal-output__name { display: grid; min-width: 0; gap: 4px; }
.journal-result__identity strong, .journal-output__name strong { color: var(--on-surface-heading); font-size: 13px; overflow-wrap: anywhere; }
.journal-result__identity > span, .journal-output__name > span, .journal-output__size { color: var(--on-surface-muted); font-size: 11px; }
.journal-output__name > span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.journal-result__partial { margin: 12px 0 0; color: var(--on-surface-muted); font-size: 12px; overflow-wrap: anywhere; }
.journal-result__outputs { list-style: none; padding: 0; margin: 14px 0 0; display: grid; gap: 10px; }
.journal-result__files { margin-top: 12px; }
.journal-result__files summary { color: var(--on-surface-muted); font-size: 12px; cursor: pointer; }
.journal-results__pagination { justify-content: flex-end; }
.journal-output__row { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; }
.journal-output__name { flex: 1 1 240px; }
.journal-output__preview { margin-top: 12px; padding: 10px; background: var(--surface-2); border-radius: 10px; }
.journal-output__preview img, .journal-output__preview video { display: block; max-width: 100%; max-height: 320px; margin: auto; }
.journal-output__preview audio { width: 100%; }
@media (max-width: 700px) { .journal-result__head { align-items: flex-start; flex-wrap: wrap; } .journal-results__head { align-items: flex-start; } }
</style>
