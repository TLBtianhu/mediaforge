/**
 * 计划预检的前端判断：block 拦下、skip 摘步、不可用放行。
 *
 * 这是"UI 也能提前说清楚"的那一层，所以只测判断，不测弹窗。
 */
import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import {
  dropSkippedSteps,
  summarizePrecheck,
  type PrecheckResult,
} from '../src/utils/mediaPrecheck.ts'

function result(findings: PrecheckResult['findings'], blocking = false): PrecheckResult {
  return { ok: !blocking && findings.length === 0, blocking, findings }
}

describe('summarizePrecheck', () => {
  it('没有预检结果时放行', () => {
    const summary = summarizePrecheck(null)
    assert.equal(summary.runnable, true)
    assert.deepEqual(summary.dropped, [])
    assert.deepEqual(summary.notices, [])
  })

  it('block 级发现拦住整条计划', () => {
    const summary = summarizePrecheck(
      result([{ code: 'input_missing', severity: 'block', message: '输入不存在：a.png' }], true),
    )
    assert.equal(summary.runnable, false)
    assert.deepEqual(summary.notices, ['输入不存在：a.png'])
    assert.deepEqual(summary.dropped, [])
  })

  it('skip 级发现只摘掉对应能力', () => {
    const summary = summarizePrecheck(
      result([
        { code: 'no_watermark', severity: 'skip', capability: 'watermark_removal', message: '没有检测到水印' },
      ]),
    )
    assert.equal(summary.runnable, true)
    assert.deepEqual(summary.dropped, ['watermark_removal'])
    assert.deepEqual(summary.notices, ['没有检测到水印'])
  })

  it('同一能力被多次判死只摘一次', () => {
    const summary = summarizePrecheck(
      result([
        { code: 'no_watermark', severity: 'skip', capability: 'watermark_removal', message: 'A' },
        { code: 'mask_too_large', severity: 'skip', capability: 'watermark_removal', message: 'B' },
      ]),
    )
    assert.deepEqual(summary.dropped, ['watermark_removal'])
    assert.equal(summary.notices.length, 2)
  })

  it('空 findings 视为通过', () => {
    assert.deepEqual(summarizePrecheck(result([])), { runnable: true, dropped: [], notices: [] })
  })
})

describe('dropSkippedSteps', () => {
  const steps = [
    { capability: 'watermark_removal' },
    { capability: 'upscale' },
  ]

  it('摘掉被跳过的能力', () => {
    assert.deepEqual(dropSkippedSteps(steps, ['watermark_removal']), [{ capability: 'upscale' }])
  })

  it('没东西可摘时原样返回（同一个引用，避免无谓重渲染）', () => {
    assert.equal(dropSkippedSteps(steps, []), steps)
  })

  it('全部被摘掉时返回空数组，调用方据此中止', () => {
    assert.deepEqual(dropSkippedSteps(steps, ['watermark_removal', 'upscale']), [])
  })
})
