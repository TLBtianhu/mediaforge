import { mutateMediaWorkflows } from '@/api/native'
import { ref } from 'vue'
import { defineStore } from 'pinia'
import { isTauriRuntime, loadAppStore, saveAppStore } from '@/utils/appStore'
import { applyMediaWorkflowMutation, normalizeMediaWorkflowDefinition, normalizeMediaWorkflowState,
  type MediaWorkflowDefinition, type MediaWorkflowMutation, type MediaWorkflowPreset, type MediaWorkflowResponse } from '@/features/media/workflowPresets'
export type { MediaWorkflowDefinition, MediaWorkflowPreset, MediaPlanStep } from '@/features/media/workflowPresets'

export class MediaWorkflowConflictError extends Error {
  readonly code = 'MEDIA_WORKFLOW_CONFLICT'
  constructor() { super('Media workflow changed in another window'); this.name = 'MediaWorkflowConflictError' }
}

// Serialize preview instances as well; native windows use one backend transaction.
let previewQueue: Promise<unknown> = Promise.resolve()
export const useMediaWorkflowsStore = defineStore('mediaWorkflows', () => {
  const presets = ref<MediaWorkflowPreset[]>([])
  const loading = ref(false)
  const error = ref('')
  let initialized = false
  let refreshing: Promise<void> | undefined
  let revision = 0
  let mutationQueue: Promise<unknown> = Promise.resolve()

  function accept(response: MediaWorkflowResponse) {
    const state = normalizeMediaWorkflowState(response.state)
    presets.value = state.presets
    revision++
    if (response.conflict) throw new MediaWorkflowConflictError()
  }
  function refresh(): Promise<void> {
    if (refreshing) return refreshing
    loading.value = true
    const atStart = revision
    refreshing = (async () => {
      try {
        const state = normalizeMediaWorkflowState(await loadAppStore('media-workflows'))
        if (revision === atStart) {
          presets.value = state.presets
          initialized = true
          error.value = ''
        }
      } catch (cause) {
        if (revision === atStart) {
          error.value = cause instanceof Error ? cause.message : String(cause)
          throw cause
        }
      } finally { loading.value = false; refreshing = undefined }
    })()
    return refreshing
  }
  async function initialize() { if (!initialized) await refresh() }
  function mutate(mutation: MediaWorkflowMutation): Promise<void> {
    const operation = async () => {
      try {
        let response: MediaWorkflowResponse
        if (isTauriRuntime()) response = await mutateMediaWorkflows<MediaWorkflowResponse>(mutation)
        else {
          const preview = previewQueue.catch(() => {}).then(async () => {
            const result = applyMediaWorkflowMutation(await loadAppStore('media-workflows'), mutation)
            if (!result.conflict) await saveAppStore('media-workflows', result.state)
            return result
          })
          previewQueue = preview
          response = await preview
        }
        accept(response)
        initialized = true
        error.value = ''
      } catch (cause) {
        error.value = cause instanceof Error ? cause.message : String(cause)
        throw cause
      }
    }
    const pending = mutationQueue.catch(() => {}).then(operation)
    mutationQueue = pending
    return pending
  }
  async function save(input: { id?: string; name: string; definition: MediaWorkflowDefinition; expectedUpdatedAt?: number }) {
    // Freeze before any queued write or read can yield to the editor.
    const mutation: MediaWorkflowMutation = { action: 'upsert', id: input.id || `media_${crypto.randomUUID().replaceAll('-', '')}`,
      name: input.name, definition: normalizeMediaWorkflowDefinition(input.definition), expectedUpdatedAt: input.expectedUpdatedAt }
    if (input.id && input.expectedUpdatedAt == null) throw new Error('A saved preset requires its original revision')
    await mutate(mutation)
    const saved = presets.value.find(item => item.id === mutation.id)
    if (!saved) throw new Error('Saved media workflow is missing')
    return JSON.parse(JSON.stringify(saved)) as MediaWorkflowPreset
  }
  async function remove(id: string, expectedUpdatedAt: number) { await mutate({ action: 'delete', id, expectedUpdatedAt }) }
  return { presets, loading, error, initialize, refresh, save, remove }
})
