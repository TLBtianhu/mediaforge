<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { invoke } from '@tauri-apps/api/core'
import { listen, type UnlistenFn } from '@tauri-apps/api/event'
import { open } from '@tauri-apps/plugin-dialog'
import { useI18n } from 'vue-i18n'
import { useMessage } from 'naive-ui'
import { GitNetworkOutline, CloudUploadOutline, DocumentOutline, CloseCircleOutline, FolderOpenOutline, PlayOutline } from '@vicons/ionicons5'
import MediaPageHeader from '@/components/media/MediaPageHeader.vue'
import WorkbenchCard from '@/components/media/WorkbenchCard.vue'
import WorkbenchSteps from '@/components/media/WorkbenchSteps.vue'
import ProcessingPlan from '@/components/media/ProcessingPlan.vue'

defineOptions({ name: 'MediaWorkflowView' })

type PlanStep = { capability: string; bindings: Record<string, string>; params: Record<string, unknown> }
type WorkflowOutput = { path: string; source: string; steps?: string[]; size?: number }
type WorkerEvent = { type?: string; requestId?: string | null; payload?: Record<string, unknown> }

const { t } = useI18n()
const message = useMessage()
const inputs = ref<string[]>([])
const outputDir = ref('')
const modality = ref<'image' | 'video'>('image')
const modelDir = ref('')
const steps = ref<PlanStep[]>([{ capability: 'upscale', bindings: { upscaler: 'media:real-esrgan-x2' }, params: { scale: 2 } }])
const busy = ref(false)
const progress = ref(0)
const status = ref('')
const outputs = ref<WorkflowOutput[]>([])
let requestId = ''
let unlisten: UnlistenFn | undefined

const stepsSummary = computed(() => steps.value.map((step, index) => ({
  title: `${index + 1}. ${t(`plan.capabilities.${step.capability}`)}`,
  text: step.bindings[Object.keys(step.bindings)[0]] || step.capability,
})))
const workflowSteps = computed(() => [
  { title: t('mediaWorkflow.input'), text: t('mediaWorkflow.inputHint') },
  { title: t('plan.title'), text: t('mediaWorkflow.stepHint') },
  { title: t('mediaWorkflow.result'), text: t('mediaWorkflow.resultHint') },
])

function addStep() {
  steps.value.push({ capability: 'upscale', bindings: { upscaler: 'media:real-esrgan-x2' }, params: { scale: 2 } })
}
function removeStep(index: number) {
  if (steps.value.length <= 1) return
  steps.value.splice(index, 1)
}
function updateStep(index: number, value: PlanStep) {
  steps.value[index] = value
}
function fileName(path: string) {
  return path.split(/[\\/]/).filter(Boolean).pop() || path
}
function addInputs(paths: string[]) {
  const seen = new Set(inputs.value)
  inputs.value = [...inputs.value, ...paths.filter(path => path && !seen.has(path))]
}
async function pickInputs() {
  const selected = await open({ multiple: true, filters: [{ name: modality.value === 'image' ? 'Images' : 'Videos', extensions: modality.value === 'image' ? ['png', 'jpg', 'jpeg', 'webp', 'gif'] : ['mp4', 'mkv', 'mov', 'webm', 'avi'] }] })
  if (typeof selected === 'string') addInputs([selected])
  else if (Array.isArray(selected)) addInputs(selected)
}
async function pickOutput() {
  const selected = await open({ directory: true, multiple: false })
  if (typeof selected === 'string') outputDir.value = selected
}
async function pickModelDir() {
  const selected = await open({ directory: true, multiple: false })
  if (typeof selected === 'string') modelDir.value = selected
}
function onProgress(payload: Record<string, unknown>) {
  progress.value = Math.round(Number(payload.fraction || 0) * 100)
  status.value = String(payload.current || status.value)
}
async function start() {
  if (!inputs.value.length) {
    message.warning(t('mediaWorkflow.noInput'))
    return
  }
  if (!outputDir.value) {
    message.warning(t('mediaWorkflow.noOutput'))
    return
  }
  busy.value = true
  progress.value = 0
  outputs.value = []
  requestId = `media-workflow-${Date.now()}`
  status.value = t('mediaWorkflow.starting')
  try {
    await invoke('start_audio_tool', {
      payload: {
        operation: 'media_workflow',
        requestId,
        modality: modality.value,
        inputs: inputs.value,
        outputDir: outputDir.value,
        modelDir: modelDir.value,
        steps: steps.value,
      },
    })
  } catch (error) {
    busy.value = false
    status.value = error instanceof Error ? error.message : String(error)
    message.error(status.value)
  }
}
onMounted(async () => {
  unlisten = await listen<WorkerEvent>('pymss://worker-event', event => {
    const payload = event.payload
    const id = payload?.requestId || payload?.taskId || null
    if (!requestId || id !== requestId) return
    if (payload?.type === 'audio_tool_progress' && payload.payload) {
      onProgress(payload.payload)
      return
    }
    if (payload?.type === 'audio_tool_result' && payload.payload) {
      const result = payload.payload
      outputs.value = Array.isArray(result.outputs) ? result.outputs as WorkflowOutput[] : []
      busy.value = false
      progress.value = 100
      status.value = outputs.value.length ? t('mediaWorkflow.completed') : t('mediaWorkflow.failed')
      if (outputs.value.length) message.success(status.value)
      else message.warning(status.value)
    }
  })
})
onBeforeUnmount(() => unlisten?.())
</script>

