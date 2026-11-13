export type MediaPlanStep = { capability: string; bindings: Record<string, string>; params: Record<string, unknown> }
export type MediaWorkflowDefinition = { version: 1; modality: 'image' | 'video'; steps: MediaPlanStep[] }
export type MediaWorkflowPreset = { id: string; name: string; definition: MediaWorkflowDefinition; createdAt: number; updatedAt: number }
export type MediaWorkflowState = { version: 1; presets: MediaWorkflowPreset[] }
export type MediaWorkflowMutation = { action: 'upsert' | 'delete'; id: string; name?: string; definition?: MediaWorkflowDefinition; expectedUpdatedAt?: number }
export type MediaWorkflowResponse = { state: MediaWorkflowState; conflict?: { id: string; expectedUpdatedAt: number; actualUpdatedAt: number } }

const object = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value)
const clone = <T>(value: T): T => JSON.parse(JSON.stringify(value))
const invalid = () => { throw new Error('Invalid media workflow preset') }
const validId = (value: unknown): value is string => typeof value === 'string' && /^[a-zA-Z0-9][a-zA-Z0-9_-]{0,119}$/.test(value)

// Persist only a reusable recipe. Inputs, local paths and worker request IDs
// belong to a submitted task, never to this definition envelope.
export function normalizeMediaWorkflowDefinition(value: unknown): MediaWorkflowDefinition {
  if (!object(value) || value.version !== 1 || value.modality !== 'image' && value.modality !== 'video'
    || !Array.isArray(value.steps) || !value.steps.length || value.steps.length > 64) return invalid()
  const steps = value.steps.map(step => {
    if (!object(step) || typeof step.capability !== 'string' || !step.capability.trim() || step.capability.length > 128
      || !object(step.bindings) || !Object.keys(step.bindings).length || Object.keys(step.bindings).length > 32 || !object(step.params)) return invalid()
    if (Object.entries(step.bindings).some(([key, model]) => !key.trim() || key.length > 128 || typeof model !== 'string' || !model.trim() || model.length > 256)) return invalid()
    return { capability: step.capability.trim(), bindings: clone(step.bindings) as Record<string, string>, params: clone(step.params) }
  })
  const definition: MediaWorkflowDefinition = { version: 1, modality: value.modality as 'image' | 'video', steps }
  if (new TextEncoder().encode(JSON.stringify(definition)).length > 256 * 1024) return invalid()
  return definition
}

export function normalizeMediaWorkflowState(value: unknown): MediaWorkflowState {
  if (value == null) return { version: 1, presets: [] }
  if (!object(value) || value.version !== 1 || !Array.isArray(value.presets) || value.presets.length > 1000) return invalid()
  const ids = new Set<string>()
  const presets = value.presets.map(entry => {
    if (!object(entry) || !validId(entry.id) || ids.has(entry.id) || typeof entry.name !== 'string'
      || !entry.name.trim() || entry.name.trim().length > 120 || !Number.isSafeInteger(entry.createdAt)
      || Number(entry.createdAt) <= 0 || !Number.isSafeInteger(entry.updatedAt) || Number(entry.updatedAt) < Number(entry.createdAt)) return invalid()
    ids.add(entry.id)
    return { id: entry.id, name: entry.name.trim(), definition: normalizeMediaWorkflowDefinition(entry.definition),
      createdAt: Number(entry.createdAt), updatedAt: Number(entry.updatedAt) }
  })
  return { version: 1, presets: presets.sort((a, b) => b.updatedAt - a.updatedAt || a.id.localeCompare(b.id)) }
}

/** Browser preview equivalent of the backend read/validate/modify transaction. */
export function applyMediaWorkflowMutation(value: unknown, mutation: MediaWorkflowMutation, timestamp = Date.now()): MediaWorkflowResponse {
  const state = normalizeMediaWorkflowState(value)
  if (!validId(mutation.id) || !['upsert', 'delete'].includes(mutation.action)) return invalid()
  const index = state.presets.findIndex(item => item.id === mutation.id)
  const previous = state.presets[index]
  const actual = previous?.updatedAt || 0
  const expected = mutation.expectedUpdatedAt
  const creating = mutation.action === 'upsert' && expected == null
  if (!creating && (!Number.isSafeInteger(expected) || Number(expected) <= 0)) return invalid()
  if (creating ? !!previous : actual !== expected) return { state, conflict: { id: mutation.id, expectedUpdatedAt: expected || 0, actualUpdatedAt: actual } }
  if (mutation.action === 'delete') state.presets.splice(index, 1)
  else {
    if (typeof mutation.name !== 'string' || !mutation.name.trim() || mutation.name.trim().length > 120 || creating && state.presets.length >= 1000) return invalid()
    const updatedAt = Math.max(Math.trunc(timestamp), actual + 1, 1)
    const entry: MediaWorkflowPreset = { id: mutation.id, name: mutation.name.trim(), definition: normalizeMediaWorkflowDefinition(mutation.definition),
      createdAt: previous?.createdAt || updatedAt, updatedAt }
    if (index < 0) state.presets.push(entry)
    else state.presets[index] = entry
  }
  return { state: normalizeMediaWorkflowState(state) }
}
