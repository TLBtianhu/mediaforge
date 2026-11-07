import { computed, onMounted, ref, watch } from 'vue'
import { useMediaJobsStore, isJournalTerminal } from '@/stores/mediaJobs'

type Labels = { queued: () => string; running: () => string; completed: () => string; failed: () => string; cancelled: () => string }
type Messages = { success(text: string): unknown; warning(text: string): unknown; error(text: string): unknown; info(text: string): unknown }

export function useMediaJob<T extends { path: string }>(operation: string, labels: Labels, message: Messages, restore?: (payload: Record<string, unknown>) => void) {
  const store = useMediaJobsStore()
  const submitting = ref(false)
  const job = computed(() => store.latest(operation))
  const busy = computed(() => submitting.value || !!job.value && !isJournalTerminal(job.value.status))
  const progress = computed(() => Math.round(Math.max(0, Math.min(1, job.value?.fraction || 0)) * 100))
  const outputs = computed<T[]>(() => Array.isArray(job.value?.result?.outputs) ? job.value.result.outputs as T[] : [])
  const failedCount = computed(() => Array.isArray(job.value?.result?.failed) ? job.value.result.failed.length : 0)
  const status = computed(() => {
    const current = job.value
    if (!current) return ''
    if (current.status === 'queued') return labels.queued()
    if (current.message && !['queued', 'starting worker', 'completed', 'Cancelled'].includes(current.message)) return current.message
    if (current.status === 'completed') return labels.completed()
    if (current.status === 'cancelled') return labels.cancelled()
    if (current.status === 'failed' || current.status === 'interrupted') return labels.failed()
    return labels.running()
  })
  watch(() => job.value?.jobId, () => {
    if (job.value?.payload) restore?.(JSON.parse(JSON.stringify(job.value.payload)))
  }, { immediate: true })
  // Existing terminal records are restored quietly; only mounted transitions toast.
  watch(() => job.value?.status, (next, previous) => {
    if (!previous || next === previous || !next || !isJournalTerminal(next)) return
    if (next === 'completed') message.success(status.value)
    else if (next === 'cancelled') message.info(status.value)
    else if (failedCount.value) message.warning(status.value)
    else message.error(status.value)
  }, { flush: 'sync' })
  onMounted(() => { void store.initialize().catch(() => { /* Browser previews have no event bridge. */ }) })
  async function start(payload: Record<string, unknown>) {
    if (busy.value) return
    const previousId = job.value?.jobId
    submitting.value = true
    try { await store.start(payload) }
    catch (cause) {
      // Native failures after registration already reach the transition watcher.
      if (!job.value || job.value.jobId === previousId || !isJournalTerminal(job.value.status)) message.error(cause instanceof Error ? cause.message : String(cause))
    } finally { submitting.value = false }
  }
  return { job, busy, progress, outputs, failedCount, status, start }
}
