<script setup lang="ts">
import { pickPreviewFiles, revealPath } from '@/api/native'
import { useMediaDrop } from '../composables/useMediaDrop'
import { computed, onMounted, ref } from 'vue'

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
import WorkbenchSteps from '@/components/media/WorkbenchSteps.vue'
import WorkbenchCard from '@/components/media/WorkbenchCard.vue'

import { useSettingsStore } from '@/stores/settings'
import { useMediaJob } from '@/composables/useMediaJob'

defineOptions({ name: 'FactoryView' })

type OutputFile = { path: string; kind?: string; format?: string; size?: number }

const { t } = useI18n()
const message = useMessage()
const route = useRoute()
const settings = useSettingsStore()

const inputs = ref<string[]>([])
const outputDir = ref(settings.outputDir || '')
const format = ref('mp4')
const crf = ref(20)
const preset = ref('medium')

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
const { job, busy, progress, status, outputs, start: startJob } = useMediaJob<OutputFile>(
  'media_convert', {
    queued: () => t('tasks.waitingForResources'), running: () => t('factory.processing'), completed: () => t('factory.completed'),
    failed: () => t('factory.failed'), cancelled: () => t('tasks.cancelSuccess'),
  }, message, payload => {
    if (Array.isArray(payload.inputs)) inputs.value = payload.inputs as string[]
    if (typeof payload.outputDir === 'string') outputDir.value = payload.outputDir
    if (typeof payload.format === 'string') format.value = payload.format
    if (typeof payload.crf === 'number') crf.value = payload.crf
    if (typeof payload.preset === 'string') preset.value = payload.preset
  })

function fileName(path: string) {
  return path.split(/[\\/]/).filter(Boolean).pop() || path
}

function addInputs(paths: string[]) {
  const current = new Set(inputs.value)
  inputs.value = [...inputs.value, ...paths.filter(path => path && !current.has(path))]
}

useMediaDrop({
  onFiles: paths => addInputs(paths),
  onDirectory: (_zone, folder) => { outputDir.value = folder },
  scanImages: false,
})

