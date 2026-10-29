/**
 * 渲染计划：模型接口 ∩ 布局档。
 *
 * 这组测试守的是**解耦本身**，不是像素：
 *   1. 展示顺序来自布局档，不来自模型接口的键序
 *   2. 模型多出来的槽位不会消失，按词汇表兜底进自动分组
 *   3. 模型接口里出现布局字段 → 直接判违规
 *   4. 布局档里出现模型名 → 直接判违规
 *   5. 每个槽位都能在词汇表里查到（模型、布局、词汇表三边一致）
 */
import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import { DEFAULT_PROFILE, LAYOUTS, layoutFor, layoutIds } from '../src/litegraph/layouts.ts'
import {
  buildRenderPlan,
  validateInterface,
  validateLayout,
  type ModelInterface,
} from '../src/litegraph/renderPlan.ts'
import { SLOTS, slotIds, slotSpec } from '../src/litegraph/slots.ts'

function model(slots: Record<string, { bind: string } & Record<string, unknown>>): ModelInterface {
  return { modelId: 'test:model', task: 'separation', slots }
}

describe('渲染计划：顺序由布局档决定', () => {
  const separation = LAYOUTS.separation

  it('模型接口的键序不影响展示顺序', () => {
    const a = model({
      overlap: { bind: 'overlap', default: 2 },
      chunk_size: { bind: 'chunk_size', default: 1024 },
      stems: { bind: 'target_instrument' },
    })
    const b = model({
      stems: { bind: 'target_instrument' },
      chunk_size: { bind: 'chunk_size', default: 1024 },
      overlap: { bind: 'overlap', default: 2 },
    })
    const planA = buildRenderPlan(a, separation)
    const planB = buildRenderPlan(b, separation)
    const order = (plan: typeof planA) => plan.sections.flatMap(section => section.fields.map(field => field.slot))
    assert.deepEqual(order(planA), order(planB))
    assert.deepEqual(order(planA), ['stems', 'chunk_size', 'overlap'])
  })

  it('分节来自布局档', () => {
    const plan = buildRenderPlan(model({ stems: { bind: 's' }, chunk_size: { bind: 'c' } }), separation)
    assert.deepEqual(plan.sections.map(section => section.id), ['quality', 'speed'])
    assert.equal(plan.sections[0].titleKey, 'section.quality')
  })

  it('布局档写了但模型没有的槽位不渲染，只记进 missing', () => {
    const plan = buildRenderPlan(model({ stems: { bind: 's' } }), separation)
    assert.deepEqual(plan.missing, ['chunk_size', 'overlap'])
    assert.deepEqual(plan.sections.flatMap(section => section.fields.map(f => f.slot)), ['stems'])
  })

  it('空分节被丢弃', () => {
    const plan = buildRenderPlan(model({ stems: { bind: 's' } }), separation)
    assert.deepEqual(plan.sections.map(section => section.id), ['quality'])
  })
})

describe('渲染计划：未列出的槽位不丢', () => {
  it('没写进布局档的槽位按词汇表兜底进自动分组', () => {
    const plan = buildRenderPlan(
      model({
        stems: { bind: 's' },
        chunk_size: { bind: 'c' },
        tile: { bind: 'tile' },        // 词汇表里 fallbackSection = speed
        strength: { bind: 'strength' }, // fallbackSection = quality
      }),
      LAYOUTS.separation,
    )
    const ids = plan.sections.map(section => section.id)
    assert.ok(ids.includes('auto-quality'), `实际分节：${ids.join(',')}`)
    assert.ok(ids.includes('auto-speed'))
    const flat = plan.sections.flatMap(section => section.fields.map(field => field.slot))
    assert.ok(flat.includes('tile'))
    assert.ok(flat.includes('strength'))
    // 档里写了 overlap、但这个模型没有 → 只记 missing，不渲染
    assert.deepEqual(plan.missing, ['overlap'])
  })

  it('默认档（无分节）把全部槽位收进自动分组', () => {
    const plan = buildRenderPlan(model({ stems: { bind: 's' }, fp16: { bind: 'f' } }), DEFAULT_PROFILE)
    assert.deepEqual(plan.sections.map(section => section.id), ['auto-quality', 'auto-speed'])
    assert.equal(plan.sections.flatMap(section => section.fields).length, 2)
  })
})

