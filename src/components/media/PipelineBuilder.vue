<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { invoke } from '@tauri-apps/api/core'
import { useI18n } from 'vue-i18n'
import { AddOutline, ArrowDownOutline, ArrowUpOutline, CloseOutline } from '@vicons/ionicons5'

defineOptions({ name: 'PipelineBuilder' })

type PipelineStep = {
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
  modelValue?: PipelineStep[]
  modality: 'image' | 'video'
  busy?: boolean
}>(), {
  modelValue: () => [],
  busy: false,
})
const emit = defineEmits<{ 'update:modelValue': [value: PipelineStep[]] }>()
const { t } = useI18n()
const capabilities = ref<Capability[]>([])
const loading = ref(false)

const steps = computed(() => props.modelValue || [])
const available = computed(() => capabilities.value.filter(item => item.implemented && item.modalities.includes(props.modality)))
const addOptions = computed(() => available.value.map(item => ({ label: item.label, key: item.id })))

function modelsFor(capability: Capability, role: string) {
  return capability.models
    .filter(model => model.roles.includes(role) && model.implemented)
    .map(model => ({ label: `${model.label}${model.downloaded ? '' : ' · ' + t('pipeline.notDownloaded')}`, value: model.id }))
}

function defaultStep(capability: Capability): PipelineStep {
  const bindings: Record<string, string> = {}
  for (const [role, spec] of Object.entries(capability.roles)) {
    const candidates = capability.models.filter(model => model.roles.includes(role) && model.implemented)
    if (candidates[0]) bindings[role] = candidates[0].id
    else if (spec.required !== false) bindings[role] = ''
  }
  const params: Record<string, unknown> = {}
  for (const [key, spec] of Object.entries(capability.params)) params[key] = spec.default
  return { capability: capability.id, bindings, params }
}

function update(next: PipelineStep[]) {
  emit('update:modelValue', next)
}

function add(capabilityId: string) {
  const capability = capabilities.value.find(item => item.id === capabilityId)
  if (!capability || props.busy) return
  update([...steps.value, defaultStep(capability)])
}

function remove(index: number) {
  if (props.busy) return
  update(steps.value.filter((_, position) => position !== index))
}

function move(index: number, offset: number) {
  if (props.busy) return
  const target = index + offset
  if (target < 0 || target >= steps.value.length) return
  const next = [...steps.value]
  ;[next[index], next[target]] = [next[target], next[index]]
  update(next)
}

function capabilityOf(step: PipelineStep) {
  return capabilities.value.find(item => item.id === step.capability)
}

function setBinding(index: number, role: string, value: string) {
  const step = steps.value[index]
  if (!step) return
  update(steps.value.map((item, position) => position === index
    ? { ...item, bindings: { ...item.bindings, [role]: value } }
    : item))
}

function setParam(index: number, key: string, value: unknown) {
  const step = steps.value[index]
  if (!step) return
  update(steps.value.map((item, position) => position === index
    ? { ...item, params: { ...item.params, [key]: value } }
    : item))
}

function paramOptions(spec: ParamSpec) {
  return (spec.enum || []).map(value => ({ label: value, value }))
}