async function pickFiles() {
  try {
    addInputs(await pickPreviewFiles('library'))
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

async function run() {
  if (busy.value) return
  if (!inputs.value.length) {
    message.warning(t('factory.selectFirst'))
    return
  }
  if (!outputDir.value) {
    message.warning(t('factory.selectOutput'))
    return
  }
  await startJob({
    operation: 'media_convert', inputs: inputs.value, outputDir: outputDir.value,
    format: format.value, crf: crf.value, preset: preset.value,
  })
}

async function openOutput() {
  const path = String(job.value?.payload?.outputDir || outputDir.value)
  if (!path) return
  try {
    await revealPath(path)
  } catch (error) {
    message.error(error instanceof Error ? error.message : String(error))
  }
}

onMounted(async () => {
  const requested = route.query.asset
  if (typeof requested === 'string' && requested) addInputs([requested])
})

const workbenchSteps = computed(() => [{ title: t('factory.input'), text: t('factory.chooseFiles') }, { title: t('factory.output'), text: t('factory.hint') }, { title: t('factory.start'), text: 'FFmpeg · local' }])
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
      <router-link to="/queue">
        <n-button quaternary size="small">{{ t('agent.queue') }}</n-button>
      </router-link>
      <n-tag type="info" :bordered="false">FFmpeg</n-tag>
    </MediaPageHeader>

    <WorkbenchSteps :steps="workbenchSteps" />

    <div class="media-workbench">
      <WorkbenchCard class="workbench-input" index="01" :title="t('factory.input')" :subtitle="t('factory.chooseFiles')">
        <template #actions><n-tag size="small" :bordered="false">{{ t('factory.fileCount', { count: inputs.length }) }}</n-tag></template>
        <div v-if="!inputs.length" class="mf-dropzone" @click="pickFiles">
          <span class="mf-dropzone__icon"><n-icon :component="DocumentOutline" :size="26" /></span>
          <span class="mf-dropzone__title">{{ t('factory.chooseFiles') }}</span>
          <span class="mf-dropzone__hint">{{ t('factory.hint') }}</span>
          <div class="mf-dropzone__actions">
            <n-button type="primary" :disabled="busy" @click.stop="pickFiles">{{ t('factory.chooseFiles') }}</n-button>
          </div>
        </div>
        <template v-else>
          <div class="mf-file-list">
            <div v-for="path in inputs" :key="path" class="mf-file-row">
              <n-icon :component="DocumentOutline" size="18" />
              <span class="mf-file-row__name" :title="path">{{ fileName(path) }}</span>
              <n-button text :disabled="busy" @click="removeInput(path)"><template #icon><n-icon :component="CloseCircleOutline" /></template></n-button>
            </div>
          </div>
          <n-button text class="mf-add-more" :disabled="busy" @click="pickFiles">+ {{ t('factory.addFiles') }}</n-button>
        </template>
      </WorkbenchCard>

      <WorkbenchCard class="workbench-output" index="02" :title="t('factory.output')" :subtitle="t('factory.outputDir')">
        <div class="mf-form-section">
          <label class="mf-form-label">{{ t('factory.outputDir') }}</label>
          <div data-drop-zone="outputDir" class="factory-path">
            <n-input :value="outputDir" readonly :placeholder="t('factory.outputDir')" />
            <n-button secondary :disabled="busy" @click="pickOutput"><template #icon><n-icon :component="FolderOpenOutline" /></template>{{ t('factory.browse') }}</n-button>
          </div>
        </div>
      </WorkbenchCard>

      <WorkbenchCard class="workbench-plan" :icon="SwapHorizontalOutline" :title="t('plan.title')" :subtitle="t('factory.hint')">
        <n-space vertical :size="14">
          <div class="mf-form-section">
            <div class="mf-form-row mf-form-row--two">
              <div><label class="mf-form-label">{{ t('factory.format') }}</label><n-select v-model:value="format" :options="formatOptions" :disabled="busy" /></div>
              <div><label class="mf-form-label">{{ t('factory.preset') }}</label><n-select v-model:value="preset" :options="presetOptions" :disabled="busy || !isVideoFormat" /></div>
            </div>
          </div>
          <div v-if="isVideoFormat" class="mf-form-section">
            <label class="mf-form-label">{{ t('factory.crf') }}</label>
            <n-select v-model:value="crf" :options="crfOptions" :disabled="busy" />
          </div>
          <n-button type="primary" size="large" class="mf-primary-action" :loading="busy" :disabled="busy" @click="run">
            <template #icon><n-icon :component="SwapHorizontalOutline" /></template>{{ t('factory.start') }}
          </n-button>
          <n-progress v-if="busy || progress" type="line" :percentage="progress" :show-indicator="true" />
          <n-alert v-if="status" :type="status.includes('失败') || status.includes('failed') ? 'error' : 'info'" :bordered="false">{{ status }}</n-alert>
          <div v-if="outputs.length" class="factory-outputs">
            <div class="factory-outputs-head"><strong>{{ t('factory.outputs') }}</strong><n-button text @click="openOutput"><template #icon><n-icon :component="FolderOpenOutline" /></template>{{ t('factory.openFolder') }}</n-button></div>
            <div class="mf-file-list"><div v-for="item in outputs" :key="item.path" class="mf-file-row"><span class="mf-file-row__name" :title="item.path">{{ fileName(item.path) }}</span></div></div>
          </div>
        </n-space>
      </WorkbenchCard>
    </div>

  </div>
</template>

<style scoped>
.factory-path { display: flex; gap: 8px; align-items: center; }
.factory-outputs { border-top: 1px solid var(--outline); padding-top: 12px; }
.factory-outputs-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; }
</style>
