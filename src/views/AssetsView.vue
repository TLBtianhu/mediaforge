<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { convertFileSrc, invoke } from '@tauri-apps/api/core'
import { getCurrentWebview } from '@tauri-apps/api/webview'
import { open } from '@tauri-apps/plugin-dialog'
import { useI18n } from 'vue-i18n'
import { useMessage } from 'naive-ui'
import { useRouter } from 'vue-router'
import {
  FolderOpenOutline,
  ImagesOutline,
  MusicalNotesOutline,
  RefreshOutline,
  SwapHorizontalOutline,
  TrashOutline,
  VideocamOutline,
} from '@vicons/ionicons5'

defineOptions({ name: 'AssetsView' })

type Asset = {
  id: number
  path: string
  name: string
  kind: 'audio' | 'image' | 'video'
  format: string
  size: number
  duration: number
  width: number
  height: number
  fps: number
  thumbnail: string
  tags: string
  created_at: string
  updated_at: string
}
type LibraryStats = {
  kinds?: Record<string, { count: number; bytes: number }>
  total?: { count: number; bytes: number }
  database?: string
}
type LibraryResult = {
  ok?: boolean
  assets?: Asset[]
  errors?: Array<{ path: string; error: string }>
  stats?: LibraryStats
  total?: number
  removed?: number
}

const { t } = useI18n()
const message = useMessage()
const router = useRouter()
const assets = ref<Asset[]>([])
const stats = ref<LibraryStats>({})
const query = ref('')
const kind = ref<'all' | 'audio' | 'image' | 'video'>('all')
const busy = ref(false)
const importing = ref(false)
let unlistenDrop: (() => void) | undefined

const kindOptions = computed(() => [
  { label: t('assets.all'), value: 'all' },
  { label: t('assets.image'), value: 'image' },
  { label: t('assets.video'), value: 'video' },
  { label: t('assets.audio'), value: 'audio' },
])

const totalCount = computed(() => stats.value.total?.count ?? assets.value.length)
const statsText = computed(() => {
  const kinds = stats.value.kinds || {}
  return [
    t('assets.imageCount', { count: kinds.image?.count || 0 }),
    t('assets.videoCount', { count: kinds.video?.count || 0 }),
    t('assets.audioCount', { count: kinds.audio?.count || 0 }),
  ].join(' · ')
})

function kindLabel(value: Asset['kind']) {
  return t(`assets.${value}`)
}

function kindIcon(value: Asset['kind']) {
  if (value === 'image') return ImagesOutline
  if (value === 'video') return VideocamOutline
  return MusicalNotesOutline
}

function formatSize(bytes: number) {
  if (!bytes) return '—'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let value = bytes
  let index = 0
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024
    index += 1
  }
  return `${value.toFixed(value >= 10 || index === 0 ? 0 : 1)} ${units[index]}`
}

function formatDuration(asset: Asset) {
  if (asset.kind === 'image' || !asset.duration) return ''
  const total = Math.round(asset.duration)
  const minutes = Math.floor(total / 60)
  const seconds = total % 60
  return `${minutes}:${String(seconds).padStart(2, '0')}`
}

function resolution(asset: Asset) {
  if (!asset.width || !asset.height) return ''
  return `${asset.width}×${asset.height}`
}

function thumbnailSrc(asset: Asset) {
  return asset.thumbnail ? convertFileSrc(asset.thumbnail) : ''
}

async function refresh() {
  try {
    const result = await invoke<LibraryResult>('media_library', {
      payload: { operation: 'list', query: query.value, kind: kind.value, limit: 1000 },
    })
    assets.value = result.assets || []
    const statsResult = await invoke<LibraryResult>('media_library', {
      payload: { operation: 'stats' },
    })
    stats.value = statsResult.stats || {}
  } catch (error) {
    message.error(error instanceof Error ? error.message : String(error))
  }
}

async function importPaths(paths: string[]) {
  if (!paths.length) return
  importing.value = true
  busy.value = true
  try {
    const result = await invoke<LibraryResult>('media_library', {
      payload: { operation: 'import', paths },
    })
    const count = result.assets?.length || 0
    const errors = result.errors?.length || 0
    message.success(t('assets.imported', { count }))
    if (errors) message.warning(`${errors} file(s) skipped`)
    await refresh()
  } catch (error) {
    message.error(error instanceof Error ? error.message : String(error))
  } finally {
    importing.value = false
    busy.value = false
  }
}

async function pickFiles() {
  try {
    const selected = await open({
      multiple: true,
      filters: [{
        name: 'Media',
        extensions: ['mp4', 'mov', 'mkv', 'avi', 'webm', 'mp3', 'wav', 'flac', 'm4a', 'aac', 'ogg', 'jpg', 'jpeg', 'png', 'webp', 'gif'],
      }],
    })
    if (!selected) return
    await importPaths(Array.isArray(selected) ? selected : [selected])
  } catch (error) {
    message.error(error instanceof Error ? error.message : String(error))
  }
}

async function pickFolder() {
  try {
    const selected = await open({ directory: true, multiple: true })
    if (!selected) return
    await importPaths(Array.isArray(selected) ? selected : [selected])
  } catch (error) {
    message.error(error instanceof Error ? error.message : String(error))
  }
}

async function removeAsset(asset: Asset) {
  try {
    await invoke<LibraryResult>('media_library', {
      payload: { operation: 'remove', ids: [asset.id] },
    })
    await refresh()
  } catch (error) {
    message.error(error instanceof Error ? error.message : String(error))
  }
}

