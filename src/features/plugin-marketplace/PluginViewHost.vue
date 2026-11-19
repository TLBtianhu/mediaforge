<script setup lang="ts">
/**
 * Plugin view host: loads a plugin-contributed page (protocol v1 view
 * contribution) inside a sandboxed iframe and bridges the pymss Studio SDK
 * (JSON-RPC over postMessage) to Tauri commands.
 *
 * Security posture: the iframe is sandboxed (no same-origin), only manifest
 * allowlisted SDK methods pass, and RPC is bound to a document-specific
 * MessageChannel. A navigated document inherits neither the session nor the port.
 */
import { getBuildInfo, setPluginViewRoot, startOperationTask } from '@/api/native'
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { convertFileSrc } from '@tauri-apps/api/core'
import { listen, type UnlistenFn } from '@tauri-apps/api/event'
import { invokeWorkerCommand } from '@/api/worker'
import { open } from '@tauri-apps/plugin-dialog'
import { pluginViewUrl } from './viewUrl'
import { isTaskProgress, isTaskResult } from '@/utils/taskEvents'

type PluginViewContribution = {
  id: string
  title: string
  entry: string
  entryPath?: string
  placements: string[]
  operation?: string | null
  permissions: string[]
}

const props = defineProps<{ pluginName: string; pluginsRoot: string; view: PluginViewContribution }>()
const { t } = useI18n()

const frame = ref<HTMLIFrameElement | null>(null)
const status = ref<'loading' | 'ready' | 'error'>('loading')
const errorText = ref('')
const src = ref('')
let disposed = false

const permissions = () => props.view.permissions || ['operations']

function allowed(method: string): boolean {
  const family = method.split('.')[0]
  const list = permissions()
  return list.includes(method) || list.includes(family)
}

type RunHandlers = {
  rpcId: number
  resolve: (value: unknown) => void
  reject: (error: Error) => void
}
const runRequests = new Map<string, RunHandlers>()
let channel: MessagePort | undefined
let sessionId = ''
let loadedOnce = false
let recovering = false

function rejectRunRequests(reason: string) {
  for (const handlers of runRequests.values()) handlers.reject(new Error(reason))
  runRequests.clear()
}

function closeChannel() {
  channel?.close()
  channel = undefined
}

async function handleCall(id: number, method: string, params: Record<string, unknown>) {
  if (!allowed(method)) throw new Error(`PERMISSION_DENIED: ${method}`)
  switch (method) {
    case 'app.info': {
      const build = await getBuildInfo()
      return { studio: 'pymss-studio', version: build.version, locale: t('plugins.sdkLocale' as never) || 'en' }
    }
    case 'operations.describe':
      return await invokeWorkerCommand('describe_operation', { operation: String(params.operation) })
    case 'operations.start': {
      const requestId = `plugin_view_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`
      const promise = new Promise<unknown>((resolve, reject) => {
        runRequests.set(requestId, { rpcId: id, resolve, reject })
      })
      // Unmount can reject the request while the native start call is pending.
      void promise.catch(() => undefined)
      try {
        await startOperationTask({
          operation: params.operation,
          inputs: params.inputs || {},
          params: params.params || {},
          requestId,
        })
        return await promise
      } finally {
        runRequests.delete(requestId)
      }
    }
    case 'files.pickAudio': {
      const selected = await open({ multiple: false, title: 'Audio' })
      return typeof selected === 'string' ? { path: selected } : null
    }
    default:
      throw new Error(`UNKNOWN_METHOD: ${method}`)
  }
}

function postToFrame(message: Record<string, unknown>) {
  if (disposed || !channel || !sessionId) return
  channel.postMessage({ ...message, sessionId })
}

const THEME_TOKENS = [
  '--surface', '--surface-1', '--surface-2', '--surface-3',
  '--on-surface', '--on-surface-heading', '--on-surface-muted',
  '--outline', '--outline-strong',
  '--primary', '--primary-ink', '--primary-soft',
  '--danger', '--success', '--warning', '--radius', '--radius-small',
]

/** Capture Studio's live design tokens and push them into the plugin iframe. */
function pushTheme() {
  const rootStyle = getComputedStyle(document.documentElement)
  const cssVariables: Record<string, string> = {}
  for (const token of THEME_TOKENS) {
    const value = rootStyle.getPropertyValue(token).trim()
    if (value) cssVariables[token] = value
  }
  postToFrame({
    jsonrpc: '2.0',
    method: 'host.theme',
    params: {
      cssVariables,
      colorMode: document.documentElement.classList.contains('light-theme') ? 'light' : 'dark',
      fontFamily: getComputedStyle(document.body).fontFamily,
    },
  })
}

