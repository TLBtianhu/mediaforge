export type MediaWorkflowPayload = {
  operation: 'media_workflow'
  requestId: string
  modality: 'image' | 'video'
  inputs: string[]
  outputDir: string
  modelDir: string
  steps: unknown[]
  maskPath?: string
}

export function buildMediaWorkflowPayload(input: Omit<MediaWorkflowPayload, 'operation'>): MediaWorkflowPayload {
  const payload: MediaWorkflowPayload = { operation: 'media_workflow', ...input }
  if (!input.maskPath) delete payload.maskPath
  return payload
}
