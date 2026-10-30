/**
 * 参数编解码：把"布局顺序 = 线上格式"这条耦合拆掉。
 *
 * 关键契约：`decode(encode(params))` 必须还原同样的命名参数，
 * 而且**改 spec 的控件顺序不能改变任何命名参数的取值**。
 * 最后一条是整件事的意义所在——布局可以自由移动，语义不动。
 */
import assert from 'node:assert/strict'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { describe, it } from 'node:test'

import {
  decodeWidgets,
  encodeWidgets,
  encodeWidgetsReport,
  specFor,
  widgetIndex,
  widgetNames,
  widgetValue,
  withWidgetValue,
  withWidgetValues,
} from '../src/litegraph/paramCodec.ts'
import type { NodeSpec } from '../src/litegraph/nodeSpecs.ts'

function spec(widgets: Array<Partial<NodeSpec['widgets'][number]> & { name: string }>): NodeSpec {
  return {
    type: 'TestNode',
    title: 'Test',
    category: 'test',
    inputs: [],
    outputs: [],
    widgets: widgets.map(widget => ({
      type: 'text',
      default: '',
      ...widget,
    })) as NodeSpec['widgets'],
  }
}

describe('decode / encode 往返', () => {
  const node = spec([
    { name: 'audio', default: 'input.wav' },
    { name: 'input_name', default: '' },
    { name: 'recursive', type: 'toggle', default: false },
    { name: 'limit', type: 'number', default: 10 },
  ])

  it('命名参数经编码再解码后保持不变', () => {
    const params = { audio: 'song.wav', input_name: 'lead', recursive: true, limit: 5 }
    const values = encodeWidgets(node, params)
    assert.deepEqual(decodeWidgets(node, values).params, params)
  })

  it('编码顺序由 spec 决定，与传入对象键序无关', () => {
    const a = encodeWidgets(node, { limit: 5, recursive: true, input_name: 'lead', audio: 'song.wav' })
    const b = encodeWidgets(node, { audio: 'song.wav', input_name: 'lead', recursive: true, limit: 5 })
    assert.deepEqual(a, b)
    assert.deepEqual(a, ['song.wav', 'lead', true, 5])
  })

  it('缺少的键用 spec 默认值补齐', () => {
    const values = encodeWidgets(node, { audio: 'song.wav' })
    assert.deepEqual(values, ['song.wav', '', false, 10])
  })

  it('超出 spec 长度的旧值不丢失', () => {
    const legacy = ['song.wav', 'lead', true, 5, 'legacy-extra']
    const decoded = decodeWidgets(node, legacy)
    assert.equal(decoded.params.audio, 'song.wav')
    assert.deepEqual(decoded.extra, ['legacy-extra'])
  })

  it('缺失的尾部值回落默认值（老图缺新控件）', () => {
    const decoded = decodeWidgets(node, ['song.wav'])
    assert.equal(decoded.params.audio, 'song.wav')
    assert.equal(decoded.params.recursive, false)
    assert.equal(decoded.params.limit, 10)
  })
})

describe('顺序无关性：布局可以随便动', () => {
  it('调换 spec 里两个控件的顺序，命名取值不变', () => {
    const before = spec([
      { name: 'audio', default: 'input.wav' },
      { name: 'input_name', default: '' },
    ])
    const after = spec([
      { name: 'input_name', default: '' },
      { name: 'audio', default: 'input.wav' },
    ])
    const original = ['song.wav', 'lead']

    const decodedBefore = decodeWidgets(before, original).params
    // 同一个语义的节点，在新布局下的位置数组
    const reordered = encodeWidgets(after, decodedBefore)
    assert.deepEqual(reordered, ['lead', 'song.wav'])
    assert.deepEqual(decodeWidgets(after, reordered).params, decodedBefore)
  })

  it('widgetIndex 跟着 spec 走，而不是写死的数字', () => {
    const node = spec([
      { name: 'folder', default: '' },
      { name: 'recursive', type: 'toggle', default: true },
      { name: 'input_name', default: '' },
    ])
    assert.equal(widgetIndex(node, 'input_name'), 2)
    assert.equal(widgetIndex(node, 'nope'), -1)
    assert.deepEqual(widgetNames(node), ['folder', 'recursive', 'input_name'])
  })
})

