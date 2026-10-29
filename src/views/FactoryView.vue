<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { invoke } from '@tauri-apps/api/core'
import { listen, type UnlistenFn } from '@tauri-apps/api/event'
import { open } from '@tauri-apps/plugin-dialog'
import { useI18n } from 'vue-i18n'
import { useMessage } from 'naive-ui'
import { useRoute } from 'vue-router'
import {
  CloseCircleOutline,
  DocumentOutline,
  FolderOpenOutline,
  SwapHorizontalOutline,
} from '@vicons/ionicons5'
import MediaPageHeader from '@/components/media/MediaPageHeader.vue'
import { journalJob, journalNow } from '@/utils/operationJournal'
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
const route = useRoute()
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
const journalCreatedAt = ref(0)
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

function journal(status: 'running' | 'completed' | 'failed' | 'cancelled', fraction: number, text: string, result: Record<string, unknown> | null = null) {
  void journalJob({
    jobId: requestId.value,
    command: 'media_convert',
    source: 'manual',
    status,
    fraction,
    message: text,
    createdAt: journalCreatedAt.value,
    payload: { operation: 'media_convert', inputs: inputs.value, outputDir: outputDir.value, format: format.value },
    result,
    error: status === 'failed' ? text : null,
  })
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
  journalCreatedAt.value = journalNow()
  journal('running', 0, status.value)
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
    journal('failed', 0, status.value)
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
        journal('running', Number(payload.payload.fraction || 0), String(payload.payload.current || status.value))
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
        journal(failed ? 'failed' : 'completed', 1, status.value, result as Record<string, unknown>)
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
        journal('failed', 0, status.value)
        message.error(status.value)
        requestId.value = ''
      }
      if (payload?.type === 'task_cancelled') {
        busy.value = false
        status.value = 'Cancelled'
        journal('cancelled', 0, status.value)
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
  <div class="page mf-page" data-tone="blue">
    <MediaPageHeader
      :title="t('factory.title')"
      :subtitle="t('factory.subtitle')"
      :icon="SwapHorizontalOutline"
      eyebrow="FORMAT FACTORY"
      tone="blue"
    >
      <router-link to="/agent">
        <n-button quaternary size="small">{{ t('agent.queue') }}</n-button>
      </router-link>
      <n-tag type="info" :bordered="false">FFmpeg</n-tag>
    </MediaPageHeader>

    <div class="mf-step-strip mf-step-strip--top">
      <div class="mf-step">
        <span class="mf-step__num">01</span>
        <div><div class="mf-step__title">{{ t('factory.input') }}</div><div class="mf-step__text">{{ t('factory.chooseFiles') }}</div></div>
      </div>
      <div class="mf-step">
        <span class="mf-step__num">02</span>
        <div><div class="mf-step__title">{{ t('factory.output') }}</div><div class="mf-step__text">{{ t('factory.hint') }}</div></div>
      </div>
      <div class="mf-step">
        <span class="mf-step__num">03</span>
        <div><div class="mf-step__title">{{ t('factory.start') }}</div><div class="mf-step__text">FFmpeg · local</div></div>
      </div>
    </div>

    <div class="mf-page-grid">
      <section class="mf-panel mf-panel--input">
        <div class="mf-panel__head">
          <span class="mf-panel__title">{{ t('factory.input') }}</span>
          <span class="mf-panel__meta">{{ t('factory.fileCount', { count: inputs.length }) }}</span>
        </div>
        <div v-if="!inputs.length" class="mf-dropzone" @click="pickFiles">
          <span class="mf-dropzone__icon">
            <n-icon :component="DocumentOutline" :size="26" />
          </span>
          <span class="mf-dropzone__title">{{ t('factory.chooseFiles') }}</span>
          <span class="mf-dropzone__hint">{{ t('factory.hint') }}</span>
          <div class="mf-dropzone__actions">
            <n-button type="primary" :disabled="busy" @click.stop="pickFiles">
              {{ t('factory.chooseFiles') }}
            </n-button>
          </div>
        </div>
        <template v-else>
          <div class="mf-file-list">
            <div v-for="path in inputs" :key="path" class="mf-file-row">
              <n-icon :component="DocumentOutline" size="18" />
              <span class="mf-file-row__name" :title="path">{{ fileName(path) }}</span>
              <n-button text :disabled="busy" @click="removeInput(path)">
                <template #icon><n-icon :component="CloseCircleOutline" /></template>
              </n-button>
            </div>
          </div>
          <n-button text class="mf-add-more" :disabled="busy" @click="pickFiles">
            + {{ t('factory.addFiles') }}
          </n-button>
        </template>
      </section>

      <section class="mf-panel">
        <div class="mf-panel__head">
          <span class="mf-panel__title">{{ t('factory.output') }}</span>
          <n-tag size="small" :bordered="false" type="info">local</n-tag>
        </div>
        <n-space vertical :size="14">
          <div class="mf-form-section">
            <label class="mf-form-label">{{ t('factory.outputDir') }}</label>
            <div class="factory-path">
              <n-input :value="outputDir" readonly :placeholder="t('factory.outputDir')" />
              <n-button secondary :disabled="busy" @click="pickOutput">
                <template #icon><n-icon :component="FolderOpenOutline" /></template>
                {{ t('factory.browse') }}
              </n-button>
            </div>
          </div>

          <div class="mf-form-section">
            <div class="mf-form-row mf-form-row--two">
              <div>
                <label class="mf-form-label">{{ t('factory.format') }}</label>
                <n-select v-model:value="format" :options="formatOptions" :disabled="busy" />
              </div>
              <div>
                <label class="mf-form-label">{{ t('factory.preset') }}</label>
                <n-select v-model:value="preset" :options="presetOptions" :disabled="busy || !isVideoFormat" />
              </div>
            </div>
          </div>

          <div v-if="isVideoFormat" class="mf-form-section">
            <label class="mf-form-label">{{ t('factory.crf') }}</label>
            <n-select v-model:value="crf" :options="crfOptions" :disabled="busy" />
          </div>

          <n-button type="primary" size="large" class="mf-primary-action" :loading="busy" :disabled="busy" @click="run">
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
            <div class="mf-file-list">
              <div v-for="item in outputs" :key="item.path" class="mf-file-row">
                <span class="mf-file-row__name" :title="item.path">{{ fileName(item.path) }}</span>
              </div>
            </div>
          </div>
        </n-space>
      </section>
    </div>

  </div>
</template>

<style scoped>
.factory-path { display: flex; gap: 8px; align-items: center; }
.factory-outputs { border-top: 1px solid var(--divider-color); padding-top: 12px; }
.factory-outputs-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; }
</style>
