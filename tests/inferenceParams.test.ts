/**
 * 槽位参数 ↔ 线上参数：翻译只在接口的 `bind` 里定义。
 *
 * 以及一个结构性守卫：控件组件里不许出现具体槽位名或模型 id——
 * 出现就说明 UI 又开始认识模型了。
 */
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, it } from 'node:test'

import {
  coerceParams,
  fromWireParams,
  paramsFromInterface,
  toWireParams,
} from '../src/litegraph/inferenceParams.ts'
import type { ModelInterface } from '../src/litegraph/renderPlan.ts'

const separation: ModelInterface = {
  modelId: 'test:mel_band',
  task: 'separation',
  slots: {
    chunk_size: { bind: 'chunk_size', min: 64, max: 8192, step: 128, default: 512, derive: 'chunk_size_constraint' },
    overlap: { bind: 'overlap_size', min: 0, max: 8, step: 1, default: 2 },
    stems: { bind: 'stems', default: 'vocals', choices: ['vocals', 'instrumental'] },
  },
}

describe('默认值', () => {
  it('从接口取默认值作为表单初值', () => {
    assert.deepEqual(paramsFromInterface(separation), {
      chunk_size: 512,
      overlap: 2,
      stems: 'vocals',
    })
  })
})

describe('收敛', () => {
  it('越界数字被夹到范围内', () => {
    const { params, rejected } = coerceParams(separation, { chunk_size: 999999, overlap: -5 })
    assert.equal(params.chunk_size, 8192)
    assert.equal(params.overlap, 0)
    assert.deepEqual(rejected, [])
  })

  it('接口不认识的键被丢弃并报告', () => {
    const { params, rejected } = coerceParams(separation, { chunk_size: 512, num_overlap: 4 })
    assert.deepEqual(Object.keys(params), ['chunk_size'])
    assert.deepEqual(rejected, ['num_overlap'])
  })

  it('枚举外的取值被丢弃并报告', () => {
    const { params, rejected } = coerceParams(separation, { stems: 'drums' })
    assert.deepEqual(params, {})
    assert.deepEqual(rejected, ['stems'])
  })

  it('枚举内的取值通过', () => {
    const { params, rejected } = coerceParams(separation, { stems: 'instrumental' })
    assert.equal(params.stems, 'instrumental')
    assert.deepEqual(rejected, [])
  })

  it('布尔与数组原样通过', () => {
    const model: ModelInterface = {
      modelId: 'test:v',
      task: 'video_inpainting',
      slots: { fp16: { bind: 'fp16', default: true }, stems: { bind: 'stems', default: [] } },
    }
    const { params, rejected } = coerceParams(model, { fp16: false, stems: ['a', 'b'] })
    assert.equal(params.fp16, false)
    assert.deepEqual(params.stems, ['a', 'b'])
    assert.deepEqual(rejected, [])
  })
})

describe('线上翻译', () => {
  it('槽位名按 bind 翻成原生键', () => {
    assert.deepEqual(toWireParams(separation, { chunk_size: 512, overlap: 4 }), {
      chunk_size: 512,
      overlap_size: 4,
    })
  })

  it('原生键能翻回槽位名（读回已保存设置）', () => {
    assert.deepEqual(fromWireParams(separation, { overlap_size: 4, unknown_key: 1 }), { overlap: 4 })
  })

  it('往返一致', () => {
    const params = { chunk_size: 1024, overlap: 3, stems: 'vocals' }
    assert.deepEqual(fromWireParams(separation, toWireParams(separation, params)), params)
  })

  it('不认识的槽位不会被写进线上参数', () => {
    assert.deepEqual(toWireParams(separation, { mystery: 1 }), {})
  })
})

describe('守卫：控件组件不认识模型', () => {
  const files = [
    join(process.cwd(), 'src/components/ModelSlotField.vue'),
    join(process.cwd(), 'src/components/ModelSlotFields.vue'),
  ]

  it('组件源码里没有具体槽位名与模型 id', () => {
    const forbidden = ['chunk_size', 'overlap', 'mel_band', 'audio:', 'media:', 'hf:']
    for (const file of files) {
      const source = readFileSync(file, 'utf-8')
      for (const needle of forbidden) {
        assert.ok(!source.includes(needle), `${file} 里出现了 ${needle}：UI 不该认识它`)
      }
    }
  })

  it('组件只从 renderPlan 取类型', () => {
    for (const file of files) {
      const source = readFileSync(file, 'utf-8')
      assert.ok(source.includes('renderPlan.ts'), `${file} 应只依赖渲染计划的类型`)
    }
  })
})
