/**
 * Runtime input slots of a comfy-mss workflow.
 *
 * A load node declares a named slot via its input_name widget (slot 1 for
 * pymss_load_audio, slot 3 for pymss_load_audio_batch). Hosts key the
 * run_dag `inputs` mapping by these names (pymss >= 2.1.2 strict contract:
 * a declared-but-unprovided name is an error; no positional fallback).
 */

import { specFor, widgetValue } from '../litegraph/paramCodec.ts'
import { detectWorkflowFormat } from '../workflows/formats.ts'
import { workflowInputNodes } from '../workflows/workbench.ts'

export interface WorkflowInputSlot {
  /** slot name from the node's input_name widget */
  name: string
  /** node type (pymss_load_audio / pymss_load_audio_batch / LoadAudio) */
  nodeType: string
  nodeId: number | string
}

export interface WorkflowInputsAnalysis {
  slots: WorkflowInputSlot[]
  /** load nodes with neither an embedded source nor a runtime slot key */
  unresolved: Array<{ nodeId: number | string; widget: string; nodeType: string }>
  /** load nodes that carry their own real file path in the audio widget */
  selfContained: number
}

const LOAD_TYPES = new Set(['pymss_load_audio', 'LoadAudio', 'pymss_load_audio_batch'])
/**
 * 旧图没有控件名，只能按下标兜底——位置化知识集中在 paramCodec 里，
 * 这里仅保留"历史图的输入名在第几个位置"这一份兼容表。
 * 新图（有 input_name 控件）走按名字取，不再依赖这张表。
 */
const LEGACY_INPUT_NAME_SLOT: Record<string, number> = {
  pymss_load_audio: 1,
  LoadAudio: 1,
  pymss_load_audio_batch: 3,
}

/** 先按名字取，取不到再按历史下标取（迁移期两条路都活着）。 */
function loadWidget(node: any, type: string, name: string, legacySlot: number): string {
  const spec = specFor(type)
  const named = spec ? widgetValue(spec, node, name) : undefined
  if (named !== undefined && named !== null && String(named).trim() !== "") return String(named).trim()
  return widgetAt(node?.widgets_values, legacySlot)
}

function widgetAt(values: unknown, index: number): string {
  if (!Array.isArray(values)) return ''
  const raw = values[index]
  return raw === null || raw === undefined ? '' : String(raw).trim()
}

/** A widget value that looks like an absolute/relative path on disk. */
function looksLikePath(value: string): boolean {
  if (!value) return false
  return value.startsWith('/') || value.startsWith('\\')
    || /^[a-zA-Z]:[\\/]/.test(value)
    || value.includes('/')
    || value.includes('\\')
}

export function analyzeWorkflowInputs(definition: Record<string, unknown> | null | undefined): WorkflowInputsAnalysis {
  const nodes = definition && Array.isArray(definition.nodes) ? definition.nodes as any[] : []
  const slots: WorkflowInputSlot[] = []
  const unresolved: WorkflowInputsAnalysis['unresolved'] = []
  let selfContained = 0
  for (const node of nodes) {
    const type = String(node?.type || '')
    if (!LOAD_TYPES.has(type)) continue
    const nameSlot = LEGACY_INPUT_NAME_SLOT[type] ?? -1
    const inputName = loadWidget(node, type, 'input_name', nameSlot)
    const audioWidget = type === 'pymss_load_audio_batch'
      ? ''
      : loadWidget(node, type, 'audio', 0)
    if (inputName) {
      if (!slots.some(slot => slot.name === inputName)) {
        slots.push({ name: inputName, nodeType: type, nodeId: node.id })
      }
    } else if (looksLikePath(audioWidget)) {
      // graph carries its own input file
      selfContained += 1
    } else if (type === 'pymss_load_audio_batch') {
      const folder = loadWidget(node, type, 'folder', 0)
      if (folder) selfContained += 1
      else unresolved.push({ nodeId: node.id, widget: '', nodeType: type })
    } else if (audioWidget) {
      // Legacy pymss/ComfyUI load nodes use the audio widget itself as the
      // runtime input key when no dedicated input_name widget exists.
      if (!slots.some(slot => slot.name === audioWidget)) {
        slots.push({ name: audioWidget, nodeType: type, nodeId: node.id })
      }
    } else {
      unresolved.push({ nodeId: node.id, widget: audioWidget, nodeType: type })
    }
  }
  return { slots, unresolved, selfContained }
}

export function getSeparateWorkflowInputIssue(
  definition: Record<string, unknown>,
): { code: 'no-input' | 'multiple-inputs'; count: number } | null {
  if (detectWorkflowFormat(definition) !== 'graph') return null
  const count = workflowInputNodes(definition).length
  if (count === 1) return null
  return { code: count === 0 ? 'no-input' : 'multiple-inputs', count }
}
