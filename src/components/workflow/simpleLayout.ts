/**
 * 简单工作流编辑器的几何与布局：重叠检测、端口 key、自动布局。
 *
 * 纯函数，不碰响应式状态；workflow 辅助（simpleOutputRef/simpleSourceStepId/
 * isSimpleAudioOperation）由视图注入，保持 Node 可测。
 */
export const NODE_HEIGHT_BASE = 166
export const PORT_GAP = 26

export function stepHeight(step: any) {
  return NODE_HEIGHT_BASE + Math.max(0, step.stems.length - 2) * PORT_GAP
}

export function ensembleHeight(ensemble: any, isSimpleAudioOperation: (algorithm: unknown) => boolean) {
  return (isSimpleAudioOperation(ensemble.algorithm) ? 174 : 206) + ensemble.inputs.length * 42
}

export function nodesOverlap(
  first: { x: number; y: number },
  firstWidth: number,
  firstHeight: number,
  second: { x: number; y: number },
  secondWidth: number,
  secondHeight: number,
  gap = 20,
) {
  return first.x < second.x + secondWidth + gap
    && first.x + firstWidth + gap > second.x
    && first.y < second.y + secondHeight + gap
    && first.y + firstHeight + gap > second.y
}

export function outputPortKey(stepId: string, stem: string, simpleOutputRef: (stepId: string, stem: string) => string) {
  return `output:${simpleOutputRef(stepId, stem)}`
}

export function ensembleInputPortKey(ensembleId: string, index: number) {
  return `ensemble-input:${ensembleId}:${index}`
}

export function savePortKey(stepId: string, stem: string, simpleOutputRef: (stepId: string, stem: string) => string) {
  return `save:${simpleOutputRef(stepId, stem)}`
}

export function computeAutoLayout(draft: any, simpleSourceStepId: (source: string) => string, isSimpleAudioOperation: (algorithm: unknown) => boolean) {
  const nodes: Record<string, { x: number; y: number }> = {
    input: { x: 64, y: 250 },
  }
  const stepById = new Map<string, any>(draft.steps.map((step: any) => [step.id, step]))
  const ensembleById = new Map<string, any>(draft.ensembles.map((ensemble: any) => [ensemble.id, ensemble]))
  const depths = new Map<string, number>()
  const visiting = new Set<string>()

  const sourceDepth = (source: string): number => {
    if (!source.trim() || source === 'input') return 0
    const sourceId = simpleSourceStepId(source)
    const step = stepById.get(sourceId)
    if (step) return nodeDepth(step.id, 'step')
    const ensemble = ensembleById.get(sourceId)
    return ensemble ? nodeDepth(ensemble.id, 'ensemble') : 0
  }

  const nodeDepth = (id: string, kind: 'step' | 'ensemble'): number => {
    const cached = depths.get(id)
    if (cached !== undefined) return cached
    if (visiting.has(id)) return 1
    visiting.add(id)
    const depth = kind === 'step'
      ? sourceDepth(stepById.get(id)?.input || 'input') + 1
      : Math.max(0, ...(ensembleById.get(id)?.inputs || []).map((input: any) => sourceDepth(input.source))) + 1
    visiting.delete(id)
    depths.set(id, depth)
    return depth
  }

  draft.steps.forEach((step: any) => nodeDepth(step.id, 'step'))
  draft.ensembles.forEach((ensemble: any) => nodeDepth(ensemble.id, 'ensemble'))

  const nextY = new Map<number, number>()
  const place = (id: string, depth: number, height: number) => {
    const y = nextY.get(depth) ?? 120
    nodes[id] = { x: 360 + Math.max(0, depth - 1) * 360, y }
    nextY.set(depth, y + height + 36)
  }
  draft.steps.forEach((step: any) => place(step.id, depths.get(step.id) || 1, stepHeight(step)))
  draft.ensembles.forEach((ensemble: any) => place(ensemble.id, depths.get(ensemble.id) || 1, ensembleHeight(ensemble, isSimpleAudioOperation)))

  const maxDepth = Math.max(1, ...depths.values())
  nodes.save = { x: 360 + maxDepth * 360, y: 220 }
  return nodes
}
