<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { invoke } from '@tauri-apps/api/core'
import { listen, type UnlistenFn } from '@tauri-apps/api/event'
import { open } from '@tauri-apps/plugin-dialog'
import { useI18n } from 'vue-i18n'
import { useMessage } from 'naive-ui'
import {
  CloudDownloadOutline,
  CubeOutline,
  FolderOpenOutline,
  RefreshOutline,
  SparklesOutline,
} from '@vicons/ionicons5'
import MediaPageHeader from '@/components/media/MediaPageHeader.vue'

defineOptions({ name: 'MediaModelsView' })

type MediaModel = {
  id: string
  name: string
  label: string
  modality: string
  task: string
  size: number
  license: string
  description: string
  path: string
  actualSize: number
  downloaded: boolean
  partial: boolean
}
type Status = {
  modelDir: string
  models: MediaModel[]
  environment: Record<string, boolean>
  ready: boolean
}
type WorkerPayload = {
  fraction?: number
  current?: string
  message?: string
  outputs?: Array<{ id?: string; path?: string; skipped?: boolean }>
  failed?: Array<{ path: string; error: string }>
}
type WorkerEvent = {
  type: string
  requestId?: string | null
  taskId?: string | null
  payload?: WorkerPayload
}

const { t } = useI18n()
const message = useMessage()
const modelDir = ref('')
const status = ref<Status | null>(null)
const loading = ref(false)
const loadError = ref('')
const downloadingId = ref('')
const progress = ref<Record<string, number>>({})
const requestId = ref('')
let unlisten: UnlistenFn | undefined

const envLabels: Record<string, string> = {
  torch: 'PyTorch',
  spandrel: 'Spandrel',
  ultralytics: 'Ultralytics',
  opencv: 'OpenCV',
  pillow: 'Pillow',
  ffmpeg: 'FFmpeg',
}
const envItems = computed(() => Object.entries(status.value?.environment || {}))
const ready = computed(() => status.value?.ready === true)
const missingCount = computed(() => status.value?.models.filter(item => !item.downloaded).length || 0)

function formatSize(bytes: number) {
  if (!bytes) return '—'
  return `${(bytes / 1024 / 1024).toFixed(0)} MB`
}

function taskLabel(task: string) {
  if (task === 'inpainting') return t('mediaModels.taskInpaint')
  if (task === 'watermark_detection') return t('mediaModels.taskDetect')
  return t('mediaModels.taskSr')
}

function statusType(item: MediaModel) {
  if (item.downloaded) return 'success'
  if (item.partial) return 'warning'
  return 'default'
}

function statusLabel(item: MediaModel) {
  if (item.downloaded) return t('mediaModels.downloaded')
  if (item.partial) return t('mediaModels.partial')
  return t('mediaModels.missing')
}

async function load() {
  if (typeof window === 'undefined' || !('__TAURI_INTERNALS__' in window)) return
  loading.value = true
  loadError.value = ''
  try {
    const result = await invoke<{ ok: boolean } & Status>('media_models', {
      payload: { operation: 'status', modelDir: modelDir.value },
    })
    status.value = {
      modelDir: result.modelDir,
      models: result.models || [],
      environment: result.environment || {},
      ready: Boolean(result.ready),
    }
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
    if (typeof selected === 'string') {
      modelDir.value = selected
      await load()
    }
  } catch (error) {
    message.error(error instanceof Error ? error.message : String(error))
  }
}

async function openFolder() {
  if (!status.value?.modelDir) return
  try {
    await invoke('reveal_path', { path: status.value.modelDir })
  } catch (error) {
    message.error(error instanceof Error ? error.message : String(error))
  }
}

async function download(item: MediaModel) {
  if (downloadingId.value) return
  downloadingId.value = item.id
  progress.value = { ...progress.value, [item.id]: 0 }
  requestId.value = `model-${Date.now()}-${item.id}`
  try {
    await invoke('start_media_model_download', {
      payload: {
        operation: 'media_model_download',
        requestId: requestId.value,
        modelId: item.id,
        modelDir: modelDir.value,
      },
    })
  } catch (error) {
    downloadingId.value = ''
    message.error(error instanceof Error ? error.message : String(error))
  }
}