<template>
  <div class="page mf-page media-workflow-page" data-tone="violet">
    <MediaPageHeader
      :title="t('mediaWorkflow.title')"
      :subtitle="t('mediaWorkflow.subtitle')"
      :icon="GitNetworkOutline"
      eyebrow="MEDIA WORKFLOWS"
      tone="violet"
    >
      <n-tag type="info" :bordered="false">{{ modality === 'image' ? 'IMAGE' : 'VIDEO' }}</n-tag>
    </MediaPageHeader>

    <WorkbenchSteps :steps="workflowSteps" />

    <div class="media-workbench">
      <WorkbenchCard class="workbench-input" index="01" :title="t('mediaWorkflow.input')" :subtitle="t('mediaWorkflow.inputHint')">
        <div v-if="!inputs.length" class="mf-dropzone" @click="pickInputs">
          <span class="mf-dropzone__icon"><n-icon :component="CloudUploadOutline" :size="26" /></span>
          <span class="mf-dropzone__title">{{ t('mediaWorkflow.chooseInput') }}</span>
          <span class="mf-dropzone__hint">{{ t('mediaWorkflow.inputHint') }}</span>
          <n-button type="primary" :disabled="busy" @click.stop="pickInputs">{{ t('mediaWorkflow.chooseInput') }}</n-button>
        </div>
        <template v-else>
          <div class="mf-file-list">
            <div v-for="path in inputs" :key="path" class="mf-file-row">
              <n-icon :component="DocumentOutline" size="18" />
              <span class="mf-file-row__name" :title="path">{{ fileName(path) }}</span>
              <n-button text :disabled="busy" @click="inputs = inputs.filter(item => item !== path)">
                <template #icon><n-icon :component="CloseCircleOutline" /></template>
              </n-button>
            </div>
          </div>
          <n-button text class="mf-add-more" :disabled="busy" @click="pickInputs">+ {{ t('mediaWorkflow.addInput') }}</n-button>
        </template>
      </WorkbenchCard>

      <WorkbenchCard class="workbench-output" index="02" :title="t('mediaWorkflow.output')" :subtitle="t('mediaWorkflow.outputHint')">
        <n-space vertical :size="12">
          <div>
            <label class="mf-form-label">{{ t('mediaWorkflow.modality') }}</label>
            <n-select v-model:value="modality" :options="[{ label: 'Image', value: 'image' }, { label: 'Video', value: 'video' }]" :disabled="busy" />
          </div>
          <div>
            <label class="mf-form-label">{{ t('mediaWorkflow.outputDir') }}</label>
            <div class="media-workflow-path">
              <n-input :value="outputDir" readonly :placeholder="t('mediaWorkflow.outputDir')" />
              <n-button secondary :disabled="busy" @click="pickOutput"><template #icon><n-icon :component="FolderOpenOutline" /></template>{{ t('factory.browse') }}</n-button>
            </div>
          </div>
          <div>
            <label class="mf-form-label">{{ t('image.modelDir') }}</label>
            <div class="media-workflow-path">
              <n-input v-model:value="modelDir" :placeholder="t('mediaWorkflow.modelDirHint')" />
              <n-button secondary :disabled="busy" @click="pickModelDir"><template #icon><n-icon :component="FolderOpenOutline" /></template>{{ t('factory.browse') }}</n-button>
            </div>
          </div>
        </n-space>
      </WorkbenchCard>

      <WorkbenchCard class="workbench-plan" :icon="GitNetworkOutline" :title="t('plan.title')" :subtitle="t('mediaWorkflow.stepHint')">
        <n-space vertical :size="12">
          <div v-for="(step, index) in steps" :key="index" class="media-workflow-step">
            <div class="media-workflow-step__head">
              <strong>{{ t('mediaWorkflow.step', { index: index + 1 }) }}</strong>
              <n-button text :disabled="busy || steps.length <= 1" @click="removeStep(index)">{{ t('mediaWorkflow.removeStep') }}</n-button>
            </div>
            <ProcessingPlan :model-value="step" :modality="modality" :busy="busy" @update:model-value="value => updateStep(index, value)" />
          </div>
          <n-button secondary :disabled="busy" @click="addStep">+ {{ t('mediaWorkflow.addStep') }}</n-button>
          <n-button type="primary" size="large" class="mf-primary-action" :loading="busy" :disabled="busy" @click="start">
            <template #icon><n-icon :component="PlayOutline" /></template>
            {{ t('mediaWorkflow.start') }}
          </n-button>
          <n-progress v-if="busy || progress" type="line" :percentage="progress" />
          <n-alert v-if="status" :type="outputs.length ? 'info' : 'warning'" :bordered="false">{{ status }}</n-alert>
        </n-space>
      </WorkbenchCard>

      <WorkbenchCard v-if="outputs.length" class="workbench-result" index="03" :title="t('mediaWorkflow.result')">
        <div class="mf-file-list">
          <div v-for="item in outputs" :key="item.path" class="mf-file-row">
            <span class="mf-file-row__name" :title="item.path">{{ fileName(item.path) }}</span>
            <n-tag size="small" :bordered="false">{{ (item.steps || []).join(' → ') }}</n-tag>
          </div>
        </div>
      </WorkbenchCard>
    </div>
  </div>
</template>

<style scoped>
.media-workflow-page { display: flex; flex-direction: column; gap: 12px; }
.media-workflow-path { display: flex; gap: 8px; align-items: center; }
.media-workflow-step { display: grid; gap: 8px; padding: 12px 0; border-top: 1px solid var(--outline); }
.media-workflow-step__head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.media-workflow-step__head strong { color: var(--on-surface-heading); font-size: 12px; }
</style>
