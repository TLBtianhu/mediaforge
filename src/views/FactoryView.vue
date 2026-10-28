<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { invoke } from '@tauri-apps/api/core'
import { listen, type UnlistenFn } from '@tauri-apps/api/event'
import { open } from '@tauri-apps/plugin-dialog'
import { useI18n } from 'vue-i18n'
import { useMessage } from 'naive-ui'
import {
  CloseCircleOutline,
  DocumentOutline,
  FolderOpenOutline,
  SwapHorizontalOutline,
} from '@vicons/ionicons5'
import { useSettingsStore } from '@/stores/settings'

defineOptions({ name: 'FactoryView' })

type OutputFile = { path: string; kind?: string; format?: string; size?: number }
type WorkerPayload = {
  operation?: string
  fraction?: number
  completed?: number
  total?: number
  current?: string
  message?: string
  code?: string
  outputs?: OutputFile[]
  failed?: Array<{ path: string; error: string }>
  warnings?: string[]
}
type WorkerEvent = {
  type: string
  requestId?: string | null
  taskId?: string | null
  payload?: WorkerPayload
}

const { t } = useI18n()
const message = useMessage()
const settings = useSettingsStore()

const inputs = ref<string[]>([])
const outputDir = ref(settings.outputDir || '')
const format = ref('mp4')
const crf = ref(20)
const preset = ref('medium')
const busy = ref(false)
const progress = ref(0)
const status = ref('')
const outputs = ref<OutputFile[]>([])
const requestId = ref('')
let unlisten: UnlistenFn | undefined