onMounted(async () => {
  await load()
  if (typeof window === 'undefined' || !('__TAURI_INTERNALS__' in window)) return
  try {
    unlisten = await listen<WorkerEvent>('pymss://worker-event', event => {
      const payload = event.payload
      const id = payload?.requestId || payload?.taskId
      if (!requestId.value || id !== requestId.value) return
      if (payload?.type === 'audio_tool_progress' && payload.payload) {
        const item = status.value?.models.find(entry => entry.id === downloadingId.value)
        if (item) {
          progress.value = { ...progress.value, [item.id]: Math.round(Number(payload.payload.fraction || 0) * 100) }
        }
        return
      }
      if (payload?.type === 'audio_tool_result') {
        downloadingId.value = ''
        requestId.value = ''
        message.success(t('mediaModels.downloaded'))
        void load()
        return
      }
      if (payload?.type === 'error') {
        downloadingId.value = ''
        requestId.value = ''
        message.error(String(payload.payload?.message || t('mediaModels.downloadFailed')))
      }
    })
  } catch {
    // Browser preview has no Tauri event bridge.
  }
})

onBeforeUnmount(() => {
  unlisten?.()
})
</script>

<template>
  <div class="page mf-page" data-tone="blue">
    <MediaPageHeader
      :title="t('mediaModels.title')"
      :subtitle="t('mediaModels.subtitle')"
      :icon="CubeOutline"
      eyebrow="MODEL HUB"
      tone="blue"
    >
      <n-tag :type="ready ? 'success' : 'warning'" :bordered="false">
        {{ ready ? t('mediaModels.ready') : t('mediaModels.notReady') }}
      </n-tag>
      <n-button quaternary :disabled="loading" @click="load">
        <template #icon><n-icon :component="RefreshOutline" /></template>
      </n-button>
    </MediaPageHeader>

    <n-alert v-if="loadError" type="error" :bordered="false">
      <div class="model-load-error">
        <span>{{ loadError }}</span>
        <router-link to="/settings?section=runtime">{{ t('runtimeStatus.notReady') }} →</router-link>
      </div>
    </n-alert>

    <section class="mf-panel">
      <div class="mf-panel__head">
        <span class="mf-panel__title">{{ t('mediaModels.modelDir') }}</span>
        <n-button text @click="openFolder">
          <template #icon><n-icon :component="FolderOpenOutline" /></template>
          {{ t('mediaModels.openFolder') }}
        </n-button>
      </div>
      <div class="model-dir-row">
        <n-input v-model:value="modelDir" :placeholder="t('mediaModels.modelDirPlaceholder')" />
        <n-button secondary @click="pickDir">
          <template #icon><n-icon :component="FolderOpenOutline" /></template>
          {{ t('mediaModels.browse') }}
        </n-button>
      </div>
      <div class="model-env">
        <span class="model-env__label">{{ t('mediaModels.environment') }}</span>
        <n-tag
          v-for="[key, ok] in envItems"
          :key="key"
          size="small"
          :type="ok ? 'success' : 'error'"
          :bordered="false"
        >
          {{ envLabels[key] || key }}
        </n-tag>
        <span v-if="missingCount" class="model-env__hint">{{ missingCount }} / 4</span>
      </div>
    </section>

    <n-spin :show="loading">
      <div class="model-grid">
        <article v-for="item in status?.models || []" :key="item.id" class="model-card">
          <div class="model-card__head">
            <span class="model-card__icon"><n-icon :component="SparklesOutline" :size="22" /></span>
            <div class="model-card__title">
              <strong>{{ item.label }}</strong>
              <span>{{ item.name }}</span>
            </div>
            <n-tag size="small" :type="statusType(item)" :bordered="false">{{ statusLabel(item) }}</n-tag>
          </div>
          <p class="model-card__desc">{{ item.description }}</p>
          <div class="model-card__meta">
            <span>{{ taskLabel(item.task) }}</span>
            <span>{{ formatSize(item.size) }}</span>
            <span>{{ item.license }}</span>
          </div>
          <n-progress
            v-if="downloadingId === item.id"
            type="line"
            :percentage="progress[item.id] || 0"
            :show-indicator="true"
          />
          <n-button
            v-if="!item.downloaded"
            type="primary"
            class="mf-primary-action"
            :loading="downloadingId === item.id"
            :disabled="Boolean(downloadingId)"
            @click="download(item)"
          >
            <template #icon><n-icon :component="CloudDownloadOutline" /></template>
            {{ downloadingId === item.id ? t('mediaModels.downloading') : t('mediaModels.download') }}
          </n-button>
          <div v-else class="model-card__ready">
            <n-icon :component="SparklesOutline" />
            {{ t('mediaModels.downloaded') }}
          </div>
        </article>
      </div>
    </n-spin>

    <div class="mf-step-strip">
      <div class="mf-step">
        <span class="mf-step__num">01</span>
        <div><div class="mf-step__title">{{ t('mediaModels.environment') }}</div><div class="mf-step__text">torch · spandrel · ultralytics</div></div>
      </div>
      <div class="mf-step">
        <span class="mf-step__num">02</span>
        <div><div class="mf-step__title">{{ t('mediaModels.download') }}</div><div class="mf-step__text">{{ t('mediaModels.required') }}</div></div>
      </div>
      <div class="mf-step">
        <span class="mf-step__num">03</span>
        <div><div class="mf-step__title">{{ t('image.title') }} / {{ t('video.title') }}</div><div class="mf-step__text">{{ t('mediaModels.ready') }}</div></div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.model-dir-row { display: flex; gap: 8px; align-items: center; }
