<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { invoke } from '@tauri-apps/api/core'
import { listen, type UnlistenFn } from '@tauri-apps/api/event'
import { open } from '@tauri-apps/plugin-dialog'
import { useI18n } from 'vue-i18n'
import { useMessage } from 'naive-ui'
import { useRoute } from 'vue-router'
import {
  CloudDownloadOutline,
  CubeOutline,
  FolderOpenOutline,
  LayersOutline,
  MusicalNotesOutline,
  RefreshOutline,
  SparklesOutline,
  VideocamOutline,
} from '@vicons/ionicons5'
import MediaPageHeader from '@/components/media/MediaPageHeader.vue'

defineOptions({ name: 'ModelHubView' })

type Modality = 'all' | 'audio' | 'image' | 'video'
type ModelRecord = {
  id: string
  provider: string
  modality: 'audio' | 'image' | 'video'
  name: string
  label: string
  task: string
  category: string
  categoryCn: string
  architecture: string
  size: number
  license: string
  description: string
  downloaded: boolean
  partial: boolean
  runtime: string
}
type HubResult = {
  ok: boolean
  modelDir: string
  models: ModelRecord[]
  environment: Record<string, boolean>
  count: number
  downloadedCount: number
}
type WorkerPayload = {
  fraction?: number
  current?: string
  message?: string
}
type WorkerEvent = {
  type: string
  requestId?: string | null
  taskId?: string | null
  payload?: WorkerPayload
}

const { t } = useI18n()
const message = useMessage()
const route = useRoute()
const models = ref<ModelRecord[]>([])
const modelDir = ref('')
const query = ref('')
const modality = ref<Modality>('all')
const downloadedOnly = ref(false)
const loading = ref(false)
const loadError = ref('')
const downloadingId = ref('')
const progress = ref<Record<string, number>>({})
const requestId = ref('')
const environment = ref<Record<string, boolean>>({})
let unlisten: UnlistenFn | undefined

const tabs = computed(() => [
  { value: 'all', label: t('modelHub.all'), count: models.value.length },
  { value: 'audio', label: t('modelHub.audio'), count: models.value.filter(item => item.modality === 'audio').length },
  { value: 'image', label: t('modelHub.image'), count: models.value.filter(item => item.modality === 'image').length },
  { value: 'video', label: t('modelHub.video'), count: models.value.filter(item => item.modality === 'video').length },
])
const filtered = computed(() => downloadedOnly.value ? models.value.filter(item => item.downloaded) : models.value)
const downloadedCount = computed(() => models.value.filter(item => item.downloaded).length)
const envLabels: Record<string, string> = {
  pymss: 'pymss', torch: 'PyTorch', spandrel: 'Spandrel', ultralytics: 'Ultralytics', opencv: 'OpenCV', ffmpeg: 'FFmpeg',
}

function formatSize(bytes: number) {
  if (!bytes) return '—'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let value = bytes
  let index = 0
  while (value >= 1024 && index < units.length - 1) { value /= 1024; index += 1 }
  return `${value.toFixed(value >= 10 || index === 0 ? 0 : 1)} ${units[index]}`
}

function modalityLabel(value: string) {
  return t(`modelHub.${value}`)
}

function setModality(value: string) {
  modality.value = value as Modality
}

function modalityIcon(value: string) {
  if (value === 'audio') return MusicalNotesOutline
  if (value === 'video') return VideocamOutline
  if (value === 'image') return SparklesOutline
  return LayersOutline
}

function runtimeLabel(value: string) {
  return value === 'pymss' ? 'pymss' : 'PyTorch'
}

async function load() {
  if (typeof window === 'undefined' || !('__TAURI_INTERNALS__' in window)) return
  loading.value = true
  loadError.value = ''
  try {
    const result = await invoke<HubResult>('model_hub', {
      payload: { operation: 'list', query: query.value, modality: modality.value, source: 'modelscope', modelDir: modelDir.value },
    })
    models.value = result.models || []
    environment.value = result.environment || {}
    if (!modelDir.value) modelDir.value = result.modelDir
  } catch (error) {
    loadError.value = error instanceof Error ? error.message : String(error)
    message.error(loadError.value)
  } finally {
    loading.value = false
  }
}

async function pickDir() {
  try {
    const selected = await open({ directory: true, multiple: false })
    if (typeof selected === 'string') { modelDir.value = selected; await load() }
  } catch (error) {
    message.error(error instanceof Error ? error.message : String(error))
  }
}

async function openFolder() {
  try {
    await invoke('reveal_path', { path: modelDir.value })
  } catch (error) {
    message.error(error instanceof Error ? error.message : String(error))
  }
}

