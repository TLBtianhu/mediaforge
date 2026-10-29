<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { invoke } from '@tauri-apps/api/core'
import { useI18n } from 'vue-i18n'

defineOptions({ name: 'ProcessingPlan' })

type PlanStep = {
  capability: string
  bindings: Record<string, string>
  params: Record<string, unknown>
}
type RoleSpec = { label: string; task: string; required?: boolean }
type ParamSpec = { type?: string; enum?: string[]; default?: unknown }
type CapabilityModel = { id: string; label: string; roles: string[]; downloaded: boolean; implemented: boolean }
type Capability = {
  id: string
  label: string
  labelEn?: string
  modalities: string[]
  roles: Record<string, RoleSpec>
  params: Record<string, ParamSpec>
  implemented: boolean
  models: CapabilityModel[]
}

const props = withDefaults(defineProps<{
  modelValue?: PlanStep
  modality: 'image' | 'video'
  busy?: boolean
}>(), {
  busy: false,
})
const emit = defineEmits<{ 'update:modelValue': [value: PlanStep] }>()
const { t } = useI18n()
const capabilities = ref<Capability[]>([])

const available = computed(() => capabilities.value.filter(item => item.implemented && item.modalities.includes(props.modality)))
const current = computed(() => capabilities.value.find(item => item.id === props.modelValue?.capability))
const displayRoles = ['inpainter', 'upscaler', 'face_restorer', 'interpolator', 'primary']
const displayRole = computed(() => {
  const cap = current.value
  if (!cap) return ''
  return displayRoles.find(role => cap.roles[role]) || Object.keys(cap.roles)[0] || ''
})
const modelOptions = computed(() => {
  const cap = current.value
  if (!cap || !displayRole.value) return []
  return cap.models
    .filter(model => model.roles.includes(displayRole.value) && model.implemented)
    .map(model => ({ label: `${model.label}${model.downloaded ? '' : ' · ' + t('pipeline.notDownloaded')}`, value: model.id }))
})
const selectedModel = computed(() => current.value && displayRole.value ? props.modelValue?.bindings[displayRole.value] || '' : '')
const paramEntries = computed(() => Object.entries(current.value?.params || {}))

function modelForRole(cap: Capability, role: string, except?: string) {
  const model = cap.models.find(item => item.roles.includes(role) && item.implemented && item.id !== except)
  return model?.id || ''
}

function buildStep(cap: Capability, modelId?: string): PlanStep {
  const role = displayRoles.find(item => cap.roles[item]) || Object.keys(cap.roles)[0] || ''
  const chosen = modelId || cap.models.find(item => item.roles.includes(role) && item.implemented)?.id || ''
  const bindings: Record<string, string> = {}
  for (const key of Object.keys(cap.roles)) {
    bindings[key] = key === role ? chosen : modelForRole(cap, key, chosen)
  }
  const params: Record<string, unknown> = {}
  for (const [key, spec] of Object.entries(cap.params)) params[key] = spec.default
  return { capability: cap.id, bindings, params }
}

function selectCapability(capabilityId: string) {
  const cap = capabilities.value.find(item => item.id === capabilityId)
  if (cap) emit('update:modelValue', buildStep(cap))
}

function selectModel(modelId: string) {
  const cap = current.value
  if (!cap) return
  emit('update:modelValue', { ...buildStep(cap, modelId), params: { ...(props.modelValue?.params || {}) } })
}

function setParam(key: string, value: unknown) {
  if (!props.modelValue) return
  emit('update:modelValue', { ...props.modelValue, params: { ...props.modelValue.params, [key]: value } })
}

async function load() {
  if (typeof window === 'undefined' || !('__TAURI_INTERNALS__' in window)) return
  try {
    const result = await invoke<{ capabilities: Capability[] }>('media_capabilities')
    capabilities.value = result.capabilities || []
    if (!props.modelValue?.capability && available.value[0]) emit('update:modelValue', buildStep(available.value[0]))
  } catch {
    capabilities.value = []
  }
}

watch(() => props.modality, () => {
  if (!available.value.length) return
  if (!available.value.some(item => item.id === props.modelValue?.capability)) {
    emit('update:modelValue', buildStep(available.value[0]))
  }
})

onMounted(load)
</script>

<template>
  <section class="processing-plan">
    <div class="processing-plan__head">
      <span>{{ t('plan.oneModel') }}</span>
      <n-tag size="small" :type="modelOptions.length ? 'success' : 'warning'" :bordered="false">
        {{ modelOptions.length }} {{ t('plan.models') }}
      </n-tag>
    </div>

    <div class="processing-plan__field">
      <label>{{ t('plan.capability') }}</label>
      <n-select
        :value="modelValue?.capability"
        :options="available.map(item => ({ label: item.label, value: item.id }))"
        :disabled="busy"
        @update:value="selectCapability"
      />
    </div>

    <div class="processing-plan__field">
      <label>{{ t('plan.model') }}</label>
      <n-select :value="selectedModel" :options="modelOptions" :disabled="busy" @update:value="selectModel" />
    </div>

    <div v-if="paramEntries.length" class="processing-plan__params">
      <div v-for="[key, spec] in paramEntries" :key="key" class="processing-plan__field">
        <label>{{ key }}</label>
        <n-select
          v-if="spec.enum"
          :value="modelValue?.params?.[key]"
          :options="(spec.enum || []).map(value => ({ label: value, value }))"
          :disabled="busy"
          @update:value="(value: unknown) => setParam(key, value)"
        />
        <n-input-number
          v-else-if="spec.type === 'integer' || spec.type === 'number'"
          :value="Number(modelValue?.params?.[key] ?? 0)"
          :disabled="busy"
          @update:value="(value: number | null) => setParam(key, value ?? 0)"
        />
        <n-input
          v-else
          :value="String(modelValue?.params?.[key] ?? '')"
          :disabled="busy"
          @update:value="(value: string) => setParam(key, value)"
        />
      </div>
    </div>

    <div class="processing-plan__note">
      {{ t('plan.workflowHint') }}
    </div>
  </section>
</template>

<style scoped>
.processing-plan { display: grid; gap: 12px; }
.processing-plan__head { display: flex; gap: 12px; align-items: flex-start; justify-content: space-between; }
.processing-plan__head > div { display: flex; flex-direction: column; gap: 3px; }
.processing-plan__head span { color: var(--on-surface-muted); font-size: 10.5px; }
.processing-plan__field { display: grid; gap: 5px; }
.processing-plan__field label { color: var(--on-surface-muted); font-size: 9px; font-weight: 700; letter-spacing: .06em; text-transform: uppercase; }
.processing-plan__params { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 9px; }
.processing-plan__note { padding-top: 2px; color: var(--on-surface-muted); font-size: 10px; line-height: 1.5; }
</style>
