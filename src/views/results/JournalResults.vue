<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { convertFileSrc, invoke } from '@tauri-apps/api/core'
import { useI18n } from 'vue-i18n'
import { useMessage } from 'naive-ui'
import { FolderOpenOutline, RefreshOutline } from '@vicons/ionicons5'
import type { JournalResult } from './journalResults'
import { formatBytes } from '@/utils/format'

const props = defineProps<{ results: JournalResult[]; total: number; loading?: boolean; error?: string }>()
defineEmits<{ refresh: [] }>()
const { t } = useI18n()
const message = useMessage()
const previews = ref<Record<string, string>>({})
const page = ref(1)
const pageSize = 12
const pageResults = computed(() => props.results.slice((page.value - 1) * pageSize, page.value * pageSize))
watch(() => props.results.map(result => result.id).join('|'), () => { page.value = 1; previews.value = {} })
const operationKeys: Record<string, string> = {
  media_image: 'results.journal.image', media_video: 'results.journal.video', media_workflow: 'results.journal.workflow',
  media_convert: 'results.journal.convert', infer: 'results.journal.separate', infer_workflow: 'results.journal.audioWorkflow',
}
function operationLabel(operation: string) { return operationKeys[operation] ? t(operationKeys[operation]) : operation }
function statusLabel(status: string) { return t(`results.journal.status.${status === 'done' ? 'completed' : status}`) }
function time(value: number) { return value ? new Date(value).toLocaleString() : '—' }
async function reveal(path: string) {
  try { await invoke('reveal_path', { path }) }
  catch (error) { message.error(error instanceof Error ? error.message : t('results.journal.openFailed')) }
}
function togglePreview(id: string, path: string) { previews.value[id] = previews.value[id] === path ? '' : path }
</script>

<template>
  <section class="journal-results" :aria-label="t('results.journal.title')">
    <header class="journal-results__head">
      <div><h2>{{ t('results.journal.title') }}</h2><p>{{ t('results.journal.hint') }}</p></div>
      <div class="journal-results__tools">
        <span>{{ results.length }} / {{ total }}</span>
        <n-button quaternary size="small" :loading="loading" :aria-label="t('results.journal.refresh')" @click="$emit('refresh')"><template #icon><n-icon :component="RefreshOutline" /></template></n-button>
      </div>
    </header>
    <n-alert v-if="error" type="error" :bordered="false">{{ error }}</n-alert>
    <div v-if="!results.length" class="journal-results__empty">{{ loading ? t('results.journal.loading') : total ? t('results.noMatchTitle') : t('results.journal.empty') }}</div>
    <article v-for="result in pageResults" :key="result.id" class="journal-result" :data-journal-result="result.id">
      <div class="journal-result__head">
        <div class="journal-result__identity"><strong :title="result.inputs[0] || result.title">{{ result.title }}</strong><span>{{ operationLabel(result.operation) }} · {{ time(result.updatedAt) }}</span></div>
        <div class="journal-result__tags"><n-tag size="small" :bordered="false">{{ t(`results.journal.source.${result.source}`) }}</n-tag><n-tag size="small" :type="['done', 'completed'].includes(result.status) ? 'success' : 'warning'" :bordered="false">{{ statusLabel(result.status) }}</n-tag></div>
      </div>
      <p v-if="result.status !== 'completed' && result.status !== 'done'" class="journal-result__partial">{{ t('results.journal.partial') }}<span v-if="result.message"> · {{ result.message }}</span></p>
      <details class="journal-result__files" :open="result.outputs.length <= 3">
      <summary>{{ t('results.journal.outputs', { count: result.outputs.length }) }}</summary>
      <ul class="journal-result__outputs">
        <li v-for="output in result.outputs" :key="output.path">
          <div class="journal-output__row">
            <div class="journal-output__name"><strong>{{ output.name }}</strong><span :title="output.path">{{ output.path }}</span></div>
            <span v-if="output.size !== undefined" class="journal-output__size">{{ formatBytes(output.size) }}</span>
            <n-button v-if="output.preview" size="small" secondary @click="togglePreview(result.id, output.path)">{{ previews[result.id] === output.path ? t('results.journal.closePreview') : t('results.journal.preview') }}</n-button>
            <n-button size="small" secondary :aria-label="t('results.journal.openOutput', { name: output.name })" @click="reveal(output.path)"><template #icon><n-icon :component="FolderOpenOutline" /></template>{{ t('results.journal.openLocation') }}</n-button>
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
