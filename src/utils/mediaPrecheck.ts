/**
 * 计划预检（前端侧）：跑之前先问后端"这计划能成吗"。
 *
 * 后端 `media.precheck` 返回 findings，每条带 severity：
 *   - `block`：整条计划不该跑
 *   - `skip` ：只摘掉对应能力的那一步
 *
 * 这里只做**判断与文案整理**，不决定 UI 怎么显示——那样才能单测。
 */
import { invoke } from '@tauri-apps/api/core'

export type PrecheckSeverity = 'block' | 'skip'

export interface PrecheckFinding {
  code: string
  severity: PrecheckSeverity
  capability?: string
  path?: string
  message: string
}

export interface PrecheckResult {
  ok: boolean
  blocking: boolean
  findings: PrecheckFinding[]
}

export interface PrecheckSummary {
  /** 能不能继续跑 */
  runnable: boolean
  /** 需要摘掉的能力（severity=skip） */
  dropped: string[]
  /** 需要告诉用户的话 */
  notices: string[]
}

export function summarizePrecheck(result: PrecheckResult | null | undefined): PrecheckSummary {
  if (!result || !Array.isArray(result.findings) || result.findings.length === 0) {
    return { runnable: true, dropped: [], notices: [] }
  }
  const notices = result.findings.map(finding => finding.message).filter(Boolean)
  const dropped = [...new Set(
    result.findings
      .filter(finding => finding.severity === 'skip' && finding.capability)
      .map(finding => String(finding.capability)),
  )]
  return { runnable: !result.blocking, dropped, notices }
}

/** 摘掉被预检判死的能力；没有 steps 时返回空数组。 */
export function dropSkippedSteps<T extends { capability: string }>(steps: T[], dropped: string[]): T[] {
  if (!dropped.length) return steps
  return steps.filter(step => !dropped.includes(step.capability))
}

/** 调后端预检。失败不抛出：预检不可用不该拦住主流程。 */
export async function precheckPlan(
  steps: Array<{ capability: string; bindings?: Record<string, string>; params?: Record<string, unknown> }>,
  inputs: string[],
): Promise<PrecheckResult | null> {
  try {
    const response = await invoke<{ precheck?: PrecheckResult | null }>('media_precheck', {
      payload: { steps, inputs },
    })
    return response?.precheck ?? null
  } catch {
    return null
  }
}
