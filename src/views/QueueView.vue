<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { invoke } from '@tauri-apps/api/core'
import { useI18n } from 'vue-i18n'
import { useMessage } from 'naive-ui'
import { FolderOpenOutline, RefreshOutline, SparklesOutline } from '@vicons/ionicons5'
import MediaPageHeader from '@/components/media/MediaPageHeader.vue'

defineOptions({ name: 'QueueView' })

type AgentJob = {
  jobId: string
  command: string
  status: string
  source?: 'manual' | 'agent'
  cancelTaskId?: string
  fraction: number
  message: string
  createdAt: number
  finishedAt?: number | null
  payload?: Record<string, unknown>
  result?: Record<string, unknown> | null
  error?: string | null
}
type AgentResponse = {
  ok: boolean
  dataRoot: string
  workspace: string
  index: { version: number; jobs: AgentJob[] }
}

const { t } = useI18n()
const message = useMessage()
const jobs = ref<AgentJob[]>([])
const dataRoot = ref('')
const workspace = ref('')
const loading = ref(false)
const error = ref('')
const query = ref('')
const sourceFilter = ref<'all' | 'manual' | 'agent'>('all')
const statusFilter = ref<'all' | 'active' | 'completed' | 'failed'>('all')
let timer: ReturnType<typeof setInterval> | undefined

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
    if (statusFilter.value === 'active' && !['running', 'queued'].includes(job.status)) return false
    if (statusFilter.value === 'completed' && job.status !== 'completed') return false
    if (statusFilter.value === 'failed' && job.status !== 'failed') return false
    if (!text) return true
    return [job.command, job.jobId, job.message, job.error].some(value => String(value || '').toLowerCase().includes(text))
  })
})

const running = computed(() => jobs.value.filter(job => job.status === 'running' || job.status === 'queued').length)
const completed = computed(() => jobs.value.filter(job => job.status === 'completed').length)
const failed = computed(() => jobs.value.filter(job => job.status === 'failed').length)

function statusType(status: string) {
  if (status === 'completed') return 'success'
  if (status === 'failed') return 'error'
  if (status === 'cancelled') return 'default'
  return 'info'
}

function formatTime(value?: number | null) {
  if (!value) return '—'
  return new Date(value * 1000).toLocaleString()
}

function outputCount(job: AgentJob) {
  const outputs = job.result?.outputs
  return Array.isArray(outputs) ? outputs.length : 0
}

async function load() {
  if (typeof window === 'undefined' || !('__TAURI_INTERNALS__' in window)) return
  loading.value = true
  try {
    const response = await invoke<AgentResponse>('agent_jobs')
    jobs.value = Array.isArray(response.index?.jobs) ? response.index.jobs : []
    dataRoot.value = response.dataRoot || ''
    workspace.value = response.workspace || ''
    error.value = ''
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  } finally {
    loading.value = false
  }
}

async function openWorkspace() {
  try {
    await invoke('reveal_path', { path: dataRoot.value })
  } catch (cause) {
    message.error(cause instanceof Error ? cause.message : String(cause))
  }
}

async function cancel(job: AgentJob) {
  try {
    if ((job.source || 'agent') === 'manual') {
      await invoke('cancel_task', { taskId: job.cancelTaskId || job.jobId })
    } else {
      await invoke('cancel_agent_job', { jobId: job.jobId })
    }
    message.success(t('agent.cancelRequested'))
    await load()
  } catch (cause) {
    message.error(cause instanceof Error ? cause.message : String(cause))
  }
}

onMounted(() => {
  void load()
  timer = setInterval(() => { void load() }, 3000)
})

onBeforeUnmount(() => {
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
            <div class="agent-job__title"><strong>{{ job.command }}</strong><span>{{ job.jobId }}</span></div>
            <div class="agent-job__actions">
              <n-button v-if="job.status === 'running' || job.status === 'queued'" text size="tiny" @click="cancel(job)">{{ t('agent.cancel') }}</n-button>
              <n-tag size="small" :type="statusType(job.status)" :bordered="false">{{ job.status }}</n-tag>
            </div>
          </div>
          <div class="agent-job__meta"><span>{{ job.source === 'manual' ? t('agent.manual') : t('agent.agent') }}</span><span>{{ formatTime(job.createdAt) }}</span><span>{{ outputCount(job) }} {{ t('agent.outputs') }}</span></div>
          <n-progress v-if="job.status === 'running' || job.status === 'queued'" type="line" :percentage="Math.round(Number(job.fraction || 0) * 100)" />
          <p class="agent-job__message">{{ job.error || job.message }}</p>
        </article>
      </div>
    </n-spin>
  </div>
</template>

<style scoped>
.agent-stats { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
.agent-stats div { display: flex; align-items: baseline; gap: 8px; padding: 13px 15px; border: 1px solid var(--divider-color); border-radius: 14px; background: var(--card-color); }
.agent-stats strong { color: var(--text-color-1); font-size: 20px; }
.agent-stats span { color: var(--text-color-3); font-size: 11px; }
.agent-workspace { display: grid; gap: 7px; }
.agent-toolbar { display: grid; grid-template-columns: minmax(0, 1fr) 150px 150px; gap: 10px; }
.agent-path { overflow: hidden; color: var(--text-color-2); font-family: var(--font-mono, monospace); font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
.agent-path--muted { color: var(--text-color-3); font-size: 10px; }
.agent-command { padding: 7px 10px; border: 1px solid var(--divider-color); border-radius: 8px; background: var(--action-color); color: var(--text-color-2); font-size: 11px; }
.agent-list { display: grid; gap: 10px; }
.agent-job { display: grid; gap: 8px; padding: 14px 16px; border: 1px solid var(--divider-color); border-radius: 14px; background: var(--card-color); }
.agent-job__head { display: flex; gap: 10px; align-items: center; justify-content: space-between; }
.agent-job__actions { display: flex; gap: 8px; align-items: center; }
.agent-job__title { display: flex; min-width: 0; flex-direction: column; gap: 2px; }
.agent-job__title strong { color: var(--text-color-1); font-size: 12px; }
.agent-job__title span { overflow: hidden; color: var(--text-color-3); font-family: var(--font-mono, monospace); font-size: 10px; text-overflow: ellipsis; white-space: nowrap; }
.agent-job__meta { display: flex; gap: 14px; color: var(--text-color-3); font-size: 10px; }
.agent-job__message { margin: 0; overflow: hidden; color: var(--text-color-2); font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
@media (max-width: 760px) { .agent-stats { grid-template-columns: repeat(2, minmax(0, 1fr)); } .agent-toolbar { grid-template-columns: 1fr; } }
</style>

