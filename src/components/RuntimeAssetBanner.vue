<script setup lang="ts">
// 首次运行时：应用本身能开（界面不依赖 Python），但处理能力还没就绪。
// 这里负责把"缺运行时"变成一条可操作的引导，并在装好后原地刷新状态。
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { invoke } from '@tauri-apps/api/core'
import { listen, type UnlistenFn } from '@tauri-apps/api/event'
import { useI18n } from 'vue-i18n'
import { useAppStore } from '@/stores/app'
import { useModelStore } from '@/stores/model'

const { t } = useI18n()
const app = useAppStore()
const models = useModelStore()

const installed = ref(true)
const stale = ref(false)
const checked = ref(false)
const running = ref(false)
const percent = ref(0)
const downloadedMb = ref(0)
const totalMb = ref(0)
const stage = ref('')
const error = ref('')
let unlisten: UnlistenFn | null = null

const visible = computed(() => checked.value && !installed.value)
const canStart = computed(() => !running.value && !error.value)

async function refreshStatus() {
  try {
    const status = await invoke<{
      installed: boolean
      stale?: boolean
      size: number
      sha256: string
    }>('runtime_asset_status')
    installed.value = Boolean(status.installed)
    // stale=true 表示本地那份运行时存在但不是当前版本（缺依赖或版本不符），
    // 这时的文案要说"需要更新"，否则用户会以为要重装整个应用。
    stale.value = Boolean(status.stale)
    totalMb.value = Math.round((status.size || 0) / 1024 / 1024)
  } catch (err) {
    // 命令不存在（旧版本）时不要打扰用户
    installed.value = true
    error.value = ''
  } finally {
    checked.value = true
  }
}

async function startInstall() {
  if (!canStart.value) return
  running.value = true
  error.value = ''
  percent.value = 0
  try {
    await invoke('runtime_asset_install')
  } catch (err) {
    running.value = false
    error.value = err instanceof Error ? err.message : String(err)
  }
}

async function onProgress(payload: { stage?: string; percent?: number; downloaded?: number; message?: string }) {
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
    installed.value = true
    // worker 每条命令都会重读 active-runtime.json，所以这里只需刷新状态，不用重启应用。
    await Promise.all([
      app.checkRuntimeInfo().catch(() => {}),
      models.loadModels().catch(() => {}),
    ])
  }
}

onMounted(async () => {
  await refreshStatus()
  try {
    unlisten = await listen('mediaforge://runtime-asset', event => {
      onProgress(event.payload as Parameters<typeof onProgress>[0])
    })
  } catch {
    // 浏览器预览没有 Tauri 事件桥
  }
})

onBeforeUnmount(() => {
  unlisten?.()
  unlisten = null
})
</script>

<template>
  <div v-if="visible" class="runtime-asset-banner" role="status">
    <div class="runtime-asset-banner__head">
      <strong>{{ stale ? t('runtimeAsset.staleTitle') : t('runtimeAsset.title') }}</strong>
      <span class="runtime-asset-banner__size">{{ totalMb }} MB</span>
    </div>
    <p class="runtime-asset-banner__body">{{ stale ? t('runtimeAsset.staleBody') : t('runtimeAsset.body') }}</p>
    <div v-if="running" class="runtime-asset-banner__progress">
      <div class="runtime-asset-banner__bar">
        <div class="runtime-asset-banner__fill" :style="{ width: `${percent}%` }" />
      </div>
      <span>{{ percent }}% · {{ downloadedMb }} / {{ totalMb }} MB</span>
    </div>
    <p v-if="error" class="runtime-asset-banner__error">{{ error }}</p>
    <div class="runtime-asset-banner__actions">
      <n-button size="small" type="primary" :loading="running" :disabled="!canStart" @click="startInstall">
        {{ running ? t('runtimeAsset.downloading') : stale ? t('runtimeAsset.update') : t('runtimeAsset.start') }}
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
  color: #e8e8ea;
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
  color: #9a9aa2;
  font-size: 12px;
}
.runtime-asset-banner__body {
  margin: 0 0 10px;
  color: #b6b6bd;
  line-height: 1.5;
}
.runtime-asset-banner__bar {
  height: 6px;
  border-radius: 3px;
  background: rgba(255, 255, 255, 0.12);
  overflow: hidden;
}
.runtime-asset-banner__fill {
  height: 100%;
  background: #e8c069;
  transition: width 0.3s ease;
}
.runtime-asset-banner__progress span {
  display: block;
  margin-top: 6px;
  color: #9a9aa2;
  font-size: 12px;
}
.runtime-asset-banner__error {
  margin: 8px 0 0;
  color: #f07178;
}
.runtime-asset-banner__actions {
  margin-top: 12px;
  display: flex;
  justify-content: flex-end;
}
</style>
