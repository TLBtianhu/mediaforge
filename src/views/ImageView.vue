<script setup lang="ts">
import { dropSkippedSteps, precheckPlan, summarizePrecheck } from '../utils/mediaPrecheck.ts'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { convertFileSrc, invoke } from '@tauri-apps/api/core'
import { getCurrentWebview } from '@tauri-apps/api/webview'
import { listen, type UnlistenFn } from '@tauri-apps/api/event'
import { open } from '@tauri-apps/plugin-dialog'
import { useI18n } from 'vue-i18n'
import { useMessage } from 'naive-ui'
import { useRoute } from 'vue-router'
import {
  CloseCircleOutline,
  DocumentOutline,
  FolderOpenOutline,
  SparklesOutline,
  CubeOutline,
} from '@vicons/ionicons5'
import MediaPageHeader from '@/components/media/MediaPageHeader.vue'
import WorkbenchSteps from '@/components/media/WorkbenchSteps.vue'
import ProcessingPlan from '@/components/media/ProcessingPlan.vue'
import WorkbenchCard from '@/components/media/WorkbenchCard.vue'
import { journalJob, journalNow } from '@/utils/operationJournal'
import { useSettingsStore } from '@/stores/settings'

defineOptions({ name: 'ImageView' })

type OutputFile = {
  path: string
  source: string
  mode: string
  size?: number
  info?: {
    device?: string
    input_size?: number[]
    output_size?: number[]
    scale?: number
    boxes?: number
    mask_ratio?: number
    elapsed?: number
  }
}
type WorkerPayload = {
  operation?: string
  fraction?: number
  completed?: number
  total?: number
  current?: string
  message?: string
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
const modelDir = ref('')
const busy = ref(false)
const progress = ref(0)
const status = ref('')
const failedCount = ref(0)
const outputs = ref<OutputFile[]>([])
const compare = ref(50)
const requestId = ref('')
const journalCreatedAt = ref(0)
type PlanStep = { capability: string; bindings: Record<string, string>; params: Record<string, unknown> }
const planStep = ref<PlanStep>({
  capability: 'watermark_removal',
  bindings: { detector: 'media:watermark-yolo11x', inpainter: 'media:lama' },
  params: { conf: 0.25, expand: 8, detectMode: 'fixed' },
})
const planAvailable = ref(true)
let unlisten: UnlistenFn | undefined
let unlistenDrop: UnlistenFn | undefined
const dragging = ref(false)

const pipelineSummary = computed(() => t(`plan.capabilities.${planStep.value.capability}`))
const pipelineSteps = computed(() => [planStep.value])
const firstOutput = computed(() => outputs.value[0] || null)
const resultSrc = computed(() => firstOutput.value ? convertFileSrc(firstOutput.value.path) : '')
const originalSrc = computed(() => firstOutput.value ? convertFileSrc(firstOutput.value.source) : '')
const resultInfo = computed(() => {
  const info = firstOutput.value?.info
  if (!info) return []
  const rows: Array<{ label: string; value: string }> = []
  if (info.output_size) rows.push({ label: 'Output', value: `${info.output_size[0]}×${info.output_size[1]}` })
  if (info.scale) rows.push({ label: 'Scale', value: `${info.scale}×` })
  if (info.boxes !== undefined) rows.push({ label: 'Boxes', value: String(info.boxes) })
  if (info.mask_ratio !== undefined) rows.push({ label: 'Mask', value: `${(info.mask_ratio * 100).toFixed(1)}%` })
  if (info.elapsed !== undefined) rows.push({ label: 'Time', value: `${info.elapsed}s` })
  if (info.device) rows.push({ label: 'Device', value: info.device })
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
      filters: [{ name: 'Images', extensions: ['jpg', 'jpeg', 'png', 'webp', 'bmp', 'tif', 'tiff'] }],
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
  status.value = String(payload.current || t('image.title'))
}

function journal(status: 'running' | 'completed' | 'failed' | 'cancelled', fraction: number, text: string, result: Record<string, unknown> | null = null) {
  void journalJob({
    jobId: requestId.value,
    command: 'media_image',
    source: 'manual',
    status,
    fraction,
    message: text,
    createdAt: journalCreatedAt.value,
    payload: { operation: 'media_image', inputs: inputs.value, outputDir: outputDir.value },
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
  // 跑之前先问后端：输入可用吗、目标比原图大吗、图上有没有水印
  const precheck = summarizePrecheck(await precheckPlan(pipelineSteps.value, inputs.value))
  if (!precheck.runnable) {
    message.error(precheck.notices.join('；') || t('factory.processing'))
    return
  }
  const stepsToRun = dropSkippedSteps(pipelineSteps.value, precheck.dropped)
  if (precheck.notices.length) {
    if (!stepsToRun.length) {
      // 唯一的步骤被预检判死：直接说清楚，不让用户等一轮失败
      message.warning(precheck.notices.join('；'))
      return
    }
    message.warning(precheck.notices.join('；'))
  }
  busy.value = true
  progress.value = 0
  status.value = t('factory.processing')
  failedCount.value = 0
  outputs.value = []
  requestId.value = `image-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
  journalCreatedAt.value = journalNow()
  journal('running', 0, status.value)
  try {
    await invoke('start_audio_tool', {
      payload: {
        operation: 'media_image',
        requestId: requestId.value,
        inputs: inputs.value,
        outputDir: outputDir.value,
        steps: stepsToRun,
        modelDir: modelDir.value,
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
  // 拖入图片或文件夹：目录会被 Rust 侧递归展开
  if (typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window) {
    try {
      unlistenDrop = await getCurrentWebview().onDragDropEvent(async (event) => {
        const type = event.payload.type
        if (type === 'over' || type === 'enter') {
          dragging.value = true
          return
        }
        dragging.value = false
        if (type !== 'drop') return
        const paths = (event.payload as { paths?: string[] }).paths || []
        if (!paths.length) return
        const result = await invoke<{ files: string[]; warnings: string[] }>('scan_image_paths', { paths })
        const files = result.files || []
        if (files.length) {
          addInputs(files)
          message.success(t('factory.fileCount', { count: files.length }))
        } else {
          message.warning(t('factory.noFiles'))
        }
      })
    } catch {
      // 浏览器预览里没有原生拖放，静默降级
    }
  }

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
        failedCount.value = result.failed?.length || 0
        busy.value = false
        progress.value = 100
        compare.value = 50
        status.value = failedCount.value
          ? `${t('factory.failed')} (${failedCount.value})`
          : t('factory.completed')
        journal(failedCount.value ? 'failed' : 'completed', 1, status.value, result as Record<string, unknown>)
        if (failedCount.value) message.warning(status.value)
        else message.success(status.value)
        requestId.value = ''
        return
      }
      if (payload?.type === 'error') {
        busy.value = false
        status.value = String(payload.payload?.message || 'Image processing failed')
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
    // Browser preview has no Tauri event bridge.
  }
})

onBeforeUnmount(() => {
  unlistenDrop?.()
  unlisten?.()
})
const workbenchSteps = computed(() => [
  { title: t('factory.input'), text: t('pipeline.stepInput') },
  { title: t('plan.title'), text: t('pipeline.stepCompose') },
  { title: t('image.compare'), text: pipelineSummary.value },
])
</script>

<template>
  <div class="page mf-page image-page" data-tone="violet">
    <MediaPageHeader
      :title="t('image.title')"
      :subtitle="t('image.subtitle')"
      :icon="SparklesOutline"
      eyebrow="IMAGE PROCESSING"
      tone="violet"
    >
      <router-link to="/queue">
        <n-button quaternary size="small">{{ t('agent.queue') }}</n-button>
      </router-link>
      <router-link to="/models?modality=image">
        <n-button quaternary size="small">{{ t('nav.models') }}</n-button>
      </router-link>
      <n-tag type="success" :bordered="false">{{ pipelineSummary }}</n-tag>
    </MediaPageHeader>

    <WorkbenchSteps :steps="workbenchSteps" />

    <div class="media-workbench">
      <WorkbenchCard class="workbench-input" index="01" :title="t('factory.input')" :subtitle="t('plan.inputHint')">
        <div v-if="!inputs.length" class="mf-dropzone" @click="pickFiles">
          <span class="mf-dropzone__icon">
            <n-icon :component="DocumentOutline" :size="26" />
          </span>
          <span class="mf-dropzone__title">{{ t('factory.chooseFiles') }}</span>
          <span class="mf-dropzone__hint">{{ t('image.subtitle') }}</span>
          <div class="mf-dropzone__actions">
            <n-button type="primary" :disabled="busy" @click.stop="pickFiles">{{ t('factory.chooseFiles') }}</n-button>
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
          <n-button text class="mf-add-more" :disabled="busy" @click="pickFiles">+ {{ t('factory.addFiles') }}</n-button>
        </template>
      </WorkbenchCard>

      <WorkbenchCard class="workbench-output" index="02" :title="t('factory.output')" :subtitle="t('plan.outputHint')">
        <n-space vertical :size="12">
          <div>
            <label class="image-label">{{ t('factory.outputDir') }}</label>
            <div class="image-path">
              <n-input :value="outputDir" readonly :placeholder="t('factory.outputDir')" />
              <n-button secondary :disabled="busy" @click="pickOutput">
                <template #icon><n-icon :component="FolderOpenOutline" /></template>
                {{ t('factory.browse') }}
              </n-button>
            </div>
          </div>
          <div>
            <label class="image-label">{{ t('image.modelDir') }}</label>
            <div class="image-path">
              <n-input v-model:value="modelDir" :placeholder="t('image.modelDirPlaceholder')" />
              <n-button secondary :disabled="busy" @click="pickModelDir">
                <template #icon><n-icon :component="FolderOpenOutline" /></template>
                {{ t('factory.browse') }}
              </n-button>
            </div>
            <p class="image-hint">{{ t('image.modelDirHint') }}</p>
          </div>
        </n-space>
      </WorkbenchCard>

      <WorkbenchCard class="workbench-plan" :icon="CubeOutline" :title="t('plan.title')" :subtitle="t('pipeline.stepCompose')">
        <n-space vertical :size="12">
          <ProcessingPlan v-model="planStep" modality="image" :busy="busy" :model-dir="modelDir" @availability="planAvailable = $event" />
          <n-button type="primary" size="large" class="mf-primary-action" :loading="busy" :disabled="busy || !planAvailable" @click="run">
            <template #icon><n-icon :component="SparklesOutline" /></template>
            {{ t('factory.start') }}
          </n-button>
          <n-progress v-if="busy || progress" type="line" :percentage="progress" />
          <n-alert v-if="status" :type="failedCount ? 'error' : 'info'" :bordered="false">{{ status }}</n-alert>
          <div v-if="outputs.length" class="image-outputs">
            <div class="image-outputs-head">
              <strong>{{ t('factory.outputs') }}</strong>
              <n-button text @click="openOutput">
                <template #icon><n-icon :component="FolderOpenOutline" /></template>
                {{ t('factory.openFolder') }}
              </n-button>
            </div>
            <n-list hoverable>
              <n-list-item v-for="item in outputs" :key="item.path">
                <span class="image-file" :title="item.path">{{ fileName(item.path) }}</span>
              </n-list-item>
            </n-list>
          </div>
        </n-space>
      </WorkbenchCard>

    <WorkbenchCard v-if="firstOutput" class="workbench-result" index="03" :title="t('image.compare')">
      <div class="image-compare">
        <img class="compare-base" :src="resultSrc" :alt="t('image.after')" />
        <img class="compare-top" :src="originalSrc" :alt="t('image.before')" :style="{ clipPath: `inset(0 ${100 - compare}% 0 0)` }" />
        <span class="compare-label compare-label--left">{{ t('image.before') }}</span>
        <span class="compare-label compare-label--right">{{ t('image.after') }}</span>
      </div>
      <n-slider v-model:value="compare" :min="0" :max="100" class="compare-slider" />
      <div class="image-info">
        <n-tag v-for="row in resultInfo" :key="row.label" size="small" :bordered="false">{{ row.label }}: {{ row.value }}</n-tag>
      </div>
    </WorkbenchCard>

  </div>
  </div>
</template>

<style scoped>
.image-page { display: flex; flex-direction: column; gap: 12px; }
.image-picker { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
.image-count { color: var(--text-color-3); font-size: 12px; }
.image-file { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.image-label { display: block; margin-bottom: 6px; color: var(--on-surface-muted); font-size: 12px; font-weight: 600; }
.image-hint { margin: 4px 0 0; color: var(--on-surface-muted); font-size: 11px; }
.image-path { display: flex; gap: 8px; align-items: center; }
.image-row { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
.image-outputs { border-top: 1px solid var(--divider-color); padding-top: 10px; }
.image-outputs-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px; }
.image-compare { position: relative; overflow: hidden; border-radius: 8px; background: #111; }
.compare-base, .compare-top { display: block; width: 100%; height: auto; }
.compare-top { position: absolute; inset: 0; }
.compare-label { position: absolute; top: 8px; padding: 2px 8px; border-radius: 10px; background: rgba(0, 0, 0, 0.55); color: #fff; font-size: 11px; }
.compare-label--left { left: 8px; }
.compare-label--right { right: 8px; }
.compare-slider { margin-top: 10px; }
.image-info { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
@media (max-width: 900px) { .image-row { grid-template-columns: 1fr 1fr; } }
</style>