describe('严格模式', () => {
  const node = spec([{ name: 'audio', default: '' }])

  it('多传 spec 没有的键会报错并列出已知键', () => {
    assert.throws(
      () => encodeWidgets(node, { audio: 'a.wav', chunkSize: 1024 }),
      /unknown widget\(s\) for TestNode: chunkSize/,
    )
  })

  it('宽容模式返回被忽略的键（导入外部图时用）', () => {
    const report = encodeWidgetsReport(node, { audio: 'a.wav', chunkSize: 1024 })
    assert.deepEqual(report.values, ['a.wav'])
    assert.deepEqual(report.ignored, ['chunkSize'])
  })
})

describe('按名字读写节点', () => {
  it('widgetValue 读不存在的控件返回 undefined，不抛', () => {
    const node = spec([{ name: 'folder', default: '/music' }])
    assert.equal(widgetValue(node, { widgets_values: ['/music'] }, 'folder'), '/music')
    assert.equal(widgetValue(node, { widgets_values: ['/music'] }, 'nope'), undefined)
    assert.equal(widgetValue(undefined, { widgets_values: [] }, 'folder'), undefined)
  })

  it('withWidgetValue 不修改入参，并保持其余位置值', () => {
    const node = spec([
      { name: 'folder', default: '' },
      { name: 'recursive', type: 'toggle', default: false },
    ])
    const info = { type: 'TestNode', widgets_values: ['/music', false, 'legacy'] }
    const next = withWidgetValue(node, info, 'recursive', true)
    assert.deepEqual(info.widgets_values, ['/music', false, 'legacy'])
    assert.deepEqual(next.widgets_values, ['/music', true, 'legacy'])
  })

  it('withWidgetValues 批量写', () => {
    const node = spec([
      { name: 'a', default: '' },
      { name: 'b', default: '' },
      { name: 'c', default: '' },
    ])
    const next = withWidgetValues(node, { widgets_values: ['1', '2', '3'] }, { a: 'x', c: 'z' })
    assert.deepEqual(next.widgets_values, ['x', '2', 'z'])
  })

  it('spec 里没有的控件不会改动节点', () => {
    const node = spec([{ name: 'a', default: '' }])
    const info = { widgets_values: ['1'] }
    assert.equal(withWidgetValue(node, info, 'ghost', 2), info)
    assert.equal(withWidgetValues(node, info, { ghost: 2 }), info)
  })
})

describe('真实节点规格', () => {
  it('pymss_load_audio 的 input_name 能按名字取到（不再靠写死的下标）', () => {
    const loadSpec = specFor('pymss_load_audio')
    assert.ok(loadSpec, 'pymss_load_audio 应该在 NODE_SPECS 里')
    assert.ok(widgetNames(loadSpec).includes('input_name'))
    const node = { widgets_values: ['song.wav', 'lead'] }
    assert.equal(widgetValue(loadSpec, node, 'input_name'), 'lead')
    assert.equal(widgetValue(loadSpec, node, 'audio'), 'song.wav')
  })

  it('pymss_load_audio_batch 有 folder 控件', () => {
    const batchSpec = specFor('pymss_load_audio_batch')
    assert.ok(batchSpec)
    assert.ok(widgetNames(batchSpec).includes('folder'))
  })
})

/** 结构性防回归：下标访问只允许出现在编解码层与明确的迁移函数里。 */
describe('下标访问守卫', () => {
  const ALLOWED = new Set([
    'src/litegraph/paramCodec.ts',
    // 历史图迁移：旧格式没有名字，只能按下标读（已在本轮标注）
    'src/litegraph/registerNodes.ts',
  ])

  function walk(dir: string, out: string[] = []): string[] {
    for (const entry of readdirSync(dir)) {
      const full = join(dir, entry)
      if (statSync(full).isDirectory()) walk(full, out)
      else if (/\.(ts|vue)$/.test(entry)) out.push(full)
    }
    return out
  }

  it('src 下没有直接按下标读 widgets_values 的代码', () => {
    const root = join(process.cwd(), 'src')
    const offenders: string[] = []
    for (const file of walk(root)) {
      const relativePath = relative(process.cwd(), file).replace(/\\/g, '/')
      if (ALLOWED.has(relativePath)) continue
      const source = readFileSync(file, 'utf-8')
      if (/widgets_values\s*\??\.?\s*\[/.test(source)) offenders.push(relativePath)
    }
    assert.deepEqual(offenders, [], `这些文件仍在按下标读 widgets_values：${offenders.join(', ')}`)
  })
})