async function download(item: ModelRecord) {
  if (downloadingId.value) return
  downloadingId.value = item.id
  progress.value = { ...progress.value, [item.id]: 0 }
  requestId.value = `model-${Date.now()}`
  try {
    await invoke('start_model_hub_download', {
      payload: { operation: 'model_download', requestId: requestId.value, modelId: item.id, modelDir: modelDir.value, source: 'modelscope' },
    })
  } catch (error) {
    downloadingId.value = ''
    message.error(error instanceof Error ? error.message : String(error))
  }
}

let searchTimer: ReturnType<typeof setTimeout> | undefined

watch([query, modality], () => {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => { void load() }, 250)
})

onMounted(async () => {
  const requested = route.query.modality
  if (requested === 'audio' || requested === 'image' || requested === 'video') modality.value = requested
  await load()
  if (typeof window === 'undefined' || !('__TAURI_INTERNALS__' in window)) return
  try {
    unlisten = await listen<WorkerEvent>('pymss://worker-event', event => {
      const payload = event.payload
      const id = payload?.requestId || payload?.taskId
      if (!requestId.value || id !== requestId.value) return
      if (payload?.type === 'audio_tool_progress' && payload.payload) {
        if (downloadingId.value) {
          progress.value = { ...progress.value, [downloadingId.value]: Math.round(Number(payload.payload.fraction || 0) * 100) }
        }
        return
      }
      if (payload?.type === 'audio_tool_result') {
        downloadingId.value = ''
        requestId.value = ''
        message.success(t('modelHub.downloaded'))
        void load()
        return
      }
      if (payload?.type === 'error') {
        downloadingId.value = ''
        requestId.value = ''
        message.error(String(payload.payload?.message || t('modelHub.downloadFailed')))
      }
    })
  } catch {
    // Browser preview has no Tauri event bridge.
  }
})

onBeforeUnmount(() => {
  if (searchTimer) clearTimeout(searchTimer)
  unlisten?.()
})
</script>

<template>
  <div class="page mf-page" data-tone="blue">
    <MediaPageHeader
      :title="t('modelHub.title')"
      :subtitle="t('modelHub.subtitle')"
      :icon="CubeOutline"
      eyebrow="MODEL HUB"
      tone="blue"
    >
      <n-tag :type="downloadedCount === models.length ? 'success' : 'warning'" :bordered="false">
        {{ downloadedCount }} / {{ models.length }}
      </n-tag>
      <n-button quaternary :disabled="loading" @click="load">
        <template #icon><n-icon :component="RefreshOutline" /></template>
      </n-button>
    </MediaPageHeader>

    <n-alert v-if="loadError" type="error" :bordered="false">
      <div class="hub-error"><span>{{ loadError }}</span><router-link to="/settings?section=runtime">运行环境 →</router-link></div>
    </n-alert>

    <section class="mf-panel">
      <div class="mf-panel__head">
        <span class="mf-panel__title">{{ t('modelHub.modelDir') }}</span>
        <n-button text @click="openFolder">
          <template #icon><n-icon :component="FolderOpenOutline" /></template>
          {{ t('modelHub.openFolder') }}
        </n-button>
      </div>
      <div class="hub-dir"><n-input v-model:value="modelDir" :placeholder="t('modelHub.modelDirPlaceholder')" /><n-button secondary @click="pickDir"><template #icon><n-icon :component="FolderOpenOutline" /></template>{{ t('modelHub.browse') }}</n-button></div>
      <div class="hub-env"><span class="hub-env__label">{{ t('modelHub.environment') }}</span><n-tag v-for="(ok, key) in environment" :key="key" size="small" :type="ok ? 'success' : 'error'" :bordered="false">{{ envLabels[key] || key }}</n-tag></div>
    </section>

    <div class="hub-toolbar">
      <n-input v-model:value="query" clearable :placeholder="t('modelHub.search')" />
      <div class="hub-tabs">
        <button v-for="tab in tabs" :key="tab.value" type="button" :class="{ active: modality === tab.value }" @click="setModality(tab.value)">
          {{ tab.label }} <span>{{ tab.count }}</span>
        </button>
      </div>
      <n-checkbox v-model:checked="downloadedOnly">{{ t('modelHub.downloadedOnly') }}</n-checkbox>
    </div>

    <n-spin :show="loading">
      <div v-if="!filtered.length" class="mf-empty">
        <span class="mf-empty__icon"><n-icon :component="CubeOutline" :size="30" /></span>
        <div class="mf-empty__title">{{ t('modelHub.empty') }}</div>
      </div>
      <div v-else class="hub-grid">
        <article v-for="item in filtered" :key="item.id" class="hub-card">
          <div class="hub-card__head">
            <span class="hub-card__icon"><n-icon :component="modalityIcon(item.modality)" :size="23" /></span>
            <div class="hub-card__title"><strong>{{ item.label }}</strong><span :title="item.name">{{ item.name }}</span></div>
            <n-tag size="small" :type="item.downloaded ? 'success' : item.partial ? 'warning' : 'default'" :bordered="false">{{ item.downloaded ? t('modelHub.downloaded') : item.partial ? t('modelHub.partial') : t('modelHub.missing') }}</n-tag>
          </div>
          <div class="hub-card__badges"><n-tag size="small" :bordered="false">{{ modalityLabel(item.modality) }}</n-tag><span>{{ item.categoryCn }}</span><span>{{ runtimeLabel(item.runtime) }}</span></div>
          <p class="hub-card__desc">{{ item.description }}</p>
          <div class="hub-card__meta"><span>{{ formatSize(item.size) }}</span><span>{{ item.license }}</span><span>{{ item.architecture }}</span></div>
          <n-progress v-if="downloadingId === item.id" type="line" :percentage="progress[item.id] || 0" />
          <n-button v-if="!item.downloaded" type="primary" class="mf-primary-action" :loading="downloadingId === item.id" :disabled="Boolean(downloadingId)" @click="download(item)">
            <template #icon><n-icon :component="CloudDownloadOutline" /></template>
            {{ downloadingId === item.id ? t('modelHub.downloading') : t('modelHub.download') }}
          </n-button>
          <div v-else class="hub-card__ready"><n-icon :component="SparklesOutline" />{{ t('modelHub.downloaded') }}</div>
        </article>
      </div>
    </n-spin>
  </div>
