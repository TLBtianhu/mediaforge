<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { invoke } from '@tauri-apps/api/core'
import { useI18n } from 'vue-i18n'
import { useMessage } from 'naive-ui'
import { FolderOpenOutline, RefreshOutline } from '@vicons/ionicons5'
import { useWorkbenchStore } from '@/stores/workbench'
import { useSettingsStore } from '@/stores/settings'
import { workbenchOutputDirectory, workflowInputNodes, workflowOutputNodes } from '@/workflows/workbench'
import { detectWorkflowFormat } from '@/workflows/formats'
import TaskOutputSettings from '@/components/TaskOutputSettings.vue'
import TaskInputFiles from '@/components/TaskInputFiles.vue'

const props = withDefaults(defineProps<{
  workflowKey: string; definition: Record<string, unknown>
  mode?: 'inputs' | 'outputs' | 'all'; nodeId?: string; action?: string; compact?: boolean; aggregateOutput?: boolean
}>(), { mode: 'all', nodeId: '', action: '', compact: false, aggregateOutput: false })
const store = useWorkbenchStore()
const settings = useSettingsStore()
const { t } = useI18n()
const message = useMessage()
const busy = ref(false)
const session = computed(() => store.session(props.workflowKey))
const nativeGraph = computed(() => detectWorkflowFormat(props.definition) === 'graph')
const inputs = computed(() => props.mode === 'outputs' ? [] : workflowInputNodes(props.definition).filter(node => !props.nodeId || node.id === props.nodeId))
const outputs = computed(() => props.mode === 'inputs' ? [] : props.aggregateOutput
  ? [{ id: 'save', label: t('workflows.saveNode') }] : workflowOutputNodes(props.definition).filter(node => !props.nodeId || node.id === props.nodeId))
function outputDirectory(id: string) {
  if (props.aggregateOutput) return session.value.bindings.outputDirectory
    || (!nativeGraph.value && Object.values(session.value.bindings.outputs)[0]) || settings.outputDir
  const node = outputs.value.find(item => item.id === id)!
  return workbenchOutputDirectory(node, session.value.bindings.outputs[id], settings.outputDir)
}
function resetOutput(id: string) {
  if (props.aggregateOutput) {
    delete session.value.bindings.outputDirectory
    session.value.bindings.outputs = {}
  } else delete session.value.bindings.outputs[id]
}
function isDefaultOutput(id: string) {
  return !(props.aggregateOutput
    ? session.value.bindings.outputDirectory || (!nativeGraph.value && Object.keys(session.value.bindings.outputs).length)
    : session.value.bindings.outputs[id])
}
function files(id: string) {
  return session.value.bindings.inputs[id]?.files || (inputs.value.find(node => node.id === id)?.embeddedPath ? [inputs.value.find(node => node.id === id)!.embeddedPath!] : [])
}
function setFiles(id: string, paths: string[], folder?: string) {
  session.value.bindings.inputs[id] = { files: [...new Set(paths)], ...(folder ? { folder } : {}) }
  if (!session.value.bindings.primaryInput) session.value.bindings.primaryInput = id
}
async function safely(action: () => Promise<void>) {
  if (busy.value) return
  busy.value = true
  try { await action() } catch (error) { message.error(error instanceof Error ? error.message : String(error)) }
  finally { busy.value = false }
}
function pickFiles(id: string) { return safely(async () => {
  const selected = await invoke<string[]>('pick_media_files')
  if (selected?.length) setFiles(id, [...files(id), ...selected])
}) }
function pickFolder(id: string) { return safely(async () => {
  const folder = await invoke<string | null>('pick_input_folder')
  if (!folder) return
  const scanned = await invoke<{ files: string[]; warnings: string[] }>('scan_media_paths', { paths: [folder] })
  setFiles(id, [...files(id), ...scanned.files], folder)
  if (!scanned.files.length) message.warning(t('workbench.emptyFolder'))
}) }
function pickOutput(id: string) { return safely(async () => {
  const folder = await settings.pickFolder()
  if (!folder) return
  if (props.aggregateOutput) {
    session.value.bindings.outputDirectory = folder
    session.value.bindings.outputs = {}
  } else session.value.bindings.outputs[id] = folder
}) }
watch(() => [props.nodeId, props.action], () => {
  if (!props.nodeId) return
  if (props.action === 'files') void pickFiles(props.nodeId)
  if (props.action === 'folder') void pickFolder(props.nodeId)
  if (props.action === 'output') void pickOutput(props.nodeId)
}, { immediate: true })
</script>

