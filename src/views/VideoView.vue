<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { convertFileSrc, invoke } from '@tauri-apps/api/core'
import { listen, type UnlistenFn } from '@tauri-apps/api/event'
import { open } from '@tauri-apps/plugin-dialog'
import { useI18n } from 'vue-i18n'
import { useMessage } from 'naive-ui'
import { useRoute } from 'vue-router'
import {
  CloseCircleOutline,
  DocumentOutline,
  FolderOpenOutline,
  VideocamOutline,
} from '@vicons/ionicons5'
import MediaPageHeader from '@/components/media/MediaPageHeader.vue'
import { useSettingsStore } from '@/stores/settings'

defineOptions({ name: 'VideoView' })

type ImageMode = 'upscale' | 'watermark' | 'watermark_upscale'
type OutputFile = {
  path: string
  source: string
  mode: string
  size?: number
  info?: {
    device?: string
    fps?: number
    frames?: number
    duration?: number
    input_size?: number[]
    output_size?: number[]
    scale?: number
    audio?: boolean
    boxes?: number
    elapsed?: number
  }
}
type WorkerPayload = {
  fraction?: number
  completed?: number
  total?: number
  current?: string
  message?: string
  outputs?: OutputFile[]
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
const route = useRoute()
const settings = useSettingsStore()

const inputs = ref<string[]>([])
const outputDir = ref(settings.outputDir || '')
const mode = ref<ImageMode>('watermark_upscale')
const scale = ref(2)
const target = ref<'none' | '1080p' | '2k' | '4k' | 'custom'>('none')
const width = ref(1920)
const height = ref(1080)
const fit = ref<'contain' | 'pad' | 'stretch' | 'cover'>('contain')
const detectMode = ref<'fixed' | 'per_frame'>('fixed')
const conf = ref(0.25)
const expand = ref(8)
const previewSeconds = ref(0)
const modelDir = ref('')
const crf = ref(18)
const preset = ref('fast')
const busy = ref(false)
const progress = ref(0)
const status = ref('')
const failedCount = ref(0)
const outputs = ref<OutputFile[]>([])
const requestId = ref('')
let unlisten: UnlistenFn | undefined

const modeOptions = computed(() => [
  { label: t('image.upscale'), value: 'upscale' },
  { label: t('image.watermark'), value: 'watermark' },
  { label: t('image.watermarkUpscale'), value: 'watermark_upscale' },
])
const scaleOptions = [2, 4].map(value => ({ label: `${value}×`, value }))
const targetOptions = computed(() => [
  { label: t('image.original'), value: 'none' },
  { label: '1080p', value: '1080p' },
  { label: '2K', value: '2k' },
  { label: '4K', value: '4k' },
  { label: t('image.custom'), value: 'custom' },
])
const fitOptions = computed(() => [
  { label: t('image.contain'), value: 'contain' },
  { label: t('image.pad'), value: 'pad' },
  { label: t('image.stretch'), value: 'stretch' },
  { label: t('image.cover'), value: 'cover' },
])
const detectOptions = computed(() => [
  { label: t('video.fixed'), value: 'fixed' },
  { label: t('video.perFrame'), value: 'per_frame' },
])
const presetOptions = ['ultrafast', 'fast', 'medium', 'slow'].map(value => ({ label: value, value }))
const showUpscale = computed(() => mode.value !== 'watermark')
const showWatermark = computed(() => mode.value !== 'upscale')
const firstOutput = computed(() => outputs.value[0] || null)
const videoSrc = computed(() => firstOutput.value ? convertFileSrc(firstOutput.value.path) : '')
const resultInfo = computed(() => {
  const info = firstOutput.value?.info
  if (!info) return []
  const rows: Array<{ label: string; value: string }> = []
  if (info.output_size) rows.push({ label: 'Output', value: `${info.output_size[0]}×${info.output_size[1]}` })
  if (info.frames) rows.push({ label: 'Frames', value: String(info.frames) })
  if (info.fps) rows.push({ label: 'FPS', value: String(info.fps) })
  if (info.audio !== undefined) rows.push({ label: 'Audio', value: info.audio ? 'yes' : 'no' })
  if (info.boxes !== undefined) rows.push({ label: 'Boxes', value: String(info.boxes) })
  if (info.elapsed !== undefined) rows.push({ label: 'Time', value: `${info.elapsed}s` })
  if (info.scale) rows.push({ label: 'Scale', value: `${info.scale}×` })
  return rows
})

function fileName(path: string) {
  return path.split(/[\\/]/).filter(Boolean).pop() || path
}

function addInputs(paths: string[]) {
  const current = new Set(inputs.value)
  inputs.value = [...inputs.value, ...paths.filter(path => path && !current.has(path))]
}

async function pickFiles() {
  try {
    const selected = await open({
      multiple: true,
      filters: [{ name: 'Videos', extensions: ['mp4', 'mov', 'mkv', 'avi', 'webm', 'flv', 'wmv', 'm4v'] }],
    })
    if (!selected) return
    addInputs(Array.isArray(selected) ? selected : [selected])
  } catch (error) {
    message.error(error instanceof Error ? error.message : String(error))
  }
}

async function pickOutput() {
  try {
    const selected = await open({ directory: true, multiple: false })
    if (typeof selected === 'string') outputDir.value = selected
  } catch (error) {
    message.error(error instanceof Error ? error.message : String(error))
  }
}

async function pickModelDir() {
  try {
    const selected = await open({ directory: true, multiple: false })
    if (typeof selected === 'string') modelDir.value = selected
  } catch (error) {
    message.error(error instanceof Error ? error.message : String(error))
  }
}

function removeInput(path: string) {
  inputs.value = inputs.value.filter(item => item !== path)
}

function onProgress(payload: WorkerPayload) {
  const fraction = Number(payload.fraction || 0)
  const completed = Number(payload.completed || 0)
  const total = Number(payload.total || 0)
  const percentage = fraction > 0 ? fraction * 100 : (total > 0 ? (completed / total) * 100 : 0)
  progress.value = Math.max(0, Math.min(99, Math.round(percentage)))
  status.value = String(payload.current || t('factory.processing'))
}

async function run() {
  if (!inputs.value.length) {
    message.warning(t('factory.selectFirst'))
    return
  }
  if (!outputDir.value) {
    message.warning(t('factory.selectOutput'))
    return
  }
  busy.value = true
  progress.value = 0
  status.value = t('factory.processing')
  failedCount.value = 0
  outputs.value = []
  requestId.value = `video-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
  try {
    await invoke('start_audio_tool', {
      payload: {
        operation: 'media_video',
        requestId: requestId.value,
        inputs: inputs.value,
        outputDir: outputDir.value,
        mode: mode.value,
        scale: scale.value,
        target: target.value,
        width: width.value,
        height: height.value,
        fit: fit.value,
        detectMode: detectMode.value,
        conf: conf.value,
        expand: expand.value,
        previewSeconds: previewSeconds.value,
        modelDir: modelDir.value,
        crf: crf.value,
        preset: preset.value,
      },
    })
  } catch (error) {
    busy.value = false
    status.value = error instanceof Error ? error.message : String(error)
    message.error(status.value)
  }
}

async function openOutput() {
  if (!outputDir.value) return
  try {
    await invoke('reveal_path', { path: outputDir.value })
  } catch (error) {
    message.error(error instanceof Error ? error.message : String(error))
  }
}

onMounted(async () => {
  const requested = route.query.asset
  if (typeof requested === 'string' && requested) addInputs([requested])
  if (typeof window === 'undefined' || !('__TAURI_INTERNALS__' in window)) return
  try {
    unlisten = await listen<WorkerEvent>('pymss://worker-event', event => {
      const payload = event.payload
      const id = payload?.requestId || payload?.taskId
      if (!requestId.value || id !== requestId.value) return

      if (payload?.type === 'audio_tool_progress' && payload.payload) {
        onProgress(payload.payload)
        return
      }
      if (payload?.type === 'audio_tool_result' && payload.payload) {
        const result = payload.payload
        outputs.value = result.outputs || []
        failedCount.value = result.failed?.length || 0
        busy.value = false
        progress.value = 100
        status.value = failedCount.value
          ? `${t('factory.failed')} (${failedCount.value})`
          : t('factory.completed')
        if (failedCount.value) message.warning(status.value)
        else message.success(status.value)
        requestId.value = ''
        return
      }
      if (payload?.type === 'error') {
        busy.value = false
        status.value = String(payload.payload?.message || 'Video processing failed')
        message.error(status.value)
        requestId.value = ''
      }
      if (payload?.type === 'task_cancelled') {
        busy.value = false
        status.value = 'Cancelled'
        requestId.value = ''
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
  <div class="page mf-page video-page" data-tone="amber">
    <MediaPageHeader
      :title="t('video.title')"
      :subtitle="t('video.subtitle')"
      :icon="VideocamOutline"
      eyebrow="VIDEO RESTORE"
      tone="amber"
    >
      <router-link to="/media-models">
        <n-button quaternary size="small">{{ t('nav.mediaModels') }}</n-button>
      </router-link>
      <n-tag type="warning" :bordered="false">FFmpeg · Real-ESRGAN · LaMa</n-tag>
    </MediaPageHeader>

    <div class="mf-page-grid">
      <n-card :title="t('factory.input')" size="small">
        <div v-if="!inputs.length" class="mf-dropzone" @click="pickFiles">
          <span class="mf-dropzone__icon">
            <n-icon :component="VideocamOutline" :size="26" />
          </span>
          <span class="mf-dropzone__title">{{ t('factory.chooseFiles') }}</span>
          <span class="mf-dropzone__hint">{{ t('video.subtitle') }}</span>
          <div class="mf-dropzone__actions">
            <n-button type="primary" :disabled="busy" @click.stop="pickFiles">{{ t('factory.chooseFiles') }}</n-button>
          </div>
        </div>
        <template v-else>
          <div class="mf-file-list">
            <div v-for="path in inputs" :key="path" class="mf-file-row">
              <n-icon :component="VideocamOutline" size="18" />
              <span class="mf-file-row__name" :title="path">{{ fileName(path) }}</span>
              <n-button text :disabled="busy" @click="removeInput(path)">
                <template #icon><n-icon :component="CloseCircleOutline" /></template>
              </n-button>
            </div>
          </div>
          <n-button text class="mf-add-more" :disabled="busy" @click="pickFiles">+ {{ t('factory.addFiles') }}</n-button>
        </template>
      </n-card>

      <n-card :title="t('factory.output')" size="small">
        <n-space vertical :size="12">
          <div>
            <label class="video-label">{{ t('factory.outputDir') }}</label>
            <div class="video-path">
              <n-input :value="outputDir" readonly :placeholder="t('factory.outputDir')" />
              <n-button secondary :disabled="busy" @click="pickOutput">
                <template #icon><n-icon :component="FolderOpenOutline" /></template>
                {{ t('factory.browse') }}
              </n-button>
            </div>
          </div>

          <div>
            <label class="video-label">{{ t('image.modelDir') }}</label>
            <div class="video-path">
              <n-input v-model:value="modelDir" :placeholder="t('image.modelDirPlaceholder')" />
              <n-button secondary :disabled="busy" @click="pickModelDir">
                <template #icon><n-icon :component="FolderOpenOutline" /></template>
                {{ t('factory.browse') }}
              </n-button>
            </div>
          </div>

          <div>
            <label class="video-label">{{ t('image.mode') }}</label>
            <n-select v-model:value="mode" :options="modeOptions" :disabled="busy" />
          </div>

          <template v-if="showUpscale">
            <div class="video-row">
              <div>
                <label class="video-label">{{ t('image.target') }}</label>
                <n-select v-model:value="target" :options="targetOptions" :disabled="busy" />
              </div>
              <div v-if="target === 'none'">
                <label class="video-label">{{ t('image.scale') }}</label>
                <n-select v-model:value="scale" :options="scaleOptions" :disabled="busy" />
              </div>
              <div>
                <label class="video-label">{{ t('image.fit') }}</label>
                <n-select v-model:value="fit" :options="fitOptions" :disabled="busy" />
              </div>
            </div>
            <div v-if="target === 'custom'" class="video-row">
              <div>
                <label class="video-label">{{ t('image.width') }}</label>
                <n-input-number v-model:value="width" :min="64" :max="8192" :disabled="busy" />
              </div>
              <div>
                <label class="video-label">{{ t('image.height') }}</label>
                <n-input-number v-model:value="height" :min="64" :max="8192" :disabled="busy" />
              </div>
            </div>
          </template>

          <template v-if="showWatermark">
            <div class="video-row">
              <div>
                <label class="video-label">{{ t('video.detectMode') }}</label>
                <n-select v-model:value="detectMode" :options="detectOptions" :disabled="busy" />
              </div>
              <div>
                <label class="video-label">{{ t('image.conf') }}</label>
                <n-input-number v-model:value="conf" :min="0.05" :max="0.95" :step="0.05" :disabled="busy" />
              </div>
              <div>
                <label class="video-label">{{ t('image.expand') }}</label>
                <n-input-number v-model:value="expand" :min="0" :max="64" :disabled="busy" />
              </div>
            </div>
            <n-alert type="info" :bordered="false">{{ t('video.detectorHint') }}</n-alert>
          </template>

          <div class="video-row">
            <div>
              <label class="video-label">{{ t('video.preview') }}</label>
              <n-input-number v-model:value="previewSeconds" :min="0" :max="600" :disabled="busy" />
            </div>
            <div>
              <label class="video-label">{{ t('factory.crf') }}</label>
              <n-input-number v-model:value="crf" :min="12" :max="35" :disabled="busy" />
            </div>
            <div>
              <label class="video-label">{{ t('factory.preset') }}</label>
              <n-select v-model:value="preset" :options="presetOptions" :disabled="busy" />
            </div>
          </div>
          <p class="video-hint">{{ t('video.previewHint') }}</p>

          <n-button type="primary" size="large" class="mf-primary-action" :loading="busy" :disabled="busy" @click="run">
            <template #icon><n-icon :component="VideocamOutline" /></template>
            {{ t('factory.start') }}
          </n-button>

          <n-progress v-if="busy || progress" type="line" :percentage="progress" />
          <n-alert v-if="status" :type="failedCount ? 'error' : 'info'" :bordered="false">{{ status }}</n-alert>
        </n-space>
      </n-card>
    </div>

    <n-card v-if="firstOutput" :title="t('video.result')" size="small">
      <video class="video-player" :src="videoSrc" controls />
      <div class="video-info">
        <n-tag v-for="row in resultInfo" :key="row.label" size="small" :bordered="false">{{ row.label }}: {{ row.value }}</n-tag>
        <n-button text @click="openOutput">
          <template #icon><n-icon :component="FolderOpenOutline" /></template>
          {{ t('video.openVideo') }}
        </n-button>
      </div>
    </n-card>

    <div class="mf-step-strip">
      <div class="mf-step">
        <span class="mf-step__num">01</span>
        <div><div class="mf-step__title">{{ t('factory.input') }}</div><div class="mf-step__text">{{ t('image.mode') }}</div></div>
      </div>
      <div class="mf-step">
        <span class="mf-step__num">02</span>
        <div><div class="mf-step__title">{{ t('image.target') }}</div><div class="mf-step__text">{{ t('video.detectMode') }} · {{ t('image.fit') }}</div></div>
      </div>
      <div class="mf-step">
        <span class="mf-step__num">03</span>
        <div><div class="mf-step__title">{{ t('video.result') }}</div><div class="mf-step__text">{{ t('video.preview') }} · audio</div></div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.video-page :deep(.n-card) { border-radius: 18px; box-shadow: 0 12px 34px rgba(29, 39, 64, 0.045); }
.video-page { display: flex; flex-direction: column; gap: 12px; }
.video-grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 16px; align-items: start; }
.video-picker { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
.video-count { color: var(--text-color-3); font-size: 12px; }
.video-file { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.video-label { display: block; margin-bottom: 6px; color: var(--text-color-2); font-size: 12px; font-weight: 600; }
.video-path { display: flex; gap: 8px; align-items: center; }
.video-row { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
.video-hint { margin: -4px 0 0; color: var(--text-color-3); font-size: 11px; }
.video-player { width: 100%; max-height: 520px; border-radius: 8px; background: #000; }
.video-info { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-top: 10px; }
@media (max-width: 900px) { .video-grid { grid-template-columns: 1fr; } .video-row { grid-template-columns: 1fr 1fr; } }
</style>
