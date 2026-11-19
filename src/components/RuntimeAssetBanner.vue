<script setup lang="ts">
// 首次运行时：应用本身能开（界面不依赖 Python），但处理能力还没就绪。
// 这里负责把"缺运行时"变成一条可操作的引导，并在装好后原地刷新状态。
import { runtimeAssetInstall, runtimeAssetStatus, versionsAcceptRuntime, versionsCheck, type RuntimeAssetStatus } from '@/api/native'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { listen, type UnlistenFn } from '@tauri-apps/api/event'
import { useI18n } from 'vue-i18n'
import { useAppStore } from '@/stores/app'
import { useModelStore } from '@/stores/model'
import { runtimeAssetOfferBackend } from '@/utils/runtime'
import RuntimePreparationProgress from '@/components/RuntimePreparationProgress.vue'

const { t } = useI18n()
const app = useAppStore()
const models = useModelStore()

const offer = ref<RuntimeAssetStatus | null>(null)
const selectedBackend = computed(() => runtimeAssetOfferBackend(app.runtimeInfo))
const installed = computed(() => offer.value?.installed === true)
const stale = computed(() => offer.value?.stale === true)
const checked = ref(false)
const running = ref(false)
const percent = ref(0)
const downloadedMb = ref(0)
const totalMb = ref(0)
const stage = ref('')
const error = ref('')
// 远端 versions.json 声明了另一份运行时（与本地不同）时，用同一块横幅提示切换
const remoteRuntime = ref('')
let unlisten: UnlistenFn | null = null
let disposed = false
let listenerRequest: Promise<void> | null = null
let stopStatusWatch: (() => void) | undefined
let request = 0

const remoteAvailable = computed(() => Boolean(remoteRuntime.value))
const needsSetup = computed(() => app.runtimeInfo?.ready === false && !installed.value)
const canStart = computed(() => !running.value && offer.value !== null && offer.value.backend === selectedBackend.value)
const visible = computed(() => running.value || Boolean(error.value)
  || (checked.value && offer.value !== null && (needsSetup.value || remoteAvailable.value)))

async function ensureProgressListener() {
  if (unlisten || disposed) return
  if (!listenerRequest) {
    listenerRequest = (async () => {
      const stop = await listen('mediaforge://runtime-asset', event => {
        if (!disposed) void onProgress(event.payload as Parameters<typeof onProgress>[0])
      })
      if (disposed) stop()
      else unlisten = stop
    })().finally(() => { listenerRequest = null })
  }
  await listenerRequest
}

async function refreshStatus() {
  const generation = ++request
  const backend = selectedBackend.value
  offer.value = null
  remoteRuntime.value = ''
  checked.value = false
  try {
    if (!backend) return
    const status = await runtimeAssetStatus(backend)
    if (disposed || generation !== request || !status.available || status.backend !== backend) return
    offer.value = status
    if (!running.value) totalMb.value = Math.round(status.size / 1024 / 1024)
  } catch {
    // An unavailable status is not an install offer.
    return
  } finally {
    if (!disposed && generation === request) checked.value = true
  }
  if (!offer.value?.activeAssetId) return
  try {
    const result = await versionsCheck<{
      available?: { runtime?: boolean }
      remote?: { runtime?: string }
    }>(backend)
    if (disposed || generation !== request) return
    remoteRuntime.value = result?.available?.runtime ? String(result?.remote?.runtime || '') : ''
  } catch {
    // Local status stays usable when the signed version check fails.
  }
}

async function startInstall(acceptRemote = false) {
  const backend = selectedBackend.value
  if (!canStart.value || !backend) return
  running.value = true
  error.value = ''
  percent.value = 0
  downloadedMb.value = 0
  stage.value = ''
  try {
    await ensureProgressListener()
    if (disposed) return
    if (acceptRemote) {
      await versionsAcceptRuntime(backend)
      remoteRuntime.value = ''
    }
    await runtimeAssetInstall(backend)
  } catch (err) {
    running.value = false
    error.value = err instanceof Error ? err.message : String(err)
  }
}

const acceptRemoteRuntime = () => startInstall(true)