<template>
  <section class="node-bindings" :class="{ 'node-bindings--compact': compact }" @pointerdown.stop @dblclick.stop @wheel.stop>
    <div v-for="node in inputs" :key="node.id" class="binding-group" :data-workbench-input="node.id">
      <strong v-if="!compact && (inputs.length > 1 || mode === 'all')">{{ node.label }}</strong>
      <TaskInputFiles
        :files="files(node.id)"
        :busy="busy"
        @add="pickFiles(node.id)"
        @folder="pickFolder(node.id)"
        @remove="path => setFiles(node.id, files(node.id).filter(file => file !== path))"
        @clear="setFiles(node.id, [])"
      />
      <n-button v-if="workflowInputNodes(definition).length > 1" size="tiny" :type="session.bindings.primaryInput === node.id ? 'primary' : 'default'" @click="session.bindings.primaryInput = node.id">{{ t('workbench.primaryInput') }}</n-button>
    </div>
    <div v-for="node in outputs" :key="node.id" class="binding-group" :data-workbench-output="node.id">
      <strong>{{ aggregateOutput ? t('separate.outputDirectorySummary') : node.label }}</strong>
      <TaskOutputSettings
        :model-value="outputDirectory(node.id)"
        :layout="session.bindings.outputLayout"
        readonly
        :disabled="busy"
        :show-reset="true"
        @browse="pickOutput(node.id)"
        @reset="resetOutput(node.id)"
        @update:layout="session.bindings.outputLayout = $event"
      />
      <p v-if="nativeGraph" class="binding-hint">{{ t('workbench.outputRootHint') }}</p>
      <div class="binding-actions">
        <n-button size="small" secondary type="primary" :disabled="busy" @click="pickOutput(node.id)"><template #icon><n-icon :component="FolderOpenOutline" /></template>{{ t('workbench.chooseOutput') }}</n-button>
        <n-button size="small" secondary :disabled="busy || isDefaultOutput(node.id)" :title="t('separate.outputDefault')" @click="resetOutput(node.id)"><template #icon><n-icon :component="RefreshOutline" /></template>{{ t('workbench.defaultOutput') }}</n-button>
      </div>
    </div>

  </section>
</template>

<style scoped>
.node-bindings { display: grid; gap: 16px; font-size: 12px; }
.node-bindings--compact { gap: 8px; padding-top: 4px; }
.binding-group { display: grid; gap: 10px; min-width: 0; }
.binding-hint { margin: 0; color: var(--on-surface-muted); font-size: 12px; line-height: 1.6; }
.binding-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.node-bindings--compact .binding-actions { flex-wrap: nowrap; }
.node-bindings--compact .binding-actions > .n-button { flex: 1; }
.binding-selection-summary { display: flex; align-items: center; justify-content: space-between; min-height: 22px; color: var(--on-surface-muted); }
.binding-files { display: grid; gap: 6px; padding-right: 6px; }
.binding-file { display: flex; align-items: center; gap: 8px; min-width: 0; padding: 8px; border-radius: 8px; background: color-mix(in srgb, var(--surface-2) 50%, transparent); box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--outline) 76%, transparent); }
.binding-file__glyph { flex: 0 0 auto; color: var(--primary-strong); }
.binding-file__main { display: grid; gap: 3px; min-width: 0; flex: 1; }
.binding-file__main strong, .binding-file__main span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.binding-file__main strong { font-size: 12px; font-weight: 600; }
.binding-file__main strong { display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; white-space: normal; overflow-wrap: anywhere; line-height: 1.4; }
.binding-file__main span { font-size: 10px; color: var(--on-surface-muted); }
.binding-file > .n-button { flex: 0 0 auto; }
.binding-empty { display: grid; place-items: center; min-height: 72px; padding: 8px 12px; border: 1px dashed var(--outline); border-radius: 8px; background: transparent; color: var(--on-surface-muted); font-family: inherit; font-size: 11px; line-height: 1.6; text-align: center; cursor: pointer; }
.binding-empty:disabled { cursor: default; opacity: .6; }
.binding-layout { display: grid; gap: 8px; color: var(--on-surface-muted); }
</style>
