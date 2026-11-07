<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { invoke } from '@tauri-apps/api/core'
import { useI18n } from 'vue-i18n'
import { useMessage } from 'naive-ui'
import { FolderOpenOutline, RefreshOutline, SparklesOutline } from '@vicons/ionicons5'
import MediaPageHeader from '@/components/media/MediaPageHeader.vue'
import { useTaskStore } from '@/stores/task'
import { useMediaJobsStore } from '@/stores/mediaJobs'
import { cancelQueueJob, isQueueActive, isQueueFailed, mergeQueueJobs, queueOutputCount, type QueueEntry } from './queue/projection.ts'

defineOptions({ name: 'QueueView' })

const { t } = useI18n()
const message = useMessage()
const taskStore = useTaskStore()
const mediaJobs = useMediaJobsStore()
const jobs = computed(() => mergeQueueJobs(mediaJobs.jobs, taskStore.tasks))
const dataRoot = computed(() => mediaJobs.dataRoot)
const workspace = computed(() => mediaJobs.workspace)
const loading = computed(() => mediaJobs.loading)
const error = computed(() => mediaJobs.error)
const cancelling = ref(new Set<string>())
const query = ref('')
const sourceFilter = ref<'all' | 'manual' | 'agent'>('all')
const statusFilter = ref<'all' | 'active' | 'completed' | 'failed'>('all')
let timer: ReturnType<typeof setInterval> | undefined
let disposed = false

const sourceOptions = computed(() => [
  { label: t('agent.allSources'), value: 'all' },
  { label: t('agent.manual'), value: 'manual' },
  { label: t('agent.agent'), value: 'agent' },
])
const statusOptions = computed(() => [
  { label: t('agent.allStatuses'), value: 'all' },
  { label: t('agent.active'), value: 'active' },
  { label: t('agent.completed'), value: 'completed' },
  { label: t('agent.failed'), value: 'failed' },
])
const filteredJobs = computed(() => {
  const text = query.value.trim().toLowerCase()
  return jobs.value.filter(job => {
    const source = job.source || 'agent'
    if (sourceFilter.value !== 'all' && source !== sourceFilter.value) return false
    if (statusFilter.value === 'active' && !isQueueActive(job)) return false
    if (statusFilter.value === 'completed' && job.status !== 'completed') return false
    if (statusFilter.value === 'failed' && !isQueueFailed(job)) return false
    if (!text) return true
    return [job.command, job.payload?.operation, jobTitle(job), job.jobId, job.message, job.error, job.payload?.input, ...(Array.isArray(job.payload?.inputs) ? job.payload.inputs : [])]
      .some(value => String(value || '').toLowerCase().includes(text))
  })
})

const running = computed(() => jobs.value.filter(isQueueActive).length)
const completed = computed(() => jobs.value.filter(job => job.status === 'completed').length)
const failed = computed(() => jobs.value.filter(isQueueFailed).length)

function jobTitle(job: QueueEntry) {
  const operation = String(job.payload?.operation || job.command)
  const titles: Record<string, string> = {
    media_image: 'image.title', media_video: 'video.title',
    media_convert: 'factory.title', media_workflow: 'mediaWorkflow.title',
  }
  return titles[operation] ? t(titles[operation]) : operation
}

function statusType(status: string) {
  if (status === 'completed') return 'success'
  if (status === 'failed' || status === 'interrupted') return 'error'
  if (status === 'cancelled') return 'default'
  return 'info'
}

function formatTime(value?: number | null) {
  if (!value) return '—'
  return new Date(value * 1000).toLocaleString()
}

async function load() {
  if (disposed) return
  // The resident store merges live events and orders concurrent journal reads.
  // Its error/loading state remains available when this view is not mounted.
  await mediaJobs.refresh().catch(() => {})
}

async function openWorkspace() {
  try {
    await invoke('reveal_path', { path: dataRoot.value })
  } catch (cause) {
    if (!disposed) message.error(cause instanceof Error ? cause.message : String(cause))
  }
}

async function cancel(job: QueueEntry) {
  if (disposed || cancelling.value.has(job.jobId) || !isQueueActive(job)) return
  cancelling.value.add(job.jobId)
  try {
    const requested = await cancelQueueJob(job, {
      audio: taskId => taskStore.cancelTask(taskId),
      manual: taskId => invoke('cancel_task', { taskId }),
      agent: jobId => invoke('cancel_agent_job', { jobId }),
    })
    if (disposed) return
    if (requested) message.success(t('agent.cancelRequested'))
    await load()
  } catch (cause) {
    if (!disposed) message.error(cause instanceof Error ? cause.message : String(cause))
  } finally {
    if (!disposed) cancelling.value.delete(job.jobId)
  }
}

onMounted(() => {
  void mediaJobs.initialize().catch(() => {})
  timer = setInterval(() => {
    // Live events come from the resident store; polling also discovers MCP jobs.
    if (typeof document !== 'undefined' && document.hidden) return
    void load()
  }, 10000)
})

onBeforeUnmount(() => {
  disposed = true
  if (timer) clearInterval(timer)
})
</script>