</template>

<style scoped>
.hub-error { display: flex; gap: 12px; align-items: center; justify-content: space-between; }
.hub-error a { color: var(--primary-strong); font-weight: 700; white-space: nowrap; }
.hub-dir { display: flex; gap: 8px; align-items: center; }
.hub-env { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-top: 12px; }
.hub-env__label { margin-right: 4px; color: var(--text-color-3); font-size: 11px; font-weight: 700; }
.hub-toolbar { display: grid; grid-template-columns: minmax(0, 1fr) auto auto; gap: 12px; align-items: center; }
.hub-tabs { display: flex; gap: 4px; padding: 3px; border: 1px solid var(--divider-color); border-radius: 11px; background: var(--card-color); }
.hub-tabs button { padding: 6px 9px; border: 0; border-radius: 8px; background: transparent; color: var(--text-color-3); font-family: inherit; font-size: 11px; cursor: pointer; }
.hub-tabs button.active { background: var(--primary-soft); color: var(--primary-strong); font-weight: 700; }
.hub-tabs button span { opacity: 0.65; font-size: 9px; }
.hub-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
.hub-card { padding: 15px; border: 1px solid var(--divider-color); border-radius: 16px; background: var(--card-color); box-shadow: 0 12px 34px rgba(29, 39, 64, 0.04); transition: border-color 160ms ease, box-shadow 160ms ease, transform 160ms ease; }
.hub-card:hover { border-color: color-mix(in srgb, var(--primary-strong) 30%, var(--divider-color)); box-shadow: 0 16px 34px rgba(29, 39, 64, 0.08); transform: translateY(-1px); }
.hub-card__head { display: flex; gap: 9px; align-items: flex-start; }
.hub-card__icon { display: grid; flex: 0 0 auto; place-items: center; width: 38px; height: 38px; border-radius: 12px; background: var(--primary-soft); color: var(--primary-strong); }
.hub-card__title { display: flex; flex: 1; min-width: 0; flex-direction: column; gap: 2px; }
.hub-card__title strong { overflow: hidden; color: var(--text-color-1); font-size: 12.5px; text-overflow: ellipsis; white-space: nowrap; }
.hub-card__title span { overflow: hidden; color: var(--text-color-3); font-size: 10px; text-overflow: ellipsis; white-space: nowrap; }
.hub-card__badges { display: flex; flex-wrap: wrap; gap: 7px; align-items: center; margin-top: 9px; color: var(--text-color-3); font-size: 10px; }
.hub-card__desc { display: -webkit-box; overflow: hidden; min-height: 32px; margin: 9px 0; color: var(--text-color-2); font-size: 11px; line-height: 1.45; -webkit-box-orient: vertical; -webkit-line-clamp: 2; }
.hub-card__meta { display: flex; flex-wrap: wrap; gap: 5px 12px; margin-bottom: 11px; color: var(--text-color-3); font-size: 10px; }
.hub-card__ready { display: flex; gap: 6px; align-items: center; justify-content: center; min-height: 34px; border-radius: 9px; background: color-mix(in srgb, #0f9f72 10%, transparent); color: #0f9f72; font-size: 11px; font-weight: 700; }
@media (max-width: 980px) { .hub-grid { grid-template-columns: 1fr; } .hub-toolbar { grid-template-columns: 1fr; } }
</style>