async function revealAsset(asset: Asset) {
  try {
    await invoke('reveal_path', { path: asset.path })
  } catch (error) {
    message.error(error instanceof Error ? error.message : String(error))
  }
}

function openInFactory(asset: Asset) {
  void router.push({ name: 'factory', query: { asset: asset.path } })
}

watch([query, kind], () => { void refresh() })

onMounted(async () => {
  await refresh()
  if (typeof window === 'undefined' || !('__TAURI_INTERNALS__' in window)) return
  try {
    unlistenDrop = await getCurrentWebview().onDragDropEvent(event => {
      if (event.payload.type === 'drop' && event.payload.paths.length) {
        void importPaths(event.payload.paths)
      }
    })
  } catch {
    // Native drag/drop is unavailable in browser preview.
  }
})

onBeforeUnmount(() => {
  unlistenDrop?.()
})
</script>

<template>
  <div class="page assets-page">
    <div class="page-header-compact">
      <div>
        <h1>{{ t('assets.title') }}</h1>
        <p>{{ t('assets.subtitle') }}</p>
      </div>
      <n-space>
        <n-button secondary :loading="importing" :disabled="busy" @click="pickFiles">
          <template #icon><n-icon :component="ImagesOutline" /></template>
          {{ t('assets.importFiles') }}
        </n-button>
        <n-button secondary :loading="importing" :disabled="busy" @click="pickFolder">
          <template #icon><n-icon :component="FolderOpenOutline" /></template>
          {{ t('assets.importFolder') }}
        </n-button>
        <n-button quaternary :disabled="busy" @click="refresh">
          <template #icon><n-icon :component="RefreshOutline" /></template>
        </n-button>
      </n-space>
    </div>

    <div class="assets-toolbar">
      <n-input v-model:value="query" clearable :placeholder="t('assets.search')" />
      <n-select v-model:value="kind" :options="kindOptions" class="assets-kind" />
      <span class="assets-total">{{ t('assets.total', { count: totalCount }) }}</span>
    </div>

    <n-spin :show="busy">
      <n-empty v-if="!assets.length" :description="t('assets.empty')" class="assets-empty">
        <template #extra>
          <div class="assets-empty-hint">{{ t('assets.emptyHint') }}</div>
        </template>
      </n-empty>

      <div v-else class="assets-grid">
        <article v-for="asset in assets" :key="asset.id" class="asset-card">
          <div class="asset-thumb">
            <img v-if="asset.thumbnail" :src="thumbnailSrc(asset)" :alt="asset.name" loading="lazy" />
            <n-icon v-else :component="kindIcon(asset.kind)" size="34" />
            <n-tag class="asset-kind" size="small" :bordered="false">{{ kindLabel(asset.kind) }}</n-tag>
          </div>
          <div class="asset-body">
            <div class="asset-name" :title="asset.path">{{ asset.name }}</div>
            <div class="asset-sub">
              <span v-if="resolution(asset)">{{ resolution(asset) }}</span>
              <span v-if="formatDuration(asset)">{{ formatDuration(asset) }}</span>
              <span>{{ formatSize(asset.size) }}</span>
            </div>
            <div class="asset-actions">
              <n-button text size="small" @click="openInFactory(asset)">
                <template #icon><n-icon :component="SwapHorizontalOutline" /></template>
                {{ t('assets.toFactory') }}
              </n-button>
              <n-button text size="small" @click="revealAsset(asset)">
                <template #icon><n-icon :component="FolderOpenOutline" /></template>
                {{ t('assets.openFolder') }}
              </n-button>
              <n-popconfirm @positive-click="removeAsset(asset)">
                <template #trigger>
                  <n-button text size="small" type="error">
                    <template #icon><n-icon :component="TrashOutline" /></template>
                  </n-button>
                </template>
                {{ t('assets.removeConfirm') }}
              </n-popconfirm>
            </div>
          </div>
        </article>
      </div>
    </n-spin>

    <div class="assets-footer">
      <span>{{ statsText }}</span>
      <span v-if="stats.database" class="assets-db" :title="stats.database">{{ t('assets.database') }}: {{ stats.database }}</span>
    </div>
  </div>
</template>

<style scoped>
.assets-page { display: flex; flex-direction: column; gap: 12px; }
.assets-toolbar { display: grid; grid-template-columns: minmax(0, 1fr) 160px auto; gap: 10px; align-items: center; }
.assets-total { color: var(--text-color-3); font-size: 12px; white-space: nowrap; }
.assets-empty { padding: 64px 0; }
.assets-empty-hint { max-width: 440px; color: var(--text-color-3); font-size: 13px; line-height: 1.6; }
.assets-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 14px; }
.asset-card { overflow: hidden; border: 1px solid var(--divider-color); border-radius: 10px; background: var(--card-color); }
.asset-thumb { position: relative; display: flex; align-items: center; justify-content: center; aspect-ratio: 16 / 10; overflow: hidden; background: var(--action-color); color: var(--text-color-3); }
.asset-thumb img { width: 100%; height: 100%; object-fit: cover; }
.asset-kind { position: absolute; top: 8px; left: 8px; }
.asset-body { padding: 10px 12px 12px; }
.asset-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 13px; font-weight: 600; }
.asset-sub { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 4px; color: var(--text-color-3); font-size: 11px; }
.asset-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
.assets-footer { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 8px; color: var(--text-color-3); font-size: 11px; }
.assets-db { overflow: hidden; max-width: 52%; text-overflow: ellipsis; white-space: nowrap; }
@media (max-width: 760px) { .assets-toolbar { grid-template-columns: 1fr; } .assets-total { display: none; } .assets-db { max-width: 100%; } }
</style>