function onWindowMessage(event: MessageEvent) {
  const frameWindow = frame.value?.contentWindow
  if (disposed || !frameWindow || event.source !== frameWindow || channel) return
  const data = event.data
  if (!data || typeof data !== 'object' || data.jsonrpc !== '2.0') return
  if (data.method !== 'pymss.ready' || data.sessionId !== sessionId || !event.ports?.length) return
  channel = event.ports[0]
  channel.onmessage = onChannelMessage
  channel.start()
  status.value = 'ready'
  postToFrame({ jsonrpc: '2.0', method: 'host.ready' })
  pushTheme()
}

function onChannelMessage(event: MessageEvent) {
  if (disposed || !channel) return
  const data = event.data
  if (!data || typeof data !== 'object' || data.jsonrpc !== '2.0' || data.sessionId !== sessionId) return
  const id = Number(data.id)
  if (!Number.isFinite(id) || typeof data.method !== 'string') return
  handleCall(id, data.method, data.params || {})
    .then(result => postToFrame({ jsonrpc: '2.0', id, result }))
    .catch(failure => postToFrame({
      jsonrpc: '2.0',
      id,
      error: { code: -32000, message: failure instanceof Error ? failure.message : String(failure) },
    }))
}

function onFrameLoad() {
  if (disposed) return
  if (!loadedOnce) {
    loadedOnce = true
    return
  }
  if (recovering) {
    recovering = false
    return
  }
  closeChannel()
  rejectRunRequests('PLUGIN_VIEW_NAVIGATED')
  recovering = true
  sessionId = crypto.randomUUID()
  status.value = 'loading'
  src.value = pluginViewUrl(
    props.pluginName, props.view.id, props.view.entryPath || props.view.entry, convertFileSrc, sessionId,
  )
}

let unlistenWorker: UnlistenFn | undefined
let themeObserver: MutationObserver | undefined

onMounted(async () => {
  window.addEventListener('message', onWindowMessage)
  // theme.ts flips .dark-theme/.light-theme on <html> and rewrites token
  // properties via element style; observe class + inline style so plugin
  // pages follow light/dark and accent changes without reloading.
  themeObserver = new MutationObserver(() => pushTheme())
  themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['class', 'style'] })
  try {
    if (!props.view.entryPath && !props.view.entry) throw new Error('view entry not found')
    if (!props.pluginsRoot) throw new Error('plugins root not ready')
    await setPluginViewRoot(props.pluginsRoot)
    if (disposed) return
    const unlisten = await listen<{ type: string; requestId?: string; taskId?: string; payload: Record<string, unknown> }>(
      'pymss://worker-event',
      ({ payload: workerEvent }) => {
        const requestId = workerEvent.requestId || workerEvent.taskId || ''
        const handlers = runRequests.get(requestId)
        if (!handlers) return
        if (isTaskResult(workerEvent.type)) {
          runRequests.delete(requestId)
          handlers.resolve(workerEvent.payload)
        } else if (workerEvent.type === 'error' || workerEvent.type === 'task.cancelled') {
          runRequests.delete(requestId)
          handlers.reject(new Error(String(workerEvent.payload?.message || workerEvent.type)))
        } else if (isTaskProgress(workerEvent.type)) {
          postToFrame({ jsonrpc: '2.0', method: 'task.progress', params: { ...(workerEvent.payload || {}), requestId, id: handlers.rpcId } })
        }
      },
    )
    if (disposed) { unlisten(); return }
    unlistenWorker = unlisten
    sessionId = crypto.randomUUID()
    src.value = pluginViewUrl(
      props.pluginName, props.view.id, props.view.entryPath || props.view.entry, convertFileSrc, sessionId,
    )
    status.value = 'loading'
  } catch (failure) {
    if (disposed) return
    status.value = 'error'
    errorText.value = String(failure)
  }
})

onBeforeUnmount(() => {
  disposed = true
  closeChannel()
  window.removeEventListener('message', onWindowMessage)
  unlistenWorker?.()
  themeObserver?.disconnect()
  rejectRunRequests('PLUGIN_VIEW_CLOSED')
})
</script>

<template>
  <div class="plugin-view-host">
    <n-alert v-if="status === 'error'" type="error">{{ errorText }}</n-alert>
    <iframe
      v-if="status !== 'error' && src"
      ref="frame"
      class="plugin-view-frame"
      :src="src"
      sandbox="allow-scripts allow-forms"
      :title="view.title || pluginName"
      @load="onFrameLoad"
    />
  </div>
</template>

<style scoped>
.plugin-view-host { display: flex; flex-direction: column; height: 100%; min-height: 320px; }
.plugin-view-frame { flex: 1; width: 100%; border: 0; background: transparent; }
</style>
