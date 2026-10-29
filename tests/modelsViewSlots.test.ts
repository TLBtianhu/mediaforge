/**
 * 真实视图的接线守卫：模型参数对话框必须走渲染计划。
 *
 * 这是"解耦有没有真的落地"的最后一道关——纯函数测试全绿，但如果视图又回去
 * 写死 `overlap_size` 之类的字段，等于白做。所以这里扫视图源码。
 *
 * 允许存在的旧字段：没有槽位接口的模型（自定义导入）仍走原来的三个字段，
 * 这是明确的回退路径，不是遗漏。
 */
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, it } from 'node:test'

const view = readFileSync(join(process.cwd(), 'src/views/ModelsView.vue'), 'utf-8')

describe('ModelsView 的推断参数对话框', () => {
  it('用渲染计划生成表单', () => {
    assert.ok(view.includes('ModelSlotFields'), '对话框应渲染 ModelSlotFields')
    assert.ok(view.includes('buildRenderPlan'), '应从接口 + 布局档合成渲染计划')
    assert.ok(view.includes('layoutFor('), '应使用任务族的布局档')
  })

  it('槽位值与原生参数之间有明确翻译', () => {
    assert.ok(view.includes('fromWireParams'), '打开对话框时要把已保存参数翻回槽位')
    assert.ok(view.includes('toWireParams'), '保存时要把槽位翻回原生参数')
    assert.ok(view.includes('paramsFromInterface'), '没有已保存值时用接口默认值')
  })

  it('保留无接口模型的回退路径', () => {
    assert.ok(
      view.includes('v-else class="inference-editor-grid"'),
      '没有槽位接口的模型应回退到旧的三个字段',
    )
  })

  it('视图不认得具体模型', () => {
    for (const needle of ['mel_band', 'bs_roformer', 'spandrel:', 'audio:']) {
      assert.ok(!view.includes(needle), `视图里出现了模型标识 ${needle}`)
    }
  })
})
