<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useSettingsStore } from '@/stores/settings'
import { useTaskStore } from '@/stores/task'
import { useAppStore } from '@/stores/app'
import {
  MusicalNotesOutline,
  CubeOutline,
  GitNetworkOutline,
  FolderOpenOutline,
  ConstructOutline,
  ExtensionPuzzleOutline,
  SettingsOutline,
  TerminalOutline,
  SwapHorizontalOutline,
  ImagesOutline,
  SparklesOutline,
  VideocamOutline,
  LayersOutline,
} from '@vicons/ionicons5'

const route = useRoute()
const { t } = useI18n()
const settings = useSettingsStore()
const taskStore = useTaskStore()
const appStore = useAppStore()

const currentTask = computed(() => taskStore.activeWorkerTasks[0] || taskStore.runningTasks[0] || null)
const runningCount = computed(() => taskStore.activeWorkerTasks.length)
const queuedCount = computed(() => taskStore.queuedTasks.length)
const failedCount = computed(() => taskStore.failedTasks.length)
const lastFailed = computed(() => taskStore.failedTasks[0] || null)
const progress = computed(() => Math.max(0, Math.min(100, Math.round(Number(currentTask.value?.progress || 0)))))
const stageLabel = computed(() => currentTask.value ? t(`runtimeStatus.stage.${currentTask.value.status}`) : '')
const statusKind = computed(() => {
  if (appStore.workerEventConnectionStatus === 'error') return 'error'
  if (currentTask.value) return 'running'
  if (queuedCount.value) return 'queued'
  if (failedCount.value) return 'failed'
  return 'idle'
})
const statusText = computed(() => {
  if (currentTask.value) return `${progress.value}%`
  if (queuedCount.value) return `${t('runtimeStatus.queued')} ${queuedCount.value}`
  if (failedCount.value) return t('runtimeStatus.failed')
  return t('runtimeStatus.idle')
})
const statusTitle = computed(() => {
  if (currentTask.value) return `${stageLabel.value} · ${progress.value}%`
  if (queuedCount.value) return `${t('runtimeStatus.queued')} ${queuedCount.value}`
  if (failedCount.value) return t('runtimeStatus.failed')
  return t('runtimeStatus.idle')
})
const connectionLabel = computed(() => t(`runtimeStatus.connection.${appStore.workerEventConnectionStatus}`))
const connectionType = computed<'success' | 'warning' | 'error' | 'default'>(() => {
  if (appStore.workerEventConnectionStatus === 'connected') return 'success'
  if (appStore.workerEventConnectionStatus === 'connecting') return 'warning'
  if (appStore.workerEventConnectionStatus === 'error') return 'error'
  return 'default'
})
const deviceLabel = computed(() => {
  const info = appStore.runtimeInfo
  const raw = info?.backend || info?.torchBackend || info?.gpuVendors?.[0]
  return raw ? String(raw).toUpperCase() : t('runtimeStatus.unknown')
})
const runtimeReady = computed(() => appStore.runtimeInfo?.ready === true)
const runtimeLabel = computed(() => {
  if (!appStore.runtimeInfo) return t('runtimeStatus.unknown')
  return runtimeReady.value ? t('runtimeStatus.ready') : t('runtimeStatus.notReady')
})

function fileName(path: string | undefined) {
  if (!path) return ''
  return path.split(/[\\/]/).filter(Boolean).pop() || path
}

function cancelCurrent() {
  if (currentTask.value) void taskStore.cancelTask(currentTask.value.id)
}
const mainGroups = computed(() => [
  {
    key: 'workspace',
    label: t('nav.groupWorkspace'),
    items: [
      { name: 'assets', path: '/assets', icon: ImagesOutline, label: t('nav.assets') },
      { name: 'separate', path: '/', icon: MusicalNotesOutline, label: t('nav.separate') },
      { name: 'image', path: '/image', icon: SparklesOutline, label: t('nav.image') },
      { name: 'video', path: '/video', icon: VideocamOutline, label: t('nav.video') },
      { name: 'factory', path: '/factory', icon: SwapHorizontalOutline, label: t('nav.factory') },
    ],
  },
  {
    key: 'models',
    label: t('nav.groupModels'),
    items: [
      { name: 'models', path: '/models', icon: CubeOutline, label: t('nav.models') },
      { name: 'mediaModels', path: '/media-models', icon: LayersOutline, label: t('nav.mediaModels') },
    ],
  },
  {
    key: 'flow',
    label: t('nav.groupFlow'),
    items: [
      { name: 'workflows', path: '/workflows', icon: GitNetworkOutline, label: t('nav.workflows') },
      { name: 'results', path: '/results', icon: FolderOpenOutline, label: t('nav.results') },
    ],
  },
  {
    key: 'extras',
    label: t('nav.groupExtras'),
    items: [
      { name: 'tools', path: '/tools', icon: ConstructOutline, label: t('nav.tools') },
      { name: 'plugins', path: '/plugins', icon: ExtensionPuzzleOutline, label: t('nav.plugins') },
    ],
  },
])
const bottomItems = computed(() => [
  ...(settings.developerMode ? [{ name: 'debug', path: '/debug', icon: TerminalOutline, label: t('nav.debug') }] : []),
  { name: 'settings', path: '/settings', icon: SettingsOutline, label: t('nav.settings') },
])
</script>