async function onProgress(payload: { stage?: string; percent?: number; downloaded?: number; total?: number; message?: string }) {
  if (typeof payload.total === 'number') totalMb.value = Math.round(payload.total / 1024 / 1024)
  stage.value = payload.stage || stage.value
  if (typeof payload.percent === 'number') percent.value = Math.round(payload.percent)
  if (typeof payload.downloaded === 'number') downloadedMb.value = Math.round(payload.downloaded / 1024 / 1024)
  if (payload.stage === 'error') {
    running.value = false
    error.value = payload.message || t('runtimeAsset.failed')
    return
  }
  if (payload.stage === 'done') {
    running.value = false
    error.value = ''
    remoteRuntime.value = ''
    await Promise.all([
      app.checkRuntimeInfo().catch(() => {}),
      models.loadModels().catch(() => {}),
    ])
    if (!disposed) await refreshStatus()
    return
  }
  if (payload.stage) {
    running.value = true
    error.value = ''
  }
}

onMounted(async () => {
  try {
    await ensureProgressListener()
  } catch {
    // 浏览器预览没有 Tauri 事件桥
  }
  if (disposed) return
  stopStatusWatch = watch(
    [selectedBackend, () => app.runtimeInfo?.ready, () => app.runtimeInfo?.installState?.pythonPath],
    () => { void refreshStatus() },
    { immediate: true },
  )
  if (!app.runtimeInfo) void app.ensureRuntimeInfo().catch(() => {})
})

onBeforeUnmount(() => {
  disposed = true
  stopStatusWatch?.()
  unlisten?.()
  unlisten = null
})
</script>

<template>
  <div v-if="visible" class="runtime-asset-banner" role="status">
    <div class="runtime-asset-banner__head">
      <strong>{{ remoteAvailable ? t('runtimeAsset.newRuntimeTitle') : stale ? t('runtimeAsset.staleTitle') : t('runtimeAsset.title') }}</strong>
      <span v-if="totalMb > 0" class="runtime-asset-banner__size">{{ totalMb }} MB</span>
    </div>
    <p class="runtime-asset-banner__body">{{ remoteAvailable ? t('runtimeAsset.newRuntimeBody', { asset: remoteRuntime }) : stale ? t('runtimeAsset.staleBody') : t('runtimeAsset.body') }}</p>
    <RuntimePreparationProgress
      v-if="running"
      :message="t('runtimeAsset.downloading')"
      :percent="percent"
      :downloaded-mb="downloadedMb"
      :total-mb="totalMb"
    />
    <p v-if="error" class="runtime-asset-banner__error">{{ error }}</p>
    <div class="runtime-asset-banner__actions">
      <n-button size="small" type="primary" :loading="running" :disabled="!canStart" @click="remoteAvailable ? acceptRemoteRuntime() : startInstall()">
        {{ running ? t('runtimeAsset.downloading') : remoteAvailable ? t('runtimeAsset.switchRuntime') : stale ? t('runtimeAsset.update') : t('runtimeAsset.start') }}
      </n-button>
    </div>
  </div>
</template>

<style scoped>
.runtime-asset-banner {
  position: fixed;
  right: 20px;
  bottom: 20px;
  z-index: 60;
  width: 320px;
  padding: 14px 16px;
  border-radius: 12px;
  border: 1px solid rgba(255, 255, 255, 0.12);
  background: rgba(24, 24, 28, 0.96);
  color: var(--on-surface-heading);
  box-shadow: 0 12px 32px rgba(0, 0, 0, 0.45);
  font-size: 13px;
}
.runtime-asset-banner__head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 6px;
}
.runtime-asset-banner__size {
  color: var(--on-surface-muted);
  font-size: 12px;
}
.runtime-asset-banner__body {
  margin: 0 0 10px;
  color: var(--on-surface);
  line-height: 1.5;
}
.runtime-asset-banner__error {
  margin: 8px 0 0;
  color: var(--danger);
}
.runtime-asset-banner__actions {
  margin-top: 12px;
  display: flex;
  justify-content: flex-end;
}
</style>
