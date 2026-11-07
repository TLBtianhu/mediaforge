<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useMessage } from 'naive-ui'
import { RefreshOutline } from '@vicons/ionicons5'
import { useMediaWorkflowsStore, type MediaWorkflowPreset } from '@/stores/mediaWorkflows'

type Definition = MediaWorkflowPreset['definition']
const props = defineProps<{ definition: Definition; busy?: boolean }>()
const emit = defineEmits<{ apply: [definition: Definition] }>()
const { t } = useI18n()
const message = useMessage()
const store = useMediaWorkflowsStore()
const selected = ref<MediaWorkflowPreset | null>(null)
const deleting = ref<MediaWorkflowPreset | null>(null)
const name = ref('')
const pending = ref(false)
const notice = ref('')
const conflicted = ref(false)
const locked = computed(() => Boolean(props.busy || pending.value || store.loading))
const options = computed(() => store.presets.map(preset => ({ label: preset.name, value: preset.id })))
const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value)) as T

// Snapshot the base revision explicitly. A background refresh must not let an
// old local draft overwrite a newer saved definition without a conflict.
function selectPreset(id: string | null) {
  if (locked.value) return
  const preset = store.presets.find(item => item.id === id)
  selected.value = preset ? clone(preset) : null
  name.value = preset?.name || ''
  notice.value = ''
  conflicted.value = false
}

function applySelected() {
  if (locked.value || !selected.value) return
  const latest = store.presets.find(item => item.id === selected.value!.id)
  if (!latest) { notice.value = t('mediaWorkflow.presets.notFound'); return }
  selected.value = clone(latest)
  name.value = latest.name
  notice.value = ''
  conflicted.value = false
  emit('apply', clone(latest.definition))
  message.success(t('mediaWorkflow.presets.loaded', { name: latest.name }))
}

function report(error: unknown) {
  conflicted.value = Boolean(error && typeof error === 'object' && 'code' in error && error.code === 'MEDIA_WORKFLOW_CONFLICT')
  notice.value = conflicted.value ? t('mediaWorkflow.presets.conflict')
    : error instanceof Error ? error.message : String(error || t('mediaWorkflow.presets.failed'))
  if (conflicted.value) message.warning(notice.value)
  else message.error(notice.value)
}

async function refresh() {
  if (locked.value) return
  pending.value = true
  try {
    await store.refresh()
    if (store.error) notice.value = store.error
    else if (!conflicted.value) notice.value = ''
    // Keep selected/name/base revision and the editor draft until the user
    // explicitly chooses or loads a saved plan.
  } catch (error) { report(error) }
  finally { pending.value = false }
}

function currentDefinition(): Definition {
  return clone<Definition>({ version: 1, modality: props.definition.modality, steps: props.definition.steps.map(step => ({
    capability: step.capability, bindings: step.bindings, params: step.params,
  })) })
}

async function save(asNew: boolean) {
  if (locked.value || (!asNew && !selected.value)) return
  const trimmed = name.value.trim()
  if (!trimmed) { notice.value = t('mediaWorkflow.presets.nameRequired'); return }
  const base = selected.value
  const request = { name: trimmed, definition: currentDefinition(), ...(!asNew && base ? { id: base.id, expectedUpdatedAt: base.updatedAt } : {}) }
  pending.value = true
  try {
    const saved = await store.save(request)
    selected.value = clone(saved)
    name.value = saved.name
    notice.value = ''
    conflicted.value = false
    message.success(t(asNew ? 'mediaWorkflow.presets.savedNew' : 'mediaWorkflow.presets.updated'))
  } catch (error) { report(error) }
  finally { pending.value = false }
}

function prepareRemoval() {
  if (!locked.value && selected.value) deleting.value = clone(selected.value)
}

async function removeSelected() {
  if (locked.value || !deleting.value) return false
  const target = deleting.value
  pending.value = true
  try {
    await store.remove(target.id, target.updatedAt)
    if (selected.value?.id === target.id) { selected.value = null; name.value = '' }
    deleting.value = null
    notice.value = ''
    conflicted.value = false
    message.success(t('mediaWorkflow.presets.deleted'))
  } catch (error) { report(error); return false }
  finally { pending.value = false }
}

onMounted(async () => {
  try { await store.initialize(); if (store.error) notice.value = store.error }
  catch (error) { report(error) }
})
</script>

<template>
  <section class="media-presets" :aria-label="t('mediaWorkflow.presets.title')">
    <div class="media-presets__head">
      <div><strong>{{ t('mediaWorkflow.presets.title') }}</strong><p>{{ t('mediaWorkflow.presets.hint') }}</p></div>
      <n-button text :disabled="locked" :loading="store.loading" :aria-label="t('mediaWorkflow.presets.refresh')" @click="refresh"><template #icon><n-icon :component="RefreshOutline" /></template></n-button>
    </div>
    <div class="media-presets__row">
      <n-select :value="selected?.id || null" :options="options" clearable filterable :disabled="locked" :placeholder="t('mediaWorkflow.presets.choose')" :aria-label="t('mediaWorkflow.presets.choose')" @update:value="selectPreset" />
      <n-button secondary :disabled="locked || !selected" @click="applySelected">{{ t('mediaWorkflow.presets.load') }}</n-button>
    </div>
    <div class="media-presets__row media-presets__row--save">
      <n-input v-model:value="name" :disabled="locked" :maxlength="120" :placeholder="t('mediaWorkflow.presets.name')" :aria-label="t('mediaWorkflow.presets.name')" />
      <div class="media-presets__actions">
        <n-button secondary :disabled="locked || !name.trim()" @click="save(true)">{{ t('mediaWorkflow.presets.saveNew') }}</n-button>
        <n-button secondary :disabled="locked || !selected || !name.trim()" @click="save(false)">{{ t('mediaWorkflow.presets.update') }}</n-button>
        <n-popconfirm :disabled="locked || !selected" :positive-text="t('mediaWorkflow.presets.delete')" :negative-text="t('common.cancel')" @positive-click="removeSelected">
          <template #trigger><n-button text type="error" :disabled="locked || !selected" @click="prepareRemoval">{{ t('mediaWorkflow.presets.delete') }}</n-button></template>
          {{ t('mediaWorkflow.presets.deleteConfirm', { name: deleting?.name || selected?.name || '' }) }}
        </n-popconfirm>
      </div>
    </div>
    <n-alert v-if="notice" :type="conflicted ? 'warning' : 'error'" :bordered="false" class="media-presets__notice">{{ notice }}</n-alert>
  </section>
</template>

<style scoped>
.media-presets { padding: 14px; display: grid; gap: 12px; border-radius: 12px; background: var(--surface-2); box-shadow: inset 0 0 0 1px var(--outline); }
.media-presets__head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.media-presets__head strong { color: var(--on-surface-heading); font-size: 13px; }
.media-presets__head p { margin: 5px 0 0; color: var(--on-surface-muted); font-size: 11px; line-height: 1.5; }
.media-presets__row { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: center; gap: 10px; }
.media-presets__actions { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.media-presets__notice { font-size: 12px; }
@media (max-width: 1100px) { .media-presets__row--save { grid-template-columns: minmax(0, 1fr); } }
</style>
