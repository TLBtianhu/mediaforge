// DebugView 的更新端点覆盖：草稿、校验与保存。
import { computed, ref, type Ref } from 'vue'

type Options = {
  t: (key: string, params?: Record<string, unknown>) => string
  message: { success: (content: string) => void; error: (content: string) => void }
  updateEndpointOverride: Ref<string>
}

export function useUpdateEndpoint({ t, message, updateEndpointOverride }: Options) {
const updateEndpointEditing = ref(false)

const updateEndpointDraft = ref('')

const effectiveUpdateEndpoint = computed(() => updateEndpointOverride.value.trim() || t('debug.updateEndpointManaged'))

const updateEndpointMode = computed(() => updateEndpointOverride.value.trim() ? t('debug.updateEndpointCustom') : t('debug.updateEndpointDefault'))

function beginUpdateEndpointEdit() {
  updateEndpointDraft.value = updateEndpointOverride.value.trim()
  updateEndpointEditing.value = true
}

function cancelUpdateEndpointEdit() {
  updateEndpointDraft.value = ''
  updateEndpointEditing.value = false
}

function saveUpdateEndpointOverride() {
  const endpoint = updateEndpointDraft.value.trim()
  if (!endpoint) {
    updateEndpointOverride.value = ''
    cancelUpdateEndpointEdit()
    message.success(t('debug.updateEndpointRestored'))
    return
  }
  try {
    new URL(endpoint)
  } catch {
    message.error(t('debug.updateEndpointInvalid'))
    return
  }
  updateEndpointOverride.value = endpoint
  cancelUpdateEndpointEdit()
  message.success(t('debug.updateEndpointSaved'))
}

function resetUpdateEndpointOverride() {
  updateEndpointOverride.value = ''
  updateEndpointDraft.value = ''
  updateEndpointEditing.value = false
  message.success(t('debug.updateEndpointRestored'))
}

  return {
    updateEndpointEditing, updateEndpointDraft, effectiveUpdateEndpoint, updateEndpointMode,
    beginUpdateEndpointEdit, cancelUpdateEndpointEdit, saveUpdateEndpointOverride, resetUpdateEndpointOverride,
  }
}
