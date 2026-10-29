<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { invoke } from '@tauri-apps/api/core'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { CubeOutline } from '@vicons/ionicons5'

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
const emit = defineEmits<{ 'update:modelValue': [value: PlanStep]; availability: [value: boolean] }>()
const { t } = useI18n()
const router = useRouter()
const capabilities = ref<Capability[]>([])
const loading = ref(false)

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
const hasUsableModel = computed(() => Boolean(current.value?.models.some(model => model.downloaded && model.implemented && model.roles.includes(displayRole.value))))
watch(hasUsableModel, value => emit('availability', value), { immediate: true })
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

function paramLabel(key: string) {
  const known: Record<string, string> = {
    conf: t('plan.params.conf'), expand: t('plan.params.expand'), detectMode: t('plan.params.detectMode'),
    target: t('plan.params.target'), fit: t('plan.params.fit'), scale: t('plan.params.scale'),
    width: t('plan.params.width'), height: t('plan.params.height'),
  }
  return known[key] || key
}

function setParam(key: string, value: unknown) {
  if (!props.modelValue) return
  emit('update:modelValue', { ...props.modelValue, params: { ...props.modelValue.params, [key]: value } })
}

async function load() {
  if (typeof window === 'undefined' || !('__TAURI_INTERNALS__' in window)) return
  loading.value = true
  try {
    const result = await invoke<{ capabilities: Capability[] }>('media_capabilities')
    capabilities.value = result.capabilities || []
    if (!props.modelValue?.capability && available.value[0]) emit('update:modelValue', buildStep(available.value[0]))
  } catch {
    capabilities.value = []
  } finally {
    loading.value = false
  }
}

function openModels() {
  router.push({ path: '/models', query: { modality: props.modality } })
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
    <n-spin v-if="loading" :show="true" class="processing-plan__loading" />
    <div v-else-if="!hasUsableModel" class="plan-empty">
      <span class="plan-empty__glyph"><n-icon :component="CubeOutline" /></span>
      <strong>{{ t('plan.noModelTitle') }}</strong>
      <p>{{ t('plan.noModelHint') }}</p>
      <n-button secondary @click="openModels">{{ t('plan.openModels') }}</n-button>
    </div>
    <template v-else>
      <div class="processing-plan__head">
        <span>{{ t('plan.oneModel') }}</span>
        <n-tag size="small" type="success" :bordered="false">{{ modelOptions.length }} {{ t('plan.models') }}</n-tag>
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
          <label>{{ paramLabel(key) }}</label>
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
    </template>
  </section>
</template>

<style scoped>
.processing-plan { display: grid; gap: 12px; }
.processing-plan__loading { display: grid; min-height: 240px; place-items: center; }
.plan-empty { display: flex; min-height: 280px; flex-direction: column; align-items: center; justify-content: center; gap: 8px; padding: 28px; text-align: center; }
.plan-empty__glyph { display: grid; width: 56px; height: 56px; margin-bottom: 4px; place-items: center; border-radius: 16px; color: var(--primary-strong); background: color-mix(in srgb, var(--primary-soft) 30%, var(--surface-2)); box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--primary-border) 26%, transparent); font-size: 26px; }
.plan-empty strong { color: var(--on-surface-heading); font-size: 14px; }
.plan-empty p { max-width: 320px; margin: 0; color: var(--on-surface-muted); font-size: 12px; line-height: 1.5; }
.plan-empty :deep(.n-button) { margin-top: 8px; color: var(--on-surface-heading); background: var(--surface-2); }
.processing-plan__head { display: flex; gap: 12px; align-items: flex-start; justify-content: space-between; }
.processing-plan__head > div { display: flex; flex-direction: column; gap: 3px; }
.processing-plan__head span { color: var(--on-surface-muted); font-size: 10.5px; }
.processing-plan__field { display: grid; gap: 5px; }
.processing-plan__field label { color: var(--on-surface-muted); font-size: 9px; font-weight: 700; letter-spacing: .06em; text-transform: uppercase; }
.processing-plan__params { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 9px; }
.processing-plan__note { padding-top: 2px; color: var(--on-surface-muted); font-size: 10px; line-height: 1.5; }
</style>