.model-load-error { display: flex; gap: 12px; align-items: center; justify-content: space-between; }
.model-load-error a { color: var(--primary-strong); font-weight: 700; white-space: nowrap; }
.model-env { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-top: 12px; }
.model-env__label { margin-right: 4px; color: var(--text-color-3); font-size: 11px; font-weight: 700; }
.model-env__hint { margin-left: auto; color: var(--text-color-3); font-size: 11px; }
.model-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
.model-card {
  padding: 16px;
  border: 1px solid var(--divider-color);
  border-radius: 16px;
  background: var(--card-color);
  box-shadow: 0 12px 34px rgba(29, 39, 64, 0.04);
  transition: border-color 160ms ease, box-shadow 160ms ease, transform 160ms ease;
}
.model-card:hover { border-color: color-mix(in srgb, var(--mf-accent) 34%, var(--divider-color)); box-shadow: 0 16px 34px rgba(29, 39, 64, 0.08); transform: translateY(-1px); }
.model-card__head { display: flex; gap: 10px; align-items: flex-start; }
.model-card__icon { display: grid; flex: 0 0 auto; place-items: center; width: 38px; height: 38px; border-radius: 12px; background: var(--mf-soft); color: var(--mf-accent); }
.model-card__title { display: flex; flex: 1; min-width: 0; flex-direction: column; gap: 2px; }
.model-card__title strong { color: var(--text-color-1); font-size: 13px; }
.model-card__title span { overflow: hidden; color: var(--text-color-3); font-size: 10.5px; text-overflow: ellipsis; white-space: nowrap; }
.model-card__desc { min-height: 34px; margin: 10px 0; color: var(--text-color-2); font-size: 11.5px; line-height: 1.5; }
.model-card__meta { display: flex; flex-wrap: wrap; gap: 6px 12px; margin-bottom: 12px; color: var(--text-color-3); font-size: 10.5px; }
.model-card__ready { display: flex; gap: 6px; align-items: center; justify-content: center; min-height: 34px; border-radius: 9px; background: color-mix(in srgb, #0f9f72 10%, transparent); color: #0f9f72; font-size: 11px; font-weight: 700; }
@media (max-width: 900px) { .model-grid { grid-template-columns: 1fr; } .model-dir-row { flex-direction: column; align-items: stretch; } }
</style>
