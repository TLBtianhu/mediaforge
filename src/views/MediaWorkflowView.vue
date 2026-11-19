<script setup lang="ts">
import { pickPreviewFiles } from '@/api/native'
import { useMediaDrop } from '../composables/useMediaDrop'
import { computed, ref } from 'vue'
import { open } from '@tauri-apps/plugin-dialog'
import { useI18n } from 'vue-i18n'
import { useMessage } from 'naive-ui'
import { GitNetworkOutline, CloudUploadOutline, DocumentOutline, CloseCircleOutline, FolderOpenOutline, PlayOutline } from '@vicons/ionicons5'
import MediaPageHeader from '@/components/media/MediaPageHeader.vue'
import WorkbenchCard from '@/components/media/WorkbenchCard.vue'
import WorkbenchSteps from '@/components/media/WorkbenchSteps.vue'
import ProcessingPlan from '@/components/media/ProcessingPlan.vue'
import MediaWorkflowPresets from '@/components/media/MediaWorkflowPresets.vue'
import { buildMediaWorkflowPayload } from '@/features/media/workflowPayload'
import { useMediaJob } from '@/composables/useMediaJob'

defineOptions({ name: 'MediaWorkflowView' })

type PlanStep = { capability: string; bindings: Record<string, string>; params: Record<string, unknown> }
type WorkflowOutput = { path: string; source: string; steps?: string[]; size?: number }

const { t } = useI18n()
const message = useMessage()
const inputs = ref<string[]>([])
const outputDir = ref('')
const modality = ref<'image' | 'video'>('image')
const modelDir = ref('')
const steps = ref<PlanStep[]>([{ capability: 'upscale', bindings: { upscaler: 'media:real-esrgan-x2' }, params: { scale: 2 } }])
const maskPath = ref('')
const stepAvailability = ref<boolean[]>([])
const planAvailable = computed(() => steps.value.length > 0 && !stepAvailability.value.some(value => value === false))
type PresetDefinition = { version: 1; modality: 'image' | 'video'; steps: PlanStep[] }
const presetDefinition = computed<PresetDefinition>(() => ({ version: 1, modality: modality.value, steps: steps.value }))
function applyPreset(definition: PresetDefinition) {
  if (busy.value) return
  modality.value = definition.modality
  steps.value = JSON.parse(JSON.stringify(definition.steps)) as PlanStep[]
  stepAvailability.value = []
}
const needsMask = computed(() => steps.value.some(step => step.capability === 'video_inpainting') && modality.value === 'video')
const { busy, progress, status, outputs, start: startJob } = useMediaJob<WorkflowOutput>(
  'media_workflow', { queued: () => t('tasks.waitingForResources'), running: () => t('mediaWorkflow.starting'), completed: () => t('mediaWorkflow.completed'), failed: () => t('mediaWorkflow.failed'), cancelled: () => t('tasks.cancelSuccess') }, message, payload => {
    if (Array.isArray(payload.inputs)) inputs.value = payload.inputs as string[]
    if (typeof payload.outputDir === 'string') outputDir.value = payload.outputDir
    if (typeof payload.modelDir === 'string') modelDir.value = payload.modelDir
    if (payload.modality === 'image' || payload.modality === 'video') modality.value = payload.modality
    if (Array.isArray(payload.steps)) { steps.value = payload.steps as PlanStep[]; stepAvailability.value = [] }
    if (typeof payload.maskPath === 'string') maskPath.value = payload.maskPath
  })

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
  stepAvailability.value.splice(index, 1)
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

useMediaDrop({
  onFiles: paths => addInputs(paths),
  onDirectory: (_zone, folder) => { outputDir.value = folder },
  scanImages: false,
})
async function pickInputs() {
  addInputs(await pickPreviewFiles(modality.value))
}
async function pickOutput() {
  const selected = await open({ directory: true, multiple: false })
  if (typeof selected === 'string') outputDir.value = selected
}
async function pickModelDir() {
  const selected = await open({ directory: true, multiple: false })
  if (typeof selected === 'string') modelDir.value = selected
}
async function pickMaskPath() {
  const selected = await open({ directory: true, multiple: false })
  if (typeof selected === 'string') maskPath.value = selected
}
async function start() {
  if (busy.value || !planAvailable.value) return
  if (!inputs.value.length) {
    message.warning(t('mediaWorkflow.noInput'))
    return
  }
  if (!outputDir.value) {
    message.warning(t('mediaWorkflow.noOutput'))
    return
  }
  await startJob(buildMediaWorkflowPayload({
    requestId: `media-workflow-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    modality: modality.value,
    inputs: inputs.value,
    outputDir: outputDir.value,
    modelDir: modelDir.value,
    steps: steps.value,
    maskPath: needsMask.value ? maskPath.value : '',
  }))
}

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
            <div class="media-workflow-path" data-drop-zone="outputDir">
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
          <div v-if="needsMask">
            <label class="mf-form-label">{{ t('mediaWorkflow.maskPath') }}</label>
            <div class="media-workflow-path">
              <n-input v-model:value="maskPath" :placeholder="t('mediaWorkflow.maskPathHint')" />
              <n-button secondary :disabled="busy" @click="pickMaskPath"><template #icon><n-icon :component="FolderOpenOutline" /></template>{{ t('mediaWorkflow.chooseMaskDir') }}</n-button>
            </div>
          </div>
        </n-space>
      </WorkbenchCard>

      <WorkbenchCard class="workbench-plan" :icon="GitNetworkOutline" :title="t('plan.title')" :subtitle="t('mediaWorkflow.stepHint')">
        <n-space vertical :size="12">
          <MediaWorkflowPresets key="media-workflow-presets" :definition="presetDefinition" :busy="busy" @apply="applyPreset" />
          <div v-for="(step, index) in steps" :key="`media-step-${index}`" class="media-workflow-step">
            <div class="media-workflow-step__head">
              <strong>{{ t('mediaWorkflow.step', { index: index + 1 }) }}</strong>
              <n-button text :disabled="busy || steps.length <= 1" @click="removeStep(index)">{{ t('mediaWorkflow.removeStep') }}</n-button>
            </div>
            <ProcessingPlan :model-value="step" :modality="modality" :busy="busy" :model-dir="modelDir" :workflow="true" :step-count="steps.length" @availability="value => stepAvailability[index] = value" @update:model-value="value => updateStep(index, value)" />
          </div>
          <n-button key="media-workflow-add-step" secondary :disabled="busy || steps.length >= 64" @click="addStep">+ {{ t('mediaWorkflow.addStep') }}</n-button>
          <n-button key="media-workflow-start" type="primary" size="large" class="mf-primary-action" :loading="busy" :disabled="busy || !planAvailable" @click="start">
            <template #icon><n-icon :component="PlayOutline" /></template>
            {{ t('mediaWorkflow.start') }}
          </n-button>
          <n-progress v-if="busy || progress" key="media-workflow-progress" type="line" :percentage="progress" />
          <n-alert v-if="status" key="media-workflow-status" :type="outputs.length ? 'info' : 'warning'" :bordered="false">{{ status }}</n-alert>
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