<template>
  <aside class="side-nav" data-guide="workspace-navigation">
    <div class="side-nav__main">
      <template v-for="(group, index) in mainGroups" :key="group.key">
        <div class="nav-group" :class="{ 'nav-group--first': index === 0 }">
          <div class="nav-group__label">{{ group.label }}</div>
          <router-link
            v-for="item in group.items"
            :key="item.name"
            class="nav-item"
            :class="{ active: route.name === item.name }"
            :to="item.path"
            :title="item.label"
          >
            <span class="nav-icon">
              <n-icon :component="item.icon" :size="18" />
            </span>
            <span>{{ item.label }}</span>
          </router-link>
        </div>
      </template>
    </div>
    <div class="side-nav__status">
      <n-popover trigger="click" placement="right-end" :show-arrow="false" class="status-popover">
        <template #trigger>
          <button
            type="button"
            class="status-pill"
            :class="`status-pill--${statusKind}`"
            :title="statusTitle"
          >
            <span class="status-pill__dot"></span>
            <span class="status-pill__text">{{ statusText }}</span>
            <span v-if="currentTask" class="status-pill__bar"><i :style="{ width: `${progress}%` }"></i></span>
          </button>
        </template>
        <div class="status-panel">
          <div class="status-panel__head">
            <strong>{{ t('runtimeStatus.title') }}</strong>
            <n-tag size="small" :type="connectionType" :bordered="false">{{ connectionLabel }}</n-tag>
          </div>

          <div v-if="currentTask" class="status-panel__task">
            <div class="status-panel__row">
              <span class="status-panel__name" :title="currentTask.input">{{ fileName(currentTask.input) || currentTask.model }}</span>
              <strong>{{ progress }}%</strong>
            </div>
            <div class="status-panel__stage">{{ stageLabel }} · {{ currentTask.model }}</div>
            <n-progress type="line" :percentage="progress" :show-indicator="false" />
            <n-button text type="error" size="tiny" @click="cancelCurrent">{{ t('runtimeStatus.cancel') }}</n-button>
          </div>
          <div v-else class="status-panel__empty">{{ t('runtimeStatus.noTask') }}</div>

          <div class="status-panel__stats">
            <span><b>{{ runningCount }}</b> {{ t('runtimeStatus.active') }}</span>
            <span><b>{{ queuedCount }}</b> {{ t('runtimeStatus.queue') }}</span>
            <span :class="{ 'status-panel__failed': failedCount }"><b>{{ failedCount }}</b> {{ t('runtimeStatus.failed') }}</span>
          </div>

          <div class="status-panel__runtime">
            <span>{{ t('runtimeStatus.device') }}: <b>{{ deviceLabel }}</b></span>
            <n-tag size="small" :type="runtimeReady ? 'success' : 'default'" :bordered="false">{{ runtimeLabel }}</n-tag>
          </div>

          <div v-if="lastFailed" class="status-panel__failure">
            <span>{{ t('runtimeStatus.recentFailure') }}</span>
            <p :title="lastFailed.error">{{ lastFailed.error || lastFailed.message }}</p>
          </div>

          <router-link to="/results" class="status-panel__link">{{ t('runtimeStatus.viewResults') }} →</router-link>
        </div>
      </n-popover>
    </div>
    <div class="side-nav__bottom">
      <router-link
        v-for="item in bottomItems"
        :key="item.name"
        class="nav-item"
        :class="[
          { active: route.name === item.name },
          'nav-item--utility',
          item.name === 'debug' ? 'nav-item--debug' : '',
        ]"
        :to="item.path"
        :title="item.label"
      >
        <span class="nav-icon">
          <n-icon :component="item.icon" :size="18" />
        </span>
        <span>{{ item.label }}</span>
      </router-link>
    </div>
  </aside>
</template>
