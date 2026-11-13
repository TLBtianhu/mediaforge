/**
 * 当前运行配置与历史文件校验；不转换旧格式或吞并未来版本。
 *
 * 纯函数，不碰 store 状态；类型从 task.ts 引入（import type 擦除，无运行时循环）。
 */
import type { OutputLayout, OutputNamingConfig } from './task.ts'
import type { TaskStatus } from '../features/tasks/lifecycle.ts'

export const CURRENT_INFERENCE_PARAMS_VERSION = 3
const record = (value: unknown): value is Record<string, unknown> => Boolean(value) && typeof value === 'object' && !Array.isArray(value)
const incompatible = (name: string): never => { throw new Error(`Unsupported ${name} schema; the original file was preserved`) }

export function validateSeparateState<T>(value: T): T {
  if (value !== null && (!record(value) || value.version !== 1)) return incompatible('separate-state')
  return value
}

export function normalizeOutputLayout(value: unknown): OutputLayout {
  return value === 'flat' ? 'flat' : 'folders'
}

export function normalizeOutputNaming(value: unknown): OutputNamingConfig | undefined {
  if (!value || typeof value !== 'object') return undefined
  const raw = value as Partial<OutputNamingConfig>
  const template = typeof raw.template === 'string' ? raw.template.trim() : ''
  const stemOrder = Array.isArray(raw.stemOrder)
    ? raw.stemOrder.map(item => String(item || '').trim()).filter(Boolean)
    : []
  return {
    enabled: raw.enabled === true,
    template,
    stemOrder,
  }
}

export const STAGE_META: Record<TaskStatus, { progress: number; label: string }> = {
  queued: { progress: 2, label: 'Queued' },
  preparing: { progress: 8, label: 'Preparing' },
  validating_input: { progress: 12, label: 'Validating input' },
  downloading_model: { progress: 22, label: 'Preparing model' },
  ensuring_model: { progress: 22, label: 'Preparing model' },
  loading_model: { progress: 35, label: 'Loading model' },
  separating: { progress: 0, label: 'Separating' },
  writing_output: { progress: 92, label: 'Collecting outputs' },
  done: { progress: 100, label: 'Done' },
  failed: { progress: 100, label: 'Failed' },
  cancelled: { progress: 100, label: 'Cancelled' },
}

export function normalizeStatus(status: unknown): TaskStatus {
  if (typeof status !== 'string') return 'queued'
  if (status in STAGE_META) return status as TaskStatus
  return 'preparing'
}