const formatOptions = [
  { label: 'MP4 (H.264)', value: 'mp4' },
  { label: 'MKV (H.264)', value: 'mkv' },
  { label: 'WebM (VP9)', value: 'webm' },
  { label: 'MP3', value: 'mp3' },
  { label: 'WAV', value: 'wav' },
  { label: 'FLAC', value: 'flac' },
  { label: 'M4A', value: 'm4a' },
  { label: 'OGG', value: 'ogg' },
  { label: 'JPG', value: 'jpg' },
  { label: 'PNG', value: 'png' },
  { label: 'WEBP', value: 'webp' },
  { label: 'GIF', value: 'gif' },
]
const crfOptions = [18, 20, 23, 26, 30].map(value => ({ label: String(value), value }))
const presetOptions = ['ultrafast', 'fast', 'medium', 'slow'].map(value => ({ label: value, value }))
const isVideoFormat = computed(() => ['mp4', 'mkv', 'mov', 'webm', 'avi'].includes(format.value))

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
      filters: [{
        name: 'Media',
        extensions: ['mp4', 'mov', 'mkv', 'avi', 'webm', 'mp3', 'wav', 'flac', 'm4a', 'aac', 'ogg', 'jpg', 'jpeg', 'png', 'webp', 'gif'],
      }],
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
  outputs.value = []
  requestId.value = `media-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
  try {
    await invoke('start_audio_tool', {
      payload: {
        operation: 'media_convert',
        requestId: requestId.value,
        inputs: inputs.value,
        outputDir: outputDir.value,
        format: format.value,
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
        busy.value = false
        progress.value = 100
        const failed = result.failed?.length || 0
        status.value = failed
          ? `${t('factory.failed')} (${failed})`
          : t('factory.completed')
        if (failed) {
          message.warning(status.value)
        } else {
          message.success(status.value)
        }
        requestId.value = ''
        return
      }
      if (payload?.type === 'error') {
        busy.value = false
        status.value = String(payload.payload?.message || 'Conversion failed')
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
    // Browser-only preview has no Tauri event bridge.
  }
})

onBeforeUnmount(() => {
  unlisten?.()
})
</script>

<template>
  <div class="page factory-page">
    <div class="page-header-compact">
      <div>
        <h1>{{ t('factory.title') }}</h1>
        <p>{{ t('factory.subtitle') }}</p>
      </div>
      <n-tag type="info" :bordered="false">FFmpeg</n-tag>
    </div>

    <p class="factory-hint">{{ t('factory.hint') }}</p>

    <div class="factory-grid">
      <n-card :title="t('factory.input')" size="small">
        <div class="factory-picker">
          <n-button secondary :disabled="busy" @click="pickFiles">
            <template #icon><n-icon :component="DocumentOutline" /></template>
            {{ inputs.length ? t('factory.addFiles') : t('factory.chooseFiles') }}
          </n-button>
          <span class="factory-count">{{ inputs.length }}</span>
        </div>
        <n-empty v-if="!inputs.length" :description="t('factory.noFiles')" size="small" />
        <n-list v-else hoverable>
          <n-list-item v-for="path in inputs" :key="path">
            <span class="factory-file" :title="path">{{ fileName(path) }}</span>
            <template #suffix>
              <n-button text :disabled="busy" @click="removeInput(path)">
                <template #icon><n-icon :component="CloseCircleOutline" /></template>
              </n-button>
            </template>
          </n-list-item>
        </n-list>
      </n-card>

      <n-card :title="t('factory.output')" size="small">
        <n-space vertical :size="14">
          <div>
            <label class="factory-label">{{ t('factory.outputDir') }}</label>
            <div class="factory-path">
              <n-input :value="outputDir" readonly :placeholder="t('factory.outputDir')" />
              <n-button secondary :disabled="busy" @click="pickOutput">
                <template #icon><n-icon :component="FolderOpenOutline" /></template>
                {{ t('factory.browse') }}
              </n-button>
            </div>
          </div>

          <div class="factory-row">
            <div>
              <label class="factory-label">{{ t('factory.format') }}</label>
              <n-select v-model:value="format" :options="formatOptions" :disabled="busy" />
            </div>
            <div>
              <label class="factory-label">{{ t('factory.preset') }}</label>
              <n-select v-model:value="preset" :options="presetOptions" :disabled="busy || !isVideoFormat" />
            </div>
          </div>

          <div v-if="isVideoFormat">
            <label class="factory-label">{{ t('factory.crf') }}</label>
            <n-select v-model:value="crf" :options="crfOptions" :disabled="busy" />
          </div>

          <n-button type="primary" size="large" :loading="busy" :disabled="busy" @click="run">
            <template #icon><n-icon :component="SwapHorizontalOutline" /></template>
            {{ t('factory.start') }}
          </n-button>

          <n-progress v-if="busy || progress" type="line" :percentage="progress" :show-indicator="true" />
          <n-alert v-if="status" :type="status.includes('失败') || status.includes('failed') ? 'error' : 'info'" :bordered="false">
            {{ status }}
          </n-alert>
          <div v-if="outputs.length" class="factory-outputs">
            <div class="factory-outputs-head">
              <strong>{{ t('factory.outputs') }}</strong>
              <n-button text @click="openOutput">
                <template #icon><n-icon :component="FolderOpenOutline" /></template>
                {{ t('factory.openFolder') }}
              </n-button>
            </div>
            <n-list hoverable>
              <n-list-item v-for="item in outputs" :key="item.path">
                <span class="factory-file" :title="item.path">{{ fileName(item.path) }}</span>
              </n-list-item>
            </n-list>
          </div>
        </n-space>
      </n-card>
    </div>
  </div>
</template>

<style scoped>
.factory-page { display: flex; flex-direction: column; gap: 12px; }
.factory-hint { margin: 0; color: var(--text-color-3); font-size: 13px; }
.factory-grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 16px; align-items: start; }
.factory-picker { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
.factory-count { color: var(--text-color-3); font-size: 12px; }
.factory-file { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.factory-label { display: block; margin-bottom: 6px; color: var(--text-color-2); font-size: 12px; font-weight: 600; }
.factory-path { display: flex; gap: 8px; align-items: center; }
.factory-row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.factory-outputs { border-top: 1px solid var(--divider-color); padding-top: 12px; }
.factory-outputs-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; }
@media (max-width: 900px) { .factory-grid { grid-template-columns: 1fr; } }
</style>