describe('守卫：接口不得携带布局', () => {
  it('出现 order / group / width 就判违规', () => {
    const problems = validateInterface(
      model({
        stems: { bind: 's' } as never,
        chunk_size: { bind: 'c', order: 1 } as never,
      }),
    )
    assert.ok(problems.some(problem => problem.includes('layout key order')), problems.join('; '))
  })

  it('词汇表里没有的槽位判违规', () => {
    const problems = validateInterface(model({ mystery: { bind: 'm' } }))
    assert.ok(problems.some(problem => problem.includes('not in the vocabulary')), problems.join('; '))
  })

  it('缺少 bind 目标判违规', () => {
    const problems = validateInterface(model({ stems: { bind: '' } }))
    assert.ok(problems.some(problem => problem.includes('needs a bind target')), problems.join('; '))
  })

  it('真实的扩展槽位接口是干净的', () => {
    const clean = model({
      chunk_size: { bind: 'chunk_size', min: 64, max: 8192, step: 64, default: 1024, derive: 'chunk_size_constraint' },
      overlap: { bind: 'overlap', min: 0, max: 8, default: 2 },
      stems: { bind: 'target_instrument', choices: ['vocals', 'instrumental'] },
    })
    assert.deepEqual(validateInterface(clean), [])
  })
})

describe('守卫：布局档只认槽位', () => {
  it('所有内置布局档都合法', () => {
    for (const id of layoutIds()) {
      const problems = validateLayout(LAYOUTS[id])
      assert.deepEqual(problems, [], `${id}: ${problems.join('; ')}`)
    }
  })

  it('布局档里出现模型 id 判违规', () => {
    const problems = validateLayout({
      id: 'bad',
      sections: [{ id: 'quality', titleKey: 'section.quality', slots: ['audio:mel_band_roformer'] }],
    })
    assert.ok(problems.some(problem => problem.includes('references a model')), problems.join('; '))
  })

  it('未知槽位判违规', () => {
    const problems = validateLayout({
      id: 'bad',
      sections: [{ id: 'quality', titleKey: 'section.quality', slots: ['nonsense'] }],
    })
    assert.ok(problems.some(problem => problem.includes('unknown slot')), problems.join('; '))
  })
})

describe('词汇表', () => {
  it('每个槽位都有 kind 与 unit，且 id 与键一致', () => {
    for (const id of slotIds()) {
      const spec = slotSpec(id)!
      assert.equal(spec.id, id)
      assert.ok(spec.kind)
      assert.ok(spec.unit)
      assert.ok(['timeline', 'ratio', 'spectrum', 'grid', 'none'].includes(spec.diagram))
    }
  })

  it('derive 只指向后端已有的约束名', () => {
    // 后端 python/worker_inference_constraints.py 里存在的约束
    const known = new Set(['chunk_size_constraint'])
    for (const id of slotIds()) {
      const derive = slotSpec(id)!.derive
      if (derive) assert.ok(known.has(derive), `${id} 引用了不存在的约束 ${derive}`)
    }
  })

  it('每个任务族都有布局档（或落到默认档）', () => {
    for (const task of ['separation', 'upscale', 'restoration', 'matting', 'frame_interpolation']) {
      const profile = layoutFor(task)
      assert.ok(profile.id.startsWith('layout.'))
    }
    assert.equal(layoutFor('unknown-task').id, 'layout.default')
  })

  it('槽位总数稳定（防止有人悄悄删词汇）', () => {
    assert.equal(slotIds().length, 18)
  })
})