async function load() {
  if (typeof window === 'undefined' || !('__TAURI_INTERNALS__' in window)) return
  loading.value = true
  try {
    const result = await invoke<{ capabilities: Capability[] }>('media_capabilities')
    capabilities.value = result.capabilities || []
  } catch {
    capabilities.value = []
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <section class="pipeline-builder">
    <div class="pipeline-builder__head">
      <div>
        <strong>{{ t('pipeline.title') }}</strong>
        <span>{{ t('pipeline.subtitle') }}</span>
      </div>
      <n-dropdown :options="addOptions" :disabled="busy || !addOptions.length" @select="add">
        <n-button size="small" secondary :disabled="busy || !addOptions.length">
          <template #icon><n-icon :component="AddOutline" /></template>
          {{ t('pipeline.addStep') }}
        </n-button>
      </n-dropdown>
    </div>

    <n-spin :show="loading">
      <div v-if="!steps.length" class="pipeline-empty">
        <span>{{ t('pipeline.empty') }}</span>
        <n-button size="small" text :disabled="busy || !addOptions.length" @click="add(addOptions[0]?.key || '')">{{ t('pipeline.addFirst') }}</n-button>
      </div>
      <div v-else class="pipeline-steps">
        <article v-for="(step, index) in steps" :key="`${step.capability}-${index}`" class="pipeline-step">
          <div class="pipeline-step__head">
            <span class="pipeline-step__num">{{ String(index + 1).padStart(2, '0') }}</span>
            <strong>{{ capabilityOf(step)?.label || step.capability }}</strong>
            <div class="pipeline-step__actions">
              <n-button text size="tiny" :disabled="busy || index === 0" @click="move(index, -1)"><template #icon><n-icon :component="ArrowUpOutline" /></template></n-button>
              <n-button text size="tiny" :disabled="busy || index === steps.length - 1" @click="move(index, 1)"><template #icon><n-icon :component="ArrowDownOutline" /></template></n-button>
              <n-button text size="tiny" :disabled="busy" @click="remove(index)"><template #icon><n-icon :component="CloseOutline" /></template></n-button>
            </div>
          </div>
          <div v-if="capabilityOf(step)" class="pipeline-step__body">
            <div v-for="(roleSpec, role) in capabilityOf(step)!.roles" :key="role" class="pipeline-field">
              <label>{{ roleSpec.label }}</label>
              <n-select
                :value="step.bindings[role]"
                :options="modelsFor(capabilityOf(step)!, String(role))"
                :disabled="busy"
                @update:value="(value: string) => setBinding(index, String(role), value)"
              />
            </div>
            <div v-for="(paramSpec, key) in capabilityOf(step)!.params" :key="key" class="pipeline-field">
              <label>{{ key }}</label>
              <n-select
                v-if="paramSpec.enum"
                :value="step.params[String(key)]"
                :options="paramOptions(paramSpec)"
                :disabled="busy"
                @update:value="(value: unknown) => setParam(index, String(key), value)"
              />
              <n-input-number
                v-else-if="paramSpec.type === 'integer' || paramSpec.type === 'number'"
                :value="Number(step.params[String(key)] ?? 0)"
                :disabled="busy"
                @update:value="(value: number | null) => setParam(index, String(key), value ?? 0)"
              />
              <n-input
                v-else
                :value="String(step.params[String(key)] ?? '')"
                :disabled="busy"
                @update:value="(value: string) => setParam(index, String(key), value)"
              />
            </div>
          </div>
        </article>
      </div>
    </n-spin>
  </section>
</template>

<style scoped>
.pipeline-builder { display: grid; gap: 10px; padding: 13px; border: 1px solid var(--divider-color); border-radius: 14px; background: var(--action-color); }
.pipeline-builder__head { display: flex; gap: 12px; align-items: center; justify-content: space-between; }
.pipeline-builder__head > div { display: flex; flex-direction: column; gap: 2px; }
.pipeline-builder__head strong { color: var(--text-color-1); font-size: 12px; }
.pipeline-builder__head span { color: var(--text-color-3); font-size: 10px; }
.pipeline-empty { display: flex; gap: 10px; align-items: center; padding: 12px; border: 1px dashed var(--divider-color); border-radius: 10px; color: var(--text-color-3); font-size: 11px; }
.pipeline-steps { display: grid; gap: 9px; }
.pipeline-step { display: grid; gap: 9px; padding: 10px; border: 1px solid var(--divider-color); border-radius: 11px; background: var(--card-color); }
.pipeline-step__head { display: flex; gap: 8px; align-items: center; }
.pipeline-step__num { color: var(--mf-accent, var(--primary-strong)); font-size: 10px; font-weight: 800; }
.pipeline-step__head strong { flex: 1; color: var(--text-color-1); font-size: 11px; }
.pipeline-step__actions { display: flex; align-items: center; }
.pipeline-step__body { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 8px; }
.pipeline-field { display: grid; gap: 4px; }
.pipeline-field label { color: var(--text-color-3); font-size: 9px; text-transform: uppercase; letter-spacing: .05em; }
</style>