<template>
  <div class="page mf-page" data-tone="violet">
    <MediaPageHeader
      :title="t('agent.title')"
      :subtitle="t('agent.subtitle')"
      :icon="SparklesOutline"
      eyebrow="SHARED QUEUE"
      tone="violet"
    >
      <n-tag :type="running ? 'info' : 'default'" :bordered="false">{{ running }} {{ t('agent.running') }}</n-tag>
      <n-button quaternary :loading="loading" @click="load">
        <template #icon><n-icon :component="RefreshOutline" /></template>
      </n-button>
    </MediaPageHeader>

    <n-alert v-if="error" type="error" :bordered="false">{{ error }}</n-alert>

    <section class="agent-stats">
      <div><strong>{{ jobs.length }}</strong><span>{{ t('agent.total') }}</span></div>
      <div><strong>{{ running }}</strong><span>{{ t('agent.running') }}</span></div>
      <div><strong>{{ completed }}</strong><span>{{ t('agent.completed') }}</span></div>
      <div><strong>{{ failed }}</strong><span>{{ t('agent.failed') }}</span></div>
    </section>

    <section class="mf-panel agent-workspace">
      <div class="mf-panel__head">
        <span class="mf-panel__title">{{ t('agent.workspace') }}</span>
        <n-button text @click="openWorkspace"><template #icon><n-icon :component="FolderOpenOutline" /></template>{{ t('agent.openWorkspace') }}</n-button>
      </div>
      <div class="agent-path">{{ dataRoot || t('agent.noWorkspace') }}</div>
      <div class="agent-path agent-path--muted">{{ workspace }}</div>
    </section>

    <section class="agent-toolbar">
      <n-input v-model:value="query" clearable :placeholder="t('agent.search')" />
      <n-select v-model:value="sourceFilter" :options="sourceOptions" />
      <n-select v-model:value="statusFilter" :options="statusOptions" />
    </section>

    <n-spin :show="loading">
      <div v-if="!filteredJobs.length" class="mf-empty">
        <span class="mf-empty__icon"><n-icon :component="SparklesOutline" :size="30" /></span>
        <div class="mf-empty__title">{{ jobs.length ? t('agent.noMatch') : t('agent.emptyTitle') }}</div>
        <template v-if="!jobs.length">
          <div class="mf-empty__hint">{{ t('agent.emptyHint') }}</div>
          <code class="agent-command">.\scripts\start-mcp.ps1</code>
        </template>
      </div>
      <div v-else class="agent-list">
        <article v-for="job in filteredJobs" :key="job.jobId" class="agent-job">
          <div class="agent-job__head">
            <div class="agent-job__title"><strong>{{ jobTitle(job) }}</strong><span>{{ job.jobId }}</span></div>
            <div class="agent-job__actions">
              <n-button v-if="isQueueActive(job)" text size="tiny" :loading="cancelling.has(job.jobId)" :disabled="cancelling.has(job.jobId)" @click="cancel(job)">{{ t('agent.cancel') }}</n-button>
              <n-tag size="small" :type="statusType(job.status)" :bordered="false">{{ job.status }}</n-tag>
            </div>
          </div>
          <div class="agent-job__meta"><span>{{ job.source === 'manual' ? t('agent.manual') : t('agent.agent') }}</span><span>{{ formatTime(job.createdAt) }}</span><span>{{ queueOutputCount(job) }} {{ t('agent.outputs') }}</span></div>
          <n-progress v-if="isQueueActive(job)" type="line" :percentage="Math.round(Number(job.fraction || 0) * 100)" />
          <p class="agent-job__message">{{ job.error || job.message }}</p>
        </article>
      </div>
    </n-spin>
  </div>
</template>

<style scoped>
.agent-stats { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
.agent-stats div { display: flex; align-items: baseline; gap: 8px; padding: 17px 18px; border-radius: 18px; background: color-mix(in srgb, var(--surface-1) 78%, transparent); box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--outline) 80%, transparent), inset 0 1px 0 rgba(255,255,255,.03), var(--shadow-panel); }
.agent-stats strong { color: var(--on-surface-heading); font-size: 20px; }
.agent-stats span { color: var(--on-surface-muted); font-size: 11px; }
.agent-workspace { display: grid; gap: 7px; }
.agent-toolbar { display: grid; grid-template-columns: minmax(0, 1fr) 150px 150px; gap: 10px; }
.agent-path { overflow: hidden; color: var(--on-surface); font-family: var(--font-mono, monospace); font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
.agent-path--muted { color: var(--on-surface-muted); font-size: 10px; }
.agent-command { padding: 7px 10px; border: 0; border-radius: 8px; background: var(--surface-2); box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--outline) 80%, transparent); color: var(--on-surface-muted); font-size: 11px; }
.agent-list { display: grid; gap: 10px; }
.agent-job { display: grid; gap: 8px; padding: 17px 18px; border-radius: 18px; background: color-mix(in srgb, var(--surface-1) 78%, transparent); box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--outline) 80%, transparent), inset 0 1px 0 rgba(255,255,255,.03), var(--shadow-panel); }
.agent-job__head { display: flex; gap: 10px; align-items: center; justify-content: space-between; }
.agent-job__actions { display: flex; gap: 8px; align-items: center; }
.agent-job__title { display: flex; min-width: 0; flex-direction: column; gap: 2px; }
.agent-job__title strong { color: var(--on-surface-heading); font-size: 12px; }
.agent-job__title span { overflow: hidden; color: var(--on-surface-muted); font-family: var(--font-mono, monospace); font-size: 10px; text-overflow: ellipsis; white-space: nowrap; }
.agent-job__meta { display: flex; gap: 14px; color: var(--on-surface-muted); font-size: 10px; }
.agent-job__message { margin: 0; overflow: hidden; color: var(--on-surface-muted); font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
@media (max-width: 760px) { .agent-stats { grid-template-columns: repeat(2, minmax(0, 1fr)); } .agent-toolbar { grid-template-columns: 1fr; } }
</style>
